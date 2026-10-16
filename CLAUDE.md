# mcp-freecad

MCP server para FreeCAD headless. Python, `uv`, layout `src/`.

## Regra que não pode ser quebrada

Definida no [ADR 0001](docs/adr/0001-freecadcmd-headless.md):

- **Toda operação roda no `runner.py`, dentro do `freecadcmd`.** O servidor nunca
  faz `import FreeCAD`.
- **O resultado volta só pelo `result.json`.** Rc e stdout do freecadcmd são
  diagnóstico: exceção solta sai com rc=0 e o stdout é poluído.
- **Erro só via códigos `MF-<área>-<n>`.** Toda exceção exposta ao cliente herda de
  `McpFreecadError`; as tools passam por `_guard()`, que devolve `{"error": ...}`. Erro
  novo = classe nova em `errors.py`, com código.

## O runner

`src/mcp_freecad/runner.py` roda no Python do FreeCAD (3.11 no AppImage), não no do
servidor:

- Só stdlib e módulos do FreeCAD. Não importa `mcp`, pydantic nem o resto do pacote.
- O freecadcmd o executa com `__name__` diferente de `__main__`: o `main(...)` é
  chamado no nível do módulo, no fim do arquivo.
- Falha esperada (entrada inválida) é `UserError`: vai sem traceback. O resto vira
  `{"ok": false}` com traceback curto.

## Armadilhas conhecidas (medidas no FreeCAD 1.1.4, salvo as marcadas)

Antes de "simplificar", confirme que não é uma destas:

- `hasattr(obj, "Shape")` é verdadeiro até para `App::Line`/`App::Plane`. Use
  `isDerivedFrom("Part::Feature")`.
- `TechDraw.project()` omite a silhueta: esfera e cilindro de lado ficam sem contorno.
  Use `projectEx` e some visíveis (`V`) e silhueta (`VO`).
- `Import.insert` não cria objeto nenhum para IGES em headless; use `Part.read`.
- `obj.Shape` e `Part.getShape(obj)` vêm **sem** o Placement dos `App::Part` que contêm o
  objeto: numa montagem aninhada as peças ficam todas perto da origem. Use `_shape()`, que
  aplica `getGlobalPlacement()`. `Part.export` também grava só o Placement local, por isso
  os exportadores de CAD recebem `_placed()`.
- `sys.exit(0)` num script do usuário é fim normal; só código diferente de zero é falha.
- O processo direto do AppImage é o AppRun: no timeout mate o grupo, não só o pid.
- stdin do freecadcmd tem de ser `/dev/null`.
- No macOS 1.1.x o headless é `Contents/Resources/bin/freecadcmd`. `Contents/MacOS/` só
  tem o launcher da GUI e `FreeCADCmd` não existe lá. Lido no código de empacotamento,
  não executado em Mac.
- Flatpak e Snap isolam o `/tmp` do host, onde ficam os arquivos de job. Não foi
  testado aqui; o remédio (`TMPDIR` no home) está no README.

## Convenções

- Comentário só onde a razão não é óbvia no código.
- Comunicação e docs em PT-BR. Jargão consagrado em inglês (STEP, mesh, watertight,
  headless).
- `ruff` com `line-length = 100`. Arquivos com menos de 500 linhas.
- Caminho vindo do cliente passa por `paths.check_path()` antes de chegar ao runner.

## Verificar

```bash
uv run ruff check . && uv run pytest -q
```

```bash
uv run pytest -q -m "not integration"   # só o que não precisa do FreeCAD
```
