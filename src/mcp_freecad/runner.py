"""Runner do mcp-freecad. Roda DENTRO do Python do freecadcmd, não do Python do servidor.

Por isso usa só stdlib e módulos do FreeCAD e não importa nada do resto do pacote.
Uso: freecadcmd runner.py job.json, com job = {"op": ..., "args": {...}, "result_path": ...}.

O resultado vai por arquivo JSON porque o stdout do freecadcmd é poluído (banner,
estatísticas do OCC) e uma exceção solta num script dele sai com rc=0. Aqui tudo é
capturado e vira {"ok": false, "error": ...}; o rc só reforça o status.
"""

import contextlib
import io
import json
import math
import os
import sys
import tempfile
import traceback

import FreeCAD
import Part

V = FreeCAD.Vector
MAX_OBJECTS = 100  # doc_inspect detalha só os primeiros: a placa de um KiCad passa de 1000 peças
MAX_STDOUT = 20_000
MAX_LISTED_FAILURES = 20
SOLID_EXT = {".step", ".stp", ".iges", ".igs", ".brep"}
MESH_EXT = {".stl", ".obj", ".3mf", ".ply"}

# Eixo do corte/projeção -> (normal do plano, linhas X', Y', Z' da matriz modelo -> vista).
# A vista nunca é espelhada: z = topo (olha de +Z), x = lateral direita (olha de +X),
# y = frontal (olha de -Y, para que X' continue sendo o x do modelo). Z' aponta para o
# observador e o plano de corte vira o plano XY, que é o que DXF/SVG de laser esperam.
_VIEWS = {
    "z": ((0, 0, 1), ((1, 0, 0), (0, 1, 0), (0, 0, 1))),
    "x": ((1, 0, 0), ((0, 1, 0), (0, 0, 1), (1, 0, 0))),
    "y": ((0, 1, 0), ((1, 0, 0), (0, 0, 1), (0, -1, 0))),
}


class UserError(Exception):
    """Falha esperada (entrada inválida): segue para o cliente sem traceback."""


def _r(value):
    return round(value, 4) + 0.0  # + 0.0 troca -0.0 por 0.0


def _bbox(bb):
    return {
        "x": _r(bb.XLength),
        "y": _r(bb.YLength),
        "z": _r(bb.ZLength),
        "min": [_r(bb.XMin), _r(bb.YMin), _r(bb.ZMin)],
        "max": [_r(bb.XMax), _r(bb.YMax), _r(bb.ZMax)],
    }


def _ext(path):
    return os.path.splitext(path)[1].lower()


def _size(path):
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        raise RuntimeError(f"a exportação não gerou {path}")
    return os.path.getsize(path)


def _load(path):
    """Devolve (kind, doc) para sólido/documento e ("mesh", malha) para malha."""
    ext = _ext(path)
    if ext == ".fcstd":
        return "document", FreeCAD.openDocument(path)
    if ext in SOLID_EXT:
        doc = FreeCAD.newDocument("entrada")
        if ext in (".step", ".stp"):
            import Import

            Import.insert(path, doc.Name)  # mantém nomes e hierarquia do STEP
        else:
            # Import.insert não cria objeto nenhum para IGES em headless (medido no 1.1.4).
            doc.addObject("Part::Feature", "forma").Shape = Part.read(path)
        return "solid", doc
    if ext in MESH_EXT:
        import Mesh

        return "mesh", Mesh.Mesh(path)
    raise UserError(f"extensão de entrada não suportada: {ext or '(sem extensão)'}")


def _is_shape(obj):
    # hasattr(obj, "Shape") não serve: App::Line/App::Plane também respondem a ele.
    return obj.isDerivedFrom("Part::Feature") and not obj.Shape.isNull()


def _final(doc):
    """Objetos finais: com forma e não consumidos por outra feature (Body, Cut, Pad...)."""
    return [
        o
        for o in doc.Objects
        if _is_shape(o) and not any(p.isDerivedFrom("Part::Feature") for p in o.InList)
    ]


