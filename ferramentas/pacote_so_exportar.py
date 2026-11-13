"""Gera a versao "so exportar" da extensao, para instalar em outro computador.

Sem o wa servir: nada de transcricao, OCR ou servidor local. Exporta a conversa
aberta para .md e baixa os anexos, com os audios como arquivo (.opus).

    python ferramentas/pacote_so_exportar.py [--sufixo NOME] [--saida PASTA]

A extensao/ do repositorio (a do usuario) nao muda: as trocas abaixo valem so para
a copia, em dist/ (fora do git), com o .zip e o LEIA-ME.txt. Cada troca tem que
achar o trecho exatamente uma vez; se o codigo mudar, o script para e diz qual.
"""

import argparse
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
EXTENSAO = RAIZ / "extensao"
FORA = {"testes", "gerar.py"}
# Sobe a cada mudanca da copia: o painel mostra <versao da extensao>.<REVISAO>.
REVISAO = 3

# Painel so com o modo e "Baixar anexos"; a fila da tela comeca e fica parada. Sem o
# Exportar todas: ele precisa do pepita-wa para saber o que ja foi exportado.
TROCAS_PONTE = [
    ("  const CAPTURA = true;", "  const CAPTURA = false;"),
    (
        "chrome.storage.local.get({ porta: 8765, audio: true, imagem: true, anexos: true })",
        "chrome.storage.local.get({ porta: 8765, audio: false, imagem: false, anexos: true })",
    ),
    ("  let auto = true;\n  let mostrar = true;", "  let auto = false;\n  let mostrar = false;"),
    ("const antigo = c.naTela ?? true;", "const antigo = c.naTela ?? false;"),
    (
        "    code { font-size: 11.5px; }\n",
        "    code { font-size: 11.5px; }\n"
        "    .servidor, label:has([data-r=audio]), label:has([data-r=imagem]), fieldset:has([data-r=auto]),\n"
        "    fieldset:has([data-r=exportarTodas]),\n"
        "      details { display: none !important; }\n",
    ),
    ("Baixar anexos (fotos, videos, documentos)", "Baixar anexos (fotos, videos, audios, documentos)"),
]

# Audio tambem vira anexo: sem transcricao, e o unico jeito de nao perder o conteudo.
TROCAS_NUCLEO = [
    ("new Set(['image', 'video', 'document'])", "new Set(['image', 'video', 'document', 'ptt', 'audio'])"),
    (
        "'application/pdf': 'pdf', 'text/plain': 'txt',",
        "'application/pdf': 'pdf', 'text/plain': 'txt',\n"
        "    'audio/ogg': 'opus', 'audio/mpeg': 'mp3', 'audio/mp4': 'm4a', 'audio/aac': 'aac',",
    ),
    (
        "{ image: 'jpg', video: 'mp4', document: 'bin' }",
        "{ image: 'jpg', video: 'mp4', document: 'bin', ptt: 'opus', audio: 'opus' }",
    ),
    (
        ": m.tipo === 'video' ? (m.gif ? 'gif' : 'video') : 'documento';",
        ": m.tipo === 'video' ? (m.gif ? 'gif' : 'video') : ehAudio(m) ? 'audio' : 'documento';",
    ),
    # Sem OCR, nada prende o anexo a ordem da conversa: 4 downloads ao mesmo tempo.
    # Um por vez, um grupo com 500 anexos levava mais de uma hora (~15 s cada).
    (
        "    let feitosAnexo = 0;\n",
        "    let feitosAnexo = 0;\n    const vezAnexo = criarPiscina(4);\n    const baixando = [];\n",
    ),
    ("      deps.passo?.(++feitos, trabalho.length);\n", ""),
    (
        "        mostrar(`baixando anexo ${++feitosAnexo} de ${nomes.size}...`);\n"
        "        anexos.set(m.id, await op.etapa(deps.anexar(m, nomes.get(m.id))));\n"
        "      }\n"
        "    }\n",
        "        const p = vezAnexo(() => {\n"
        "          op.verificar();\n"
        "          return deps.anexar(m, nomes.get(m.id));\n"
        "        }).then((a) => {\n"
        "          anexos.set(m.id, a);\n"
        "          mostrar(`baixando anexos: ${++feitosAnexo} de ${nomes.size}...`);\n"
        "          deps.passo?.(feitosAnexo, nomes.size);\n"
        "        });\n"
        "        p.catch(() => {}); // quem espera e o Promise.all; cancelado, ninguem espera\n"
        "        baixando.push(p);\n"
        "      }\n"
        "    }\n"
        "    await op.etapa(Promise.all(baixando));\n",
    ),
]

TROCAS_MARKDOWN = [
    (
        "`🎙️ *audio ${duracao(m.duracao || 0)}${m.visualizacaoUnica ? ' (visualizacao unica)' : ''}*`,",
        "`🎙️ *audio ${duracao(m.duracao || 0)}${m.visualizacaoUnica ? ' (visualizacao unica)' : ''}*` + anexo(m, anexos),",
    ),
    ("(fotos, videos e documentos)", "(fotos, videos, audios e documentos)"),
    (
        "`> ⚠️ transcricao dos audios desligada nesta exportacao: ${audios - daTela} de ${audios} audio(s) sem texto` +",
        "`> versao so exportar, sem transcricao: ${audios - daTela} de ${audios} audio(s) sem texto; "
        "com \"Baixar anexos\", o arquivo de cada um esta em anexos/` +",
    ),
]

# Sem servidor, sem relogio: o fundo.js nao pergunta pela rodada automatica.
TROCAS_FUNDO = [("const ROTINA = true;", "const ROTINA = false;"), ("const AGENTE = true;", "const AGENTE = false;")]

