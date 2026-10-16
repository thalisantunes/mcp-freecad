import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

from mcp_freecad import freecad_cmd
from mcp_freecad.errors import (
    FreecadNotFound,
    FreecadRunFailed,
    FreecadTimeout,
    InvalidArgument,
    OperationFailed,
)

# --- descoberta do binário -------------------------------------------------


@pytest.fixture
def bare_machine(monkeypatch, tmp_path):
    """Máquina sem FreeCAD nenhum: nada no PATH, nenhum caminho conhecido executável."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(freecad_cmd.shutil, "which", lambda name: None)
    monkeypatch.setattr(freecad_cmd, "_executable", lambda path: False)
    return tmp_path


def test_env_override_wins_and_is_split_like_a_shell(monkeypatch):
    monkeypatch.setenv("MCP_FREECAD_CMD", "flatpak run --command=freecadcmd org.freecad.FreeCAD")
    monkeypatch.setattr(freecad_cmd.shutil, "which", lambda name: "/usr/bin/" + name)
    cmd, rule = freecad_cmd.resolve_cmd_with_rule()
    assert cmd == ["flatpak", "run", "--command=freecadcmd", "org.freecad.FreeCAD"]
    assert rule == "env:MCP_FREECAD_CMD"


def test_env_override_with_an_open_quote_is_an_mf_error(monkeypatch):
    monkeypatch.setenv("MCP_FREECAD_CMD", "'/opt/aberto/freecadcmd")
    with pytest.raises(FreecadNotFound, match=r"MF-CMD-1.*MCP_FREECAD_CMD inválido"):
        freecad_cmd.resolve_cmd()


def test_env_override_accepts_quoted_path_with_spaces(monkeypatch):
    monkeypatch.setenv("MCP_FREECAD_CMD", "'/opt/Free CAD/bin/freecadcmd' --flag")
    assert freecad_cmd.resolve_cmd() == ["/opt/Free CAD/bin/freecadcmd", "--flag"]


@pytest.mark.parametrize("name", ["freecadcmd", "FreeCADCmd"])
def test_path_lookup(bare_machine, monkeypatch, name):
    monkeypatch.setattr(
        freecad_cmd.shutil, "which", lambda n: f"/somewhere/{n}" if n == name else None
    )
    assert freecad_cmd.resolve_cmd_with_rule() == ([f"/somewhere/{name}"], f"PATH:{name}")


def test_path_wins_over_known_paths(bare_machine, monkeypatch):
    monkeypatch.setattr(freecad_cmd.shutil, "which", lambda n: "/p/freecadcmd")
    monkeypatch.setattr(freecad_cmd, "_executable", lambda path: True)
    assert freecad_cmd.resolve_cmd() == ["/p/freecadcmd"]


@pytest.mark.parametrize(
    ("relative_to_home", "path", "extra"),
    [
        (False, "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd", []),
        (True, "Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd", []),
        # layout de versões antigas, só como fallback
        (False, "/Applications/FreeCAD.app/Contents/MacOS/FreeCADCmd", []),
        (True, "Applications/FreeCAD.app/Contents/MacOS/FreeCADCmd", []),
        (True, ".local/opt/freecad/FreeCAD.AppImage", ["freecadcmd"]),
        (False, "/usr/bin/freecadcmd", []),
    ],
)
def test_known_paths(bare_machine, monkeypatch, relative_to_home, path, extra):
    full = bare_machine / path if relative_to_home else Path(path)
    monkeypatch.setattr(freecad_cmd, "_executable", lambda p: p == full)
    cmd, rule = freecad_cmd.resolve_cmd_with_rule()
    assert cmd == [str(full), *extra]
    assert rule == f"known-path:{full}"


def test_macos_prefers_resources_bin_over_the_old_layout(bare_machine, monkeypatch):
    monkeypatch.setattr(freecad_cmd, "_executable", lambda p: True)
    assert freecad_cmd.resolve_cmd() == [
        "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd"
    ]


def test_macos_new_layout_in_home_beats_old_layout_in_system_applications(
    bare_machine, monkeypatch
):
    system_old = Path("/Applications/FreeCAD.app/Contents/MacOS/FreeCADCmd")
    user_new = bare_machine / "Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd"
    monkeypatch.setattr(freecad_cmd, "_executable", lambda p: p in (system_old, user_new))
    assert freecad_cmd.resolve_cmd() == [str(user_new)]


def test_macos_does_not_look_for_a_launcher_in_contents_macos(bare_machine, monkeypatch):
    # Contents/MacOS/FreeCAD é o launcher da GUI: sem display ele não serve para headless.
    launcher = Path("/Applications/FreeCAD.app/Contents/MacOS/FreeCAD")
    monkeypatch.setattr(freecad_cmd, "_executable", lambda p: p == launcher)
    with pytest.raises(FreecadNotFound):
        freecad_cmd.resolve_cmd()


def test_macos_comes_before_appimage(bare_machine, monkeypatch):
    mac = Path("/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd")
    appimage = bare_machine / ".local/opt/freecad/FreeCAD.AppImage"
    monkeypatch.setattr(freecad_cmd, "_executable", lambda p: p in (mac, appimage))
    assert freecad_cmd.resolve_cmd() == [str(mac)]


def _fake_flatpak(monkeypatch, returncode):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, returncode)

    monkeypatch.setattr(
        freecad_cmd.shutil, "which", lambda n: "/usr/bin/flatpak" if n == "flatpak" else None
    )
    monkeypatch.setattr(freecad_cmd.subprocess, "run", run)
    return calls


def test_flatpak_when_installed(bare_machine, monkeypatch):
    calls = _fake_flatpak(monkeypatch, 0)
    cmd, rule = freecad_cmd.resolve_cmd_with_rule()
    assert cmd == ["flatpak", "run", "--command=freecadcmd", "org.freecad.FreeCAD"]
    assert rule == "flatpak"
    [(argv, kwargs)] = calls
    assert argv == ["flatpak", "info", "org.freecad.FreeCAD"]
    # o stdin do servidor é o canal JSON-RPC do MCP: a sonda não pode herdá-lo
    assert kwargs["stdin"] is subprocess.DEVNULL


def test_flatpak_present_but_freecad_not_installed(bare_machine, monkeypatch):
    _fake_flatpak(monkeypatch, 1)
    with pytest.raises(FreecadNotFound):
        freecad_cmd.resolve_cmd()


def test_flatpak_probe_timeout_is_not_fatal(bare_machine, monkeypatch):
    monkeypatch.setattr(
        freecad_cmd.shutil, "which", lambda n: "/usr/bin/flatpak" if n == "flatpak" else None
    )

    def hang(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 15)

    monkeypatch.setattr(freecad_cmd.subprocess, "run", hang)
    with pytest.raises(FreecadNotFound):
        freecad_cmd.resolve_cmd()


def test_snap_is_the_last_resort(bare_machine, monkeypatch):
    monkeypatch.setattr(freecad_cmd, "_executable", lambda p: str(p) == "/snap/bin/freecad.cmd")
    assert freecad_cmd.resolve_cmd_with_rule() == (["/snap/bin/freecad.cmd"], "snap")


def test_blank_env_override_is_ignored(bare_machine, monkeypatch):
    monkeypatch.setenv("MCP_FREECAD_CMD", "   ")
    with pytest.raises(FreecadNotFound):
        freecad_cmd.resolve_cmd()


def test_command_that_cannot_be_started_is_an_mf_error(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_FREECAD_CMD", str(tmp_path / "nao-existe" / "freecadcmd"))
    with pytest.raises(FreecadNotFound, match="não foi possível executar"):
        freecad_cmd.run_job("version", {})


def test_nothing_found_explains_how_to_fix(bare_machine):
    with pytest.raises(FreecadNotFound) as exc:
        freecad_cmd.resolve_cmd()
    assert "MF-CMD-1" in str(exc.value)
    assert "MCP_FREECAD_CMD" in str(exc.value)


# --- timeout -------------------------------------------------------------


def test_timeout_default_env_and_clamp(monkeypatch):
    assert freecad_cmd._timeout(None) == 120
    monkeypatch.setenv("MCP_FREECAD_TIMEOUT_S", "45")
    assert freecad_cmd._timeout(None) == 45
    assert freecad_cmd._timeout(200) == 200  # o argumento explícito vence o env
    assert freecad_cmd._timeout(1) == 5
    assert freecad_cmd._timeout(99999) == 900
    monkeypatch.setenv("MCP_FREECAD_TIMEOUT_S", "99999")
    assert freecad_cmd._timeout(None) == 900


def test_timeout_env_invalid(monkeypatch):
    monkeypatch.setenv("MCP_FREECAD_TIMEOUT_S", "abc")
    with pytest.raises(InvalidArgument, match="MF-ARG-1"):
        freecad_cmd._timeout(None)


# --- protocolo do job (freecadcmd falso) -------------------------------------


def test_job_and_result_roundtrip(fake_freecad):
    fake_freecad(
        """
        assert sys.argv[1].endswith("runner.py") and os.path.isfile(sys.argv[1]), sys.argv
        out = {"op": job["op"], "args": job["args"], "stdin_is_empty": sys.stdin.read() == "",
               "own_process_group": os.getpgrp() == os.getpid(),
               "workdir": os.path.dirname(sys.argv[2])}
        json.dump({"ok": True, "result": out}, open(job["result_path"], "w"))
        """
    )
    result = freecad_cmd.run_job("inspect", {"path": "/x/y.step", "n": 1})
    assert result["op"] == "inspect"
    assert result["args"] == {"path": "/x/y.step", "n": 1}
    assert result["stdin_is_empty"], "stdin precisa ser /dev/null: sem isso o freecadcmd trava"
    assert result["own_process_group"], "precisa de sessão própria para matar o grupo no timeout"
    assert not os.path.exists(result["workdir"]), "o diretório temporário do job deve ser removido"


def test_non_ascii_args_survive(fake_freecad):
    fake_freecad(
        """
        out = {"path": job["args"]["path"]}
        json.dump({"ok": True, "result": out}, open(job["result_path"], "w"))
        """
    )
    assert (
        freecad_cmd.run_job("inspect", {"path": "/tmp/gabinete_ção.step"})["path"]
        == "/tmp/gabinete_ção.step"
    )


def test_missing_result_reports_clean_output_tail(fake_freecad):
    fake_freecad(
        """
        for i in range(50):
            print(f"\\x1b[31mlinha {i}\\x1b[0m")
        print("boom", file=sys.stderr)
        sys.exit(3)
        """
    )
    with pytest.raises(FreecadRunFailed) as exc:
        freecad_cmd.run_job("version", {})
    msg = str(exc.value)
    assert "MF-RUN-1" in msg
    assert "boom" in msg
    assert "linha 49" in msg
    assert "linha 0\n" not in msg and "linha 20\n" not in msg, "só as últimas 20 linhas"
    assert "\x1b" not in msg, "sem códigos ANSI"


def test_exit_zero_without_result_is_still_a_failure(fake_freecad):
    # Exceção solta num script do freecadcmd sai com rc=0: só o result.json prova sucesso.
    fake_freecad('print("Exception while processing file: oops")')
    with pytest.raises(FreecadRunFailed, match="oops"):
        freecad_cmd.run_job("version", {})


def test_unreadable_result(fake_freecad):
    fake_freecad('open(job["result_path"], "w").write("{nao e json")')
    with pytest.raises(FreecadRunFailed, match="ilegível"):
        freecad_cmd.run_job("version", {})


def test_operation_failure_carries_traceback_and_stdout(fake_freecad):
    fake_freecad(
        """
        err = {"type": "ZeroDivisionError", "message": "division by zero",
               "traceback": '  File "<script>", line 5, in <module>', "stdout": "antes do erro"}
        json.dump({"ok": False, "error": err}, open(job["result_path"], "w"))
        sys.exit(1)
        """
    )
    with pytest.raises(OperationFailed) as exc:
        freecad_cmd.run_job("run_script", {})
    msg = str(exc.value)
    assert msg.startswith("[MF-OP-1] ZeroDivisionError: division by zero")
    assert 'File "<script>", line 5' in msg
    assert "antes do erro" in msg


def test_user_error_is_reported_without_type_prefix(fake_freecad):
    fake_freecad(
        """
        err = {"type": "UserError", "message": "extensão não suportada: .xyz", "traceback": ""}
        json.dump({"ok": False, "error": err}, open(job["result_path"], "w"))
        """
    )
    with pytest.raises(OperationFailed) as exc:
        freecad_cmd.run_job("export", {})
    assert str(exc.value) == "[MF-OP-1] extensão não suportada: .xyz"


def _alive(pid: int) -> bool:
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return bool(state.stdout.strip()) and not state.stdout.strip().startswith("Z")


def test_timeout_kills_the_whole_process_group(fake_freecad, monkeypatch, tmp_path):
    monkeypatch.setattr(freecad_cmd, "MIN_TIMEOUT_S", 0.5)
    pidfile = tmp_path / "grandchild.pid"
    fake_freecad(
        f"""
        import subprocess, time
        # neto que ignora SIGTERM: só o SIGKILL no grupo o encerra
        child = subprocess.Popen([sys.executable, "-c",
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"])
        open({str(pidfile)!r}, "w").write(str(child.pid))
        time.sleep(60)
        """
    )
    started = time.monotonic()
    with pytest.raises(FreecadTimeout, match="MF-RUN-2"):
        freecad_cmd.run_job("version", {}, timeout_s=0.5)
    assert time.monotonic() - started < 15
    pid = int(pidfile.read_text())
    for _ in range(50):
        if not _alive(pid):
            break
        time.sleep(0.1)
    assert not _alive(pid), "o processo neto sobreviveu ao timeout"


def test_unit_tests_start_without_the_users_freecad_configuration():
    for var in ("MCP_FREECAD_CMD", "MCP_FREECAD_ALLOWED_PATHS", "MCP_FREECAD_TIMEOUT_S"):
        assert var not in os.environ


class _ServerDied(BaseException):
    """Faz o papel de Ctrl-C ou da queda do servidor: não é Exception nem TimeoutExpired."""


def test_interrupt_in_the_middle_of_a_call_does_not_orphan_freecad(fake_freecad, tmp_path):
    pidfile = tmp_path / "pids"
    fake_freecad(
        f"""
        import subprocess, time
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        open({str(pidfile)!r}, "w").write(f"{{os.getpid()}} {{child.pid}}")
        time.sleep(60)
        """
    )

    def die(signum, frame):
        raise _ServerDied

    previous = signal.signal(signal.SIGALRM, die)
    signal.setitimer(signal.ITIMER_REAL, 1.0)
    try:
        with pytest.raises(_ServerDied):
            freecad_cmd.run_job("version", {}, timeout_s=60)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)

    pids = [int(p) for p in pidfile.read_text().split()]
    for _ in range(50):
        if not any(_alive(pid) for pid in pids):
            break
        time.sleep(0.1)
    assert not any(_alive(pid) for pid in pids), "o freecadcmd ou o neto sobreviveu à interrupção"


def test_version_adds_command_and_rule(fake_freecad):
    fake_freecad(
        """
        json.dump({"ok": True, "result": {"version": "9.9.9", "python": "3.11.0"}},
                  open(job["result_path"], "w"))
        """
    )
    result = freecad_cmd.version()
    assert result["version"] == "9.9.9"
    assert result["discovered_by"] == "env:MCP_FREECAD_CMD"
    assert result["command"] == os.environ["MCP_FREECAD_CMD"]