def _pick(doc, names):
    """Objetos pedidos por Name ou Label; sem `names`, os objetos finais."""
    if not names:
        objs = _final(doc)
        if not objs:
            raise UserError("nenhum objeto com forma sólida (Part::Feature) no documento")
        return objs
    objs, missing = [], []
    for name in names:
        hit = [doc.getObject(name)] if doc.getObject(name) else doc.getObjectsByLabel(name)
        if hit:
            objs.extend(hit)
        else:
            missing.append(name)
    if missing:
        raise UserError(
            f"objeto(s) não encontrado(s): {missing}. "
            f"Disponíveis (Name): {[o.Name for o in _final(doc)][:MAX_OBJECTS]}"
        )
    return objs


def _shape(obj):
    """Forma do objeto no espaço global do documento.

    Nem obj.Shape nem Part.getShape(obj) trazem o Placement dos App::Part que contêm o
    objeto (medido no 1.1.4): numa montagem aninhada as peças ficariam todas perto da
    origem. O novo handle compartilha a geometria; só a posição dele muda.
    """
    shape = Part.Shape(obj.Shape)
    shape.Placement = obj.getGlobalPlacement()
    return shape


def _placed(objs):
    """Part::Feature soltos, com a forma já no espaço global, para os exportadores de CAD.

    Part.export grava o Placement local de cada objeto e ignora o dos App::Part.
    """
    doc = FreeCAD.newDocument("posicionados")
    features = []
    for obj in objs:
        feature = doc.addObject("Part::Feature", obj.Name)
        feature.Label = obj.Label
        feature.Shape = _shape(obj)
        features.append(feature)
    return features


def _describe(obj):
    s = _shape(obj)
    return {
        "name": obj.Name,
        "label": obj.Label,
        "type": obj.TypeId,
        "bbox": _bbox(s.BoundBox),
        "volume_mm3": _r(s.Volume),
        "area_mm2": _r(s.Area),
        "solids": len(s.Solids),
        "faces": len(s.Faces),
        "is_valid": s.isValid(),
    }


def _tessellate(objs, linear, angular):
    import MeshPart

    shape = Part.Compound([_shape(o) for o in objs])
    return MeshPart.meshFromShape(
        Shape=shape, LinearDeflection=linear, AngularDeflection=angular, Relative=False
    )


def _to_view(shape, axis, offset):
    """Leva o modelo para a vista de `axis` com o plano `axis=offset` em Z'=0."""
    normal, rows = _VIEWS[axis]
    shift = -sum(a * b for a, b in zip(rows[2], normal, strict=True)) * offset
    matrix = FreeCAD.Matrix(*rows[0], 0, *rows[1], 0, *rows[2], shift, 0, 0, 0, 1)
    return shape.transformed(matrix)


# --- operações -------------------------------------------------------------


def op_version(_args):
    return {"version": ".".join(FreeCAD.Version()[:3]), "python": sys.version.split()[0]}


def op_inspect(args):
    path = args["path"]
    kind, src = _load(path)
    out = {"file": path, "kind": kind, "units": "mm"}

    if kind == "mesh":
        solid, non_manifold = src.isSolid(), src.hasNonManifolds()
        out["mesh"] = {
            "facets": src.CountFacets,
            "points": src.CountPoints,
            "is_solid": solid,
            "non_manifold": non_manifold,
            "self_intersections": src.hasSelfIntersections(),
            "volume_mm3": _r(src.Volume),
            "area_mm2": _r(src.Area),
            "bbox": _bbox(src.BoundBox),
        }
        out["checks"] = {"watertight": solid and not non_manifold}
        return out

    objs = _pick(src, None)
    bb = FreeCAD.BoundBox()
    for o in objs:
        bb.add(_shape(o).BoundBox)
    out["bbox"] = _bbox(bb)
    out["objects_total"] = len(objs)
    out["objects"] = [_describe(o) for o in objs[:MAX_OBJECTS]]
    if len(objs) > MAX_OBJECTS:
        out["truncated"] = True
    # Numa montagem um objeto solto derruba o veredito inteiro: diga quais são (até 20).
    open_ = [o.Label for o in objs if not (o.Shape.Solids and o.Shape.isClosed())]
    invalid = [o.Label for o in objs if not o.Shape.isValid()]
    out["checks"] = {"watertight": not open_, "all_valid": not invalid}
    if open_:
        out["checks"]["not_watertight"] = open_[:MAX_LISTED_FAILURES]
    if invalid:
        out["checks"]["invalid"] = invalid[:MAX_LISTED_FAILURES]
    return out


