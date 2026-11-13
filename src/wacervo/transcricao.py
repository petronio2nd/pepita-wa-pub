"""Transcricao dos audios: Gemini pelo `agy`, com o servico da distro WSL2 de apoio.

Ate a 0.8.0 quem transcrevia era o faster-whisper no container da distro
(`servico-whisper/`). Em 2026-10-20 o usuario trocou pelo Gemini 3.8 Flash Low,
chamado pelo CLI `agy` (Google Antigravity) na assinatura dele: num teste com 18
audios reais de WhatsApp, o whisper errou 21 nomes e termos juridicos e o Gemini
com a lista de termos acertou todos. O audio sai da maquina: decisao do usuario.

O caminho de cada audio (`TranscricaoGemini`):

1. O servico da distro mede a fala (`/fala`, Silero VAD, na CPU). Sem fala, o
   texto e vazio e o Gemini nem e chamado: em silencio e ruido ele inventa uma
   frase inteira e diz que ouviu (5 de 5 no teste de 2026-10-20).
2. O `agy` transcreve, com saida em JSON pelo esquema `ESQUEMA`.
3. Texto com muito mais palavras do que cabe na fala medida e suspeito.
4. Cota, login, falha do agy ou texto suspeito devolvem falha, sem reserva.
   Cota e login pausam o Gemini por `pausa_min`.

`ErroDeTranscricao` vira HTTP 500; `Indisponivel`, 503 (tentativa posterior).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .cache import assinatura_gemini, prefixo_gemini
from .config import Config
from .log import obter

log = obter(__name__)

PRAZO_FALA_S = 120.0
PRAZO_STATUS_S = 2.0

# Abaixo disto (segundos de fala pelo VAD) o audio e tratado como sem fala.
FALA_MINIMA_S = 0.3
# Texto suspeito: mais palavras do que alguem fala no tempo medido. Portugues
# corrido fica em 2 a 3,5 palavras por segundo; o VAD ainda soma uma folga em
# volta de cada trecho.
PALAVRAS_POR_SEGUNDO_MAX = 5.0
PALAVRAS_FOLGA = 10

# Sem proxy: o urllib pegaria o proxy do sistema, e o audio tem de ir direto
# para o 127.0.0.1.
_ABRIR = urllib.request.build_opener(urllib.request.ProxyHandler({})).open

SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ErroDeTranscricao(RuntimeError):
    """Falha deste audio, ou falha sem causa conhecida. A mensagem vai para a extensao."""


class Indisponivel(ErroDeTranscricao):
    """O motor esta fora agora (cota, agy, distro): o mesmo audio pode dar certo depois."""


class FalhaDoAgy(ErroDeTranscricao):
    """O agy rodou e nao trouxe a transcricao."""


class CotaEsgotada(Indisponivel):
    """A assinatura do usuario bateu no limite."""


class SemLogin(Indisponivel):
    """O agy nao esta logado: o usuario precisa entrar na conta."""


class SemAgy(Indisponivel):
    """O agy.exe nao foi achado ou nao abriu."""


class EmPausa(Indisponivel):
    """A pausa comecou enquanto este audio esperava a vaga: nao chama o agy."""


@dataclass(frozen=True)
class Resultado:
    """O texto e quem o produziu. `assinatura` None = a do motor principal no cache."""

    texto: str
    motor: str
    assinatura: str | None = None


# --- servico da distro: detector de voz ---------------------------------


class Transcritor:
    """Cliente do detector de voz na CPU da distro."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.url = cfg.transcricao.servico.rstrip("/")

    def _pedir(self, metodo: str, rota: str, corpo: bytes | None, prazo: float) -> dict:
        pedido = urllib.request.Request(f"{self.url}{rota}", data=corpo, method=metodo)
        if corpo is not None:
            pedido.add_header("Content-Type", "application/octet-stream")
        try:
            with _ABRIR(pedido, timeout=prazo) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as erro:
            try:
                motivo = json.load(erro).get("erro") or erro.reason
            except (ValueError, AttributeError):
                motivo = erro.reason
            # 503: falhou a maquina (memoria, ONNX), nao o arquivo.
            classe = Indisponivel if erro.code == 503 else ErroDeTranscricao
            raise classe(f"servico de transcricao: {motivo}") from erro
        except (urllib.error.URLError, OSError) as erro:
            causa = getattr(erro, "reason", erro)
            raise Indisponivel(
                f"servico de transcricao fora do ar em {self.url} ({causa}); "
                "confira a distro ubuntu-dev e o container pepita_wa_whisper"
            ) from erro

    def __call__(self, dados: bytes) -> str:
        raise Indisponivel("ASR local removido; use o Gemini")

    def fala(self, dados: bytes) -> dict:
        """`{"duracao_s", "fala_s", "trechos"}` pelo detector de voz da distro."""
        resposta = self._pedir("POST", "/fala", dados, PRAZO_FALA_S)
        if not isinstance(resposta.get("fala_s"), (int, float)):
            raise ErroDeTranscricao("servico de transcricao: /fala sem fala_s")
        return resposta

    def aquecer(self) -> None:
        raise Indisponivel("ASR local removido; o detector de voz nao requer aquecimento")

    def situacao(self) -> dict:
        """`{"estado", "erro", "carregado"}` para o /status do `wa servir`.

        `estado` e o do servico (`em_espera`, `carregando`, `pronto`,
        `processando`, `erro`) ou `fora`, quando ele nao responde.
        """
        try:
            dados = self._pedir("GET", "/status", None, PRAZO_STATUS_S)
        except ErroDeTranscricao as erro:
            return {"estado": "fora", "erro": str(erro), "carregado": False}
        estado = dados.get("estado") or "em_espera"
        return {
            "estado": estado,
            "erro": dados.get("erro"),
            "carregado": False,  # campo legado: nenhum modelo ASR e carregado
        }

