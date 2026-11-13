"""Cache dos resultados por sha256 do arquivo e assinatura da configuracao.

Exportar o mesmo chat de novo, ou um audio encaminhado para dois chats, nao
paga a GPU outra vez. A chave inclui o tipo (`audio`/`imagem`) e a assinatura
do processamento (modelo, idioma, limiar do OCR...): mudar a configuracao faz o
arquivo ser processado de novo, em vez de devolver em silencio o texto antigo.

Linhas gravadas antes da assinatura existir (esquema 1) ficam com a assinatura
`legado`: continuam na base, mas nenhuma configuracao as reutiliza.
`wa limpar-cache --so-legado` apaga so elas.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Config

# Suba quando mudar o jeito de processar (parametros do whisper, pos-processamento
# do OCR...) de forma que o texto antigo nao valha mais. A v2 saiu com o
# `condition_on_previous_text=False` na transcricao (piloto de 2026-09-20).
VERSAO_PROCESSAMENTO = 2
# A do Gemini anda sozinha: subir a dele nao invalida o whisper da reserva.
VERSAO_GEMINI = 1
LEGADO = "legado"

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS resultados (
    sha256     TEXT NOT NULL,
    tipo       TEXT NOT NULL,
    assinatura TEXT NOT NULL,
    texto      TEXT NOT NULL,
    motor      TEXT NOT NULL,
    criado     TEXT NOT NULL,
    PRIMARY KEY (sha256, tipo, assinatura)
)
"""


def sha256(dados: bytes) -> str:
    return hashlib.sha256(dados).hexdigest()


def assinatura(cfg: Config, tipo: str) -> str:
    """O que, na configuracao, muda o texto que sai do motor. O limiar do OCR
    nao entra: o cache guarda a leitura inteira e o limiar vale na resposta.

    Audio: a do motor principal. O que a reserva (whisper) transcreve fica com a
    assinatura dele (`assinatura_whisper`), e a do Gemini continua vazia: quando
    o Gemini voltar, o audio e transcrito por ele."""
    if tipo == "audio":
        if cfg.transcricao.motor == "gemini":
            return assinatura_gemini(cfg)
        return assinatura_whisper(cfg)
    return f"v{VERSAO_PROCESSAMENTO}|windows-ocr|{cfg.ocr.idioma}"


def assinatura_whisper(cfg: Config) -> str:
    t = cfg.transcricao
    return (
        f"v{VERSAO_PROCESSAMENTO}|whisper|{t.modelo}|{t.tipo_computacao}"
        f"|{t.idioma or 'auto'}|vad|sem-contexto"
    )


class Prefixo(str):
    """Assinatura que casa com qualquer outra que comece assim; vale a mais recente."""


def prefixo_gemini(cfg: Config) -> Prefixo:
    """A do Gemini com qualquer prompt. Mudar os termos ou o contexto vale para os
    audios novos e nao refaz os que ja foram transcritos (usuario, 2026-10-21); mudar o
    modelo ou o VERSAO_GEMINI refaz."""
    return Prefixo(f"g{VERSAO_GEMINI}|gemini|{cfg.transcricao.gemini.modelo}|")


def assinatura_gemini(cfg: Config) -> str:
    """Modelo e um resumo do prompt."""
    from .transcricao import prompt_gemini  # transcricao importa este modulo.

    resumo = hashlib.sha256(prompt_gemini(cfg).encode("utf-8")).hexdigest()[:12]
    return f"{prefixo_gemini(cfg)}{resumo}|vad"


