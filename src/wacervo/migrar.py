"""`wa migrar`: leva um lote antigo do "Exportar todas" para a pasta nova e marca.

O lote antigo (uma pasta qualquer, com <nome>_<datas>/<nome>_<datas>.md e anexos/,
ou so os .md soltos) vai para D:\\70-WHATSAPP no formato novo: uma pasta por contato
(<contato>/), com o .md e os anexos dentro. Cada conversa ganha a marca do trecho
que o .md cobre, sem o comeco do historico (`completaAntes` falso): a primeira rodada
nova le o historico inteiro e grava so o que ficou de fora (usuario, 2026-10-24).

So copia: a origem fica como esta, e arquivo que ja existe no destino com o mesmo
tamanho nao e copiado de novo. Rodar duas vezes da o mesmo resultado.

O trecho sai do cabecalho do .md, que tem minuto e nao segundo. O comeco marcado fica
no fim do minuto da primeira mensagem, e o fim logo antes do minuto da ultima: na
duvida, a rodada nova repete as mensagens daqueles dois minutos, mas nao perde nenhuma. So entra .md do modo "Tudo o que esta carregado", que traz as mensagens
mais recentes ate a coleta. A folga de 1 h que havia aqui (2026-10-24) fez a primeira
rodada repetir uma hora inteira de cada conversa.
"""

from __future__ import annotations

import filecmp
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .marcas import Marcas

# Sem tzdata no Windows: os fusos do Brasil tem deslocamento fixo desde 2019.
_FUSOS = {
    **dict.fromkeys(
        ("America/Sao_Paulo", "America/Bahia", "America/Fortaleza", "America/Recife",
         "America/Belem", "America/Maceio", "America/Araguaina", "America/Santarem"), -3),
    **dict.fromkeys(
        ("America/Manaus", "America/Cuiaba", "America/Campo_Grande", "America/Porto_Velho",
         "America/Boa_Vista"), -4),
    **dict.fromkeys(("America/Rio_Branco", "America/Eirunepe"), -5),
    "America/Noronha": -2,
}
_MINUTO = 60

_TITULO = re.compile(r"^# (.+)$")
_CONTATO = re.compile(
    r"^> contato · `([^`]+@[^`]+)` · .* · exportado em (\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2})\s*$"
)
_FUSO = re.compile(r"· fuso ([A-Za-z_/]+)\s*$")
_DIA = re.compile(r"^## (\d{4}-\d{2}-\d{2})\s*$")
_HORA = re.compile(r"^\*{1,2}(\d{2}):(\d{2}) · ")


@dataclass
class Conversa:
    md: Path
    anexos: Path | None
    id: str
    nome: str
    primeira: int
    ultima: int
    pasta: str = ""


def ler_cabecalho(texto: str) -> dict:
    """id, nome e o trecho coberto (segundos). So confia no que o gerador escreve
    antes da primeira mensagem: o texto das mensagens pode imitar um cabecalho."""
    linhas = texto.splitlines()
    titulo = _TITULO.match(linhas[0]) if linhas else None
    contato = next((c for c in map(_CONTATO.match, linhas[1:4]) if c), None)
    if not titulo or not contato:
        raise ValueError("nao e um .md de conversa individual do pepita-wa")
    # So o modo "carregado" traz as mensagens mais recentes ate a hora da coleta; um
    # periodo antigo exportado hoje nao cobre o que veio depois dele.
    if not any(l.startswith("> coleta: mensagens ja carregadas na tela") for l in linhas[2:6]):
        raise ValueError("nao foi exportado em 'Tudo o que esta carregado'")
    fuso_nome = next((f.group(1) for f in map(_FUSO.search, linhas[1:6]) if f), "America/Sao_Paulo")
    if fuso_nome not in _FUSOS:
        raise ValueError(f"fuso {fuso_nome} desconhecido")
    fuso = timezone(timedelta(hours=_FUSOS[fuso_nome]))

    # O texto de uma mensagem pode imitar `## dia` ou `**HH:MM`, mas nao antes da primeira
    # mensagem real: hora invalida ou depois do "exportado em" fica de fora.
    _, data, hh, mm = contato.groups()
    exportado = int(datetime.fromisoformat(f"{data}T{hh}:{mm}").replace(tzinfo=fuso).timestamp())
    dia = None
    horas = []
    for linha in linhas:
        d = _DIA.match(linha)
        if d:
            dia = d.group(1)
            continue
        h = _HORA.match(linha) if dia else None
        if not h:
            continue
        try:
            t = int(datetime.fromisoformat(f"{dia}T{h.group(1)}:{h.group(2)}").replace(tzinfo=fuso).timestamp())
        except ValueError:
            continue
        if t <= exportado:
            horas.append(t)
    if not horas:
        raise ValueError("sem mensagem")
    # A coleta pode ter parado no meio do minuto da ultima mensagem: esse minuto fica de
    # fora da marca e volta na rodada nova.
    ultima = max(horas) - 1
    inicio = min(horas[0] + _MINUTO, ultima)
    return {"id": contato.group(1), "nome": titulo.group(1).strip(), "primeira": inicio, "ultima": ultima}


