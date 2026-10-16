import os

import pytest

from mcp_freecad.errors import InvalidPath
from mcp_freecad.paths import allowed_roots, check_path


@pytest.fixture
def root(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("MCP_FREECAD_ALLOWED_PATHS", str(allowed))
    return allowed


def test_inside_allowed_root(root):
    f = root / "a.step"
    f.write_text("x")
    assert check_path(f, write=False) == f.resolve()


def test_outside_allowed_root(root, tmp_path):
    with pytest.raises(InvalidPath, match="MF-PATH-1"):
        check_path(tmp_path / "other.step", write=True)


def test_prefix_match_is_by_component_not_string(tmp_path, monkeypatch):
    # O irmão tmpevil existe de verdade (como /tmpevil ao lado de /tmp). Com um startswith
    # no lugar de is_relative_to ele passaria no check, então o teste só vale se ele existir:
    # um caminho inexistente também levanta InvalidPath, por outro motivo.
    base = tmp_path / "tmp"
    base.mkdir()
    evil = tmp_path / "tmpevil"
    evil.mkdir()
    (evil / "x.step").write_text("x")
    monkeypatch.setenv("MCP_FREECAD_ALLOWED_PATHS", str(base))
    with pytest.raises(InvalidPath, match="fora dos caminhos permitidos"):
        check_path(evil / "x.step", write=False)
    with pytest.raises(InvalidPath, match="fora dos caminhos permitidos"):
        check_path(evil / "novo.step", write=True)
    assert check_path(base / "x.step", write=True) == base.resolve() / "x.step"


def test_default_roots_reject_tmpfoo(monkeypatch):
    # Caso literal do requisito: /tmpfoo não casa com /tmp (nem com home ou /var/tmp).
    monkeypatch.delenv("MCP_FREECAD_ALLOWED_PATHS", raising=False)
    with pytest.raises(InvalidPath, match="fora dos caminhos permitidos"):
        check_path("/tmpfoo/x.step", write=False)
    assert all(str(r) != "/tmpfoo" for r in allowed_roots())


def test_symlink_escaping_root_is_rejected(root, tmp_path):
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "a.step").write_text("x")
    (root / "link").symlink_to(secret)
    with pytest.raises(InvalidPath):
        check_path(root / "link" / "a.step", write=False)


def test_symlink_inside_root_is_followed(root):
    real = root / "real"
    real.mkdir()
    (real / "a.step").write_text("x")
    (root / "link").symlink_to(real)
    assert check_path(root / "link" / "a.step", write=False) == (real / "a.step").resolve()


def test_root_given_through_symlink_is_resolved(tmp_path, monkeypatch):
    # No macOS /tmp é link para /private/tmp: o prefixo também precisa passar por realpath.
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    monkeypatch.setenv("MCP_FREECAD_ALLOWED_PATHS", str(link))
    (real / "a.step").write_text("x")
    assert check_path(real / "a.step", write=False) == (real / "a.step").resolve()


def test_multiple_roots_separated_by_colon(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    monkeypatch.setenv("MCP_FREECAD_ALLOWED_PATHS", f"{a}:{b}")
    assert check_path(a / "x", write=True).parent == a.resolve()
    assert check_path(b / "x", write=True).parent == b.resolve()


def test_write_creates_parent_directory(root):
    target = root / "novo" / "sub" / "out.stl"
    assert check_path(target, write=True) == target.resolve()
    assert target.parent.is_dir()


def test_read_requires_existing_path(root):
    with pytest.raises(InvalidPath, match="não encontrado"):
        check_path(root / "nao_existe.step", write=False)


def test_expanduser(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("MCP_FREECAD_ALLOWED_PATHS", raising=False)
    (tmp_path / "m.step").write_text("x")
    assert check_path("~/m.step", write=False) == (tmp_path / "m.step").resolve()


def test_relative_path_is_resolved_against_cwd(root, monkeypatch):
    monkeypatch.chdir(root)
    (root / "rel.step").write_text("x")
    assert check_path("rel.step", write=False) == (root / "rel.step").resolve()
    assert os.path.isabs(check_path("rel.step", write=False))
