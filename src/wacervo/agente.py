"""Ponte do agente (0.19.0): o MCP (`wa mcp`) pede, a extensao executa no WhatsApp Web.

O `wa servir` so guarda a fila. Quem pede e o processo do MCP (POST /agente/pedir, sem
`Origin` e com o token de `dados/agente.token`); quem executa e a extensao, que fica
perguntando (POST /agente/proximo, long-poll de ate 20 s) e devolve o resultado
(POST /agente/resposta). Nada de conversa passa pelo disco: a fila vive na memoria.

Garantias:
- Pedido que nenhuma extensao pegou no prazo de entrega sai da fila e nunca roda.
- Pedido entregue nunca volta para a fila (um envio roda no maximo uma vez).
- Aceite em dois tempos: a extensao so executa depois que o servidor aceitou o
  `POST /agente/aceite`. Sem aceite em `ACEITE_S` (a conexao do long-poll caiu, o
  service worker foi parado), o pedido conta como NAO rodado, e o aceite tardio e
  recusado, para a extensao nao executar.
- `executar_ate` (epoch ms) vai junto: a extensao recusa o que chegou tarde nela.
- O log so leva a operacao e o tempo, nunca texto, nome ou id de conversa.

So biblioteca padrao: o `wa mcp` importa `ler_token` daqui.
"""

from __future__ import annotations

import csv
import os
import secrets
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .log import obter

log = obter(__name__)

# Operacoes que o MCP pode pedir: (prazo para a extensao pegar, prazo para responder,
# janela para a extensao comecar depois de pegar), em segundos.
OPERACOES: dict[str, tuple[float, float, float]] = {
    "listar": (10, 30, 15),
    "ler": (10, 150, 15),
    "enviar": (10, 45, 15),
    "arquivar": (10, 45, 15),  # 0.28.0
    "grupos": (10, 30, 15),  # todos os grupos, para o `wa grupos` (nao passa pelo MCP dos agentes)
}
# A extensao pergunta a cada 20 s no maximo; sem pergunta ha este tanto, esta desligada.
VISTA_VALE_S = 90
ESPERA_MAXIMA_S = 20
ACEITE_S = 8


class ErroDoAgente(Exception):
    """Pedido que nao rodou ou nao respondeu. `tipo` vai ao MCP:
    sem_extensao, nao_entregue (nao rodou), sem_resposta (estado desconhecido)."""

    def __init__(self, tipo: str, mensagem: str) -> None:
        super().__init__(mensagem)
        self.tipo = tipo


@dataclass
class _Pedido:
    id: str
    op: str
    args: dict
    criado: float
    entregue: bool = False
    aceito: bool = False
    perdido: bool = False
    resposta: dict | None = None
    pronto: threading.Event = field(default_factory=threading.Event)


