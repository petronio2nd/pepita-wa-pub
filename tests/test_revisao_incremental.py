"""Achados da revisao da 0.12.0 (incremental do "Exportar todas") do lado do Python.

Os do `nucleo.js` estao em `extensao/testes/incremental.test.js`. O que ainda nao
foi corrigido fica como `xfail(strict=True)`, como no `test_seguranca.py`: quando a
correcao entrar, o teste passa e o xfail estrito acusa; tire a marca.
"""

from __future__ import annotations

import pytest

from wacervo.migrar import _copiar

from test_servidor import MARCA, ambiente, pedir, pedir_json  # noqa: F401  (fixture)


@pytest.mark.xfail(strict=True, reason="achado 5, escolha de produto: a marca nao diz em qual pasta "
                   "70-WHATSAPP foi gravada; outra pasta com esse nome herda as marcas e so recebe o novo")
def test_marca_distingue_a_pasta_70_whatsapp(ambiente):
    servidor, _ = ambiente()
    assert pedir_json(servidor, "/marca", {**MARCA, "raiz": "D:\\70-WHATSAPP"})[0] == 200
    _, dados = pedir(servidor, "GET", "/marcas")
    # Exportar todas em E:\70-WHATSAPP le esta mesma marca: nada nela diz que era a do D:.
    assert "raiz" in dados["conversas"][MARCA["id"]]


def test_copia_da_migracao_compara_o_conteudo(tmp_path):
    fonte = tmp_path / "lote" / "renata-silva.md"
    destino = tmp_path / "70-WHATSAPP" / "renata-silva" / "renata-silva.md"
    fonte.parent.mkdir(parents=True)
    destino.parent.mkdir(parents=True)
    fonte.write_text("# Renata Silva\n\n**14:24 · Renata Silva**\nOi\n", encoding="utf-8")
    destino.write_text("# Carla Mendes\n\n**14:24 · Carla Mendes**\nOi\n", encoding="utf-8")
    assert fonte.stat().st_size == destino.stat().st_size
    assert _copiar(fonte, destino) is True
    assert destino.read_bytes() == fonte.read_bytes()
    # Igual de verdade: nao copia de novo.
    assert _copiar(fonte, destino) is False


def test_linha_da_rodada_nao_le_todas_as_marcas(ambiente):
    servidor, _ = ambiente()
    for i in range(50):
        pedir_json(servidor, "/marca", {**MARCA, "id": f"5562900000{i:02d}@c.us", "pasta": f"p{i}"})
    pedir_json(servidor, "/rodada", {"total": 50, "exportadas": 50})
    # A linha da rodada le GET /rodada, sem as 50 conversas.
    codigo, dados = pedir(servidor, "GET", "/rodada")
    assert codigo == 200 and "conversas" not in dados and dados["rodada"]["total"] == 50


def test_rodada_sem_rodada_e_pede_origem_de_extensao(ambiente):
    servidor, _ = ambiente()
    assert pedir(servidor, "GET", "/rodada") == (200, {"rodada": None})
    codigo, _ = pedir(servidor, "GET", "/rodada", cabecalhos={"Origin": "https://web.whatsapp.com"})
    assert codigo == 403

