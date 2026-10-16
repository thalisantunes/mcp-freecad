"""Integração com o freecadcmd de verdade. Pula se o FreeCAD não for encontrado."""

import math
import os
import re
import subprocess
import time
from collections import Counter

import pytest

from mcp_freecad import server
from mcp_freecad.errors import FreecadNotFound
from mcp_freecad.freecad_cmd import resolve_cmd


def _freecad_available() -> bool:
    try:
        resolve_cmd()
    except FreecadNotFound:
        return False
    return True


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _freecad_available(), reason="FreeCAD (freecadcmd) não encontrado"),
]

BOX_VOLUME = 40 * 30 * 20


def run(script, out_dir, **kwargs):
    result = server.part_run_script(script, str(out_dir), **kwargs)
    assert "error" not in result, result
    return result


def dxf_pairs(path):
    """O DXF é uma sequência de pares (código de grupo, valor), um por linha."""
    lines = [line.strip() for line in path.read_text().splitlines()]
    return list(zip(lines[::2], lines[1::2], strict=False))


def dxf_entities(path):
    kinds = {"LINE", "CIRCLE", "ARC", "SPLINE", "LWPOLYLINE", "POLYLINE", "ELLIPSE"}
    return Counter(v for code, v in dxf_pairs(path) if code == "0" and v in kinds)


def dxf_line_points(path):
    """Extremos (x, y, z) de todos os LINE do DXF."""
    points, line = set(), None
    for code, value in [*dxf_pairs(path), ("0", "FIM")]:
        if code == "0":
            if line is not None:
                points.add((line["10"], line["20"], line["30"]))
                points.add((line["11"], line["21"], line["31"]))
            line = {} if value == "LINE" else None
        elif line is not None and code in ("10", "20", "30", "11", "21", "31"):
            line[code] = round(float(value), 3)
    return points


@pytest.fixture(scope="module")
def models(tmp_path_factory):
    """Peças geradas uma vez: caixa, cilindro, esfera, peça em L, 120 caixinhas e uma montagem."""
    out = tmp_path_factory.mktemp("models")
    run(
        """
V = FreeCAD.Vector
Part.makeBox(40, 30, 20).exportStep(OUT_DIR + "/box.step")
Part.makeCylinder(5, 20).exportStep(OUT_DIR + "/cyl.step")
Part.makeSphere(8).exportStep(OUT_DIR + "/sph.step")
# Peça em L assimétrica com furo: o bloco alto fica no canto x 30..40, y 20..30.
base = Part.makeBox(40, 30, 20).fuse(Part.makeBox(10, 10, 30, V(30, 20, 0)))
base.cut(Part.makeCylinder(4, 40, V(10, 10, -5))).exportStep(OUT_DIR + "/lpart.step")
Part.Compound([Part.makeBox(1, 1, 1, V(i * 2, 0, 0)) for i in range(120)]).exportStep(
    OUT_DIR + "/many.step")
a = doc.addObject("Part::Box", "Caixa"); a.Length, a.Width, a.Height = 10, 10, 10
b = doc.addObject("Part::Cylinder", "Tubo"); b.Radius, b.Height = 5, 20
b.Placement.Base = V(50, 0, 0)
doc.recompute()
doc.saveAs(OUT_DIR + "/duas.FCStd")
mista = FreeCAD.newDocument("mista")
mista.addObject("Part::Box", "Caixa")
folha = mista.addObject("Part::Plane", "Folha")
folha.Length, folha.Width = 5, 5
mista.recompute()
mista.saveAs(OUT_DIR + "/mista.FCStd")
# Montagem aninhada: Conjunto desloca x em 100 e Sub, dentro dele, gira 90 graus e desloca y.
P, R = FreeCAD.Placement, FreeCAD.Rotation
mont = FreeCAD.newDocument("montagem")
conjunto = mont.addObject("App::Part", "Conjunto")
conjunto.Placement = P(V(100, 0, 0), R())
cb = mont.addObject("Part::Box", "CaixaB"); cb.Length = cb.Width = cb.Height = 10
cb.Placement = P(V(0, 0, 5), R())
conjunto.addObject(cb)
sub = mont.addObject("App::Part", "Sub")
sub.Placement = P(V(0, 20, 0), R(V(0, 0, 1), 90))
conjunto.addObject(sub)
cc = mont.addObject("Part::Box", "CaixaC"); cc.Length, cc.Width, cc.Height = 10, 4, 2
sub.addObject(cc)
solta = mont.addObject("Part::Box", "Solta"); solta.Length = solta.Width = solta.Height = 3
solta.Placement = P(V(-50, 0, 0), R())
mont.recompute()
mont.saveAs(OUT_DIR + "/montagem.FCStd")
""",
        out,
    )
    return out


