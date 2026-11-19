"""O --porta do pacote completo: cada instalacao numa porta, sem ajuste manual.

Caso: duas contas de WhatsApp no mesmo computador, uma instalacao por conta. A
extensao e o servidor da segunda instalacao tem que nascer na mesma porta, ou ela
fala com o servidor da primeira e as contas se misturam.
"""

import importlib.util
import zipfile
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("pacote_completo", RAIZ / "ferramentas" / "pacote_completo.py")
pacote = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pacote)


def _ler(zip_path: Path, nome: str, arquivo: str) -> str:
    with zipfile.ZipFile(zip_path) as z:
        return z.read(f"{nome}/{arquivo}").decode("utf-8")


def test_porta_vai_ao_servidor_a_extensao_e_ao_painel(tmp_path):
    saida = pacote.gerar(tmp_path, "teste", porta=8766)
    assert "porta = 8766" in _ler(saida, "teste", "config.exemplo.toml")
    fundo = _ler(saida, "teste", "extensao/fundo.js")
    assert "const PORTA_PADRAO = 8766;" in fundo
    ponte = _ler(saida, "teste", "extensao/ponte.js")
    assert "porta: 8766" in ponte and "|| 8766" in ponte
    for arquivo in ("config.exemplo.toml", "extensao/fundo.js", "extensao/ponte.js"):
        assert "8765" not in _ler(saida, "teste", arquivo).replace("18765", "")
    assert "8766" in _ler(saida, "teste", "CLAUDE.md")


def test_sem_porta_o_zip_fica_como_antes(tmp_path):
    saida = pacote.gerar(tmp_path, "teste")
    assert "const PORTA_PADRAO = 8765;" in _ler(saida, "teste", "extensao/fundo.js")
    assert "porta = 8765" in _ler(saida, "teste", "config.exemplo.toml")


def test_portas_diferentes_nao_se_sobrescrevem(tmp_path):
    a = pacote.gerar(tmp_path, "teste", porta=8765)
    b = pacote.gerar(tmp_path, "teste", porta=8766)
    assert a != b and a.is_file() and b.is_file()
    assert "porta8766" in b.name


def test_trecho_que_sumiu_para_sem_deixar_zip(tmp_path, monkeypatch):
    monkeypatch.setitem(pacote.TRECHOS_PORTA, "extensao/fundo.js", (("trecho que nao existe", 1),))
    with pytest.raises(RuntimeError):
        pacote.gerar(tmp_path, "teste", porta=8766)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("porta", [0, 80, 18765, 70000])
def test_porta_invalida_ou_do_servico_da_distro_e_recusada(tmp_path, porta):
    with pytest.raises(ValueError):
        pacote.gerar(tmp_path, "teste", porta=porta)
