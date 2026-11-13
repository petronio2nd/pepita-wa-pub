"""`wa migrar`: lote antigo do Exportar todas -> pasta por contato, com marca."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from wacervo.marcas import Marcas
from wacervo.migrar import aplicar, ler_cabecalho, planejar

BRT = timezone(timedelta(hours=-3))


def md(nome="Renata Silva", ident="556290000000@c.us", exportado="2026-10-24 21:58", corpo=None):
    corpo = corpo if corpo is not None else [
        "## 2026-09-17", "", "**14:24 · Renata Silva**", "Oi", "", "**14:25 · Eu**",
        "**25:00 · Eu**", "## 2099-01-01", "**10:00 · Eu**", "",
        "## 2026-09-18", "", "*09:00 · Carla Mendes adicionou Renata Silva*",
    ]
    return "\n".join([
        f"# {nome}", "",
        f"> contato · `{ident}` · 2026-09-17 a 2026-09-18 · 3 mensagens · exportado em {exportado}",
        "> coleta: mensagens ja carregadas na tela · fuso America/Sao_Paulo", "", *corpo, "",
    ])


def segundos(texto):
    return int(datetime.fromisoformat(texto).replace(tzinfo=BRT).timestamp())


def test_cabecalho_da_o_trecho_com_folga_e_ignora_cabecalho_imitado():
    c = ler_cabecalho(md())
    assert c["id"] == "556290000000@c.us"
    assert c["nome"] == "Renata Silva"
    # Comeco no fim do minuto da primeira (14:24); fim logo antes do minuto da ultima real
    # (09:00). O "25:00" e o "## 2099-01-01" imitados no texto ficam de fora.
    assert c["primeira"] == segundos("2026-09-17T14:25")
    assert c["ultima"] == segundos("2026-09-18T09:00") - 1


def test_conversa_recente_nao_inverte_o_trecho():
    c = ler_cabecalho(md(exportado="2026-09-17 14:24"))
    assert c["primeira"] == c["ultima"] == segundos("2026-09-17T14:24") - 1


def test_minuto_da_ultima_mensagem_fica_fora_da_marca():
    # A coleta pode ter parado no meio desse minuto: o que chegou depois volta na rodada nova.
    c = ler_cabecalho(md(corpo=["## 2026-09-17", "", "**14:24 · Renata Silva**", "Oi", "", "## 2026-09-18", "",
                                "*09:00 · Carla Mendes adicionou Renata Silva*"]))
    assert c["ultima"] == segundos("2026-09-18T09:00") - 1


@pytest.mark.parametrize("texto", [
    "# Grupo\n\n> grupo · `120363000000000000@g.us` · 2026-09-17 · 1 mensagens · exportado em 2026-10-24 21:58\n",
    md(corpo=[]),
    "qualquer coisa",
    md().replace("fuso America/Sao_Paulo", "fuso Europe/Lisbon"),
    md().replace("> coleta: mensagens ja carregadas na tela", "> periodo pedido: 2026-09-17 a 2026-09-18"),
])
def test_cabecalho_recusa_o_que_nao_e_conversa_individual(texto):
    with pytest.raises(ValueError):
        ler_cabecalho(texto)


def _lote(raiz):
    a = raiz / "renata-silva_2026-09-17_a_2026-09-18"
    (a / "anexos").mkdir(parents=True)
    (a / f"{a.name}.md").write_text(md(), encoding="utf-8")
    (a / "anexos" / "2026-09-17_142509_imagem.jpg").write_bytes(b"jpg")
    # Homonimo, com outro id, e sem anexos (.md solto).
    (raiz / "renata-silva_2026-09-20.md").write_text(md(ident="199999999999999@lid"), encoding="utf-8")
    (raiz / "carla-mendes_2026-09-18.md").write_text(md("Carla Mendes", "556290000001@c.us"), encoding="utf-8")
    (raiz / "notas.md").write_text("rascunho", encoding="utf-8")
    return raiz


def test_migrar_copia_para_a_pasta_do_contato_e_marca(tmp_path):
    origem = _lote(tmp_path / "lote")
    destino = tmp_path / "70-WHATSAPP"
    marcas = Marcas(tmp_path / "marcas.sqlite3")
    conversas, problemas = planejar(origem, marcas.todas())
    assert [p.split(":")[0] for p in problemas] == ["notas.md"]
    pastas = {c.id: c.pasta for c in conversas}
    # Em ordem de nome: o primeiro Renata fica com o nome; o homonimo leva o numero do id.
    assert pastas == {
        "556290000001@c.us": "carla-mendes",
        "556290000000@c.us": "renata-silva",
        "199999999999999@lid": "renata-silva-199999999999999",
    }

    r = aplicar(conversas, destino, marcas)
    assert r == {"conversas": 3, "copiados": 4}
    p = pastas["556290000000@c.us"]
    assert (destino / p / "renata-silva_2026-09-17_a_2026-09-18.md").exists()
    assert (destino / p / "anexos" / "2026-09-17_142509_imagem.jpg").read_bytes() == b"jpg"
    assert (origem / "renata-silva_2026-09-20.md").exists()  # a origem fica
    m = marcas.todas()["556290000000@c.us"]
    assert (m["pasta"], m["completaAntes"]) == (p, False)
    assert m["primeira"] == segundos("2026-09-17T14:25")

    # Segunda vez: nada a copiar, as pastas e as marcas ficam iguais.
    de_novo, _ = planejar(origem, marcas.todas())
    assert {c.id: c.pasta for c in de_novo} == pastas
    assert aplicar(de_novo, destino, marcas)["copiados"] == 0
    assert marcas.todas()["556290000000@c.us"] == {**m, "atualizado": marcas.todas()["556290000000@c.us"]["atualizado"]}
    marcas.fechar()


def test_contato_repetido_no_lote_fica_de_fora(tmp_path):
    origem = tmp_path / "lote"
    origem.mkdir()
    (origem / "renata-silva_2026-09-17.md").write_text(md(), encoding="utf-8")
    (origem / "renata-silva_2026-10-01.md").write_text(md(), encoding="utf-8")
    conversas, problemas = planejar(origem, {})
    assert len(conversas) == 1
    assert "repetido" in problemas[0]


def test_cli_sem_sim_so_mostra(tmp_path, monkeypatch, capsys):
    from wacervo import cli
    from wacervo.config import Config

    origem = _lote(tmp_path / "lote")
    destino = tmp_path / "70-WHATSAPP"
    monkeypatch.setattr(cli, "carregar", lambda: Config(raiz=tmp_path))
    assert cli.main(["migrar", str(origem), "--destino", str(destino)]) == 0
    assert "3 conversa(s) iriam" in capsys.readouterr().out
    assert not destino.exists()
    assert cli.main(["migrar", str(origem), "--destino", str(destino), "--sim"]) == 0
    assert "3 conversa(s) marcadas" in capsys.readouterr().out
    assert len(list(destino.iterdir())) == 3