def op_export(args):
    path, out = args["path"], args["out_path"]
    ext, names = _ext(out), args.get("objects")
    if ext not in MESH_EXT | SOLID_EXT | {".fcstd"}:
        raise UserError(f"extensão de saída não suportada: {ext or '(sem extensão)'}")
    kind, src = _load(path)
    result = {"out_path": out}

    if kind == "mesh":
        if names:
            raise UserError("`objects` não se aplica a malha")
        if ext in SOLID_EXT:
            raise UserError(
                "a fonte é uma malha: .step/.iges/.brep exigem sólido "
                "(converter malha em sólido gera uma face por triângulo)"
            )
        if ext == ".fcstd":
            doc = FreeCAD.newDocument("saida")
            doc.addObject("Mesh::Feature", "Malha").Mesh = src
            doc.saveAs(out)
        else:
            src.write(out)
            result["facets"] = src.CountFacets
    elif ext in MESH_EXT:
        angular = math.radians(args["angular_deflection_deg"])
        mesh = _tessellate(_pick(src, names), args["linear_deflection"], angular)
        mesh.write(out)  # o formato sai da extensão
        result["facets"] = mesh.CountFacets
    elif ext == ".fcstd":
        if names:
            objs = _pick(src, names)
            dst = FreeCAD.newDocument("saida")
            copies = dst.copyObject(objs, True)
            dst.recompute()  # a booleana zera o Placement aqui: o ajuste vem depois
            for original, copy in zip(objs, copies, strict=True):
                # a cópia sai da montagem: devolve a posição que o objeto tinha nela
                if original.getGlobalPlacement() != original.Placement:
                    copy.Placement = original.getGlobalPlacement()
            dst.saveAs(out)
        else:
            src.saveAs(out)
    else:
        Part.export(_placed(_pick(src, names)), out)

    result["bytes"] = _size(out)
    return result


def op_export_2d(args):
    path, out, mode = args["path"], args["out_path"], args["mode"]
    axis, offset = args["axis"], args["offset"]
    ext = _ext(out)
    if ext not in (".svg", ".dxf"):
        raise UserError(f"extensão de saída deve ser .svg ou .dxf, recebido: {ext or '(nenhuma)'}")
    kind, src = _load(path)
    if kind == "mesh":
        raise UserError("doc_export_2d exige sólido ou documento (STEP, IGES, BREP, FCStd)")

    model = Part.Compound([_shape(o) for o in _pick(src, args.get("objects"))])
    view = _to_view(model, axis, offset)
    if mode == "section":
        edges = view.slice(V(0, 0, 1), 0.0)
        if not edges:
            bb = model.BoundBox
            lo, hi = getattr(bb, axis.upper() + "Min"), getattr(bb, axis.upper() + "Max")
            raise UserError(
                f"o plano {axis}={offset:g} não corta o modelo "
                f"(faixa do modelo em {axis}: {lo:.3f} a {hi:.3f})"
            )
    else:
        import TechDraw

        # Visíveis: arestas vivas (V) e silhueta (VO). É o que TechDraw.projectToSVG
        # desenha; TechDraw.project() omite a silhueta e esfera/cilindro ficam sem contorno.
        proj = TechDraw.projectEx(view, V(0, 0, 1))
        edges = proj[0].Edges + proj[3].Edges
        if not edges:
            raise UserError("a projeção não tem arestas visíveis")

    # DXF/SVG saem do Draft a partir de um objeto do documento; o objeto só existe aqui.
    feature = src.addObject("Part::Feature", "Perfil")
    feature.Shape = Part.Compound(edges)
    if ext == ".dxf":
        import importDXF

        importDXF.export([feature], out)
    else:
        import importSVG

        importSVG.export([feature], out)
    return {"out_path": out, "bytes": _size(out), "wires": len(edges)}


