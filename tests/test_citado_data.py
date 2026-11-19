"""INS-12: a leitura diz de quando e a mensagem citada (qual imagem foi citada)."""
from wacervo.mcp_whatsapp import formatar_leitura, formatar_mensagem

CONVERSA = "556290000000@c.us"
CHAT = {"id": CONVERSA, "nome": "Renata Silva", "telefone": "556290000000"}
T0 = 1_800_000_000  # 15/01 05:00 em Brasilia


def _m(stanza, t, **extra):
    return {"id": f"false_{CONVERSA}_{stanza}", "t": t, "tipo": "chat", "autor": "Renata Silva", "texto": "", **extra}


def test_citada_entre_as_lidas_ganha_a_data_dela():
    ms = [
        _m("AAA1", T0, tipo="image"),
        _m("AAA2", T0 + 3600, tipo="image"),
        _m("AAA3", T0 + 7200, texto="essa aqui", citado={"autor": "Eu", "texto": "[imagem]", "id": "AAA1", "t": 0}),
    ]
    saida = formatar_leitura(CHAT, ms)
    assert '↩ Eu: "[imagem]" (citada de 15/01 05:00)' in saida


def test_citada_em_grupo_casa_pelo_stanza_mesmo_com_participante_no_id():
    grupo = "120363000000000000@g.us"
    ms = [
        {"id": f"false_{grupo}_BBB1_5562911111111@c.us", "t": T0, "tipo": "image", "autor": "Ana"},
        {"id": f"true_{grupo}_BBB2", "t": T0 + 60, "tipo": "chat", "autor": "Eu", "texto": "ok",
         "citado": {"autor": "Ana", "texto": "[imagem]", "id": "BBB1"}},
    ]
    assert "(citada de 15/01 05:00)" in formatar_leitura({"id": grupo, "nome": "Familia", "grupo": True}, ms)


def test_citada_fora_das_lidas_usa_a_hora_que_a_extensao_mandou():
    ms = [_m("AAA3", T0 + 7200, texto="essa", citado={"autor": "", "texto": "[imagem]", "id": "ZZZ9", "t": T0})]
    assert "(citada de 15/01 05:00)" in formatar_leitura(CHAT, ms)


def test_midia_citada_sem_data_avisa_que_ficou_fora_das_lidas():
    ms = [_m("AAA3", T0, texto="essa", citado={"autor": "", "texto": "[imagem]", "id": "ZZZ9", "t": 0})]
    saida = formatar_leitura(CHAT, ms)
    assert "citada fora das mensagens lidas" in saida and "citada de" not in saida


def test_texto_citado_sem_data_nao_ganha_aviso():
    ms = [_m("AAA3", T0, texto="sim", citado={"autor": "", "texto": "voce vem amanha?", "id": "ZZZ9", "t": 0})]
    saida = formatar_leitura(CHAT, ms)
    assert '↩ "voce vem amanha?"\n' in saida


def test_extensao_antiga_sem_id_nem_hora_segue_como_antes():
    m = _m("AAA3", T0, texto="essa", citado={"autor": "Eu", "texto": "[imagem]"})
    assert formatar_mensagem(m).splitlines()[0] == '   ↩ Eu: "[imagem]"'


def test_id_ou_hora_estranhos_na_citacao_nao_derrubam_a_leitura():
    ms = [_m("AAA3", T0, texto="x", citado={"autor": "", "texto": "[video]", "id": {"a": 1}, "t": "ontem"}),
          {"id": None, "t": T0, "tipo": "chat", "autor": "Eu", "texto": "y"}]
    assert "[15/01 05:00]" in formatar_leitura(CHAT, ms)
