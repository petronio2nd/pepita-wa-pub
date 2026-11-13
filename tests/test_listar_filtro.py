"""whatsapp_listar com filtro (0.23.0): ativas, arquivadas, nao_lidas. So dado ficticio."""

import pytest

from wacervo.mcp_whatsapp import FERRAMENTAS, Ferramentas, Leituras, Recusa

A = {"id": "556290000000@c.us", "nome": "Renata Silva", "naoLidas": 0, "t": 1_800_000_000, "arquivada": False}
B = {"id": "556290000001@c.us", "nome": "Carla Mendes", "naoLidas": 2, "t": 1_800_000_100, "arquivada": True}
G = {"id": "120363000000000002@g.us", "nome": "Condominio", "grupo": True, "naoLidas": 0, "t": 1, "arquivada": False}


def _f(conversas, eco=True):
    pedidos = []

    def pedir(op, args):
        pedidos.append((op, dict(args)))
        return {"ok": True, "dados": {"conversas": conversas, **({"filtro": args.get("filtro")} if eco else {})}}

    f = Ferramentas(pedir, Leituras(), grupos=lambda: frozenset())
    f.pedidos = pedidos
    return f


def test_padrao_pede_nao_lidas_e_nao_quebra():
    f = _f([B])
    t = f.listar({})
    assert f.pedidos == [("listar", {"filtro": "nao_lidas"})]
    assert "Carla Mendes" in t and "2 nao lida" in t


def test_ativas_inclui_sem_nao_lidas_e_tira_arquivada_e_grupo_fora_da_lista():
    f = _f([A, B, G])
    t = f.listar({"filtro": "ativas"})
    assert f.pedidos == [("listar", {"filtro": "ativas"})]
    assert "Renata Silva" in t and "Carla Mendes" not in t and "Condominio" not in t


def test_arquivadas_marca_arquivada():
    t = _f([A, B]).listar({"filtro": "arquivadas"})
    assert "Carla Mendes" in t and "· arquivada" in t and "Renata Silva" not in t


def test_filtro_invalido_recusa():
    with pytest.raises(Recusa):
        _f([A]).listar({"filtro": "todas"})


def test_extensao_antiga_sem_campo_arquivada_recusa():
    velha = {"id": A["id"], "nome": "Renata Silva", "naoLidas": 1, "t": 1}
    with pytest.raises(Recusa, match="desatualizada"):
        _f([velha]).listar({"filtro": "ativas"})


def test_schema_declara_filtro():
    esquema = next(x for x in FERRAMENTAS if x["name"] == "whatsapp_listar")["inputSchema"]
    assert esquema["properties"]["filtro"]["enum"] == ["nao_lidas", "ativas", "arquivadas"]


def test_extensao_antiga_com_lista_vazia_nao_afirma_nenhuma_ativa():
    with pytest.raises(Recusa, match="desatualizada"):
        _f([], eco=False).listar({"filtro": "ativas"})