def _written_since(root, since_ns):
    """Arquivos de `root` criados ou alterados desde `since_ns`, numa varredura só.

    Usa o ctime, que muda em criação, escrita e rename e não se forja com utime: pega até
    o arquivo copiado com a data original. É uma varredura, e não uma antes e outra depois,
    porque out_dir pode ser o home inteiro. Entrada que some ou nega o stat no meio do
    caminho é ignorada: não pode derrubar um script que deu certo.
    """
    found = []
    for base, _dirs, names in os.walk(root):
        for name in names:
            path = os.path.join(base, name)
            try:
                st = os.lstat(path)
            except OSError:
                continue
            if st.st_ctime_ns >= since_ns:
                found.append({"path": path, "bytes": st.st_size})
    return sorted(found, key=lambda f: f["path"])


def op_run_script(args):
    out_dir = args["out_dir"]
    # O ctime de um arquivo criado agora, em out_dir, marca o início. Criar no próprio
    # out_dir mantém a granularidade de timestamp do sistema de arquivos dos dois lados.
    with tempfile.TemporaryFile(dir=out_dir) as marker:
        since = os.fstat(marker.fileno()).st_ctime_ns
    doc = FreeCAD.newDocument("script")
    # __name__ = "__main__": scripts com `if __name__ == "__main__":` rodariam em silêncio.
    # FreeCAD/App/Part já importados: quase todo script os usa e o NameError custa uma volta.
    namespace = {
        "__name__": "__main__",
        "doc": doc,
        "OUT_DIR": out_dir,
        "FreeCAD": FreeCAD,
        "App": FreeCAD,
        "Part": Part,
    }
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                exec(compile(args["script"], "<script>", "exec"), namespace)
            except SystemExit as exc:
                if exc.code not in (None, 0):  # sys.exit() e sys.exit(0) são fim normal
                    raise
        doc.recompute()
    except BaseException as exc:
        exc.script_stdout = buf.getvalue()[-MAX_STDOUT:]  # vai junto do erro para depuração
        raise
    stdout = buf.getvalue()
    if len(stdout) > MAX_STDOUT:
        stdout = stdout[:MAX_STDOUT] + f"\n[truncado em {MAX_STDOUT} caracteres]"
    return {
        "stdout": stdout,
        "files": _written_since(out_dir, since),
        "objects": [
            {
                "name": o.Name,
                "type": o.TypeId,
                "volume_mm3": _r(o.Shape.Volume),
                "bbox": _bbox(_shape(o).BoundBox),
            }
            for o in _final(doc)[:MAX_OBJECTS]
        ],
    }


OPS = {
    "version": op_version,
    "inspect": op_inspect,
    "export": op_export,
    "export_2d": op_export_2d,
    "run_script": op_run_script,
}


def _short_tb(exc):
    """Frames do script do usuário, se houver; senão os últimos frames do runner."""
    frames = traceback.extract_tb(exc.__traceback__)
    mine = [f for f in frames if f.filename == "<script>"]
    return "".join(traceback.format_list(mine or frames[-6:]))[-3000:]


def main(job_path):
    with open(job_path, encoding="utf-8") as f:
        job = json.load(f)
    try:
        payload = {"ok": True, "result": OPS[job["op"]](job["args"])}
        text = json.dumps(payload)
    except BaseException as exc:  # inclui SystemExit de script: nada pode escapar sem result.json
        error = {"type": type(exc).__name__, "message": str(exc), "traceback": ""}
        if not isinstance(exc, (UserError, SyntaxError)):  # SyntaxError já traz a linha
            error["traceback"] = _short_tb(exc)
        if hasattr(exc, "script_stdout"):
            error["stdout"] = exc.script_stdout
        payload = {"ok": False, "error": error}
        text = json.dumps(payload)
    with open(job["result_path"], "w", encoding="utf-8") as f:
        f.write(text)
    raise SystemExit(0 if payload["ok"] else 1)


# O freecadcmd executa este arquivo com __name__ diferente de "__main__": chama direto.
main(sys.argv[-1])
