"""Gravacao do "Exportar todas" pelo `wa servir` (0.16.0).

Antes, a extensao gravava em C:\\WHATSAPP pelo seletor de pasta da pagina do
WhatsApp. A rodada automatica (usuario, 2026-10-31) nao tem clique para abrir o
seletor, e o Chromium nao deixa a extensao guardar a pasta escolhida fora da
origem do WhatsApp. Agora a extensao manda o .md e os anexos para ca e o
servidor grava, sempre dentro da raiz configurada (`[exportar] raiz`).

Regras do caminho que vem da extensao (`caminho_relativo`): so `/` como
separador, 2 ou 3 partes (`<contato>/<arquivo>` ou `<contato>/anexos/<arquivo>`),
nada de `..`, `.`, caminho absoluto, letra de unidade, caractere que o Windows
recusa ou nome reservado (CON, NUL...). Depois de montado, o caminho resolvido
(seguindo atalhos e junções) tem que continuar dentro da raiz.

Nunca sobrescreve arquivo que ja tem conteudo: o .md pede `livre` e ganha `-2`,
`-3`; o anexo que ja existe e pulado pela extensao (GET /arquivo). Cada
exportacao de conversa e um `lote`: desfazer o lote apaga so o que ele criou.
"""

from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

# Uma parte de um anexo grande por pedido: o service worker manda em pedacos.
PARTE_MAXIMA = 16 * 1024 * 1024
_PROIBIDOS = set('<>:"/\\|?*')
_RESERVADOS = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$", re.IGNORECASE)
_LOTE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SUFIXO_PARCIAL = ".parcial"
# Lote sem desfazer nem concluir (aba fechada no meio): sai da memoria depois disto.
_VIDA_DO_LOTE_S = 6 * 3600


class ErroDeGravacao(ValueError):
    """Pedido de gravacao recusado. `codigo` e o HTTP que volta para a extensao."""

    def __init__(self, mensagem: str, codigo: int = 400) -> None:
        super().__init__(mensagem)
        self.codigo = codigo


def _parte_valida(parte: str) -> bool:
    return bool(
        parte
        and parte not in (".", "..")
        and len(parte) <= 180
        and not any(c in _PROIBIDOS or ord(c) < 32 for c in parte)
        and not parte.endswith((".", " "))
        and not parte.startswith(" ")
        and not _RESERVADOS.match(parte)
        and not parte.endswith(_SUFIXO_PARCIAL)
    )


def caminho_relativo(texto: str) -> tuple[str, ...]:
    if not isinstance(texto, str) or not texto:
        raise ErroDeGravacao("caminho vazio")
    partes = tuple(texto.split("/"))
    if not 2 <= len(partes) <= 3 or (len(partes) == 3 and partes[1] != "anexos"):
        raise ErroDeGravacao("caminho fora do formato <contato>/<arquivo> ou <contato>/anexos/<arquivo>")
    if not all(_parte_valida(p) for p in partes):
        raise ErroDeGravacao("caminho invalido")
    return partes


@dataclass
class _Lote:
    criado: float
    arquivos: list[Path] = field(default_factory=list)
    pastas: list[Path] = field(default_factory=list)
    # destino final -> (arquivo .parcial, bytes ja gravados)
    parciais: dict[Path, tuple[Path, int]] = field(default_factory=dict)


