"""VAD dentro do `wa servir` (vad = "local", 0.18.0), para computador sem a distro.

Sem o extra vad-local instalado (o caso do Windows do usuario), os testes com audio de
verdade sao pulados; os outros usam modulos falsos.
"""

import io
import importlib.util
from pathlib import Path

import pytest

from wacervo import cli
from wacervo.config import Config, ConfigTranscricao, ErroDeConfig, carregar
from wacervo.transcricao import ErroDeTranscricao, Indisponivel, TranscricaoGemini, Transcritor
from wacervo.vad_local import TAXA, VadLocal, medir_fala

RAIZ = Path(__file__).resolve().parent.parent
TEM_EXTRA = all(importlib.util.find_spec(m) for m in ("numpy", "onnxruntime", "av"))


def _modulos_falsos(trechos, amostras=TAXA * 3):
    class Opcoes:
        pass

    def decodificar(arquivo, sampling_rate):
        assert sampling_rate == TAXA
        assert isinstance(arquivo, io.BytesIO)
        return [0.0] * amostras

    return lambda: (decodificar, Opcoes, lambda a, o: trechos)


# --- config ----------------------------------------------------------------------


def test_padrao_e_a_distro_e_local_so_quando_pedido(tmp_path):
    assert ConfigTranscricao().vad == "distro"
    (tmp_path / "config.toml").write_text('[transcricao]\nvad = "local"\n', encoding="utf-8")
    assert carregar(raiz=tmp_path).transcricao.vad == "local"


def test_vad_desconhecida_e_recusada(tmp_path):
    (tmp_path / "config.toml").write_text('[transcricao]\nvad = "gpu"\n', encoding="utf-8")
    with pytest.raises(ErroDeConfig, match="distro, local"):
        carregar(raiz=tmp_path)


def test_cli_escolhe_o_detector_pela_config(tmp_path):
    distro = Config(raiz=tmp_path)
    local = Config(raiz=tmp_path, transcricao=ConfigTranscricao(vad="local"))
    assert isinstance(cli._detector(distro), Transcritor)
    assert isinstance(cli._detector(local), VadLocal)
    motor, detector = cli._transcricao(local)
    assert motor.distro is detector


# --- medida ------------------------------------------------------------------------


def test_medida_tem_a_forma_e_o_arredondamento_do_servico_da_distro():
    trechos = [{"start": 0, "end": 12345}, {"start": 20000, "end": 30001}]
    medida = medir_fala(b"x", _modulos_falsos(trechos))
    assert medida == {"duracao_s": 3.0, "fala_s": round(22346 / TAXA, 2), "trechos": 2}


def test_sem_o_extra_e_indisponivel_com_o_comando_para_instalar():
    def sem_extra():
        raise ImportError("No module named 'av'")

    with pytest.raises(Indisponivel, match="uv sync --extra vad-local"):
        medir_fala(b"x", sem_extra)


def test_audio_que_nao_decodifica_e_erro_do_arquivo():
    def quebra(arquivo, sampling_rate):
        raise ValueError("Invalid data found when processing input")

    with pytest.raises(ErroDeTranscricao) as erro:
        medir_fala(b"x", lambda: (quebra, object, None))
    assert not isinstance(erro.value, Indisponivel)
    assert "audio invalido" in str(erro.value)


def test_detector_que_falha_e_indisponivel_e_nao_falha_da_midia():
    def sem_onnx(amostras, opcoes):
        raise RuntimeError("Applying the VAD filter requires the onnxruntime package")

    modulos = lambda: (lambda arquivo, sampling_rate: [0.0] * TAXA, object, sem_onnx)  # noqa: E731
    with pytest.raises(Indisponivel, match="detector fora"):
        medir_fala(b"x", modulos)


def test_situacao_fora_sem_o_extra_bloqueia_o_gemini(tmp_path):
    def sem_extra():
        raise ImportError("No module named 'onnxruntime'")

    agy = tmp_path / "agy.exe"
    agy.write_bytes(b"")
    cfg = Config(raiz=tmp_path, transcricao=ConfigTranscricao(vad="local"))
    vad = VadLocal(cfg, modulos=sem_extra)
    assert vad.situacao()["estado"] == "fora"
    situacao = TranscricaoGemini(cfg, vad).situacao()
    assert "vad-local" in (situacao["bloqueio"] or "")


def test_sem_fala_nao_chama_o_gemini(tmp_path):
    cfg = Config(raiz=tmp_path, transcricao=ConfigTranscricao(vad="local"))
    vad = VadLocal(cfg, medir=lambda dados: {"duracao_s": 3.0, "fala_s": 0.0, "trechos": 0})

    def gemini(*a):
        raise AssertionError("o Gemini nao pode ser chamado sem fala")

    resultado = TranscricaoGemini(cfg, vad, gemini=gemini)(b"x")
    assert (resultado.texto, resultado.motor) == ("", "vad:sem-fala")


# --- copia do faster-whisper -------------------------------------------------------------


def test_copia_traz_o_modelo_e_a_licenca():
    pasta = RAIZ / "src" / "wacervo" / "vad"
    assert (pasta / "assets" / "silero_vad_v6.onnx").stat().st_size > 1_000_000
    assert "MIT License" in (pasta / "LICENCA-faster-whisper.txt").read_text(encoding="utf-8")


@pytest.mark.skipif(not TEM_EXTRA, reason="extra vad-local nao instalado")
def test_status_com_vad_local_nao_derruba_o_processo(tmp_path):
    """O winrt (OCR) carregado antes do onnxruntime dava access violation no import."""
    import subprocess
    import sys

    (tmp_path / "config.toml").write_text('[transcricao]\nvad = "local"\n', encoding="utf-8")
    codigo = (
        "import sys; from pathlib import Path; from wacervo import cli, config;"
        f"config.raiz_do_projeto = lambda: Path(r'{tmp_path}');"
        "sys.argv = ['wa', 'status']; sys.exit(cli.main())"
    )
    feito = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True,
                           encoding="utf-8", timeout=120)
    assert feito.returncode in (0, 1), feito.stderr[-2000:]


@pytest.mark.skipif(not TEM_EXTRA, reason="extra vad-local nao instalado")
def test_silencio_e_tom_em_opus_nao_tem_fala():
    import av
    import numpy as np

    buf = io.BytesIO()
    with av.open(buf, "w", format="ogg") as saida:
        fluxo = saida.add_stream("libopus", rate=48000)
        fluxo.layout = "mono"
        t = np.arange(48000 * 2) / 48000
        sinal = np.concatenate([np.zeros(48000 * 2), 0.3 * np.sin(2 * np.pi * 220 * t)]).astype(np.float32)
        for i in range(0, len(sinal), 960):
            quadro = av.AudioFrame.from_ndarray(sinal[i:i + 960].reshape(1, -1), format="flt", layout="mono")
            quadro.sample_rate = 48000
            for pacote in fluxo.encode(quadro):
                saida.mux(pacote)
        for pacote in fluxo.encode(None):
            saida.mux(pacote)
    medida = medir_fala(buf.getvalue())
    assert medida["duracao_s"] == pytest.approx(4.0, abs=0.1)
    assert medida["fala_s"] == 0.0
