# mcp-freecad

Servidor MCP que dá ao agente (Claude Code, Claude Desktop e outros clientes MCP)
acesso ao FreeCAD **sem interface gráfica**: inspecionar um modelo, converter entre
formatos CAD e de malha, tirar perfis 2D para corte a laser e rodar scripts Python do
FreeCAD. Toda operação roda no `freecadcmd`, o FreeCAD de linha de comando, então
funciona em servidor e em CI, sem o FreeCAD aberto. Linux e macOS.

## Para que serve

- **Impressão 3D (FDM):** conferir se a peça é estanque (watertight), medir e converter
  STEP em STL ou 3MF com a fineza escolhida.
- **Corte a laser:** gerar DXF ou SVG em mm a partir de um corte do modelo 3D.
- **Gabinetes para eletrônica:** ler o STEP da placa exportado pelo KiCad, pegar as
  dimensões e modelar o gabinete em volta (receita [mais abaixo](#receita-gabinete-a-partir-do-kicad)).
- **Automação:** tudo é headless, então roda em CI.

## Requisitos

- FreeCAD 1.0 ou superior; use a 1.1.4 ou mais nova (é a testada). A 1.1.4 corrige duas
  falhas de segurança disparadas ao abrir um `.FCStd` malicioso, e todas as versões
  anteriores são afetadas, segundo as
  [notas da versão](https://github.com/FreeCAD/FreeCAD/releases/tag/1.1.4). Isso importa
  para quem abre arquivos de terceiros com `doc_inspect` ou `doc_export`.
- Python 3.11 ou superior e [uv](https://docs.astral.sh/uv/).
- Linux ou macOS. Windows não é suportado.

## Instalar o FreeCAD

O servidor procura o `freecadcmd` sozinho (ordem: `MCP_FREECAD_CMD`, PATH, caminhos
conhecidos, Flatpak, Snap). A tool `freecad_version` diz qual comando ele achou e por
qual regra.

### Linux: AppImage oficial

Recomendado: é o que foi testado.

```bash
mkdir -p ~/.local/opt/freecad && cd ~/.local/opt/freecad
# Baixe, em https://github.com/FreeCAD/FreeCAD/releases (release 1.1.4), o AppImage
# (~780 MB) e o arquivo de checksum ao lado dele:
#   FreeCAD_1.1.4-Linux-x86_64-py311.AppImage
#   FreeCAD_1.1.4-Linux-x86_64-py311.AppImage-SHA256.txt
sha256sum -c FreeCAD_1.1.4-Linux-x86_64-py311.AppImage-SHA256.txt
chmod +x FreeCAD_1.1.4-Linux-x86_64-py311.AppImage
ln -sf FreeCAD_1.1.4-Linux-x86_64-py311.AppImage FreeCAD.AppImage
```

O servidor procura `~/.local/opt/freecad/FreeCAD.AppImage` e o chama como
`FreeCAD.AppImage freecadcmd`. Teste:

```bash
~/.local/opt/freecad/FreeCAD.AppImage freecadcmd -c "import FreeCAD; print(FreeCAD.Version()[:3])" < /dev/null
```

Deve imprimir `['1', '1', '4']`. Duas observações:

- **Nunca rode o AppImage sem argumentos.** Sem display ele tenta abrir a GUI e falha.
  O `< /dev/null` também importa: com o stdin aberto o `freecadcmd` espera no console.
- Se o AppImage reclamar de FUSE, instale o `libfuse2t64` (Ubuntu 24.04 ou mais novo;
  nas versões anteriores o pacote é `libfuse2`). A alternativa é extrair o AppImage, o que
  dispensa o FUSE e ainda deixa cada chamada mais rápida (cerca de 0,1 s em vez de 0,3 s,
  porque nada é montado a cada vez):

  ```bash
  cd ~/.local/opt/freecad
  ./FreeCAD.AppImage --appimage-extract    # cria squashfs-root/, cerca de 3 GB
  ```

  O servidor não procura a pasta extraída: aponte `MCP_FREECAD_CMD` para ela, com
  caminho absoluto, por exemplo
  `/home/USUARIO/.local/opt/freecad/squashfs-root/AppRun freecadcmd`.

### Linux: Flatpak ou Snap

Alternativas **não testadas** nesta versão. O servidor as detecta sozinho, ou você
aponta o comando:

| Pacote | Instalar | `MCP_FREECAD_CMD` |
|---|---|---|
| Flatpak | `flatpak install flathub org.freecad.FreeCAD` | `flatpak run --command=freecadcmd org.freecad.FreeCAD` |
| Snap | `sudo snap install freecad` | `/snap/bin/freecad.cmd` |

Esses pacotes rodam isolados e, em geral, **não enxergam o `/tmp` do host**, onde o
servidor grava os arquivos de job. Se as operações falharem com `MF-RUN-1`, defina `TMPDIR` no
ambiente do servidor para uma pasta dentro do home (no Snap, prefira uma que não comece
com ponto) e mantenha os modelos no home. O Flatpak oficial declara acesso ao sistema
de arquivos do host (`--filesystem=host`), mas o `/tmp` continua privado. Confira a versão
com `freecad_version`: esses pacotes podem estar atrás da 1.1.4.

### macOS

Duas opções:

1. DMG oficial (arm64 para Apple Silicon, x86_64 para Intel) em
   <https://github.com/FreeCAD/FreeCAD/releases>; arraste o app para `/Applications`.
2. `brew install --cask freecad`

O binário headless é `/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd`. Em
`Contents/MacOS/` fica só o launcher da GUI, que não serve sem display. O binário **não
está no PATH**, nem com o cask do Homebrew, e o servidor o encontra sozinho. Ele procura
em `/Applications` e em `~/Applications`, primeiro `Contents/Resources/bin/freecadcmd` e,
como fallback para versões antigas, `Contents/MacOS/FreeCADCmd`. Na primeira execução o
macOS pode pedir para autorizar o app: abra o FreeCAD uma vez pelo Finder e autorize.
Teste:

```bash
/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd -c "import FreeCAD; print(FreeCAD.Version()[:3])" < /dev/null
```

## Instalar o servidor

Dentro da pasta do repositório:

```bash
uv sync
uv run python -c "from mcp_freecad import freecad_cmd; print(freecad_cmd.version())"
```

O segundo comando roda o FreeCAD de verdade e deve imprimir a versão, o Python embutido
e o comando usado. Se terminar com `MF-CMD-1`, o FreeCAD não foi encontrado: veja
`MCP_FREECAD_CMD` abaixo.

## Registrar no cliente MCP

### Claude Code

Na pasta do repositório:

```bash
claude mcp add freecad -- uv --directory "$(pwd)" run mcp-freecad
```

### Claude Desktop

O Claude Desktop **não herda o PATH do shell**, então o `command` precisa ser o caminho
absoluto do `uv`. Descubra com `which uv`. Exemplos: `/opt/homebrew/bin/uv` (macOS com
Apple Silicon e Homebrew) e `/home/USUARIO/.local/bin/uv` (Linux com o instalador
oficial; o JSON não expande `~`). No macOS o arquivo é
`~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "freecad": {
      "command": "/opt/homebrew/bin/uv",
      "args": ["--directory", "/CAMINHO/ABSOLUTO/mcp-freecad", "run", "mcp-freecad"]
    }
  }
}
```

Para passar variáveis de ambiente, acrescente ao bloco `freecad` (vale também para o
`.mcp.json` do Claude Code):

```json
"env": { "MCP_FREECAD_ALLOWED_PATHS": "/Users/voce/projetos:/tmp" }
```

## Tools

Os caminhos aceitam `~` e são validados contra `MCP_FREECAD_ALLOWED_PATHS`. As unidades
são sempre mm.

| Tool | Obrigatórios | Opcionais (padrão) |
|---|---|---|
| `freecad_version` | | |
| `doc_inspect` | `path` | |
| `doc_export` | `path`, `out_path` | `objects` (todos), `linear_deflection` (0.1), `angular_deflection_deg` (28.6) |
| `doc_export_2d` | `path`, `out_path` | `mode` (`projection`), `axis` (`z`), `offset` (0), `objects` (todos) |
| `part_run_script` | `script`, `out_dir` | `timeout_s` (120, ou `MCP_FREECAD_TIMEOUT_S`) |

Formatos de entrada: `.FCStd`, `.step`/`.stp`, `.iges`/`.igs` e `.brep` em todas as tools de
documento. As malhas `.stl`, `.obj`, `.3mf` e `.ply` valem em `doc_inspect` e `doc_export`,
mas não em `doc_export_2d`.

### `freecad_version`

Primeira chamada de qualquer sessão: confirma que o FreeCAD foi achado.

```json
{}
```

Retorna `version`, `python`, `command` (o comando usado) e `discovered_by` (a regra que
o achou: `env:MCP_FREECAD_CMD`, `PATH:freecadcmd`, `known-path:<caminho>`, `flatpak`
ou `snap`).

### `doc_inspect`

Dimensões, volume, validade e se a peça é estanque.

```json
{ "path": "~/projetos/placa/placa.step" }
```

```json
{
  "kind": "solid",
  "units": "mm",
  "bbox": { "x": 40.0, "y": 30.0, "z": 20.0, "min": [0, 0, 0], "max": [40, 30, 20] },
  "objects_total": 1,
  "objects": [
    {
      "name": "Part__Feature", "label": "Caixa", "type": "Part::Feature",
      "bbox": { "x": 40.0, "y": 30.0, "z": 20.0, "min": [0, 0, 0], "max": [40, 30, 20] },
      "volume_mm3": 24000.0, "area_mm2": 5200.0, "solids": 1, "faces": 6, "is_valid": true
    }
  ],
  "checks": { "watertight": true, "all_valid": true }
}
```

- `kind` é `solid` (STEP, IGES, BREP), `document` (`.FCStd`) ou `mesh`.
- Posições e `bbox` estão no espaço global do documento: o `Placement` dos `App::Part` que
  contêm cada objeto é aplicado, inclusive quando eles estão aninhados. O `bbox` do topo
  cobre todos os objetos: é o tamanho da peça ou da montagem.
- `objects` traz os objetos finais (os que nenhuma outra feature consome), até 100;
  `objects_total` diz quantos existem e `truncated: true` aparece quando há corte.
  Use `name` ou `label` no parâmetro `objects` das outras tools.
- Em `checks`, `watertight` é o que decide se a peça imprime em FDM. Numa montagem um
  objeto solto derruba o veredito; `not_watertight` e `invalid` listam quais (até 20).
- Malha devolve `mesh` no lugar de `objects`: `facets`, `points`, `is_solid`,
  `non_manifold`, `self_intersections`, `volume_mm3`, `area_mm2` e `bbox`.

### `doc_export`

Converte para o formato da extensão de `out_path`.

```json
{
  "path": "~/projetos/caixa.step",
  "out_path": "~/projetos/caixa.3mf",
  "linear_deflection": 0.05
}
```

- Saídas: `.step`/`.stp`, `.iges`/`.igs`, `.brep`, `.FCStd` e malhas `.stl`, `.3mf`,
  `.obj` e `.ply`.
- Para malha a partir de sólido, `linear_deflection` (mm) e `angular_deflection_deg`
  controlam a fineza. Menor dá mais facetas e arquivo maior.
- `objects` exporta só os objetos pedidos, por `name` ou `label`.
- Em STEP, IGES e BREP cada objeto sai como peça solta, já na posição global, e a
  hierarquia de montagem não é preservada. `.FCStd` sem `objects` guarda o documento inteiro.
- Malha converte para outra malha ou para `.FCStd`, nunca para `.step`, `.iges` ou
  `.brep`. A saída nunca pode ser o próprio arquivo de entrada.

Retorna `out_path`, `bytes` e, quando a saída é malha, `facets`.

### `doc_export_2d`

Perfil 2D em `.svg` ou `.dxf`, em mm.

```json
{
  "path": "~/projetos/tampa.step",
  "out_path": "~/projetos/tampa.dxf",
  "mode": "section",
  "axis": "z",
  "offset": 1.5
}
```

- `mode: "section"` corta o modelo pelo plano `eixo = offset` e exporta os contornos.
  **É o modo para laser.** O DXF sai plano, em Z=0, com `LINE`, `ARC` e `CIRCLE` de
  verdade.
- `mode: "projection"` exporta a vista com arestas visíveis e silhueta, sem linhas
  ocultas. Serve de preview e de desenho; `offset` é ignorado.
- O desenho nunca sai espelhado: `z` é a vista de topo (eixos x, y do modelo), `y` a
  frontal (x, z) e `x` a lateral direita (y, z). As coordenadas são as do modelo nos
  dois eixos que sobram, sem recentrar; a do eixo do corte é descartada.
- Corte que não toca o modelo devolve erro com a faixa do modelo no eixo.
- Exige sólido ou documento; malha não é aceita.

Retorna `out_path`, `bytes` e `wires` (contornos no `section`, arestas na `projection`).

### `part_run_script`

A válvula de escape: roda Python do FreeCAD headless para tudo que as outras tools não
cobrem (roscas, gabinetes paramétricos, peças de jogo).

```json
{
  "script": "b = Part.makeBox(40, 30, 20)\nb.exportStl(OUT_DIR + '/caixa.stl')\nprint(b.Volume)",
  "out_dir": "~/projetos/saida"
}
```

- O script já tem no namespace `doc` (documento novo), `OUT_DIR` (string), `FreeCAD`
  (também como `App`) e `Part`. Qualquer outro módulo (`Mesh`, `Sketcher`,
  `PartDesign`) é `import` seu.
- Retorna `stdout` (o que o script imprimiu), `files` (arquivos novos ou modificados
  em `out_dir`) e `objects` (objetos finais de `doc`).
- Erro no script volta como `{"error": ...}` com o traceback das linhas do script e o
  stdout até o erro. O servidor continua de pé.
- **Não é sandbox.** O script roda com as permissões do seu usuário e pode ler e
  escrever onde você pode. `MCP_FREECAD_ALLOWED_PATHS` só valida os argumentos das
  tools.
- Cada chamada é um processo novo: nada persiste de uma chamada para a outra. Grave em
  `OUT_DIR` e releia com as outras tools.

### Erros

Toda falha volta como `{"error": "[MF-<área>-<n>] mensagem"}`:

| Código | Significa |
|---|---|
| `MF-CMD-1` | `freecadcmd` não encontrado, ou o comando configurado não pôde ser executado |
| `MF-RUN-1` | o `freecadcmd` terminou sem gravar resultado (crash); a mensagem traz o final de stderr e stdout |
| `MF-RUN-2` | estourou o timeout |
| `MF-OP-1` | a operação falhou dentro do FreeCAD: arquivo inválido, extensão não suportada, objeto não encontrado, erro no script |
| `MF-PATH-1` | caminho fora de `MCP_FREECAD_ALLOWED_PATHS`, inexistente, saída igual à entrada, ou erro do sistema de arquivos (permissão, pasta de saída que é um arquivo) |
| `MF-ARG-1` | argumento inválido |

## Variáveis de ambiente

| Variável | Efeito |
|---|---|
| `MCP_FREECAD_CMD` | Comando do `freecadcmd`, separado como num shell (aceita aspas). Vence qualquer descoberta. Ex.: `flatpak run --command=freecadcmd org.freecad.FreeCAD` |
| `MCP_FREECAD_ALLOWED_PATHS` | Prefixos separados por `:` onde o servidor pode ler e escrever. Padrão: home, o diretório temporário do sistema, `/tmp` e `/var/tmp`. Symlinks são resolvidos nos dois lados e a comparação é por componente de caminho (`/tmpfoo` não casa com `/tmp`) |
| `MCP_FREECAD_TIMEOUT_S` | Timeout padrão por operação, em segundos. Padrão 120; sempre limitado a 5–900 |

O servidor também respeita `TMPDIR` para os arquivos de job.

## Receita: gabinete a partir do KiCad

1. **KiCad para STEP e DXF**, no terminal (ou peça ao agente para rodar):

   ```bash
   kicad-cli pcb export step --force --subst-models -o ~/projetos/placa/placa.step ~/projetos/placa/placa.kicad_pcb
   kicad-cli pcb export step --force --board-only -o ~/projetos/placa/placa_so_placa.step ~/projetos/placa/placa.kicad_pcb
   kicad-cli pcb export dxf --layers Edge.Cuts --output-units mm --drill-shape-opt 0 --mode-single -o ~/projetos/placa/contorno.dxf ~/projetos/placa/placa.kicad_pcb
   ```

   - O primeiro traz a placa com os componentes. O `--subst-models` troca cada modelo
     VRML (`.wrl`) pelo `.step` de mesmo nome; sem ele, os modelos `.wrl` não entram no STEP
     e o `kicad-cli` avisa que não pode usar VRML. Quando os footprints já apontam para
     `.step`, o flag não muda nada (medido no KiCad 10.0.5).
   - O segundo traz só a placa, sem componentes.
   - O terceiro é o contorno 2D. O DXF do `kicad-cli` sai em polegadas por padrão, daí o
     `--output-units mm`. O `--drill-shape-opt 0` tira os círculos dos furos de pads e vias.
   - O STEP sai nas coordenadas da folha do KiCad, então a placa não fica na origem. Use o
     `bbox.min` do `doc_inspect` como deslocamento, ou escolha a origem com
     `--drill-origin`, `--grid-origin` ou `--user-origin`.

2. **Medir a placa** com `doc_inspect` nos dois STEPs. O `bbox` do topo cobre todas as
   peças e `objects` lista cada uma pelo `label`. Compare os dois STEPs para separar o
   tamanho da placa do que sobra dela. No demo `pic_programmer` do KiCad 10, a placa tem
   160,02 x 99,06 x 1,51 mm e, com os componentes, 168,02 x 99,06 x 19,37 mm: um conector
   passa 8 mm do contorno e os terminais saem 4,7 mm abaixo da placa. Uma placa real de
   316 peças levou 8 s.

3. **Modelar o gabinete** com `part_run_script`, usando o `bbox` como parâmetro. Este
   exemplo faz a base, uma caixa aberta em cima com parede e folga ao redor da placa:

   ```python
   W, D, H = 60.0, 40.0, 15.0  # bbox da placa (mm), vindo de doc_inspect
   wall, gap = 2.0, 1.0  # parede e folga entre a placa e a parede
   V = FreeCAD.Vector
   m = wall + gap
   casca = Part.makeBox(W + 2 * m, D + 2 * m, H + gap + wall, V(-m, -m, -wall))
   cavidade = Part.makeBox(W + 2 * gap, D + 2 * gap, H + gap + 1, V(-gap, -gap, 0))
   base = casca.cut(cavidade)  # aberta em cima
   base.exportStep(OUT_DIR + "/gabinete_base.step")
   base.exportStl(OUT_DIR + "/gabinete_base.stl")
   ```

4. **Validar e fabricar.** `doc_inspect` no STL confirma `watertight` e o tamanho
   (`66 x 46 x 18` neste exemplo). Para imprimir, `doc_export` do STEP em `.3mf`; a
   fineza (`linear_deflection`) só vale a partir de um sólido, não de uma malha. Para uma
   tampa cortada a laser, modele a chapa e use `doc_export_2d` com `mode: "section"` no
   meio da espessura:

   ```json
   { "path": "~/projetos/saida/tampa.step", "out_path": "~/projetos/saida/tampa.dxf",
     "mode": "section", "axis": "z", "offset": 1.5 }
   ```

## Limitações

- **Sem render PNG headless.** Use `doc_export_2d` em modo `projection` como preview.
- **Projeção:** o desenho tem arestas visíveis e silhueta, sem linhas ocultas. Em vista
  lateral de cilindros o círculo visto de borda sai como spline degenerada, com
  sobreposição. Para corte a laser use sempre `section`.
- **Testado só em Linux com o AppImage 1.1.4.** As regras de descoberta de macOS,
  Flatpak e Snap existem e têm testes unitários, mas não foram exercitadas com o
  FreeCAD real. O caminho do macOS vem da leitura do código de empacotamento do 1.1.4,
  não de uma execução em Mac.
- **Windows não é suportado:** o timeout usa grupos de processo POSIX.
- **`App::Link` não é seguido** (montagens do Assembly Workbench): o `doc_inspect` mostra o
  objeto original na posição dele e ignora as instâncias. Use `part_run_script`.
- **Malha não vira sólido:** `.stl`, `.obj`, `.3mf` e `.ply` não convertem para `.step`,
  `.iges` ou `.brep`. Objetos de malha dentro de um `.FCStd` são ignorados.
- **IGES** costuma trazer superfícies sem sólido; nesse caso `watertight` sai `false`.
- **Montagens grandes custam:** inspecionar um STEP de 73 MB com 1164 peças levou 45 s.
  Acima de 120 s aumente `MCP_FREECAD_TIMEOUT_S` (máximo 900).
- `doc_inspect` detalha no máximo 100 objetos por chamada.
- `doc_export` sobrescreve em silêncio uma saída que já exista (menos a própria entrada).

## Desenvolvimento

```bash
uv sync
uv run ruff check .
uv run pytest -q                        # unitários + integração
uv run pytest -q -m "not integration"   # só o que não precisa do FreeCAD
```

Os testes de integração rodam o `freecadcmd` de verdade e são pulados quando o FreeCAD
não é encontrado. `tests/test_server_lists_tools.py` sobe `uv run mcp-freecad` e fala
MCP por stdio com o cliente do SDK.

```
src/mcp_freecad/
  server.py       as 5 tools e _guard()
  freecad_cmd.py  descoberta do binário, job JSON, subprocess e timeout
  runner.py       roda DENTRO do freecadcmd; só stdlib e módulos do FreeCAD
  paths.py        validação de caminhos
  errors.py       erros MF-<área>-<n>
docs/adr/         decisões de arquitetura
```

A decisão central (por que um subprocess por chamada, por que o resultado vai por
arquivo) está no [ADR 0001](docs/adr/0001-freecadcmd-headless.md).

## Licença

MIT. Veja [LICENSE](LICENSE).
