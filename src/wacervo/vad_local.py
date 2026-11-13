"""Detector de fala (Silero VAD) dentro do `wa servir`, para computador sem a distro.

O padrao continua o servico da distro (`[transcricao] vad = "distro"`): no Windows do
usuario o detector fica no container. Em outro computador, sem WSL nem Docker,
`vad = "local"` mede a fala aqui, com o mesmo Silero e as mesmas opcoes do
`servico-whisper/servico.py`, e a mesma resposta do `/fala`. Precisa do extra
`vad-local` (`uv sync --extra vad-local`): numpy, onnxruntime e av, importados so
quando um audio chega.
"""

from __future__ import annotations

import io
import threading

from .config import Config
from .log import obter
from .transcricao import ErroDeTranscricao, Indisponivel

log = obter(__name__)

TAXA = 16000  # a do faster-whisper e do servico da distro.
SEM_EXTRA = "VAD local sem as dependencias: rode `uv sync --extra vad-local`"
_TRAVA_MODELO = threading.Lock()


def _modulos():
    """`(decode_audio, VadOptions, get_speech_timestamps)`; ImportError sem o extra."""
    from .vad.audio import decode_audio
    from .vad.vad import VadOptions, get_speech_timestamps

    return decode_audio, VadOptions, get_speech_timestamps


def precarregar() -> None:
    """Carrega o onnxruntime antes de qualquer `winrt` (OCR).

    O pacote winrt traz um `msvcp140.dll` antigo; carregado antes, o onnxruntime 1.30
    derruba o processo (access violation) no import. Na ordem inversa os dois
    funcionam (conferido em 10/11/2026). Sem o extra, nao faz nada: o /status avisa.
    """
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        pass


def medir_fala(dados: bytes, modulos=_modulos) -> dict:
    """Igual ao `medir_fala` do servico da distro: duracao, fala e trechos."""
    try:
        decode_audio, VadOptions, get_speech_timestamps = modulos()
    except ImportError as erro:
        raise Indisponivel(f"{SEM_EXTRA} ({erro})") from erro
    try:
        amostras = decode_audio(io.BytesIO(dados), sampling_rate=TAXA)
    except MemoryError as erro:
        raise Indisponivel("VAD local: falta de memoria") from erro
    except Exception as erro:  # noqa: BLE001 - o av levanta varias classes.
        raise ErroDeTranscricao(f"VAD local: audio invalido ({erro or erro.__class__.__name__})") from erro
    try:
        # A trava evita que 3 audios simultaneos carreguem o modelo 3 vezes (o
        # get_vad_model e um lru_cache sem trava); o Silero leva milissegundos.
        with _TRAVA_MODELO:
            trechos = get_speech_timestamps(amostras, VadOptions())
    except Exception as erro:  # noqa: BLE001 - onnxruntime ausente vira RuntimeError no vad.py.
        raise Indisponivel(f"VAD local: detector fora ({erro or erro.__class__.__name__})") from erro
    return {
        "duracao_s": round(len(amostras) / TAXA, 2),
        "fala_s": round(sum(t["end"] - t["start"] for t in trechos) / TAXA, 2),
        "trechos": len(trechos),
    }


class VadLocal:
    """Mesma interface do `Transcritor` (cliente da distro) no que o Gemini usa."""

    def __init__(self, cfg: Config, medir=medir_fala, modulos=_modulos) -> None:
        self.cfg = cfg
        self._medir = medir
        self._modulos = modulos
        self._trava = threading.Lock()
        self._erro_import: str | None = None
        self._conferido = False

    def __call__(self, dados: bytes) -> str:
        raise Indisponivel("ASR local removido; use o Gemini")

    def fala(self, dados: bytes) -> dict:
        """`{"duracao_s", "fala_s", "trechos"}` medidos aqui mesmo."""
        return self._medir(dados)

    def aquecer(self) -> None:
        raise Indisponivel("ASR local removido; o detector de voz nao requer aquecimento")

    def _importa(self) -> str | None:
        """None se numpy, onnxruntime e av carregam; senao, o erro (conferido uma vez)."""
        with self._trava:
            if not self._conferido:
                try:
                    self._modulos()
                    import onnxruntime  # noqa: F401 - o vad.py so o importa no 1o audio.
                except ImportError as erro:
                    self._erro_import = f"{SEM_EXTRA} ({erro})"
                    log.warning("%s", self._erro_import)
                self._conferido = True
            return self._erro_import

    def situacao(self) -> dict:
        """No formato do `Transcritor.situacao`: `fora` quando falta o extra."""
        erro = self._importa()
        if erro:
            return {"estado": "fora", "erro": erro, "carregado": False}
        return {"estado": "pronto", "erro": None, "carregado": False}
