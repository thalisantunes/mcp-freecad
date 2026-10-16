"""Cola do servidor: validação de argumentos e montagem do job, sem FreeCAD."""

import os

import pytest

from mcp_freecad import server


@pytest.fixture
def echo(fake_freecad, tmp_path, monkeypatch):
    """Roda as tools contra um freecadcmd falso que devolve o job que recebeu."""
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    monkeypatch.setenv("MCP_FREECAD_ALLOWED_PATHS", str(sandbox))
    fake_freecad(
        """
        out = {"op": job["op"], "args": job["args"]}
        json.dump({"ok": True, "result": out}, open(job["result_path"], "w"))
        """
    )
    return sandbox.resolve()


def test_doc_inspect_builds_job(echo):
    model = echo / "m.step"
    model.write_text("x")
    assert server.doc_inspect(str(model)) == {"op": "inspect", "args": {"path": str(model)}}


def test_errors_become_error_dict_with_code(echo):
    assert server.doc_inspect(str(echo / "nao_existe.step"))["error"].startswith("[MF-PATH-1]")
    assert server.doc_inspect("/etc/passwd")["error"].startswith("[MF-PATH-1]")


def test_doc_export_builds_job_and_creates_output_dir(echo):
    model = echo / "m.step"
    model.write_text("x")
    out = echo / "novo" / "m.stl"
    result = server.doc_export(str(model), str(out), objects=["Box"], linear_deflection=0.05)
    assert result["op"] == "export"
    assert result["args"] == {
        "path": str(model),
        "out_path": str(out),
        "objects": ["Box"],
        "linear_deflection": 0.05,
        "angular_deflection_deg": 28.6,
    }
    assert out.parent.is_dir()


def test_doc_export_refuses_to_overwrite_its_input(echo):
    model = echo / "m.step"
    model.write_text("x")
    assert server.doc_export(str(model), str(model))["error"].startswith("[MF-PATH-1]")


def test_doc_export_refuses_a_hardlink_to_its_input(echo):
    # O realpath não vê hardlink; em APFS, que ignora caixa, vale o mesmo para a grafia.
    model = echo / "m.step"
    model.write_text("x")
    link = echo / "outro_nome.step"
    os.link(model, link)
    assert server.doc_export(str(model), str(link))["error"].startswith("[MF-PATH-1]")


def test_a_different_existing_file_is_a_valid_output(echo):
    model, other = echo / "m.step", echo / "outro.stl"
    model.write_text("x")
    other.write_text("y")
    assert server.doc_export(str(model), str(other))["op"] == "export"


@pytest.mark.parametrize("kwargs", [{"linear_deflection": 0}, {"angular_deflection_deg": -1}])
def test_doc_export_rejects_non_positive_deflection(echo, kwargs):
    model = echo / "m.step"
    model.write_text("x")
    result = server.doc_export(str(model), str(echo / "o.stl"), **kwargs)
    assert result["error"].startswith("[MF-ARG-1]")


def test_doc_export_2d_builds_job(echo):
    model = echo / "m.step"
    model.write_text("x")
    out = echo / "perfil.dxf"
    result = server.doc_export_2d(str(model), str(out), mode="section", axis="x", offset=3.5)
    assert result["args"] == {
        "path": str(model),
        "out_path": str(out),
        "mode": "section",
        "axis": "x",
        "offset": 3.5,
        "objects": None,
    }


def test_doc_export_2d_defaults(echo):
    model = echo / "m.step"
    model.write_text("x")
    args = server.doc_export_2d(str(model), str(echo / "p.svg"))["args"]
    assert (args["mode"], args["axis"], args["offset"]) == ("projection", "z", 0.0)


def test_part_run_script_creates_out_dir_and_forwards_script(echo):
    out_dir = echo / "a" / "b"
    result = server.part_run_script("print(1)", str(out_dir))
    assert out_dir.is_dir()
    assert result["args"] == {"script": "print(1)", "out_dir": str(out_dir)}


def test_filesystem_problems_are_error_dicts_not_tracebacks(echo):
    taken = echo / "arquivo.txt"
    taken.write_text("x")
    result = server.part_run_script("print(1)", str(taken))  # out_dir que já é um arquivo
    assert result["error"].startswith("[MF-PATH-1]")


@pytest.fixture
def outside(tmp_path):
    """Um arquivo que existe, mas fora do sandbox permitido."""
    forbidden = tmp_path / "fora"
    forbidden.mkdir()
    (forbidden / "m.step").write_text("x")
    return forbidden


def test_doc_export_denies_paths_outside_the_sandbox(echo, outside, tmp_path):
    inside = echo / "m.step"
    inside.write_text("x")
    denied_input = server.doc_export(str(outside / "m.step"), str(echo / "o.stl"))
    assert denied_input["error"].startswith("[MF-PATH-1]")
    new_dir = tmp_path / "saida_fora"
    denied_output = server.doc_export(str(inside), str(new_dir / "o.stl"))
    assert denied_output["error"].startswith("[MF-PATH-1]")
    assert not new_dir.exists(), "negou a saída, mas já tinha criado a pasta"


def test_doc_export_2d_denies_paths_outside_the_sandbox(echo, outside, tmp_path):
    inside = echo / "m.step"
    inside.write_text("x")
    denied_input = server.doc_export_2d(str(outside / "m.step"), str(echo / "o.dxf"))
    assert denied_input["error"].startswith("[MF-PATH-1]")
    new_dir = tmp_path / "saida_fora"
    denied_output = server.doc_export_2d(str(inside), str(new_dir / "o.dxf"))
    assert denied_output["error"].startswith("[MF-PATH-1]")
    assert not new_dir.exists(), "negou a saída, mas já tinha criado a pasta"


def test_open_quote_in_the_command_is_an_error_dict(monkeypatch):
    monkeypatch.setenv("MCP_FREECAD_CMD", "'/opt/aberto/freecadcmd")
    assert server.freecad_version()["error"].startswith("[MF-CMD-1]")


def test_part_run_script_out_dir_must_be_allowed(echo, tmp_path):
    result = server.part_run_script("print(1)", str(tmp_path / "fora"))
    assert result["error"].startswith("[MF-PATH-1]")


def test_missing_freecad_is_an_error_dict(monkeypatch):
    monkeypatch.setattr("mcp_freecad.freecad_cmd.shutil.which", lambda name: None)
    monkeypatch.setattr("mcp_freecad.freecad_cmd._executable", lambda path: False)
    result = server.freecad_version()
    assert result["error"].startswith("[MF-CMD-1]")
