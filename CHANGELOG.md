# Changelog

## 0.1.0 — 2026-10-16

Primeira versão.

- Cinco tools sobre o `freecadcmd` headless: `freecad_version`, `doc_inspect`,
  `doc_export`, `doc_export_2d` e `part_run_script`.
- Entrada: `.FCStd`, `.step`/`.stp`, `.iges`/`.igs`, `.brep`, `.stl`, `.obj`, `.3mf`
  e `.ply`. Saída de `doc_export`: `.step`/`.stp`, `.iges`/`.igs`, `.brep`,
  `.FCStd`, `.stl`, `.3mf`, `.obj` e `.ply`. Saída de `doc_export_2d`: `.svg` e `.dxf`.
- Descoberta do `freecadcmd` por `MCP_FREECAD_CMD`, PATH, caminhos conhecidos (macOS e
  AppImage), Flatpak e Snap.
- Caminhos de arquivo validados contra `MCP_FREECAD_ALLOWED_PATHS`, com symlinks
  resolvidos.
- Timeout por operação (`MCP_FREECAD_TIMEOUT_S`, 5–900 s) que encerra o grupo de
  processos inteiro.
- Erros com código `MF-<área>-<n>`.