class Gravador:
    def __init__(self, raiz: Path, relogio=time.monotonic) -> None:
        self.raiz = Path(raiz)
        self._relogio = relogio
        self._lotes: dict[str, _Lote] = {}
        self._trava = threading.Lock()

    def situacao(self) -> dict:
        return {"raiz": str(self.raiz), "existe": self.raiz.is_dir()}

    def _base(self) -> Path:
        if not self.raiz.is_dir():
            raise ErroDeGravacao(f"a pasta {self.raiz} nao existe; crie-a ou ajuste [exportar] raiz", 409)
        return self.raiz.resolve()

    def _destino(self, texto: str) -> tuple[Path, Path]:
        partes = caminho_relativo(texto)
        base = self._base()
        alvo = base.joinpath(*partes)
        # resolve() segue atalho e juncao das partes que ja existem.
        if not alvo.resolve().is_relative_to(base):
            raise ErroDeGravacao("caminho fora da raiz", 403)
        return base, alvo

    def _lote(self, nome: str) -> _Lote:
        if not isinstance(nome, str) or not _LOTE.match(nome):
            raise ErroDeGravacao("lote invalido")
        agora = self._relogio()
        for velho in [k for k, v in self._lotes.items() if agora - v.criado > _VIDA_DO_LOTE_S]:
            self._descartar(self._lotes.pop(velho), apagar=False)
        if nome not in self._lotes:
            self._lotes[nome] = _Lote(criado=agora)
        return self._lotes[nome]

    def existe(self, texto: str) -> dict:
        _, alvo = self._destino(texto)
        if alvo.is_file():
            return {"existe": True, "tamanho": alvo.stat().st_size}
        return {"existe": False, "tamanho": 0}

    @staticmethod
    def _livre(alvo: Path) -> Path:
        tentativa = alvo
        for n in range(2, 10_000):
            ocupado = tentativa.exists() or tentativa.with_name(tentativa.name + _SUFIXO_PARCIAL).exists()
            if not ocupado:
                return tentativa
            tentativa = alvo.with_name(f"{alvo.stem}-{n}{alvo.suffix}")
        raise ErroDeGravacao("sem nome livre para o arquivo", 409)

    def gravar(self, texto: str, dados: bytes, *, lote: str, deslocamento: int = 0,
               fim: bool = True, livre: bool = False) -> dict:
        """Grava `dados` em `texto` (relativo a raiz). Um anexo grande vem em partes:
        a primeira com deslocamento 0, as outras logo depois da anterior, e a ultima
        com `fim`; so entao o arquivo aparece com o nome certo."""
        if len(dados) > PARTE_MAXIMA:
            raise ErroDeGravacao("parte grande demais", 413)
        with self._trava:
            base, alvo = self._destino(texto)
            registro = self._lote(lote)
            if deslocamento == 0:
                if alvo in registro.parciais:
                    registro.parciais.pop(alvo)[0].unlink(missing_ok=True)
                if livre:
                    alvo = self._livre(alvo)
                elif alvo.is_file() and alvo.stat().st_size > 0:
                    raise ErroDeGravacao("o arquivo ja existe", 409)
                elif alvo.exists() and not alvo.is_file():
                    raise ErroDeGravacao("o caminho e uma pasta", 409)
                self._criar_pastas(base, alvo.parent, registro)
                parcial = alvo.with_name(alvo.name + _SUFIXO_PARCIAL)
                with open(parcial, "wb") as f:
                    f.write(dados)
                escritos = len(dados)
            else:
                anterior = registro.parciais.get(alvo)
                if anterior is None or anterior[1] != deslocamento:
                    raise ErroDeGravacao("parte fora de ordem; comece o arquivo de novo", 409)
                parcial = anterior[0]
                with open(parcial, "ab") as f:
                    f.write(dados)
                escritos = deslocamento + len(dados)
            if not fim:
                registro.parciais[alvo] = (parcial, escritos)
                return {"caminho": alvo.relative_to(base).as_posix(), "tamanho": escritos, "fim": False}
            registro.parciais.pop(alvo, None)
            os.replace(parcial, alvo)
            registro.arquivos.append(alvo)
            return {"caminho": alvo.relative_to(base).as_posix(), "tamanho": escritos, "fim": True}

    @staticmethod
    def _criar_pastas(base: Path, pasta: Path, registro: _Lote) -> None:
        faltam = []
        p = pasta
        while p != base and not p.exists():
            faltam.append(p)
            p = p.parent
        for p in reversed(faltam):
            p.mkdir()
            registro.pastas.append(p)

    @staticmethod
    def _descartar(registro: _Lote, apagar: bool) -> int:
        removidos = 0
        for parcial, _ in registro.parciais.values():
            parcial.unlink(missing_ok=True)
        if not apagar:
            return 0
        for arquivo in registro.arquivos:
            try:
                arquivo.unlink()
                removidos += 1
            except FileNotFoundError:
                pass
        for pasta in reversed(registro.pastas):
            try:
                pasta.rmdir()  # so se ficou vazia: nada que nao seja do lote sai
            except OSError:
                pass
        return removidos

    def encerrar_lote(self, lote: str, desfazer: bool) -> dict:
        """`desfazer`: apaga os arquivos e as pastas que o lote criou (cancelou ou
        falhou antes do .md). Sem ele, so esquece o lote (o .md foi salvo)."""
        if not isinstance(lote, str) or not _LOTE.match(lote):
            raise ErroDeGravacao("lote invalido")
        with self._trava:
            registro = self._lotes.pop(lote, None)
            removidos = self._descartar(registro, apagar=desfazer) if registro else 0
        return {"ok": True, "removidos": removidos}