class FilaDoAgente:
    def __init__(self, relogio: Callable[[], float] = time.monotonic,
                 parede: Callable[[], float] = time.time) -> None:
        self._relogio = relogio
        self._parede = parede
        self._cond = threading.Condition()
        self._fila: list[_Pedido] = []
        self._todos: dict[str, _Pedido] = {}
        self.ultima_vez: float | None = None

    def situacao(self) -> dict:
        with self._cond:
            vista = None if self.ultima_vez is None else round(self._relogio() - self.ultima_vez, 1)
        return {"extensao_vista_ha_s": vista, "conectada": vista is not None and vista <= VISTA_VALE_S}

    # ---------- lado do MCP ----------
    def pedir(self, op: str, args: dict, prazos: tuple[float, float, float] | None = None,
              aceite_s: float = ACEITE_S) -> dict:
        if op not in OPERACOES:
            raise ValueError(f"operacao desconhecida: {op}")
        entrega, resposta, janela = prazos or OPERACOES[op]
        with self._cond:
            agora = self._relogio()
            if self.ultima_vez is None or agora - self.ultima_vez > VISTA_VALE_S:
                raise ErroDoAgente(
                    "sem_extensao",
                    "a extensao pepita-wa nao esta conectada: abra o WhatsApp Web no Chrome "
                    "(com o pepita-wa ligado e o login feito) e tente de novo",
                )
            p = _Pedido(secrets.token_hex(8), op, {**args, "_janela_s": janela}, agora)
            self._fila.append(p)
            self._todos[p.id] = p
            self._cond.notify_all()
            limite = agora + entrega
            while not p.entregue and self._relogio() < limite:
                self._cond.wait(min(0.5, max(0.01, limite - self._relogio())))
            if not p.entregue:
                self._fila.remove(p)
                del self._todos[p.id]
                log.info("agente: %s nao foi pego pela extensao em %d s (nao rodou)", op, entrega)
                raise ErroDoAgente(
                    "nao_entregue",
                    "a extensao nao pegou o pedido a tempo; ele NAO rodou. Confira se a aba do "
                    "WhatsApp Web esta aberta e tente de novo",
                )
            limite = self._relogio() + aceite_s
            while not p.aceito and self._relogio() < limite:
                self._cond.wait(min(0.5, max(0.01, limite - self._relogio())))
            if not p.aceito:
                p.perdido = True
                del self._todos[p.id]
                log.info("agente: %s pego e nao aceito em %d s (nao rodou)", op, aceite_s)
                raise ErroDoAgente(
                    "nao_entregue",
                    "a extensao pegou o pedido e nao confirmou; ele NAO rodou. Tente de novo",
                )
        try:
            if not p.pronto.wait(resposta):
                log.info("agente: %s sem resposta em %d s", op, resposta)
                raise ErroDoAgente(
                    "sem_resposta",
                    f"a extensao pegou o pedido e nao respondeu em {int(resposta)} s: o resultado "
                    "e desconhecido",
                )
            log.info("agente: %s respondido em %.1f s", op, self._relogio() - p.criado)
            return p.resposta or {}
        finally:
            with self._cond:
                self._todos.pop(p.id, None)

    # ---------- lado da extensao ----------
    def proximo(self, espera_s: float = ESPERA_MAXIMA_S) -> dict | None:
        espera_s = max(0.0, min(float(espera_s), ESPERA_MAXIMA_S))
        with self._cond:
            self.ultima_vez = self._relogio()
            limite = self.ultima_vez + espera_s
            while not self._fila and self._relogio() < limite:
                self._cond.wait(min(1.0, max(0.01, limite - self._relogio())))
            self.ultima_vez = self._relogio()
            if not self._fila:
                return None
            p = self._fila.pop(0)
            p.entregue = True
            self._cond.notify_all()
            janela = p.args.pop("_janela_s")
            return {
                "id": p.id, "op": p.op, "args": p.args,
                "executar_ate": int((self._parede() + janela) * 1000),
            }

    def aceitar(self, id_: str) -> bool:
        """A extensao vai executar. False: tarde demais (ja contado como nao rodado)."""
        with self._cond:
            p = self._todos.get(id_)
            if p is None or not p.entregue or p.perdido or p.aceito:
                return False
            p.aceito = True
            self._cond.notify_all()
            return True

    def responder(self, id_: str, corpo: dict) -> bool:
        with self._cond:
            p = self._todos.get(id_)
            if p is None or not p.aceito or p.pronto.is_set():
                return False
            p.resposta = corpo
            p.pronto.set()
            return True


def _so_o_usuario(caminho: Path) -> None:
    """Deixa o arquivo legivel so pelo usuario atual (M2, auditoria MCP de 14/11/2026): a pasta do
    projeto herda "Usuarios autenticados: Modificar" da raiz do D:. Falha vira aviso no log, sem
    impedir o servidor de subir (o token continua valendo; a ACL fica para o icacls manual)."""
    if os.name != "nt":
        os.chmod(caminho, 0o600)
        return
    sem_janela = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        # O dono e o do processo (SID pelo whoami), nunca LOGNAME/USER, que o shell pode trocar.
        quem = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True,
                              timeout=30, creationflags=sem_janela)
        campos = next(csv.reader([quem.stdout.strip()]), [])
        sid = campos[-1] if quem.returncode == 0 and campos else ""
        ok = sid.startswith("S-1-") and subprocess.run(
            ["icacls", str(caminho), "/inheritance:r", "/grant:r", f"*{sid}:F"],
            capture_output=True, timeout=30, creationflags=sem_janela).returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    if not ok:
        log.warning("dados/agente.token sem ACL restrita (icacls falhou); rode o icacls do M2 a mao")


def criar_token(caminho: Path) -> str:
    """Token que o MCP manda em `X-Wacervo-Token`. Criado pelo `wa servir` se faltar, com a ACL
    restrita antes de o segredo entrar no arquivo."""
    if caminho.exists():
        token = caminho.read_text(encoding="utf-8").strip()
        if len(token) >= 32:
            return token
        caminho.unlink()
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.touch(exist_ok=False)
    _so_o_usuario(caminho)
    token = secrets.token_urlsafe(32)
    caminho.write_text(token, encoding="utf-8")
    return token


def ler_token(caminho: Path) -> str | None:
    try:
        token = caminho.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token if len(token) >= 32 else None