class Cache:
    def __init__(self, caminho: Path) -> None:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(caminho, check_same_thread=False)
        self._migrar()
        self._trava = threading.Lock()

    def _migrar(self) -> None:
        colunas = [c[1] for c in self._con.execute("PRAGMA table_info(resultados)")]
        if colunas and "assinatura" not in colunas:
            # Esquema 1: chave (sha256, tipo). O SQLite nao troca chave primaria
            # no lugar; recria a tabela e marca as linhas antigas como legado.
            with self._con:
                self._con.execute("ALTER TABLE resultados RENAME TO resultados_v1")
                self._con.execute(_ESQUEMA)
                self._con.execute(
                    "INSERT INTO resultados (sha256, tipo, assinatura, texto, motor, criado) "
                    "SELECT sha256, tipo, ?, texto, motor, criado FROM resultados_v1",
                    (LEGADO,),
                )
                self._con.execute("DROP TABLE resultados_v1")
        self._con.execute(_ESQUEMA)
        self._con.execute("PRAGMA user_version = 2")
        self._con.commit()

    def _linha(self, sha: str, tipo: str, assinatura: str) -> tuple[str, str] | None:
        if isinstance(assinatura, Prefixo):
            filtro = "substr(assinatura, 1, ?) = ? ORDER BY criado DESC"
            valores = (len(assinatura), str(assinatura))
        else:
            filtro, valores = "assinatura = ?", (assinatura,)
        with self._trava:
            return self._con.execute(
                f"SELECT texto, motor FROM resultados WHERE sha256 = ? AND tipo = ? AND {filtro}",
                (sha, tipo, *valores),
            ).fetchone()

    def obter(self, sha: str, tipo: str, assinatura: str) -> str | None:
        linha = self._linha(sha, tipo, assinatura)
        return None if linha is None else linha[0]

    def obter_com_motor(self, sha: str, tipo: str, assinatura: str) -> tuple[str, str] | None:
        """(texto, motor) ou None. `assinatura` pode ser um Prefixo."""
        linha = self._linha(sha, tipo, assinatura)
        return None if linha is None else (linha[0], linha[1])

    def guardar(self, sha: str, tipo: str, assinatura: str, texto: str, motor: str) -> None:
        criado = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._trava:
            self._con.execute(
                "INSERT OR REPLACE INTO resultados VALUES (?, ?, ?, ?, ?, ?)",
                (sha, tipo, assinatura, texto, motor, criado),
            )
            self._con.commit()

    def contagem(self) -> dict[str, int]:
        """Resultados reutilizaveis por tipo (sem contar o legado)."""
        with self._trava:
            linhas = self._con.execute(
                "SELECT tipo, COUNT(*) FROM resultados WHERE assinatura != ? GROUP BY tipo",
                (LEGADO,),
            ).fetchall()
        return dict(linhas)

    def contagem_legado(self) -> int:
        with self._trava:
            return self._con.execute(
                "SELECT COUNT(*) FROM resultados WHERE assinatura = ?", (LEGADO,)
            ).fetchone()[0]

    def _filtro(self, so_legado: bool, dias: int | None) -> tuple[str, list]:
        condicoes, valores = [], []
        if so_legado:
            condicoes.append("assinatura = ?")
            valores.append(LEGADO)
        if dias is not None:
            limite = datetime.now(timezone.utc) - timedelta(days=dias)
            condicoes.append("criado < ?")
            valores.append(limite.isoformat(timespec="seconds"))
        return (" WHERE " + " AND ".join(condicoes)) if condicoes else "", valores

    def contar(self, so_legado: bool = False, dias: int | None = None) -> int:
        onde, valores = self._filtro(so_legado, dias)
        with self._trava:
            return self._con.execute(f"SELECT COUNT(*) FROM resultados{onde}", valores).fetchone()[0]

    def limpar(self, so_legado: bool = False, dias: int | None = None) -> int:
        """Apaga resultados. `dias`: so os gravados ha mais que isso. Devolve quantos."""
        onde, valores = self._filtro(so_legado, dias)
        with self._trava:
            apagados = self._con.execute(f"DELETE FROM resultados{onde}", valores).rowcount
            self._con.commit()
            self._con.execute("VACUUM")
        return apagados

    def fechar(self) -> None:
        self._con.close()