def test_version():
    result = server.freecad_version()
    assert result["version"].startswith("1."), result
    assert result["python"].startswith("3."), result
    assert result["command"] and result["discovered_by"]


def test_step_to_stl_to_inspect(models, tmp_path):
    info = server.doc_inspect(str(models / "box.step"))
    assert info["kind"] == "solid" and info["units"] == "mm"
    assert info["bbox"]["x"] == 40 and info["bbox"]["y"] == 30 and info["bbox"]["z"] == 20
    assert info["objects"][0]["solids"] == 1
    assert info["checks"] == {"watertight": True, "all_valid": True}
    assert info["objects"][0]["volume_mm3"] == pytest.approx(BOX_VOLUME, rel=0.01)

    stl = tmp_path / "box.stl"
    exported = server.doc_export(str(models / "box.step"), str(stl))
    assert exported["facets"] == 12 and exported["bytes"] > 0

    mesh = server.doc_inspect(str(stl))
    assert mesh["kind"] == "mesh"
    assert mesh["mesh"]["volume_mm3"] == pytest.approx(BOX_VOLUME, rel=0.01)
    assert mesh["mesh"]["is_solid"] and not mesh["mesh"]["non_manifold"]
    assert mesh["checks"]["watertight"] is True


@pytest.mark.parametrize("ext", ["3mf", "obj", "ply", "brep", "iges", "stp", "FCStd"])
def test_export_formats_keep_the_volume(models, tmp_path, ext):
    out = tmp_path / f"box.{ext}"
    result = server.doc_export(str(models / "box.step"), str(out))
    assert result["bytes"] > 0 and out.stat().st_size == result["bytes"]
    info = server.doc_inspect(str(out))
    volume = (
        info["mesh"]["volume_mm3"] if info["kind"] == "mesh" else info["objects"][0]["volume_mm3"]
    )
    assert volume == pytest.approx(BOX_VOLUME, rel=0.01)


def test_deflection_controls_facet_count(models, tmp_path):
    coarse = server.doc_export(
        str(models / "cyl.step"), str(tmp_path / "c.stl"), linear_deflection=1
    )
    fine = server.doc_export(
        str(models / "cyl.step"), str(tmp_path / "f.stl"), linear_deflection=0.01
    )
    assert fine["facets"] > coarse["facets"] * 2


def test_mesh_roundtrip_and_unsupported_conversions(models, tmp_path):
    stl = tmp_path / "box.stl"
    server.doc_export(str(models / "box.step"), str(stl))
    obj = server.doc_export(str(stl), str(tmp_path / "box.obj"))
    assert obj["facets"] == 12
    assert "exigem sólido" in server.doc_export(str(stl), str(tmp_path / "x.step"))["error"]
    assert server.doc_export(str(stl), str(tmp_path / "x.FCStd"))["bytes"] > 0


def test_objects_filter_by_name_and_label(models, tmp_path):
    info = server.doc_inspect(str(models / "duas.FCStd"))
    assert {o["name"] for o in info["objects"]} == {"Caixa", "Tubo"}
    assert info["objects_total"] == 2

    tube = tmp_path / "tubo.stl"
    server.doc_export(str(models / "duas.FCStd"), str(tube), objects=["Tubo"])
    volume = server.doc_inspect(str(tube))["mesh"]["volume_mm3"]
    assert volume == pytest.approx(math.pi * 5**2 * 20, rel=0.01)

    one = tmp_path / "caixa.FCStd"
    server.doc_export(str(models / "duas.FCStd"), str(one), objects=["Caixa"])
    assert [o["name"] for o in server.doc_inspect(str(one))["objects"]] == ["Caixa"]

    missing = server.doc_export(
        str(models / "duas.FCStd"), str(tmp_path / "x.stl"), objects=["Nada"]
    )
    assert "Nada" in missing["error"] and "Tubo" in missing["error"]


