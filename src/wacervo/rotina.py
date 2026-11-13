"""Rodada automatica do "Exportar todas" (usuario, 2026-10-31).

O `wa servir` ja fica no ar desde o logon, entao o relogio fica aqui: a cada
`[rotina] intervalo_min` (30) ele deixa uma rodada pendente. A extensao pergunta
a cada minuto (POST /rotina/vez, que tambem conta como sinal de vida dela) e,
quando recebe a rodada, faz o mesmo que o clique no *Exportar todas*: acha ou
abre a aba do WhatsApp Web, espera o wa-js e exporta o que falta.

Se a extensao nao pergunta em `ESPERA_EXTENSAO_S` depois de a rodada ficar
pendente, o Chrome esta fechado (ou o computador acabou de acordar e o Chrome
ainda nao voltou): o servidor abre o chrome.exe no WhatsApp Web e avisa a
extensao (`abriuChrome`) para ela fechar no fim o que a rotina abriu.

Nada de texto de conversa aqui: o estado (`dados/rotina.json`) tem o
liga/desliga e o resultado da ultima rodada em numeros.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .config import ConfigRotina
from .log import obter

log = obter(__name__)

URL_WHATSAPP = "https://web.whatsapp.com/"
# A extensao pergunta a cada minuto; 2,5 min sem pergunta e Chrome fechado.
ESPERA_EXTENSAO_S = 150
# Rodada pendente que ninguem pegou (o Chrome nao abriu, a extensao nao respondeu).
ESPERA_MAXIMA_S = 15 * 60
RESULTADOS = ("ok", "pulada", "falha", "interrompida")


def _iso(segundos: float | None) -> str | None:
    if segundos is None:
        return None
    return datetime.fromtimestamp(segundos, timezone.utc).isoformat(timespec="seconds")


def achar_chrome(configurado: str = "") -> str | None:
    """O chrome.exe: o do config.toml, ou os lugares de sempre do Windows."""
    if configurado:
        return configurado if Path(configurado).is_file() else None
    candidatos = []
    for variavel in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        pasta = os.environ.get(variavel)
        if pasta:
            candidatos.append(Path(pasta) / "Google" / "Chrome" / "Application" / "chrome.exe")
    for c in candidatos:
        if c.is_file():
            return str(c)
    return shutil.which("chrome") or shutil.which("google-chrome")


def abrir_chrome(cfg: ConfigRotina) -> str | None:
    """Abre o Chrome no WhatsApp Web, sem esperar por ele. Devolve o erro ou None."""
    exe = achar_chrome(cfg.chrome)
    if not exe:
        return "chrome.exe nao encontrado; ponha o caminho em [rotina] chrome"
    args = [exe]
    if cfg.perfil:
        args.append(f"--profile-directory={cfg.perfil}")
    args.append(URL_WHATSAPP)
    opcoes: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        opcoes["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(args, **opcoes)  # noqa: S603 - caminho e perfil vem do config do usuario
    except OSError as erro:
        return f"nao consegui abrir o Chrome ({erro})"
    return None


class Rotina:
    def __init__(self, cfg: ConfigRotina, caminho: Path, *,
                 lancar: Callable[[ConfigRotina], str | None] = abrir_chrome,
                 relogio: Callable[[], float] = time.time) -> None:
        self.cfg = cfg
        self.caminho = caminho
        self._lancar = lancar
        self._relogio = relogio
        self._trava = threading.Lock()
        self._parar = threading.Event()
        estado = self._ler()
        self.ligada: bool = bool(estado.get("ligada", cfg.ligada))
        self.ultima: dict | None = estado.get("ultima") if isinstance(estado.get("ultima"), dict) else None
        self.proxima: float | None = self._relogio() + cfg.primeira_apos_min * 60
        # {"id", "desde", "abriuChrome", "chromeEm"}: a rodada que a extensao ainda nao pegou.
        self.pendente: dict | None = None
        self.vista: float | None = None
        # Id da ultima rodada entregue: so ela registra o fim.
        self.entregue: str | None = None
        self._contador = 0

    # ---------- estado em disco ----------
    def _ler(self) -> dict:
        try:
            dados = json.loads(self.caminho.read_text(encoding="utf-8"))
            return dados if isinstance(dados, dict) else {}
        except (OSError, ValueError):
            return {}

    def _gravar(self) -> None:
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        temporario = self.caminho.with_suffix(".tmp")
        temporario.write_text(json.dumps({"ligada": self.ligada, "ultima": self.ultima}, ensure_ascii=False),
                              encoding="utf-8")
        os.replace(temporario, self.caminho)

    # ---------- o que a extensao chama ----------
    def situacao(self) -> dict:
        with self._trava:
            return {
                "ligada": self.ligada,
                "intervalo_min": self.cfg.intervalo_min,
                "proxima": _iso(self.proxima) if self.ligada else None,
                "pendente": self.pendente is not None,
                "ultima": self.ultima,
                "extensao_vista": _iso(self.vista),
            }

    def ligar(self, ligada: bool) -> dict:
        with self._trava:
            self.ligada = bool(ligada)
            if self.ligada:
                self.proxima = self._relogio() + 60  # ligou agora: a primeira em 1 min
            else:
                self.pendente = None
            self._gravar()
        log.info("rodada automatica %s", "ligada" if ligada else "desligada")
        return self.situacao()

    def vez(self) -> dict:
        """A extensao pergunta se e hora. Pega a rodada pendente, se houver."""
        with self._trava:
            self.vista = self._relogio()
            if not self.ligada or self.pendente is None:
                return {"rodar": False}
            p, self.pendente = self.pendente, None
            self.entregue = p["id"]
        log.info("rodada automatica %s entregue a extensao%s", p["id"],
                 " (o Chrome foi aberto pela rotina)" if p["abriuChrome"] else "")
        return {"rodar": True, "id": p["id"], "abriuChrome": p["abriuChrome"]}

    def fim(self, corpo: dict) -> dict:
        resultado = corpo.get("resultado")
        if resultado not in RESULTADOS:
            raise ValueError(f"resultado precisa ser um de: {', '.join(RESULTADOS)}")
        detalhe = str(corpo.get("detalhe") or "")[:300]
        with self._trava:
            if corpo.get("id") != self.entregue:
                # Fim atrasado de uma rodada que ja foi substituida: nao apaga o resultado novo.
                log.info("rodada automatica %s: fim fora de hora ignorado", corpo.get("id"))
                return {"ok": True, "ignorado": True}
            self.entregue = None
            self.ultima = {"quando": _iso(self._relogio()), "resultado": resultado, "detalhe": detalhe,
                           "automatica": bool(corpo.get("automatica", True))}
            self._gravar()
        log.info("rodada automatica: %s%s", resultado, f" ({detalhe})" if detalhe else "")
        return {"ok": True}

    # ---------- relogio ----------
    def passo(self) -> None:
        """Chamado a cada ~20 s pela thread (e pelos testes, com relogio falso)."""
        lancar = False
        with self._trava:
            agora = self._relogio()
            if not self.ligada:
                return
            if self.pendente is None and self.proxima is not None and agora >= self.proxima:
                self._contador += 1
                self.pendente = {"id": f"r{int(agora)}-{self._contador}", "desde": agora,
                                 "abriuChrome": False, "chromeEm": None}
                self.proxima = agora + self.cfg.intervalo_min * 60
            p = self.pendente
            if p is None:
                return
            sem_extensao = self.vista is None or agora - self.vista > ESPERA_EXTENSAO_S
            if (not p["abriuChrome"] and self.cfg.abrir_chrome and sem_extensao
                    and agora - p["desde"] >= ESPERA_EXTENSAO_S):
                p["abriuChrome"] = True
                p["chromeEm"] = agora
                lancar = True
            elif agora - p["desde"] > ESPERA_MAXIMA_S:
                self.pendente = None
                motivo = ("o Chrome foi aberto, mas a extensao nao pediu a rodada" if p["abriuChrome"]
                          else "a extensao nao pediu a rodada (o Chrome esta aberto com o pepita-wa?)")
                self.ultima = {"quando": _iso(agora), "resultado": "pulada", "detalhe": motivo, "automatica": True}
                self._gravar()
                log.warning("rodada automatica %s pulada: %s", p["id"], motivo)
        if lancar:
            log.info("rodada automatica %s: sem sinal da extensao; abrindo o Chrome no WhatsApp Web", p["id"])
            erro = self._lancar(self.cfg)
            if erro:
                with self._trava:
                    if self.pendente is p:
                        self.pendente = None
                    self.ultima = {"quando": _iso(self._relogio()), "resultado": "falha", "detalhe": erro,
                                   "automatica": True}
                    self._gravar()
                log.error("rodada automatica %s: %s", p["id"], erro)

    def em_segundo_plano(self, intervalo_s: float = 20) -> threading.Thread:
        def laco():
            while not self._parar.wait(intervalo_s):
                try:
                    self.passo()
                except Exception as erro:  # noqa: BLE001 - o relogio nao pode morrer
                    log.error("rotina: %s", erro)

        t = threading.Thread(target=laco, name="rotina", daemon=True)
        t.start()
        return t

    def parar(self) -> None:
        self._parar.set()