# --- Gemini pelo agy ---------------------------------------------------------------

CONTEUDOS = ("fala", "silencio", "ruido", "musica", "inaudivel")
ESQUEMA = {
    "type": "object",
    "properties": {
        "conteudo": {
            "type": "string", "enum": list(CONTEUDOS),
            "description": "o que de fato se ouve no arquivo",
        },
        "transcricao": {
            "type": "string",
            "description": "so a fala ouvida; vazio se conteudo nao for fala",
        },
    },
    "required": ["conteudo", "transcricao"],
}

_PROMPT = (
    "Primeiro diga o que de fato se ouve nele: fala, silêncio, ruído, música ou inaudível. "
    "Se houver fala, transcreva-a em português do Brasil, palavra por palavra, do jeito que "
    "foi falada. Devolva só a transcrição, sem comentários, sem título e sem marcar quem fala."
)
_PROMPT_FIM = (
    "Se não houver fala, a transcrição fica vazia. Nunca escreva fala que você não ouviu."
)

_RE_COTA = re.compile(
    r"quota|exhaust|rate.?limit|\b429\b|too many requests|usage limit|limit reached|"
    r"out of credits|cota",
    re.IGNORECASE,
)
_RE_LOGIN = re.compile(r"not logged in|log ?in to|sign ?in|unauthenticated|\b401\b", re.IGNORECASE)


def prompt_gemini(cfg: Config) -> str:
    """O pedido de transcricao, sem o nome do arquivo. Entra na assinatura do cache."""
    g = cfg.transcricao.gemini
    partes = [_PROMPT]
    if g.contexto.strip():
        partes.append(f"Contexto: {g.contexto.strip()}")
    if g.termos:
        partes.append(
            "Termos que podem aparecer: " + ", ".join(g.termos) + ". Use a grafia correta "
            "desses termos quando forem ditos; não acrescente nada que não foi dito."
        )
    partes.append(_PROMPT_FIM)
    return " ".join(partes)