TROCAS = {
    "fundo.js": TROCAS_FUNDO,
    "ponte.js": TROCAS_PONTE,
    "nucleo.js": TROCAS_NUCLEO,
    "nucleo-pagina.js": TROCAS_NUCLEO,
    "markdown.js": TROCAS_MARKDOWN,
}

LEIA_ME = """\
pepita-wa {versao} - so exportar
================================

Exporta uma conversa ou grupo do WhatsApp Web para um arquivo .md, com fotos,
videos, audios e documentos numa pasta anexos/ ao lado. Nao transcreve audio e nao
le texto de imagem. Nao precisa de servidor nem de outro programa: so o navegador.
Nada sai do computador: tudo fica na pasta que voce escolher.

Instalar (uma vez)
------------------
1. Use o Google Chrome (ou o Microsoft Edge) atualizado.
2. Descompacte este .zip numa pasta que vai ficar, por exemplo Documentos\\pepita-wa.
   Nao apague a pasta depois: a extensao roda dela.
3. Abra chrome://extensions (no Edge, edge://extensions).
4. Ligue o "Modo do desenvolvedor" (canto superior direito; no Edge, na lateral).
5. Clique em "Carregar sem compactacao" e escolha a pasta {pasta}
   (a que tem o arquivo manifest.json dentro).
6. Abra https://web.whatsapp.com e entre pelo QR code. Se ja estava aberto,
   recarregue a aba (F5).

Exportar o grupo
----------------
1. Abra o grupo no WhatsApp Web.
2. Clique na seta para cima no cabecalho da conversa (ou no icone da extensao,
   na barra do navegador). Abre o painel do pepita-wa.
3. Escolha o que exportar:
   - "Periodo": marque as datas de inicio e fim. Para o grupo inteiro, use
     como inicio o dia em que o grupo foi criado.
   - "Tudo o que esta carregado": role a conversa para cima ate onde quiser antes.
4. Deixe "Baixar anexos" marcada e clique em Exportar.
5. Escolha a pasta onde salvar e permita a gravacao quando o navegador perguntar.
6. Espere ate aparecer "salvo: ...". Nao troque de conversa nem feche a aba
   enquanto exporta (trocar cancela, e nada e salvo). Fechar o painel pode.

Sai uma pasta com o nome do grupo e as datas, com o .md e a pasta anexos/.
Para mandar, compacte essa pasta inteira.

Bom saber
---------
- O WhatsApp Web so tem o historico que o celular sincronizou. Se o periodo
  comecar antes, o cabecalho do .md diz onde o historico disponivel comeca.
- Se algum anexo nao baixar, o .md marca "anexo nao baixado" na mensagem, e o
  painel mostra "Tentar de novo so as falhas".
- Midia de visualizacao unica nao e entregue pelo WhatsApp Web.
- Para tirar a extensao: chrome://extensions, botao Remover.
"""


def trocar(arquivo: Path, trocas: list[tuple[str, str]]) -> None:
    texto = arquivo.read_text(encoding="utf-8")
    for de, para in trocas:
        if texto.count(de) != 1:
            sys.exit(f"{arquivo.name} mudou: o trecho nao aparece uma vez so, ajuste o pacote_so_exportar.py:\n{de}")
        texto = texto.replace(de, para)
    arquivo.write_text(texto, encoding="utf-8")


def gerar(destino: Path, nome: str) -> tuple[Path, Path]:
    manifesto = json.loads((EXTENSAO / "manifest.json").read_text(encoding="utf-8"))
    versao = f"{manifesto['version']}.{REVISAO}"
    manifesto["version"] = versao

    pasta = destino / nome
    if pasta.exists():
        shutil.rmtree(pasta)
    shutil.copytree(EXTENSAO, pasta, ignore=lambda _d, nomes: [n for n in nomes if n in FORA])
    for arquivo, trocas in TROCAS.items():
        trocar(pasta / arquivo, trocas)

    # Sem servidor local, a permissao para 127.0.0.1 nao serve para nada.
    manifesto["name"] = "pepita-wa (so exportar)"
    manifesto["description"] = (
        "Exporta a conversa aberta no WhatsApp Web em Markdown, com fotos, videos, "
        "audios e documentos numa pasta ao lado. Sem transcricao nem OCR."
    )
    manifesto["host_permissions"] = [p for p in manifesto["host_permissions"] if "127.0.0.1" not in p]
    manifesto["permissions"] = [p for p in manifesto["permissions"] if p != "alarms"]
    (pasta / "manifest.json").write_text(json.dumps(manifesto, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (pasta / "LEIA-ME.txt").write_text(LEIA_ME.format(versao=versao, pasta=nome), encoding="utf-8-sig")

    arquivo_zip = destino / f"{nome}-{versao}.zip"
    with zipfile.ZipFile(arquivo_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(pasta.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(destino).as_posix())
    return pasta, arquivo_zip


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--sufixo", default="", help="vai no nome da pasta: pepita-wa-so-exportar-<sufixo>")
    p.add_argument("--saida", type=Path, default=RAIZ / "dist")
    a = p.parse_args()
    sufixo = re.sub(r"[^a-z0-9-]+", "-", a.sufixo.lower()).strip("-")
    nome = "pepita-wa-so-exportar" + (f"-{sufixo}" if sufixo else "")
    a.saida.mkdir(parents=True, exist_ok=True)
    pasta, arquivo_zip = gerar(a.saida, nome)
    print(f"pasta: {pasta}")
    print(f"zip:   {arquivo_zip} ({arquivo_zip.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
