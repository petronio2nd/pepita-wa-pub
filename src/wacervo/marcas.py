"""Marca do "Exportar todas": ate onde cada conversa ja foi exportada.

O botao exporta para C:\\WHATSAPP, que o Pepita le, e pula o que ja foi: so
as mensagens fora do trecho marcado vao para um arquivo novo. Aqui fica so o
metadado (id, nome, pasta, o trecho coberto em segundos e se o historico ja foi
lido ate o comeco), nunca o texto de conversa (usuario, 2026-10-24).

Desde a carga historica, a tabela importadas guarda IDs, caminho e hash de
mensagens vindas do backup, sem texto e sem inventar cobertura de datas.
`primeira`/`ultima` sao segundos desde 1970 (o `t` do WhatsApp). O trecho e
continuo: cada exportacao parte dele para tras (historico antigo) e para frente
(o que chegou depois), e a uniao continua sem buraco.
"""

from __future__ import annotations

import sqlite3
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

_ESQUEMA = (
    """CREATE TABLE IF NOT EXISTS arquivos_iphone (
        sha256 TEXT PRIMARY KEY, pasta TEXT NOT NULL, arquivo TEXT NOT NULL,
        aliases TEXT NOT NULL, ids TEXT NOT NULL, pronto INTEGER NOT NULL DEFAULT 0
    )""",
    """
    CREATE TABLE IF NOT EXISTS importadas (
        conversa TEXT NOT NULL,
        mensagem TEXT NOT NULL,
        pasta TEXT NOT NULL,
        arquivo TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        PRIMARY KEY (conversa, mensagem)
    )
    """,
    'CREATE INDEX IF NOT EXISTS importadas_pastas ON importadas(conversa,pasta)',
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

    def importadas(self, conversa=None, *, incluir_ids=True) -> dict[str, dict]:
        """IDs presentes em arquivos importados; nao representam cobertura temporal."""
        with self._trava:
            if conversa is not None:
                rows = self._con.execute('SELECT conversa,mensagem,pasta FROM importadas WHERE conversa=? ORDER BY mensagem',(conversa,)).fetchall()
            elif incluir_ids:
                rows = self._con.execute('SELECT conversa,mensagem,pasta FROM importadas ORDER BY conversa,mensagem').fetchall()
            else:
                rows = self._con.execute('SELECT DISTINCT conversa,NULL,pasta FROM importadas ORDER BY conversa').fetchall()
        saida = {}
        for conversa, mensagem, pasta in rows:
            item = saida.setdefault(conversa, {'pasta': pasta, 'ids': []})
            if item['pasta'] != pasta:
                raise ValueError('importacao aponta para duas pastas')
            if incluir_ids: item['ids'].append(mensagem)
            else: item.pop('ids',None)
        return saida

    def tem_importadas(self):
        with self._trava:
            return self._con.execute('SELECT 1 FROM importadas LIMIT 1').fetchone() is not None

    def registrar_importadas(self, conversas, ids, pasta, arquivo, sha256):
        """Chamar so depois de confirmar o arquivo no acervo. Nunca altera datas."""
        from .gravacao import caminho_relativo
        caminho_relativo(f'{pasta}/{arquivo}')
        if not re.fullmatch(r'[a-f0-9]{64}', sha256):
            raise ValueError('hash invalido')
        if not conversas or not ids or any(not isinstance(x, str) or not x or len(x) > 250
                                         or any(c.isspace() for c in x) for x in [*conversas, *ids]):
            raise ValueError('ids invalidos')
        with self._trava, self._con:
            for conversa in conversas:
                for mensagem in ids:
                    antiga = self._con.execute(
                        'SELECT pasta, arquivo, sha256 FROM importadas WHERE conversa=? AND mensagem=?',
                        (conversa, mensagem)).fetchone()
                    if antiga and antiga != (pasta, arquivo, sha256):
                        raise ValueError('mensagem importada em outro arquivo')
                    self._con.execute('INSERT OR IGNORE INTO importadas VALUES (?,?,?,?,?)',
                                      (conversa, mensagem, pasta, arquivo, sha256))

    def arquivos_iphone(self):
        import json
        with self._trava:
            rows=self._con.execute('SELECT sha256,pasta,arquivo,aliases,ids,pronto FROM arquivos_iphone').fetchall()
        return [dict(sha256=h,pasta=p,arquivo=a,aliases=json.loads(c),ids=json.loads(i),pronto=bool(s))
                for h,p,a,c,i,s in rows]

    def preparar_iphone(self, aliases, ids, pasta, arquivo, sha256):
        import json
        from .gravacao import caminho_relativo
        caminho_relativo(f'{pasta}/{arquivo}')
        if not aliases or not ids or any(len(a)>250 or not re.fullmatch(r'[A-Za-z0-9_.-]+@[A-Za-z0-9.]+',a) for a in aliases):
            raise ValueError('aliases invalidos')
        if any(not re.fullmatch(r'[A-Za-z0-9_.-]{1,200}',i) for i in ids) or not re.fullmatch('[a-f0-9]{64}',sha256):
            raise ValueError('ids ou hash invalidos')
        with self._trava,self._con:
            self._con.execute('INSERT OR IGNORE INTO arquivos_iphone VALUES (?,?,?,?,?,0)',
                              (sha256,pasta,arquivo,json.dumps(aliases),json.dumps(ids)))

    def concluir_iphone(self, recibo):
        self.registrar_importadas(recibo['aliases'],recibo['ids'],recibo['pasta'],recibo['arquivo'],recibo['sha256'])
        with self._trava,self._con:
            self._con.execute('UPDATE arquivos_iphone SET pronto=1 WHERE sha256=?',(recibo['sha256'],))

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
            antiga = self._con.execute('SELECT pasta FROM conversas WHERE id=?',(ident,)).fetchone()
            if antiga:
                self._con.execute('UPDATE importadas SET pasta=? WHERE pasta=?',(pasta,antiga[0]))
                self._con.execute('UPDATE arquivos_iphone SET pasta=? WHERE pasta=?',(pasta,antiga[0]))
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
