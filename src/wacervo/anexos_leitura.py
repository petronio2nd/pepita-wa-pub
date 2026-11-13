"""Acha, para o MCP do WhatsApp, o arquivo e o OCR que a exportacao ja gravou de cada midia.

A exportacao grava `<raiz>/<contato>/anexos/AAAA-MM-DD_HHMMSS_<tipo>.<ext>` (hora de Brasilia
da mensagem) e poe o link e o texto lido da imagem no `.md` da conversa. A leitura ao vivo nao
traz nada disso; aqui se junta o que esta em disco. So biblioteca padrao, so leitura.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote

BRASILIA = timezone(timedelta(hours=-3))
TIPOS = ("image", "video", "document")
_PREFIXO = {"image": ("imagem",), "video": ("video", "gif")}
_LINK = re.compile(r"\]\((anexos/[^)\s]+)\)")


def slug(nome: str) -> str:
    s = unicodedata.normalize("NFD", nome or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "conversa"


def _quando(t: int) -> str:
    return datetime.fromtimestamp(int(t), BRASILIA).strftime("%Y-%m-%d_%H%M%S")


def ocr_do_md(texto: str) -> dict[str, dict]:
    """nome do anexo -> {"texto"|"curto"|"erro": ...}, lido dos blocos do .md exportado."""
    achados: dict[str, dict] = {}
    for bloco in re.split(r"\n\s*\n", texto):
        m = _LINK.search(bloco)
        if not m:
            continue
        nome = unquote(m.group(1)).split("/", 1)[1]
        linhas = bloco.splitlines()
        info: dict = {}
        for i, linha in enumerate(linhas):
            if linha.startswith("*texto na imagem:*"):
                citadas = [x[1:].lstrip() if x.startswith(">") else x for x in linhas[i + 1:] if x.startswith(">")]
                info["texto"] = "\n".join(citadas).strip()
            elif linha.startswith("*texto curto na imagem"):
                info["curto"] = linha.split(":*", 1)[-1].strip()
            elif linha.startswith("*[texto da imagem nao lido:"):
                info["erro"] = linha.strip("*[] ")
        achados[nome] = info
    return achados


class Anexos:
    """Resolve midias de uma leitura contra a pasta da exportacao (`raiz_de()` -> Path | None)."""

    def __init__(self, raiz_de) -> None:
        self._raiz_de = raiz_de
        self._pastas: dict[str, Path] = {}

    def _raiz(self) -> Path | None:
        try:
            r = self._raiz_de()
            return Path(r) if r else None
        except Exception:  # noqa: BLE001
            return None

    def _pasta(self, raiz: Path, chat: dict) -> Path | None:
        id_ = str(chat.get("id") or "")
        p = self._pastas.get(id_)
        if p and p.is_dir():
            return p
        marca = f"`{id_}`"
        dica = slug(str(chat.get("nome") or ""))
        try:
            pastas = [d for d in raiz.iterdir() if d.is_dir()]
        except OSError:
            return None
        pastas.sort(key=lambda d: not d.name.lower().startswith(dica))
        for d in pastas:
            try:
                for md in sorted(d.glob("*.md"))[:1]:
                    with md.open(encoding="utf-8", errors="replace") as f:
                        if marca in f.read(800):
                            self._pastas[id_] = d
                            return d
            except OSError:
                continue
        return None

    @staticmethod
    def _candidatos(anexos: Path, m: dict, quando: str) -> list[Path]:
        try:
            achados = sorted((p for p in anexos.glob(f"{quando}_*") if p.is_file()),
                             key=lambda p: (len(p.stem), p.name))
        except OSError:
            return []
        resto = lambda p: p.name[len(quando) + 1:].lower()  # noqa: E731
        tipo = m.get("tipo")
        if tipo in _PREFIXO:
            return [p for p in achados if resto(p).startswith(_PREFIXO[tipo])]
        livres = [p for p in achados if not resto(p).startswith(("imagem", "video", "gif"))]
        nome = unicodedata.normalize("NFC", str(m.get("arquivo") or "")).lower()
        stem = nome.rsplit(".", 1)[0].strip()
        pref = [p for p in livres if stem and stem in resto(p)]
        return pref or livres

    def juntar(self, chat: dict, mensagens: list[dict]) -> None:
        """Poe `anexo` em cada midia de `mensagens`: {"arquivo", "ocr"} ou {"pendente": motivo}."""
        alvos = [m for m in mensagens if m.get("tipo") in TIPOS and not m.get("visualizacaoUnica")]
        if not alvos:
            return
        raiz = self._raiz()
        pasta = self._pasta(raiz, chat) if raiz and raiz.is_dir() else None
        if pasta is None:
            motivo = "sem pasta de exportacao para esta conversa"
            for m in alvos:
                m["anexo"] = {"pendente": motivo}
            return
        ocr: dict[str, dict] = {}
        for md in sorted(pasta.glob("*.md")):
            try:
                ocr.update(ocr_do_md(md.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                continue
        usados: dict[tuple, int] = {}
        for m in alvos:
            quando = _quando(m.get("t") or 0)
            ordem = usados.get((quando, m["tipo"]), 0)
            usados[(quando, m["tipo"])] = ordem + 1
            achados = self._candidatos(pasta / "anexos", m, quando)
            if ordem >= len(achados):
                m["anexo"] = {"pendente": "ainda nao exportada"}
                continue
            arq = achados[ordem]
            m["anexo"] = {"arquivo": str(arq), "ocr": ocr.get(arq.name)}
