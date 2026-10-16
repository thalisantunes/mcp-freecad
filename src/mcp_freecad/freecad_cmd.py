"""Descoberta do binário do FreeCAD e execução do runner dentro do freecadcmd.

Cada tool grava um job JSON, roda `freecadcmd runner.py job.json` e lê o result.json que o
runner gravou. O resultado vai só por arquivo: o stdout do freecadcmd é poluído e uma exceção
solta num script dele sai com rc=0. Ver docs/adr/0001-freecadcmd-headless.md.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
from importlib import resources
from pathlib import Path
from typing import Any

from .errors import (
    FreecadNotFound,
    FreecadRunFailed,
    FreecadTimeout,
    InvalidArgument,
    OperationFailed,
)

DEFAULT_TIMEOUT_S = 120
MIN_TIMEOUT_S = 5
MAX_TIMEOUT_S = 900

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_FLATPAK_CMD = ["flatpak", "run", "--command=freecadcmd", "org.freecad.FreeCAD"]
_SNAP_CMD = "/snap/bin/freecad.cmd"
_MAC_APP = "Applications/FreeCAD.app"
# No 1.1.x o binário headless fica em Resources/bin e Contents/MacOS só tem o launcher da
# GUI. O segundo caminho é o layout de versões antigas.
_MAC_BINS = ("Contents/Resources/bin/freecadcmd", "Contents/MacOS/FreeCADCmd")


def _executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def resolve_cmd_with_rule() -> tuple[list[str], str]:
    """Comando base do freecadcmd e a regra de descoberta que o achou.

    Ordem: env MCP_FREECAD_CMD, PATH, caminhos conhecidos (macOS, AppImage, apt),
    Flatpak, Snap. Os caminhos conhecidos existem porque o FreeCAD no macOS não
    põe nada no PATH (nem pelo cask do Homebrew) e apps de GUI (Claude Desktop) não
    herdam o PATH do shell.
    """
    override = os.environ.get("MCP_FREECAD_CMD", "").strip()
    if override:
        try:
            return shlex.split(override), "env:MCP_FREECAD_CMD"
        except ValueError as exc:  # aspas abertas
            raise FreecadNotFound(f"MCP_FREECAD_CMD inválido ({exc}): {override!r}") from exc

    for name in ("freecadcmd", "FreeCADCmd"):
        found = shutil.which(name)
        if found:
            return [found], f"PATH:{name}"

    home = Path.home()
    # Layout novo antes do antigo, em /Applications e depois em ~/Applications: um 1.1.x
    # em qualquer dos dois vence um app antigo.
    known = [
        (app / rel, []) for rel in _MAC_BINS for app in (Path("/") / _MAC_APP, home / _MAC_APP)
    ]
    known += [
        # AppImage: o primeiro argumento escolhe o binário embutido (freecadcmd, não a GUI).
        (home / ".local/opt/freecad/FreeCAD.AppImage", ["freecadcmd"]),
        (Path("/usr/bin/freecadcmd"), []),
    ]
    for path, extra in known:
        if _executable(path):
            return [str(path), *extra], f"known-path:{path}"

    if shutil.which("flatpak"):
        try:
            probe = subprocess.run(
                ["flatpak", "info", "org.freecad.FreeCAD"],
                stdin=subprocess.DEVNULL,  # o stdin do servidor é o canal JSON-RPC do MCP
                capture_output=True,
                check=False,
                timeout=15,
            )
        except subprocess.TimeoutExpired:
            probe = None
        if probe is not None and probe.returncode == 0:
            return list(_FLATPAK_CMD), "flatpak"

    if _executable(Path(_SNAP_CMD)):
        return [_SNAP_CMD], "snap"

    raise FreecadNotFound(
        "freecadcmd não encontrado. Instale o FreeCAD 1.0 ou superior (ver README) ou aponte "
        "MCP_FREECAD_CMD para o binário, por exemplo "
        "MCP_FREECAD_CMD='flatpak run --command=freecadcmd org.freecad.FreeCAD'."
    )


def resolve_cmd() -> list[str]:
    return resolve_cmd_with_rule()[0]


def _timeout(timeout_s: float | None) -> float:
    if timeout_s is None:
        raw = os.environ.get("MCP_FREECAD_TIMEOUT_S")
        try:
            timeout_s = float(raw) if raw else DEFAULT_TIMEOUT_S
        except ValueError:
            raise InvalidArgument(f"MCP_FREECAD_TIMEOUT_S inválido: {raw!r}") from None
    return max(MIN_TIMEOUT_S, min(MAX_TIMEOUT_S, timeout_s))


def _diagnostics(stdout: str, stderr: str, lines: int = 20) -> str:
    """Final de cada canal, sem ANSI. Separados porque o buffering embaralha a ordem."""
    parts = [
        f"{name} (últimas {lines} linhas):\n" + "\n".join(_ANSI.sub("", text).splitlines()[-lines:])
        for name, text in (("stderr", stderr), ("stdout", stdout))
        if text.strip()
    ]
    return "\n".join(parts) or "(sem saída)"


def _kill_group(proc: subprocess.Popen[str]) -> None:
    """Encerra o freecadcmd e tudo que ele criou.

    O processo direto do AppImage é o script AppRun, e o freecadcmd é filho dele: matar
    só o AppRun deixa o FreeCAD órfão consumindo CPU (medido com um script em laço
    infinito). Por isso o grupo inteiro. SIGTERM antes de SIGKILL para o runtime do
    AppImage ter chance de desmontar o FUSE.
    """
    for sig in (signal.SIGTERM, signal.SIGKILL):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, sig)
        try:
            proc.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            continue
        return


def _run(argv: list[str], timeout: float) -> tuple[str, str]:
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,  # com stdin aberto o freecadcmd cai no console interativo
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            start_new_session=True,
        )
    except OSError as exc:
        raise FreecadNotFound(
            f"não foi possível executar {argv[0]}: {exc}. Confira MCP_FREECAD_CMD."
        ) from exc
    try:
        return proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        raise FreecadTimeout(
            f"freecadcmd excedeu {timeout:g}s. Aumente MCP_FREECAD_TIMEOUT_S (máx. "
            f"{MAX_TIMEOUT_S}s) ou, em part_run_script, timeout_s."
        ) from None
    except BaseException:
        # Ctrl-C ou queda do servidor no meio da chamada: não deixe o freecadcmd órfão.
        _kill_group(proc)
        raise


def _read_result(result: Path, stdout: str, stderr: str) -> dict[str, Any]:
    if not result.is_file():
        raise FreecadRunFailed(
            f"freecadcmd terminou sem gravar o resultado.\n{_diagnostics(stdout, stderr)}"
        )
    try:
        payload = json.loads(result.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise FreecadRunFailed(
            f"resultado ilegível ({exc}).\n{_diagnostics(stdout, stderr)}"
        ) from exc
    if payload.get("ok"):
        return payload["result"]

    err = payload.get("error", {})
    kind, message = err.get("type"), err.get("message")
    parts = [message if kind == "UserError" else f"{kind}: {message}"]
    if err.get("traceback"):
        parts.append(err["traceback"].rstrip())
    if err.get("stdout"):
        parts.append("stdout do script:\n" + err["stdout"].rstrip())
    raise OperationFailed("\n".join(parts))


def run_job(op: str, args: dict[str, Any], timeout_s: float | None = None) -> dict[str, Any]:
    """Roda uma operação do runner dentro do freecadcmd e devolve o `result` dele.

    `timeout_s=None` usa MCP_FREECAD_TIMEOUT_S, senão 120s. Sempre limitado a 5–900s.
    """
    timeout = _timeout(timeout_s)
    cmd = resolve_cmd()
    workdir = Path(tempfile.mkdtemp(prefix="mcp-freecad-"))
    try:
        job, result = workdir / "job.json", workdir / "result.json"
        job.write_text(
            json.dumps({"op": op, "args": args, "result_path": str(result)}), encoding="utf-8"
        )
        with resources.as_file(resources.files("mcp_freecad") / "runner.py") as runner:
            stdout, stderr = _run([*cmd, str(runner), str(job)], timeout)
        return _read_result(result, stdout, stderr)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def version() -> dict[str, Any]:
    """Versão do FreeCAD e qual comando/regra de descoberta o servidor está usando."""
    cmd, rule = resolve_cmd_with_rule()
    return {**run_job("version", {}), "command": " ".join(cmd), "discovered_by": rule}
