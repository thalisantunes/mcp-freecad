"""Servidor MCP.

Toda operação roda no runner, dentro do freecadcmd (ADR 0001): a tool valida os
caminhos, monta o job e devolve o resultado. Erros do projeto viram {"error": ...}.
"""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import MCPServer

from . import __version__, freecad_cmd
from .errors import InvalidArgument, InvalidPath, McpFreecadError
from .paths import check_path

# mcp>=2.0: MCPServer substituiu FastMCP (era mcp.server.fastmcp.FastMCP).
mcp = MCPServer("mcp-freecad", version=__version__)


def _guard(fn, *args, **kwargs) -> Any:
    """Converte erros do projeto em mensagem legível em vez de stack trace."""
    try:
        return fn(*args, **kwargs)
    except McpFreecadError as exc:
        return {"error": str(exc)}
    except OSError as exc:  # criar pasta de saída, ler arquivo: problema no caminho do usuário
        return {"error": str(InvalidPath(str(exc)))}


# --- Ambiente --------------------------------------------------------------


@mcp.tool()
def freecad_version() -> dict[str, Any]:
    """Versão do FreeCAD, do Python embutido e qual comando o servidor está usando.

    Primeira coisa a chamar quando algo não funciona: confirma que o freecadcmd foi
    encontrado e por qual regra (env, PATH, caminho conhecido, flatpak, snap).
    """
    return _guard(freecad_cmd.version)


# --- Documentos (arquivo → freecadcmd) -------------------------------------


@mcp.tool()
def doc_inspect(path: str) -> dict[str, Any]:
    """Abre um modelo e devolve dimensões, volume, validade e se é estanque (watertight).

    Aceita .FCStd, .step/.stp, .iges/.igs, .brep e malhas .stl/.obj/.3mf/.ply. Unidades
    sempre em mm. Para sólidos e documentos lista os objetos finais (até 100;
    `objects_total` diz quantos existem) com `name` e `label`: qualquer um dos dois serve
    no parâmetro `objects` de doc_export e doc_export_2d. Para malha devolve `mesh`
    (facetas, is_solid, non_manifold, self_intersections). `checks.watertight` é o que
    decide se a peça imprime em FDM.
    """
    return _guard(lambda: freecad_cmd.run_job("inspect", {"path": str(check_path(path, False))}))


@mcp.tool()
def doc_export(
    path: str,
    out_path: str,
    objects: list[str] | None = None,
    linear_deflection: float = 0.1,
    angular_deflection_deg: float = 28.6,
) -> dict[str, Any]:
    """Converte um modelo para o formato da extensão de `out_path`.

    Saídas: .step/.stp, .iges/.igs, .brep, .FCStd e malhas .stl/.3mf/.obj/.ply. Ao gerar
    malha a partir de sólido, `linear_deflection` (mm) e `angular_deflection_deg`
    controlam a fineza: valores menores dão mais facetas. `objects` filtra por Name ou
    Label; sem ele exporta todos os objetos finais. Malha não vira .step/.iges/.brep,
    só .FCStd ou outra malha. Não sobrescreve a própria entrada.
    """

    def run() -> dict[str, Any]:
        if linear_deflection <= 0 or angular_deflection_deg <= 0:
            raise InvalidArgument("linear_deflection e angular_deflection_deg devem ser > 0")
        src, dst = check_path(path, False), check_path(out_path, True)
        # samefile pega o hardlink e, em sistema de arquivos que ignora caixa (APFS), o mesmo
        # arquivo com outra grafia: o realpath sozinho não enxerga nenhum dos dois.
        if src == dst or (dst.exists() and src.samefile(dst)):
            raise InvalidPath(f"a saída é o próprio arquivo de entrada: {dst}")
        return freecad_cmd.run_job(
            "export",
            {
                "path": str(src),
                "out_path": str(dst),
                "objects": objects,
                "linear_deflection": linear_deflection,
                "angular_deflection_deg": angular_deflection_deg,
            },
        )

    return _guard(run)


@mcp.tool()
def doc_export_2d(
    path: str,
    out_path: str,
    mode: Literal["section", "projection"] = "projection",
    axis: Literal["x", "y", "z"] = "z",
    offset: float = 0.0,
    objects: list[str] | None = None,
) -> dict[str, Any]:
    """Gera .svg ou .dxf em mm a partir de um sólido: perfil de corte a laser ou desenho.

    `mode="section"` corta o modelo pelo plano `eixo=offset` e exporta os contornos
    (para laser, use este). `mode="projection"` exporta a vista com arestas visíveis e
    silhueta, boa como preview ou desenho; `offset` é ignorado. O desenho nunca sai
    espelhado: `z` é a vista de topo (x,y), `y` a frontal (x,z) e `x` a lateral direita
    (y,z). Exige sólido ou documento (não aceita malha). `wires` no retorno é o número
    de contornos (section) ou de arestas (projection).
    """
    return _guard(
        lambda: freecad_cmd.run_job(
            "export_2d",
            {
                "path": str(check_path(path, False)),
                "out_path": str(check_path(out_path, True)),
                "mode": mode,
                "axis": axis,
                "offset": offset,
                "objects": objects,
            },
        )
    )


# --- Escape hatch ----------------------------------------------------------


@mcp.tool()
def part_run_script(script: str, out_dir: str, timeout_s: int | None = None) -> dict[str, Any]:
    """Roda um script Python no FreeCAD headless: a válvula de escape da API completa.

    Serve para modelar o que as outras tools não cobrem (roscas, gabinetes
    paramétricos, peças de jogo). O script já tem no namespace `doc` (documento novo),
    `OUT_DIR` (str) e os módulos `FreeCAD` (também como `App`) e `Part`; qualquer outro
    (Mesh, Sketcher, PartDesign...) é import seu. Grave o que quiser em OUT_DIR, por
    exemplo `shape.exportStl(OUT_DIR + "/peca.stl")`. Retorna `stdout`,
    `files` (novos ou modificados em out_dir) e `objects` (objetos finais de `doc`).
    Erro no script volta como {error} com traceback e stdout. `timeout_s` vale de 5 a 900
    (padrão: MCP_FREECAD_TIMEOUT_S ou 120). ATENÇÃO: o script roda com as permissões do
    usuário; MCP_FREECAD_ALLOWED_PATHS valida só os argumentos, não isola o script.
    """

    def run() -> dict[str, Any]:
        out = check_path(out_dir, True)
        out.mkdir(parents=True, exist_ok=True)
        return freecad_cmd.run_job("run_script", {"script": script, "out_dir": str(out)}, timeout_s)

    return _guard(run)


def main() -> None:
    mcp.run()