def test_inspect_names_the_objects_that_fail_the_checks(models):
    checks = server.doc_inspect(str(models / "mista.FCStd"))["checks"]
    assert checks == {"watertight": False, "all_valid": True, "not_watertight": ["Folha"]}


def test_inspect_truncates_long_object_lists(models):
    info = server.doc_inspect(str(models / "many.step"))
    assert info["objects_total"] == 120
    assert len(info["objects"]) == 100 and info["truncated"] is True
    assert info["bbox"]["x"] == pytest.approx(239)  # cobre os 120, não só os listados


def test_section_of_cylinder_is_one_closed_wire(models, tmp_path):
    dxf = tmp_path / "cyl.dxf"
    result = server.doc_export_2d(
        str(models / "cyl.step"), str(dxf), mode="section", axis="z", offset=10
    )
    assert result["wires"] == 1 and result["bytes"] > 0
    assert dxf_entities(dxf) == Counter({"CIRCLE": 1})

    svg = tmp_path / "cyl.svg"
    server.doc_export_2d(str(models / "cyl.step"), str(svg), mode="section", axis="z", offset=10)
    text = svg.read_text()
    assert text.lstrip().startswith("<?xml") and "<svg" in text
    assert re.search(r'width="[\d.]+mm"', text), "o SVG precisa vir em mm para o laser"
    assert "<circle" in text


@pytest.mark.parametrize(
    ("axis", "offset", "corners", "absent"),
    [
        # Plano z=10: contorno em L (6 lados) + furo.
        ("z", 10, {(0, 0), (40, 0), (40, 30), (30, 30), (0, 30)}, set()),
        # Plano y=25 vira (x, z): o bloco alto (x 30..40, z até 30) fica à direita e em cima.
        ("y", 25, {(0, 0), (40, 0), (40, 30), (30, 30), (30, 20), (0, 20)}, {(0, 30)}),
        # Plano x=35 vira (y, z): o bloco alto (y 20..30) fica à direita e em cima.
        ("x", 35, {(0, 0), (30, 0), (30, 30), (20, 30), (20, 20), (0, 20)}, {(0, 30)}),
    ],
)
def test_section_axes_are_planar_and_not_mirrored(models, tmp_path, axis, offset, corners, absent):
    dxf = tmp_path / f"{axis}.dxf"
    server.doc_export_2d(
        str(models / "lpart.step"), str(dxf), mode="section", axis=axis, offset=offset
    )
    points = dxf_line_points(dxf)
    assert {p[2] for p in points} == {0.0}, "DXF de laser precisa ser plano em Z=0"
    xy = {(p[0], p[1]) for p in points}
    assert corners <= xy
    assert not (absent & xy)


def test_section_that_misses_the_model_explains_the_range(models, tmp_path):
    result = server.doc_export_2d(
        str(models / "lpart.step"), str(tmp_path / "x.dxf"), mode="section", axis="z", offset=100
    )
    assert "não corta o modelo" in result["error"] and "0.000 a 30.000" in result["error"]


def test_projection_svg_and_dxf(models, tmp_path):
    svg = tmp_path / "top.svg"
    result = server.doc_export_2d(str(models / "lpart.step"), str(svg), mode="projection", axis="z")
    assert result["wires"] == 9
    assert "<svg" in svg.read_text()

    dxf = tmp_path / "top.dxf"
    server.doc_export_2d(str(models / "lpart.step"), str(dxf), mode="projection", axis="z")
    assert dxf_entities(dxf) == Counter({"LINE": 8, "CIRCLE": 1})


def test_projection_keeps_the_silhouette_of_round_parts(models, tmp_path):
    # TechDraw.project() devolve 0 arestas para a esfera: a silhueta é um tipo à parte.
    dxf = tmp_path / "sph.dxf"
    assert server.doc_export_2d(str(models / "sph.step"), str(dxf), axis="z")["wires"] == 1
    assert dxf_entities(dxf) == Counter({"CIRCLE": 1})
    side = server.doc_export_2d(str(models / "sph.step"), str(tmp_path / "s2.dxf"), axis="x")
    assert side["wires"] >= 1


