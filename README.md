# pepita-wa — conversa do WhatsApp Web em Markdown, com áudio transcrito

Extensão pessoal do Chrome para o WhatsApp Web mais um servidor local em Python.
Você abre uma conversa, clica no ícone e escolhe o que exportar. Sai **um arquivo `.md`**:
os **áudios vêm transcritos** (Gemini pelo CLI `agy`, sem reserva) e as
**imagens com texto** trazem o texto
lido (OCR nativo do Windows). Você salva esse arquivo na pasta do projeto onde a IA vai
usá-lo como contexto.

> **Cópia pública.** Este repositório é uma cópia limpa do projeto pessoal do autor, com histórico
> começando do zero. Os exemplos e testes usam só nomes e números fictícios. As notas "(usuário,
> data)" nos comentários guardam o porquê de cada decisão.
>
> **Licença.** O código próprio é MIT ([LICENSE](LICENSE)); os arquivos de terceiros mantêm as licenças deles.
>
> **⚠️ Termos de uso.** A extensão automatiza o WhatsApp Web (lê conversas e, pelo MCP, pode enviar
> mensagens com aprovação). Os termos do WhatsApp restringem automação; use por sua conta e risco,
> só na sua própria conta. `extensao/vendor/wppconnect-wa.js` é de terceiros e tem licença
> própria (`extensao/vendor/LICENSE-wa-js`).

Áudios com fala vão ao Google pelo `agy`, por autorização do usuário. Silero VAD roda
na CPU antes; silêncio fica local. Imagens e OCR permanecem na máquina.

```markdown
# Renata Silva

> contato · `556290000000@c.us` · 2026-09-17 · 3 mensagens · exportado em 2026-09-18 16:00

## 2026-09-17

**14:23 · Renata Silva**
Oi, conseguiu ver aquele documento?

**14:24 · Eu** *(editada)*
> ↩ *Renata Silva: Oi, conseguiu ver aquele documento?*
🎙️ *audio 0:12* — Vi sim, ta tudo certo, só falta a assinatura.

**14:25 · Renata Silva**
🖼️ *imagem* — *legenda:* o contrato
*texto na imagem:*
> CONTRATO DE PRESTAÇÃO DE SERVIÇOS
```

