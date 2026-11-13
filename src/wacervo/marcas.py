"""Marca do "Exportar todas": ate onde cada conversa ja foi exportada.

O botao exporta para D:\\70-WHATSAPP, que o Pepita le, e pula o que ja foi: so
as mensagens fora do trecho marcado vao para um arquivo novo. Aqui fica so o
metadado (id, nome, pasta, o trecho coberto em segundos e se o historico ja foi
lido ate o comeco), nunca o texto de conversa (usuario, 2026-10-24).

`primeira`/`ultima` sao segundos desde 1970 (o `t` do WhatsApp). O trecho e
continuo: cada exportacao parte dele para tras (historico antigo) e para frente
(o que chegou depois), e a uniao continua sem buraco.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

_ESQUEMA = (
    """
    CREATE TABLE IF NOT EXISTS conversas (
        id             TEXT PRIMARY KEY,
        nome           TEXT NOT NULL,
        pasta          TEXT NOT NULL,
        primeira       INTEGER NOT NULL,
        ultima         INTEGER NOT NULL,
        completa_antes INTEGER NOT NULL,
        atualizado     TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rodadas (
        quando       TEXT NOT NULL,
        total        INTEGER NOT NULL,
        exportadas   INTEGER NOT NULL,
        sem_novidade INTEGER NOT NULL,
        parciais     INTEGER NOT NULL,
        falhas       INTEGER NOT NULL,
        interrompida INTEGER NOT NULL
    )
    """,
)

_PROIBIDOS_NA_PASTA = set('/\\<>:"|?*')
_CAMPOS_RODADA = ("total", "exportadas", "sem_novidade", "parciais", "falhas")


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _inteiro(valor, nome: str) -> int:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        raise ValueError(f"{nome} precisa ser numero")
    return int(valor)


class Marcas:
    def __init__(self, caminho: Path) -> None:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(caminho, check_same_thread=False)
        for comando in _ESQUEMA:
            self._con.execute(comando)
        self._con.commit()
        self._trava = threading.Lock()

    def todas(self) -> dict[str, dict]:
        with self._trava:
            linhas = self._con.execute(
                "SELECT id, nome, pasta, primeira, ultima, completa_antes, atualizado FROM conversas"
            ).fetchall()
        return {
            i: {"nome": n, "pasta": p, "primeira": a, "ultima": b, "completaAntes": bool(c), "atualizado": u}
            for i, n, p, a, b, c, u in linhas
        }

    def registrar(self, marca: dict) -> dict:
        """Junta o trecho novo ao que ja estava: comeco mais antigo, fim mais novo.
        A pasta e a da primeira vez (o contato pode mudar de nome)."""
        ident = str(marca.get("id") or "").strip()
        nome = str(marca.get("nome") or "").strip()
        pasta = str(marca.get("pasta") or "").strip()
        if not ident or "@" not in ident:
            raise ValueError("id invalido")
        if (not pasta or pasta in (".", "..") or any(c in _PROIBIDOS_NA_PASTA for c in pasta)
                or any(ord(c) < 32 for c in pasta)):
            raise ValueError("pasta invalida")
        primeira = _inteiro(marca.get("primeira"), "primeira")
        ultima = _inteiro(marca.get("ultima"), "ultima")
        if primeira > ultima:
            raise ValueError("primeira depois da ultima")
        completa = bool(marca.get("completaAntes"))
        with self._trava, self._con:
            antiga = self._con.execute(
                "SELECT pasta, primeira, ultima, completa_antes FROM conversas WHERE id = ?", (ident,)
            ).fetchone()
            if antiga:
                # Trecho que nao encosta no marcado: juntar marcaria o buraco entre os dois.
                if primeira > antiga[2] + 1 or ultima < antiga[1] - 1:
                    raise ValueError("o trecho nao encosta no que ja esta marcado")
                pasta = antiga[0]
                primeira = min(primeira, antiga[1])
                ultima = max(ultima, antiga[2])
                completa = completa or bool(antiga[3])
            self._con.execute(
                "INSERT OR REPLACE INTO conversas VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ident, nome or ident, pasta, primeira, ultima, int(completa), _agora()),
            )
        return {"id": ident, "nome": nome or ident, "pasta": pasta, "primeira": primeira,
                "ultima": ultima, "completaAntes": completa}

    def trocar_pasta(self, ident: str, pasta: str) -> None:
        """Usado so pelo `wa nomes --sim`, depois de renomear a pasta no disco."""
        with self._trava, self._con:
            self._con.execute("UPDATE conversas SET pasta = ? WHERE id = ?", (pasta, ident))

    def registrar_rodada(self, rodada: dict) -> None:
        valores = [_inteiro(rodada.get(c, 0), c) for c in _CAMPOS_RODADA]
        with self._trava, self._con:
            self._con.execute(
                "INSERT INTO rodadas VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_agora(), *valores, int(bool(rodada.get("interrompida")))),
            )

    def ultima_rodada(self) -> dict | None:
        with self._trava:
            linha = self._con.execute(
                "SELECT quando, total, exportadas, sem_novidade, parciais, falhas, interrompida "
                "FROM rodadas ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        if not linha:
            return None
        quando, *valores, interrompida = linha
        return {"quando": quando, **dict(zip(_CAMPOS_RODADA, valores)), "interrompida": bool(interrompida)}

    def fechar(self) -> None:
        self._con.close()