def test_export_2d_input_and_output_validation(models, tmp_path):
    stl = tmp_path / "box.stl"
    server.doc_export(str(models / "box.step"), str(stl))
    assert "exige sólido" in server.doc_export_2d(str(stl), str(tmp_path / "a.dxf"))["error"]
    assert (
        ".svg ou .dxf"
        in server.doc_export_2d(str(models / "box.step"), str(tmp_path / "a.png"))["error"]
    )


def test_run_script_saves_stl_and_reports_outputs(tmp_path):
    result = run(
        """
box = Part.makeBox(40, 30, 20)
feature = doc.addObject("Part::Feature", "Caixa")
feature.Shape = box
box.exportStl(OUT_DIR + "/caixa.stl")
print("volume", box.Volume)
""",
        tmp_path,
    )
    assert "volume 24000" in result["stdout"]
    assert [f["path"] for f in result["files"]] == [str(tmp_path / "caixa.stl")]
    assert result["files"][0]["bytes"] > 0
    assert result["objects"][0]["name"] == "Caixa"
    assert result["objects"][0]["volume_mm3"] == pytest.approx(BOX_VOLUME)
    assert server.doc_inspect(str(tmp_path / "caixa.stl"))["mesh"]["volume_mm3"] == pytest.approx(
        BOX_VOLUME, rel=0.01
    )


def test_run_script_lists_overwritten_files_and_ignores_untouched_ones(tmp_path):
    run("Part.makeBox(1, 1, 1).exportStl(OUT_DIR + '/a.stl')", tmp_path)
    second = run("Part.makeBox(2, 2, 2).exportStl(OUT_DIR + '/a.stl')", tmp_path)
    assert [f["path"] for f in second["files"]] == [str(tmp_path / "a.stl")]
    assert run("print('nada')", tmp_path)["files"] == []


def test_run_script_name_is_main(tmp_path):
    result = run("if __name__ == '__main__':\n    print('rodou')", tmp_path)
    assert "rodou" in result["stdout"]


def test_script_error_returns_error_and_server_keeps_working(tmp_path):
    result = server.part_run_script("print('antes')\n1 / 0", str(tmp_path))
    assert result["error"].startswith("[MF-OP-1] ZeroDivisionError")
    assert 'File "<script>", line 2' in result["error"]
    assert "antes" in result["error"]
    assert server.freecad_version()["version"]

    syntax = server.part_run_script("x = (", str(tmp_path))
    assert "SyntaxError" in syntax["error"] and "line 1" in syntax["error"]


def test_script_timeout_kills_freecad(tmp_path):
    result = server.part_run_script("while True:\n    pass", str(tmp_path), timeout_s=5)
    assert result["error"].startswith("[MF-RUN-2]")
    ps = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout
    leftovers = [line for line in ps.splitlines() if "mcp_freecad/runner.py" in line]
    assert leftovers == [], "sobrou processo do FreeCAD depois do timeout"


# --- App::Part com Placement: a forma de cada peça tem de vir no espaço global ------------


def assert_bbox(bbox, low, high):
    assert bbox["min"] == pytest.approx(low, abs=1e-3)
    assert bbox["max"] == pytest.approx(high, abs=1e-3)


def test_app_part_placements_are_applied_to_nested_objects(models):
    # obj.Shape e Part.getShape(obj) devolvem a forma local: sem o Placement dos App::Part
    # a CaixaB ficaria em x 0..10 e a CaixaC em x -4..0.
    info = server.doc_inspect(str(models / "montagem.FCStd"))
    assert_bbox(info["bbox"], [-50, 0, 0], [110, 30, 15])
    boxes = {o["name"]: o["bbox"] for o in info["objects"]}
    assert set(boxes) == {"CaixaB", "CaixaC", "Solta"}
    assert_bbox(boxes["CaixaB"], [100, 0, 5], [110, 10, 15])
    assert_bbox(boxes["CaixaC"], [96, 20, 0], [100, 30, 2])
    assert_bbox(boxes["Solta"], [-50, 0, 0], [-47, 3, 3])