def _usuario(ident: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", ident.split("@")[0].lower()).strip("-")


def planejar(origem: Path, marcas: dict[str, dict]) -> tuple[list[Conversa], list[str]]:
    """Le a origem e escolhe a pasta de cada contato. Devolve (conversas, problemas)."""
    candidatos: list[tuple[Path, Path | None]] = []
    for item in sorted(origem.iterdir()):
        if item.is_dir():
            mds = sorted(item.glob("*.md"))
            anexos = item / "anexos"
            candidatos += [(md, anexos if anexos.is_dir() else None) for md in mds]
        elif item.suffix == ".md":
            candidatos.append((item, None))

    conversas: list[Conversa] = []
    problemas: list[str] = []
    vistos: set[str] = set()
    for md, anexos in candidatos:
        try:
            c = ler_cabecalho(md.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError) as erro:
            problemas.append(f"{md.relative_to(origem)}: {erro}")
            continue
        # Dois trechos do mesmo contato podem nao se encostar: juntar marcaria o buraco.
        if c["id"] in vistos:
            problemas.append(f"{md.relative_to(origem)}: contato repetido no lote, fica de fora")
            continue
        vistos.add(c["id"])
        conversas.append(Conversa(md=md, anexos=anexos, **c))

    # Pasta: a da marca, se o contato ja tem; senao o nome do arquivo ate o primeiro
    # `_` (o slug do contato), com o numero do id quando outro contato ja tem esse nome.
    donos = {m["pasta"].lower(): ident for ident, m in marcas.items()}
    for c in conversas:
        if c.id in marcas:
            c.pasta = marcas[c.id]["pasta"]
            continue
        base = c.md.stem.split("_")[0] or "conversa"
        pasta = base
        if donos.get(pasta.lower(), c.id) != c.id:
            pasta = f"{base}-{_usuario(c.id)}"
        n = 2
        while donos.get(pasta.lower(), c.id) != c.id:
            pasta = f"{base}-{_usuario(c.id)}-{n}"
            n += 1
        donos[pasta.lower()] = c.id
        c.pasta = pasta
    return conversas, problemas


def _copiar(fonte: Path, destino: Path) -> bool:
    # Conteudo, nao so o tamanho: arquivo diferente com o mesmo nome e tamanho e copiado.
    # O cache do filecmp usa tamanho e mtime: no mesmo tick do relogio, e depois do
    # copy2 (que copia o mtime), devolveria a comparacao antiga.
    filecmp.clear_cache()
    if destino.exists() and filecmp.cmp(fonte, destino, shallow=False):
        return False
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(fonte, destino)
    return True


def aplicar(conversas: list[Conversa], destino: Path, marcas: Marcas) -> dict:
    """Copia e marca. A marca entra so depois de o .md estar no destino."""
    copiados = 0
    for c in conversas:
        pasta = destino / c.pasta
        if c.anexos:
            for arquivo in sorted(c.anexos.iterdir()):
                if arquivo.is_file():
                    copiados += _copiar(arquivo, pasta / "anexos" / arquivo.name)
        copiados += _copiar(c.md, pasta / c.md.name)
        marcas.registrar({"id": c.id, "nome": c.nome, "pasta": c.pasta, "primeira": c.primeira,
                          "ultima": c.ultima, "completaAntes": False})
    return {"conversas": len(conversas), "copiados": copiados}