def _extensao(dados: bytes) -> str:
    """O agy decide pelo nome como abrir o arquivo."""
    if dados[:4] == b"OggS":
        return ".opus"
    if dados[:3] == b"ID3" or dados[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return ".mp3"
    if dados[4:8] == b"ftyp":
        return ".m4a"
    if dados[:4] == b"RIFF":
        return ".wav"
    if dados[:4] == b"fLaC":
        return ".flac"
    if dados[:4] == b"\x1aE\xdf\xa3":
        return ".webm"
    return ".opus"  # WhatsApp manda ogg/opus; o resto e raro.


def caminho_agy(cfg: Config) -> str | None:
    escolhido = cfg.transcricao.gemini.agy
    if escolhido:
        return escolhido if Path(escolhido).is_file() else None
    achado = shutil.which("agy")
    if achado:
        return achado
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "agy" / "bin" / "agy.exe"
    return str(local) if local.is_file() else None


# --- o que o agy guarda de cada transcricao ----------------------------------------

PREFIXO_PASTA = "wa-audio-"  # a pasta temporaria de cada chamada (Gemini.__call__)
_ID_SEGURO = re.compile(r"[\w-]+")


def pasta_agy() -> Path:
    return Path.home() / ".gemini" / "antigravity-cli"


def _ids_do_wa(resumos: Path, temp: Path) -> list[str]:
    """Conversas cuja unica pasta de trabalho, no resumo do agy, foi uma wa-audio- do
    temp. So o metadado conta: texto no .db nao, porque uma revisao deste codigo pelo
    agy (skill dupla) cita o prefixo e o prompt."""
    if not resumos.is_file():
        return []
    padrao = f'["{temp.as_uri()}/{PREFIXO_PASTA}%"]'
    try:
        con = sqlite3.connect(f"file:{resumos.as_posix()}?mode=ro", uri=True, timeout=10)
        try:
            ids = [i for (i,) in con.execute(
                "SELECT conversation_id FROM conversation_summaries"
                " WHERE workspace_uris LIKE ? AND workspace_uris NOT LIKE '%,%'",
                (padrao,))]
        finally:
            con.close()
    except sqlite3.Error as erro:
        log.warning("nao li os resumos do agy (%s): %s", resumos, erro)
        return []
    return [i for i in ids if isinstance(i, str) and _ID_SEGURO.fullmatch(i)]


def limpar_conversas_agy(dias: int, pasta: Path | None = None, agora: float | None = None,
                         temp: Path | None = None) -> int:
    """Apaga o que o agy guardou das transcricoes do wa com mais de `dias` dias, nesta
    ordem: a pasta `brain/<id>`, o titulo (`annotations/<id>.pbtxt`), a conversa
    (`conversations/<id>.db`) e, por ultimo, a linha do resumo, que traz o comeco do
    texto. A linha so sai quando o resto saiu: enquanto ela existe, a conversa e tentada
    de novo no dia seguinte. As outras conversas do agy (revisoes da dupla, uso do usuario)
    ficam. Devolve quantas apagou."""
    pasta = pasta or pasta_agy()
    if dias <= 0:
        return 0
    limite = (time.time() if agora is None else agora) - dias * 86400
    resumos = pasta / "conversation_summaries.db"
    apagadas = []
    for cid in _ids_do_wa(resumos, temp or Path(tempfile.gettempdir())):
        db = pasta / "conversations" / f"{cid}.db"
        arquivos = [pasta / "annotations" / f"{cid}.pbtxt", db.with_name(f"{db.name}-wal"),
                    db.with_name(f"{db.name}-shm"), db]
        brain = pasta / "brain" / cid
        try:
            # A idade e a do arquivo mais novo. A data de pasta nao conta: apagar parte do
            # brain a deixa com a data de hoje. O .db sai por ultimo, entao uma limpeza que
            # falhou no meio o deixa com a data antiga. Sem arquivo nenhum, so sobrou resto.
            if any(c.stat().st_mtime >= limite for c in arquivos if c.exists()):
                continue
            if brain.exists():
                shutil.rmtree(brain)
            for arquivo in arquivos:
                arquivo.unlink(missing_ok=True)
        except OSError as erro:  # em uso ou sem permissao: o resumo fica e tenta amanha
            log.warning("nao apaguei a conversa %s do agy: %s", cid, erro)
            continue
        apagadas.append(cid)
    if apagadas:
        try:
            con = sqlite3.connect(resumos, timeout=10)
            try:
                with con:
                    con.executemany(
                        "DELETE FROM conversation_summaries WHERE conversation_id = ?",
                        [(i,) for i in apagadas])
            finally:
                con.close()
        except sqlite3.Error as erro:
            log.warning("nao tirei %d resumo(s) do agy: %s", len(apagadas), erro)
            return 0
    return len(apagadas)


def _ultimo_json(saida: str) -> dict | None:
    """O agy pode escrever linhas de andamento antes do JSON final."""
    for linha in reversed(saida.splitlines()):
        linha = linha.strip()
        if linha.startswith("{"):
            try:
                dados = json.loads(linha)
            except ValueError:
                continue
            if isinstance(dados, dict):
                return dados
    return None


class Gemini:
    """Uma chamada ao agy por audio, num diretorio temporario so com o audio."""

    def __init__(self, cfg: Config, rodar=subprocess.run) -> None:
        self.cfg = cfg
        self._rodar = rodar
        self._vagas = threading.BoundedSemaphore(cfg.transcricao.gemini.paralelo)

    def __call__(self, dados: bytes, pausado=None, pausar=None) -> tuple[str, str]:
        """(conteudo, transcricao). Levanta FalhaDoAgy, CotaEsgotada, SemLogin, SemAgy ou
        EmPausa. `pausado()`, conferido depois de pegar a vaga, devolve o motivo da pausa;
        `pausar(motivo)` e chamado na cota e no login ainda com a vaga na mao."""
        agy = caminho_agy(self.cfg)
        if agy is None:
            raise SemAgy("agy nao encontrado: instale o Antigravity CLI ou ponha o caminho em"
                         " [transcricao.gemini] agy")
        g = self.cfg.transcricao.gemini
        with self._vagas:
            motivo = pausado() if pausado else None
            if motivo:
                raise EmPausa(motivo)
            pasta = Path(tempfile.mkdtemp(prefix=PREFIXO_PASTA))
            try:
                nome = "audio" + _extensao(dados)
                (pasta / nome).write_bytes(dados)
                comando = [
                    agy, "-p",
                    f"Ouça o arquivo de áudio {nome} nesta pasta. {prompt_gemini(self.cfg)}",
                    "--model", g.modelo, "--mode", "plan", "--sandbox", "--disable-slash-commands",
                    "--print-timeout", f"{g.prazo_s}s", "--output-format", "json",
                    "--json-schema", json.dumps(ESQUEMA, ensure_ascii=False),
                ]
                try:
                    proc = self._rodar(
                        comando, cwd=pasta, capture_output=True, text=True, encoding="utf-8",
                        errors="replace", timeout=g.prazo_s + 30, stdin=subprocess.DEVNULL,
                        creationflags=SEM_JANELA,
                    )
                except subprocess.TimeoutExpired as erro:
                    raise FalhaDoAgy(f"agy nao respondeu em {g.prazo_s} s") from erro
                except OSError as erro:
                    raise SemAgy(f"agy nao abriu ({agy}): {erro}") from erro
            finally:
                shutil.rmtree(pasta, ignore_errors=True)
            try:
                return _interpretar(proc)
            except (CotaEsgotada, SemLogin) as erro:
                if pausar:  # antes de soltar a vaga: quem esperava ja ve a pausa
                    pausar(str(erro))
                raise


def _interpretar(proc) -> tuple[str, str]:
    saida, erro = proc.stdout or "", proc.stderr or ""
    dados = _ultimo_json(saida) or {}
    estruturado = dados.get("structured_output")
    if dados.get("status") == "SUCCESS" and isinstance(estruturado, dict):
        conteudo, texto = estruturado.get("conteudo"), estruturado.get("transcricao")
        if conteudo in CONTEUDOS and isinstance(texto, str):
            return conteudo, texto.strip()
        raise FalhaDoAgy("agy: resposta fora do esquema")
    tudo = f"{saida}\n{erro}"
    motivo = str(dados.get("error") or dados.get("response") or tudo.strip()[-300:])
    motivo = " ".join(motivo.split())[:300] or f"saida vazia (rc={proc.returncode})"
    if _RE_COTA.search(tudo):
        raise CotaEsgotada(f"cota do agy esgotada: {motivo}")
    if _RE_LOGIN.search(tudo):
        raise SemLogin(f"agy sem login (rode agy no terminal e entre na conta): {motivo}")
    raise FalhaDoAgy(f"agy falhou (rc={proc.returncode}): {motivo}")


def suspeito(texto: str, fala_s: float) -> bool:
    """Mais palavras do que cabem na fala medida: o modelo inventou."""
    return len(texto.split()) > PALAVRAS_POR_SEGUNDO_MAX * fala_s + PALAVRAS_FOLGA


class TranscricaoGemini:
    """VAD da distro e Gemini pelo agy, sem reserva. Devolve `Resultado`."""

    def __init__(self, cfg: Config, distro: Transcritor | None = None,
                 gemini: Gemini | None = None, relogio=time.monotonic) -> None:
        self.cfg = cfg
        self.distro = distro or Transcritor(cfg)
        self.gemini = gemini or Gemini(cfg)
        self._relogio = relogio
        self._trava = threading.Lock()
        self._pausa_ate = 0.0
        self._pausa_motivo: str | None = None
        self._ultimo_erro: str | None = None
        self.nome = f"agy:{cfg.transcricao.gemini.modelo}"

    # pausa: cota ou login ---------------------------------------------------------

    def _pausado(self) -> str | None:
        """O motivo da pausa, ou None se o Gemini esta valendo."""
        with self._trava:
            if self._pausa_motivo and self._relogio() < self._pausa_ate:
                return self._pausa_motivo
            self._pausa_motivo = None
            return None

    def _pausar(self, motivo: str) -> None:
        minutos = self.cfg.transcricao.gemini.pausa_min
        with self._trava:
            self._pausa_ate = self._relogio() + minutos * 60
            self._pausa_motivo = motivo
        log.warning("gemini em pausa por %d min: %s", minutos, motivo)

    def assinaturas(self) -> list[str]:
        """Reaproveita apenas transcricoes do Gemini, nunca o cache do Whisper."""
        return [assinatura_gemini(self.cfg), prefixo_gemini(self.cfg)]

    # transcricao ------------------------------------------------------------------

    def __call__(self, dados: bytes) -> Resultado:
        medida = self.distro.fala(dados)
        if medida["fala_s"] < FALA_MINIMA_S:
            log.info("sem fala pelo VAD (%.1f s de audio): nao vai ao Gemini",
                     medida.get("duracao_s") or 0)
            return Resultado("", "vad:sem-fala")
        motivo = self._pausado()
        if motivo:
            return self._falhar(motivo)
        try:
            conteudo, texto = self.gemini(dados, self._pausado, self._pausar)
        except EmPausa as erro:
            return self._falhar(str(erro))
        except (CotaEsgotada, SemLogin) as erro:
            if not self._pausado():  # o Gemini de verdade ja pausou com a vaga na mao
                self._pausar(str(erro))
            return self._falhar(str(erro))
        except ErroDeTranscricao as erro:  # FalhaDoAgy, SemAgy
            self._ultimo_erro = str(erro)
            log.warning("gemini falhou: %s", erro)
            return self._falhar(str(erro))
        self._ultimo_erro = None
        if conteudo != "fala":
            log.info("gemini ouviu %s em %.1f s de fala pelo VAD: texto vazio",
                     conteudo, medida["fala_s"])
            return Resultado("", self.nome)
        if suspeito(texto, medida["fala_s"]):
            motivo = (f"texto suspeito: {len(texto.split())} palavras em "
                      f"{medida['fala_s']:.1f} s de fala")
            log.warning("gemini: %s", motivo)
            return self._falhar(motivo, transitorio=False)
        return Resultado(texto, self.nome)

    def _falhar(self, motivo: str, transitorio: bool = True) -> Resultado:
        erro = Indisponivel if transitorio else ErroDeTranscricao
        raise erro(f"gemini: {motivo}")

    # estado -----------------------------------------------------------------------

    def aquecer(self) -> None:
        """O Gemini nao carrega nada; so avisa se o agy nao esta la."""
        if caminho_agy(self.cfg) is None:
            raise SemAgy("agy nao encontrado")

    def situacao(self) -> dict:
        """Para o /status. `bloqueio`: por que nenhum audio pode ser transcrito agora
        (None = pode). O Pepita le esse campo antes de mandar audio."""
        motivo = self._pausado()
        distro = self.distro.situacao()
        if caminho_agy(self.cfg) is None:
            estado, erro = "sem_agy", "agy nao encontrado"
        elif motivo:
            estado, erro = "pausado", motivo
        else:
            estado, erro = "pronto", self._ultimo_erro
        bloqueio = None
        if distro["estado"] == "fora":
            bloqueio = distro["erro"]  # sem o detector de voz nada vai ao Gemini
        elif estado != "pronto":
            bloqueio = erro
        volta_em = None
        if motivo:
            restante = max(0.0, self._pausa_ate - self._relogio())
            volta_em = (datetime.now() + timedelta(seconds=restante)).strftime("%H:%M")
        return {
            "motor": "gemini", "modelo": self.cfg.transcricao.gemini.modelo, "estado": estado,
            "erro": erro, "bloqueio": bloqueio, "volta_em": volta_em, "reserva": False,
        }
