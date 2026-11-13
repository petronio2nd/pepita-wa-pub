# Instalar o pepita-wa em outro computador (com transcrição)

Roteiro para instalar o pepita-wa completo num Windows que não tem a distro WSL do usuário:
a extensão do Chrome e o `wa servir`, com os áudios transcritos pelo Gemini (via `agy`,
na conta Google de quem usa) e o detector de fala dentro do próprio `wa servir`
(`vad = "local"`, desde a 0.18.0). Pode ser seguido por uma pessoa ou pelo Claude Code
desse computador. Quem só quer exportar, sem transcrição, usa a versão só exportar
(`ferramentas/pacote_so_exportar.py`) e não precisa de nada disto.

O que sai da máquina: só o áudio com fala, que vai ao Google pelo `agy`, na conta de quem
instalou. Imagem, OCR, conversa e cache ficam no computador.

## 1. O que precisa antes

- Windows 10 ou 11 e o Google Chrome.
- O pacote de idioma **Português (Brasil)** do Windows, para o OCR das imagens
  (Configurações > Hora e idioma > Idioma).
- O **uv** (gerenciador de Python): `winget install --id=astral-sh.uv -e`. Ele baixa o
  Python 3.13 sozinho no passo 3.
- O **Google Antigravity** instalado, com login numa conta Google (o plano gratuito
  serve), e o CLI `agy` funcionando: num terminal novo, `agy --help` responde. O `wa servir`
  procura o `agy` no PATH e em `%LOCALAPPDATA%\agy\bin\agy.exe`. Se ficar em outro lugar,
  ponha o caminho em `[transcricao.gemini] agy` (passo 4).

## 2. Descompactar

Descompacte o zip numa pasta que vai ficar, por exemplo `C:\pepita-wa`. A extensão e o
servidor rodam dessa pasta: não apague nem mova depois.

## 3. Instalar as dependências

Na pasta do pepita-wa:

```bash
uv sync --extra vad-local
```

O extra `vad-local` traz numpy, onnxruntime e av, que o detector de fala usa. Sem ele, o
`wa servir` sobe, mas não transcreve nenhum áudio (o `/status` diz o que falta).

## 4. Configurar

Copie `config.exemplo.toml` para `config.toml` (o `config.toml` é só desta máquina) e mude:

- `[transcricao] vad = "local"`. **Obrigatório** neste computador: o padrão (`"distro"`)
  procura um serviço que aqui não existe.
- `[transcricao.gemini] contexto`: quem costuma falar nos áudios e de que assunto.
  Opcional, ajuda a acertar nomes.
- `[transcricao.gemini] termos`: nomes de pessoas e termos que voltam sempre, com a grafia
  certa. Sem a chave, vale uma lista padrão de termos jurídicos.
- `[exportar] raiz`: a pasta onde o *Exportar todas* grava, por exemplo
  `"C:/WHATSAPP"`. **Crie a pasta antes**: o servidor recusa pasta que não existe.
- `[rotina] perfil`: o perfil do Chrome onde a extensão vai ficar (veja `chrome://version`,
  "Caminho do perfil": o fim do caminho, como `Default` ou `Profile 1`). Vazio = o perfil
  que o Chrome abrir.
- `[servidor] extensao_id`: preencha no passo 6, depois de instalar a extensão.

## 5. Conferir

```bash
uv run python -m wacervo.cli status
```

Tem de mostrar `ocr: ... pt-BR (ok)`, o `agy` encontrado e
`vad: Silero na CPU dentro do wa servir (vad = "local"): pronto`.

## 6. Instalar a extensão

1. Abra `chrome://extensions` e ligue o **Modo do desenvolvedor**.
2. **Carregar sem compactação** e escolha a pasta `extensao` dentro do pepita-wa.
3. Copie o ID da extensão (a linha "ID") para `[servidor] extensao_id` no `config.toml`.
   Assim só essa extensão fala com o servidor.

## 7. Ligar o servidor

- Para subir agora, sem janela: dê dois cliques em `ferramentas\servir-oculto.vbs`. O log
  fica em `dados\servidor.log`. Para encerrar: `ferramentas\parar-servidor.cmd`.
- Para subir junto com o Windows: `Win+R`, `shell:startup`, e crie ali um atalho para o
  `ferramentas\servir-oculto.vbs`.
- Encerre o servidor antes de rodar `uv sync` de novo (atualização): com ele no ar, o
  Windows nega acesso aos arquivos do `.venv`.

## 8. Usar

1. Abra https://web.whatsapp.com e entre pelo QR code (se já estava aberto, recarregue a
   aba).
2. Numa conversa com áudio, a transcrição aparece embaixo de cada áudio visível.
3. Para exportar uma conversa: a seta no cabeçalho da conversa abre o painel, *Exportar*.
4. Para exportar todas as conversas individuais: o botão do pepita-wa na barra da
   esquerda do WhatsApp, *Exportar todas*. Depois disso, a rodada automática repete a cada
   30 min só com o que chegou de novo (liga e desliga no painel).

## Bom saber

- **Cota do plano gratuito:** cada áudio com fala é uma chamada ao `agy`. A primeira
  exportação de tudo transcreve o histórico inteiro e deve esbarrar na cota semanal do
  plano gratuito. Nada se perde: o servidor pausa (`pausa_min`), tenta de novo depois e
  guarda no cache o que já transcreveu. O histórico pode levar alguns dias para fechar;
  depois, o volume do dia a dia costuma caber. Se a pausa por cota virar rotina, um plano
  pago do Google AI resolve.
- **`agy` sem login:** o painel avisa "agy sem login". Rode `agy` num terminal e entre na
  conta.
- **Smart App Control:** o servidor sobe pelo `python.exe` do `.venv` (assinado), não pelo
  `wa.exe`, que o Smart App Control bloqueia. numpy, onnxruntime e av carregam com ele
  ligado (conferido em 10/11/2026).
- **Nada de commit aqui:** esta cópia não é um repositório git. Para atualizar, receba um
  zip novo, encerre o servidor, troque os arquivos (mantendo `config.toml` e `dados\`) e
  rode o passo 3 de novo.
