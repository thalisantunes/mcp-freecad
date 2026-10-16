"""Validação dos caminhos de arquivo que chegam do cliente MCP."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .errors import InvalidPath


def _real(p: str | Path) -> Path:
    return Path(os.path.realpath(os.path.expanduser(p)))


def allowed_roots() -> list[Path]:
    """Prefixos onde o servidor pode ler e escrever, com symlinks já resolvidos.

    Cada prefixo também passa por realpath: no macOS /tmp é link para /private/tmp,
    e o caminho de entrada (resolvido) seria rejeitado se só ele fosse resolvido.
    """
    env = os.environ.get("MCP_FREECAD_ALLOWED_PATHS")
    if env:
        raw = [p for p in env.split(":") if p]
    else:
        raw = [str(Path.home()), tempfile.gettempdir(), "/tmp", "/var/tmp"]
    return [_real(p) for p in raw]


def check_path(p: str | Path, write: bool) -> Path:
    """Resolve `p` e garante que está dentro de um prefixo permitido (MF-PATH-1).

    `write=True` cria o diretório pai (o arquivo em si é gravado pelo runner);
    `write=False` exige que o caminho exista.
    """
    real = _real(p)
    if not any(real.is_relative_to(root) for root in allowed_roots()):
        raise InvalidPath(f"fora dos caminhos permitidos (MCP_FREECAD_ALLOWED_PATHS): {real}")
    if write:
        real.parent.mkdir(parents=True, exist_ok=True)
    elif not real.exists():
        raise InvalidPath(f"não encontrado: {real}")
    return real
