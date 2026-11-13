import sys

from wacervo.cli import _como_encerrar


class SaidaFalsa:
    def __init__(self, terminal: bool) -> None:
        self._terminal = terminal

    def isatty(self) -> bool:
        return self._terminal


def test_no_terminal_manda_usar_ctrl_c(monkeypatch):
    monkeypatch.setattr(sys, "stderr", SaidaFalsa(True))
    assert _como_encerrar() == "Ctrl+C"


def test_escondido_manda_usar_o_parar_servidor(monkeypatch):
    # Com a saida em dados/servidor.log nao ha console: Ctrl+C nao chega nele.
    monkeypatch.setattr(sys, "stderr", SaidaFalsa(False))
    assert _como_encerrar() == "ferramentas\\parar-servidor.cmd"


def test_cli_so_constroi_gemini_e_vad(tmp_path):
    from wacervo.cli import _transcricao, _construir_parser
    from wacervo.config import Config, ConfigTranscricao
    from wacervo.transcricao import TranscricaoGemini

    cfg = Config(raiz=tmp_path, transcricao=ConfigTranscricao(reserva=True))
    motor, vad = _transcricao(cfg)
    assert isinstance(motor, TranscricaoGemini) and motor.distro is vad
    assert cfg.transcricao.reserva is False
    assert _construir_parser().parse_args(["servir", "--sem-aquecer"]).sem_aquecer