Não há captura ao vivo. A exportação começa quando você clica: *Exportar* grava a conversa
aberta, e *Exportar todas* grava todas as conversas individuais. Depois do primeiro
*Exportar todas*, uma rodada automática repete a cada 30 minutos só com o que chegou de novo,
e a primeira rodada sai 5 minutos depois de o `wa servir` ligar. Ela vem ligada no
`config.exemplo.toml` (`[rotina] ligada = true`). Para desligar, use o liga/desliga do painel,
que vale mais que o arquivo, ou ponha `ligada = false`. Com `abrir_chrome = true`, o servidor
abre o Chrome no WhatsApp Web quando não encontra a extensão. O que fica guardado é descrito
em [O que fica guardado](#o-que-fica-guardado).

Além disso, com o WhatsApp Web aberto, cada áudio que aparece na tela da conversa aberta
ganha **a transcrição logo embaixo**, com o botão *Copiar*. É o mesmo Gemini, e o
resultado fica no cache, então exportar depois não transcreve de novo. No painel, em
*Áudios da conversa aberta*, há duas opções separadas:

- **Transcrever automaticamente.** Desligada, a fila para e cada áudio ganha um botão
  *Transcrever*.
- **Mostrar o texto embaixo de cada áudio.** Desligada, as caixas somem. Se a outra opção
  estiver ligada, a transcrição continua por trás.

A fila é da aba e não da conversa. Se você troca de conversa ou fecha o painel, ela continua,
e quando o áudio volta à tela a caixa volta com o resultado.

## Instalação

O Silero VAD roda na CPU da distro WSL2 `ubuntu-dev`, em container. Suba-o uma vez; ele
volta sozinho a cada subida da distro (`restart: unless-stopped`):

```bash
ssh wsl
cd /caminho/do/pepita-wa-pub/servico-whisper && docker compose up -d --build
```

O container publica só `127.0.0.1:18765`, e o WSL repassa essa porta ao Windows. O
serviço só mede fala: não baixa nem carrega modelo Whisper e não recebe GPU.

No Windows:

```bash
uv sync
uv run python -m wacervo.cli servir          # deixe rodando enquanto o WhatsApp Web estiver aberto (Ctrl+C encerra)
```

No Chrome, abra `chrome://extensions`, ligue o **Modo do desenvolvedor**, clique em
**Carregar sem compactação** e escolha a pasta `extensao/`. Depois recarregue o WhatsApp Web.

Para restringir o servidor a essa extensão, copie o ID dela para `extensao_id` num
`config.toml`. Use `config.exemplo.toml` como modelo. O arquivo é opcional e sem ele valem os
padrões.

### Rodar sem janela

`ferramentas\servir-oculto.vbs` liga o servidor escondido: sem console e sem ícone na barra.
O que apareceria no terminal vai para `dados\servidor.log`; passando de 5 MB ele vira
`servidor.log.1` e recomeça. Clicar no `.vbs` com o servidor já no ar não sobe um segundo.

Para subir junto com o Windows, tecle `Win+R`, abra `shell:startup` e ponha ali um atalho
para esse `.vbs`. Para desfazer, apague o atalho.

Para encerrar, `ferramentas\parar-servidor.cmd`: ele fecha só o servidor desta pasta.
Encerre antes de mexer em versão ou dependência — com o servidor no ar o `uv sync` pode
falhar com "Acesso negado".

Os comandos passam pelo `python -m wacervo.cli`, não pelo `wa.exe`: o executável que o `uv`
gera não tem assinatura, e o Smart App Control do Windows bloqueia (aviso da Segurança do
Windows, `os error 4551`). O Smart App Control não aceita exceção por pasta. Pelo mesmo
motivo o Whisper saiu do Windows: o `_ext.pyd` do CTranslate2 também não tem assinatura.

## Uso

1. Abra a conversa no WhatsApp Web e clique na seta de exportar, no cabeçalho da conversa
   (ao lado da câmera), ou no ícone do pepita-wa na barra do Chrome.
2. Escolha um dos modos:
   - **Tudo o que está carregado:** role a conversa para cima até onde quiser e exporte. O
     painel mostra quantas mensagens estão carregadas e desde quando.
   - **Período:** de uma data até outra. A extensão busca sozinha as mensagens mais antigas.
3. Marque ou desmarque *Transcrever áudios* e *Ler texto das imagens*. Desmarcada, a opção
   fica registrada no cabeçalho do `.md`. Os áudios que já aparecem transcritos na tela
   entram com o texto mesmo com *Transcrever áudios* desmarcada: o texto já está pronto.
4. Clique em **Exportar .md** e escolha onde salvar. O Chrome lembra a última pasta.

A conversa fica fixada no clique. Se ela mudar antes ou durante a leitura, a exportação para
com um aviso. **Cancelar** não salva nada. Você pode fechar o painel (× ou Esc) sem cancelar;
o ícone mostra a porcentagem da exportação ou quantos áudios ainda estão na fila.

No fim aparece um resumo com mensagens, áudios transcritos, imagens com texto, reaproveitados
e falhas. Quando há falha, o botão *Tentar de novo só as falhas* reprocessa só essas, sem
ler a conversa de novo. Se o `wa servir` estiver fora do ar, você escolhe entre *Tentar de
novo*, depois de ligá-lo, e *Exportar sem transcrição/OCR*. Nesse segundo caso o arquivo diz
que saiu sem transcrição.

O cabeçalho do `.md` registra o período pedido e o fuso. Se a busca parou antes do início
pedido, ele diz isso: pode ter sido o limite de páginas (a exportação é parcial) ou o
histórico do WhatsApp Web ter acabado antes.

Desde a 0.17.0 não há Whisper de reserva. Cota ou falha do Gemini deixa o áudio
sem transcrição até poder tentar novamente. Se o serviço de VAD da distro não responder,
o áudio também sai como falha: nada é enviado ao Gemini sem detectar fala antes.

```bash
uv run python -m wacervo.cli status                    # servidor no ar?, estado do whisper, cache, retenção
uv run python -m wacervo.cli transcrever audio.ogg     # usa o mesmo serviço da distro fora do navegador
uv run python -m wacervo.cli ocr print.png
uv run python -m wacervo.cli limpar-cache              # mostra quanto apagaria; --sim apaga
```

## O que fica guardado

- **Cache do servidor** (`dados/cache.sqlite3`). Guarda só o **texto** de cada áudio e
  imagem já processados, pelo sha256 do arquivo, junto com a configuração que o produziu.
  A mídia nunca é guardada. Com o cache, exportar de novo não reprocessa nada. Ele fica até
  você apagar:
  - `uv run python -m wacervo.cli limpar-cache --sim` apaga tudo.
  - `--mais-de-dias N` apaga só o que foi gravado há mais de N dias.
  - `--so-legado` apaga só o que veio de versões antigas.
  - Para expirar sozinho, use `manter_dias` na seção `[cache]` do `config.toml`.

  Mudar o modelo, o idioma ou o dispositivo não reaproveita resultados antigos.
- **Memória da aba.** As transcrições mostradas na tela ficam até você recarregar o WhatsApp
  Web.
- **O `.md`** fica só onde você salvou.
- **Preferências da extensão** (porta e opções do painel) ficam no `chrome.storage.local`.

## Como funciona

```
WhatsApp Web
  pagina.js   (mundo MAIN, wa-js)   lê mensagens e baixa a mídia sob pedido
      │ postMessage
  ponte.js    (content script)      painel, transcrição na tela, monta o .md (markdown.js)
      │ chrome.runtime
  fundo.js    (service worker)      único que fala com http://127.0.0.1:<porta>
      │
wa servir     (Python, Windows)     /transcrever · /ocr (Windows.Media.Ocr) · cache
      │ http://127.0.0.1:18765
pepita_wa_whisper (container na distro WSL2)   faster-whisper na GPU
```

- O acesso ao WhatsApp é feito pelo [wa-js](https://github.com/wppconnect-team/wa-js) 4.8.2
  (Apache-2.0), em `extensao/vendor/`, **modificado** por `ferramentas/patch_wajs.py` (aviso no topo do arquivo).
- O servidor só escuta em `127.0.0.1`. Ele recusa `Host` diferente de
  `127.0.0.1`/`localhost` (contra DNS rebinding) e qualquer `Origin` que não seja
  `chrome-extension://`, então uma página web aberta não consegue usá-lo.
- Uma imagem só ganha "texto na imagem" quando o OCR acha pelo menos 15 caracteres úteis
  (`minimo_caracteres`). Abaixo disso, o que foi lido aparece numa linha à parte,
  *texto curto na imagem (pode ser ruído)*, em vez de sumir.
- Cada pedido tem prazo. Assim, mídia que o WhatsApp não entrega, WhatsApp travado,
  servidor fora do ar e servidor lento aparecem como mensagens diferentes. Nada fica
  esperando para sempre.

## Testes

```bash
uv run python -m pytest -q                           # servidor, cache, config (sem rede nem GPU)
node --test extensao/testes/*.test.js      # Markdown, exportação, fila, prazos (nucleo.js)
```

## Requisitos

- Windows, Python ≥ 3.13 e [uv](https://docs.astral.sh/uv/).
- Chrome ou Edge.
- Distro WSL2 com Docker, para o detector Silero VAD na CPU.
- CLI `agy` com login e cota no Google para transcrever; nenhuma GPU necessária.
- OCR: o pacote de idioma pt-BR do Windows.

## Outras extensões

As demais extensões pessoais do Chrome ficam no repositório
[my-extensions](https://github.com/petronio2nd/my-extensions), que aponta de volta para
este. O pepita-wa fica aqui porque depende do servidor Python e sobe de versão junto com ele.
