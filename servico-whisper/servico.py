"""Detector de fala Silero VAD na CPU, sem ASR nem carga de modelo Whisper.

Somente /fala e /status. Mantem Host de loopback e recusa qualquer Origin.
O pacote faster-whisper fornece apenas o decoder e o Silero VAD embarcado.
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

log = logging.getLogger("servico-whisper")

TAMANHO_MAXIMO = 64 * 1024 * 1024  # o mesmo limite do `wa servir`.
HOSTS_LOCAIS = {"127.0.0.1", "localhost", "[::1]"}


class AudioInvalido(ValueError):
    """O arquivo nao decodifica: tentar de novo nao adianta."""


TAXA = 16000  # a do faster-whisper.


def decodificar(dados: bytes):
    """O audio em amostras de 16 kHz. Erro aqui e do arquivo, menos falta de memoria."""
    from faster_whisper.audio import decode_audio  # import tardio, como o do modelo.

    try:
        return decode_audio(io.BytesIO(dados), sampling_rate=TAXA)
    except MemoryError:
        raise
    except Exception as erro:  # noqa: BLE001 - o av levanta varias classes.
        raise AudioInvalido(str(erro) or erro.__class__.__name__) from erro


def medir_fala(dados: bytes) -> dict:
    """Quanto do audio e fala, pelo Silero VAD com as opcoes padrao do faster-whisper."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    amostras = decodificar(dados)
    trechos = get_speech_timestamps(amostras, VadOptions())
    return {
        "duracao_s": round(len(amostras) / TAXA, 2),
        "fala_s": round(sum(t["end"] - t["start"] for t in trechos) / TAXA, 2),
        "trechos": len(trechos),
    }


class _Manipulador(BaseHTTPRequestHandler):
    server: "Servico"
    protocol_version = "HTTP/1.1"

    def log_message(self, formato, *args):
        log.debug("%s " + formato, self.address_string(), *args)

    def _responder(self, codigo: int, corpo: dict, fechar: bool = False) -> None:
        dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(dados)))
        if fechar:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(dados)

    def _falhou(self, fazendo: str, dados: bytes, erro: Exception) -> None:
        """500 se o arquivo nao decodifica; 503 se a falha e da maquina."""
        codigo = 500 if isinstance(erro, AudioInvalido) else 503
        log.error("falha %s de %d KB (%d): %s", fazendo, len(dados) // 1024, codigo, erro)
        self._responder(codigo, {"erro": str(erro) or erro.__class__.__name__})

    def _recusar(self, codigo: int, corpo: dict) -> None:
        """Responde sem usar o corpo do pedido. Fechar a conexao com o corpo ainda no
        buffer faz o Windows mandar RST, e o cliente perde a resposta (WinError 10053):
        corpo de ate 1 MB e lido e jogado fora antes. Maior que isso, fecha."""
        try:
            tamanho = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            tamanho = -1
        if self.headers.get("Transfer-Encoding"):
            self._responder(codigo, corpo, fechar=True)
        elif 0 <= tamanho <= 1024 * 1024:
            if tamanho:
                self.rfile.read(tamanho)
            self._responder(codigo, corpo)
        else:
            self._responder(codigo, corpo, fechar=True)

    def _autorizado(self) -> bool:
        host = self.headers.get("Host", "")
        nome = host.rsplit(":", 1)[0] if not host.endswith("]") else host
        if nome not in HOSTS_LOCAIS:
            self._recusar(403, {"erro": "host nao permitido"})
            return False
        if self.headers.get("Origin") is not None:
            self._recusar(403, {"erro": "origem nao permitida"})
            return False
        return True

    def do_OPTIONS(self):  # noqa: N802 - nome exigido pelo http.server.
        self._recusar(403, {"erro": "sem CORS"})

    def do_GET(self):  # noqa: N802
        if not self._autorizado():
            return
        if urlsplit(self.path).path != "/status":
            self._recusar(404, {"erro": "rota desconhecida"})
            return
        self._responder(200, {
            "ok": True, "estado": "pronto", "erro": None,
            "motor": "silero-vad", "dispositivo": "cpu", "asr": False,
        })

    def do_POST(self):  # noqa: N802
        if not self._autorizado():
            return
        partes = urlsplit(self.path)
        if partes.path != "/fala":
            self._recusar(404, {"erro": "rota desconhecida"})
            return
        if self.headers.get("Transfer-Encoding"):
            # SEG-07: sem ler o corpo chunked, ele viraria o proximo pedido da conexao.
            self._responder(411, {"erro": "mande com Content-Length"}, fechar=True)
            return

        try:
            tamanho = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            tamanho = -1
        if tamanho == 0:
            self._responder(400, {"erro": "corpo vazio"})
            return
        if tamanho < 0:
            self._responder(400, {"erro": "Content-Length invalido"}, fechar=True)
            return
        if tamanho > TAMANHO_MAXIMO:
            self._responder(413, {"erro": "arquivo grande demais"}, fechar=True)
            return
        dados = self.rfile.read(tamanho)
        if len(dados) != tamanho:
            self._responder(400, {"erro": "corpo incompleto"}, fechar=True)
            return
        inicio = time.monotonic()
        if partes.path == "/fala":
            try:
                medida = self.server.medir_fala(dados)
            except Exception as erro:  # noqa: BLE001 - vira 500 ou 503 no _falhou.
                self._falhou("medindo a fala", dados, erro)
                return
            log.info(
                "fala medida em %.1f s: %.1f de %.1f s", time.monotonic() - inicio,
                medida["fala_s"], medida["duracao_s"],
            )
            self._responder(200, medida)
            return


class Servico(ThreadingHTTPServer):
    daemon_threads = True
    _DESISTIU = (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)

    def __init__(self, endereco: tuple[str, int], medir=None):
        self.medir_fala = medir or medir_fala
        super().__init__(endereco, _Manipulador)

    @property
    def porta(self) -> int:
        return self.server_address[1]

    def handle_error(self, request, client_address) -> None:
        erro = sys.exception()
        if isinstance(erro, self._DESISTIU):
            log.debug("conexao encerrada pelo outro lado: %s", erro.__class__.__name__)
            return
        super().handle_error(request, client_address)

    def em_segundo_plano(self) -> threading.Thread:
        t = threading.Thread(target=self.serve_forever, name="servico", daemon=True)
        t.start()
        return t


def main() -> int:
    logging.basicConfig(
        level=logging.DEBUG if os.environ.get("WHISPER_VERBOSO") else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Dentro do container escuta em todas as interfaces; quem limita a 127.0.0.1
    # e a porta publicada no compose.yml.
    endereco = os.environ.get("WHISPER_ENDERECO", "0.0.0.0")
    porta = int(os.environ.get("WHISPER_PORTA", "18765"))
    servico = Servico((endereco, porta))
    log.info("detector de fala CPU em %s:%d", endereco, porta)
    try:
        servico.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servico.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
