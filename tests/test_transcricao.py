"""VAD da distro com detector falso e HTTP local em porta efemera, sem modelo ASR."""

import http.client
import importlib.util
import json
import socket
import sys
from pathlib import Path

import pytest

from wacervo.config import Config, ConfigTranscricao
from wacervo.transcricao import ErroDeTranscricao, Indisponivel, Transcritor as Cliente

_CAMINHO = Path(__file__).resolve().parents[1] / "servico-whisper" / "servico.py"
_spec = importlib.util.spec_from_file_location("servico_whisper", _CAMINHO)
servico = importlib.util.module_from_spec(_spec)
sys.modules["servico_whisper"] = servico
_spec.loader.exec_module(servico)


@pytest.fixture
def no_ar():
    criados = []

    def criar(medir=None):
        s = servico.Servico(("127.0.0.1", 0), medir=medir or (lambda dados: {
            "duracao_s": 9.0, "fala_s": 7.5, "trechos": 2}))
        s.em_segundo_plano()
        criados.append(s)
        return s

    yield criar
    for s in criados:
        s.shutdown()
        s.server_close()


def pedir(s, metodo, rota, corpo=None, cabecalhos=None):
    con = http.client.HTTPConnection("127.0.0.1", s.porta, timeout=5)
    con.request(metodo, rota, body=corpo, headers=cabecalhos or {})
    resp = con.getresponse()
    dados = json.loads(resp.read() or b"{}")
    con.close()
    return resp.status, dados


def test_servico_recusa_host_de_fora_e_qualquer_origin(no_ar):
    s = no_ar()
    assert pedir(s, "GET", "/status", cabecalhos={"Host": "atacante.com"})[0] == 403
    # Navegador sempre manda Origin num POST de outra origem; o wa servir nunca manda.
    for origem in ("https://atacante.com", "chrome-extension://abc", "null"):
        codigo, _ = pedir(s, "POST", "/fala", b"ogg", {"Origin": origem})
        assert codigo == 403
    assert pedir(s, "OPTIONS", "/transcrever")[0] == 403
    assert pedir(s, "GET", "/status", cabecalhos={"Host": f"localhost:{s.porta}"})[0] == 200


def test_servico_so_oferece_vad_cpu(no_ar):
    s = no_ar()
    codigo, status = pedir(s, "GET", "/status")
    assert codigo == 200 and status["motor"] == "silero-vad"
    assert status["dispositivo"] == "cpu" and status["asr"] is False
    for rota in ("/transcrever", "/aquecer", "/transcrever?modelo=medium", "/outra"):
        assert pedir(s, "POST", rota, b"ogg")[0] == 404
    assert pedir(s, "POST", "/fala")[0] == 400
    assert not hasattr(servico, "Transcritor")


def _cfg(tmp_path, url, **t):
    return Config(raiz=tmp_path, transcricao=ConfigTranscricao(servico=url, **t))


def test_fala_mede_sem_o_modelo_e_falha_do_audio_vira_500(tmp_path, monkeypatch):
    def medir(dados):
        if dados == b"quebrado":
            raise servico.AudioInvalido("Invalid data found when processing input")
        if dados == b"onnx":
            raise RuntimeError("ONNXRuntimeError: Failed to allocate memory")
        return {"duracao_s": 9.0, "fala_s": 7.5, "trechos": 2}

    s = servico.Servico(("127.0.0.1", 0), medir=medir)
    s.em_segundo_plano()
    try:
        assert pedir(s, "POST", "/fala", b"ogg") == (200, {"duracao_s": 9.0, "fala_s": 7.5, "trechos": 2})
        assert pedir(s, "POST", "/fala")[0] == 400
        codigo, dados = pedir(s, "POST", "/fala", b"quebrado")
        assert codigo == 500 and "Invalid data" in dados["erro"]
        cliente = Cliente(_cfg(tmp_path, f"http://127.0.0.1:{s.porta}"))
        assert cliente.fala(b"ogg")["fala_s"] == 7.5
        with pytest.raises(ErroDeTranscricao, match="Invalid data") as erro:
            cliente.fala(b"quebrado")
        assert not isinstance(erro.value, Indisponivel)
        assert pedir(s, "POST", "/fala", b"onnx")[0] == 503  # a maquina, nao o arquivo
        with pytest.raises(Indisponivel, match="ONNX"):
            cliente.fala(b"onnx")
    finally:
        s.shutdown()
        s.server_close()


def test_cliente_com_servico_fora_do_ar_e_indisponivel(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        porta = sock.getsockname()[1]
    with pytest.raises(Indisponivel):
        Cliente(_cfg(tmp_path, f"http://127.0.0.1:{porta}")).fala(b"ogg")


def test_cliente_asr_e_aquecimento_desativados_sem_http(tmp_path, monkeypatch):
    cliente = Cliente(_cfg(tmp_path, "http://127.0.0.1:1"))
    monkeypatch.setattr(cliente, "_pedir", lambda *a: pytest.fail("nao deve enviar ASR"))
    with pytest.raises(Indisponivel, match="ASR local removido"):
        cliente(b"ogg")
    with pytest.raises(Indisponivel, match="ASR local removido"):
        cliente.aquecer()


def test_medir_fala_usa_decoder_e_silero_sem_asr(monkeypatch):
    import types
    amostras = [0.0] * 32000
    monkeypatch.setattr(servico, "decodificar", lambda dados: amostras)
    chamadas = []

    def trechos(audio, opcoes):
        assert audio is amostras
        chamadas.append(opcoes)
        return [{"start": 8000, "end": 24000}]

    pacote = types.ModuleType("faster_whisper")
    pacote.__path__ = []
    vad = types.ModuleType("faster_whisper.vad")
    vad.VadOptions = object
    vad.get_speech_timestamps = trechos
    monkeypatch.setitem(sys.modules, "faster_whisper", pacote)
    monkeypatch.setitem(sys.modules, "faster_whisper.vad", vad)
    assert servico.medir_fala(b"ogg") == {"duracao_s": 2.0, "fala_s": 1.0, "trechos": 1}
    assert len(chamadas) == 1