@pytest.mark.parametrize("ext", ["stl", "3mf", "step", "brep", "iges", "FCStd"])
def test_exports_keep_the_assembly_positions(models, tmp_path, ext):
    out = tmp_path / f"montagem.{ext}"
    server.doc_export(str(models / "montagem.FCStd"), str(out))
    info = server.doc_inspect(str(out))
    bbox = info["mesh"]["bbox"] if info["kind"] == "mesh" else info["bbox"]
    assert_bbox(bbox, [-50, 0, 0], [110, 30, 15])


def test_fcstd_export_with_a_filter_keeps_the_assembly_positions(models, tmp_path):
    out = tmp_path / "duas.FCStd"
    server.doc_export(str(models / "montagem.FCStd"), str(out), objects=["CaixaB", "CaixaC"])
    boxes = {o["name"]: o["bbox"] for o in server.doc_inspect(str(out))["objects"]}
    assert set(boxes) == {"CaixaB", "CaixaC"}
    assert_bbox(boxes["CaixaB"], [100, 0, 5], [110, 10, 15])
    assert_bbox(boxes["CaixaC"], [96, 20, 0], [100, 30, 2])


def test_2d_export_of_an_assembly_uses_global_coordinates(models, tmp_path):
    dxf = tmp_path / "corte.dxf"
    # z=1 corta a CaixaC (z 0..2) e a Solta (z 0..3); a CaixaB (z 5..15) fica de fora.
    result = server.doc_export_2d(
        str(models / "montagem.FCStd"), str(dxf), mode="section", axis="z", offset=1
    )
    assert result["wires"] == 2
    corners = {(p[0], p[1]) for p in dxf_line_points(dxf)}
    assert {(96, 20), (100, 30), (-50, 0), (-47, 3)} <= corners


# --- run_script: fim do script e arquivos ------------------------------------------------


def test_sys_exit_zero_in_a_script_is_a_normal_end(tmp_path):
    result = run(
        """
doc.addObject("Part::Box", "Caixa")
Part.makeBox(1, 1, 1).exportStl(OUT_DIR + "/a.stl")
print("antes")
import sys
sys.exit(0)
print("depois")
""",
        tmp_path,
    )
    assert "antes" in result["stdout"] and "depois" not in result["stdout"]
    assert [f["path"] for f in result["files"]] == [str(tmp_path / "a.stl")]
    assert result["objects"][0]["volume_mm3"] == pytest.approx(1000), "o recompute não rodou"
    run("import sys\nsys.exit()", tmp_path)  # sem argumento também é fim normal


def test_sys_exit_nonzero_in_a_script_is_an_error(tmp_path):
    result = server.part_run_script("import sys\nprint('saindo')\nsys.exit(3)", str(tmp_path))
    assert result["error"].startswith("[MF-OP-1] SystemExit: 3")
    assert "saindo" in result["error"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignora permissão de diretório")
def test_entries_that_cannot_be_statted_do_not_fail_a_successful_script(tmp_path):
    script = """
import os
os.makedirs(OUT_DIR + "/fechada")
open(OUT_DIR + "/fechada/x.txt", "w").write("x")
os.chmod(OUT_DIR + "/fechada", 0o444)  # dá para listar, mas não para dar stat nas entradas
open(OUT_DIR + "/ok.txt", "w").write("ok")
"""
    try:
        result = run(script, tmp_path)
    finally:
        os.chmod(tmp_path / "fechada", 0o755)
    assert [f["path"] for f in result["files"]] == [str(tmp_path / "ok.txt")]


def test_a_file_copied_with_its_original_date_is_still_listed(tmp_path):
    original = tmp_path / "original.txt"
    original.write_text("x")
    long_ago = time.time() - 86400
    os.utime(original, (long_ago, long_ago))
    out = tmp_path / "saida"
    result = run(f"import shutil\nshutil.copy2({str(original)!r}, OUT_DIR + '/copia.txt')", out)
    assert [f["path"] for f in result["files"]] == [str(out / "copia.txt")]


def test_integration_tests_keep_the_users_freecad_command(users_freecad_cmd):
    # O skipif já contou com essa variável: se ela fosse apagada aqui, o teste falharia
    # em vez de pular.
    assert os.environ.get("MCP_FREECAD_CMD") == users_freecad_cmd
    assert "MCP_FREECAD_ALLOWED_PATHS" not in os.environ
