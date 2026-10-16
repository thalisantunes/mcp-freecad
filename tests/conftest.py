import os
import shlex
import sys
import textwrap

import pytest

# Lido antes de qualquer fixture: o skipif dos testes de integração também avalia o ambiente real.
_USERS_FREECAD_CMD = os.environ.get("MCP_FREECAD_CMD")


@pytest.fixture
def users_freecad_cmd():
    return _USERS_FREECAD_CMD


@pytest.fixture(autouse=True)
def _clean_env(request, monkeypatch):
    """Nenhum teste herda configuração do ambiente de quem roda.

    Exceto MCP_FREECAD_CMD nos testes de integração: com Flatpak, Snap ou AppImage extraído
    é por ela que o FreeCAD é achado, e o skipif deles já contou com ela.
    """
    names = ["MCP_FREECAD_ALLOWED_PATHS", "MCP_FREECAD_TIMEOUT_S"]
    if request.node.get_closest_marker("integration") is None:
        names.append("MCP_FREECAD_CMD")
    for var in names:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def fake_freecad(tmp_path, monkeypatch):
    """Instala um 'freecadcmd' falso (script Python) via MCP_FREECAD_CMD.

    O corpo recebe `job` (o job.json já lido) e `sys.argv` = [script, runner, job.json],
    igual ao que o freecadcmd de verdade recebe.
    """

    def install(body: str) -> None:
        script = tmp_path / "fake_freecadcmd.py"
        script.write_text(
            "import json, os, sys\njob = json.load(open(sys.argv[2]))\n" + textwrap.dedent(body),
            encoding="utf-8",
        )
        monkeypatch.setenv("MCP_FREECAD_CMD", shlex.join([sys.executable, str(script)]))

    return install
