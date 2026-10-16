# ADR 0001 — Rodar o FreeCAD pelo `freecadcmd`, com runner e job JSON

**Data:** 16/10/2026 · **Status:** aceito

## Contexto

O servidor precisa abrir, converter e cortar modelos CAD sem interface gráfica, em
Linux e macOS, tanto para uso interativo quanto para automação e CI. O FreeCAD não
tem API de rede: o que ele oferece é um interpretador Python embutido, que o
`freecadcmd` expõe sem display. Medido no FreeCAD 1.1.4 (AppImage):

- `freecadcmd script.py` roda o script e sai. O stdin precisa estar fechado; um
  script completo com exports leva cerca de 1 s.
- O stdout é poluído (banner, "Recompute", estatísticas do OCC com ANSI).
- Exceção não capturada num script `.py` sai com rc=0 e só imprime "Exception while
  processing file"; com `-c` sai com rc=1. `SystemExit(n)` propaga o código nos dois.
- O Python embutido é o do FreeCAD (3.11 no AppImage) e não tem o SDK do MCP.

## Decisão

Cada tool grava um **job JSON** (`op`, `args`, `result_path`), roda
`freecadcmd runner.py job.json` num subprocess com timeout (120 s por padrão, limitado
a 5–900 s) e lê o **result JSON** que o runner gravou.

- `runner.py` é um arquivo do pacote executado **dentro** do Python do FreeCAD. Usa só
  stdlib e módulos do FreeCAD e nunca importa o resto do pacote.
- O resultado vai só por arquivo. O runner captura tudo e grava `{"ok": true, ...}` ou
  `{"ok": false, "error": ...}`. Sem `result.json` a execução falhou (`MF-RUN-1`),
  mesmo com rc=0; o final de stderr e stdout, sem ANSI, vai na mensagem.
- No timeout o cliente mata o **grupo de processos**, com SIGTERM e depois SIGKILL.
  No AppImage o processo direto é o script AppRun e o `freecadcmd` é filho dele:
  matar só o AppRun deixa o FreeCAD órfão a 100% de CPU (medido).
- Um processo por chamada: sem estado entre chamadas e sem contaminação entre modelos.
  Chamadas concorrentes não compartilham nada.
- `part_run_script` é a válvula de escape: expõe o Python do FreeCAD inteiro em vez de
  tentar cobrir a API com tools.

## Consequências

**Ganhos**

- Funciona sem o FreeCAD aberto, em servidor e em CI.
- Crash, loop infinito ou falta de memória no OCC derrubam só o subprocess; o servidor
  responde com erro.
- Zero acoplamento entre a versão do Python do servidor e a do FreeCAD.

**Custos**

- Cerca de 0,3 s de partida por chamada, mais o tempo de ler o modelo: uma placa de
  KiCad com 316 peças leva 8 s para inspecionar, uma com 1164 peças leva 44 s.
- Nada persiste entre chamadas: o script de uma chamada não vê o documento da anterior.
  Quem precisa de continuidade grava em `OUT_DIR` e reabre.
- Flatpak e Snap isolam o `/tmp`, onde ficam os arquivos de job (não testado; ver README).

## Alternativas descartadas

- **Addon/RPC dentro da GUI** (por exemplo `neka-nat/freecad-mcp`): exige o FreeCAD
  aberto com display, então não serve para automação nem CI.
- **`import FreeCAD` no Python do servidor**: obriga a casar a versão do Python do
  servidor com a do FreeCAD, e um segfault ou travamento do OCC derruba o servidor
  inteiro, sem como aplicar timeout.
