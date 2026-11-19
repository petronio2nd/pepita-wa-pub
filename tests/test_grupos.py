"""Grupos escolhidos pelo usuario (0.21.0): config, filtro do MCP e /status. So dado ficticio."""

import pytest

from wacervo.config import ErroDeConfig, carregar
from wacervo.mcp_whatsapp import Ferramentas, Leituras, Recusa, formatar_leitura

GA = "120363000000000001@g.us"   # liberado
GB = "120363000000000002@g.us"   # fora da lista
PESSOA = "556290000000@c.us"


def _g(id_, nome):
    return {"id": id_, "nome": nome, "grupo": True}


class Pedidos:
    def __init__(self, respostas):
        self.respostas, self.feitos = respostas, []

    def __call__(self, op, args):
        self.feitos.append((op, dict(args)))
        r = self.respostas[op]
        return r(args) if callable(r) else r


def _ferr(respostas, liberados=(GA,)):
    p = Pedidos(respostas)
    f = Ferramentas(p, Leituras(), grupos=lambda: frozenset(liberados))
    f.pedidos = p
    return f


def _leitura(chat):
    return {"ok": True, "dados": {"chat": chat, "mensagens": [
        {"id": "m1", "t": 1_800_000_000, "autor": "Carla Mendes", "tipo": "chat", "texto": "Oi"}]}}


def test_config_le_ids_e_aceita_arroba_largo(tmp_path):
    (tmp_path / "config.toml").write_text(
        f'[grupos]\nids = ["{GA}", "{GA}", "120363000000000003＠g.us"]\n', encoding="utf-8")
    assert carregar(raiz=tmp_path).grupos.ids == (GA, "120363000000000003@g.us")
    assert carregar(raiz=tmp_path / "nada").grupos.ids == ()


def test_config_soma_ids_do_arquivo_comum(tmp_path):
    # Lista sincronizada entre os PCs (Syncthing) somada a lista local (usuario, 2026-11-18).
    comum = tmp_path / "comum.toml"
    comum.write_text(f'ids = ["{GB}", "{GA}"]\n', encoding="utf-8")
    (tmp_path / "config.toml").write_text(
        f'[grupos]\nids = ["{GA}"]\narquivo = "{comum.as_posix()}"\n', encoding="utf-8")
    assert carregar(raiz=tmp_path).grupos.ids == (GA, GB)


def test_config_sem_o_arquivo_comum_fica_com_a_lista_local(tmp_path):
    (tmp_path / "config.toml").write_text(
        f'[grupos]\nids = ["{GA}"]\narquivo = "{(tmp_path / "falta.toml").as_posix()}"\n',
        encoding="utf-8")
    assert carregar(raiz=tmp_path).grupos.ids == (GA,)


@pytest.mark.parametrize("conteudo", ['ids = ["556290000000@c.us"]\n', "ids = [\n", 'ids = "x@g.us"\n'])
def test_config_recusa_arquivo_comum_invalido(tmp_path, conteudo):
    comum = tmp_path / "comum.toml"
    comum.write_text(conteudo, encoding="utf-8")
    (tmp_path / "config.toml").write_text(
        f'[grupos]\narquivo = "{comum.as_posix()}"\n', encoding="utf-8")
    with pytest.raises(ErroDeConfig):
        carregar(raiz=tmp_path)


@pytest.mark.parametrize("ids", ['["556290000000@c.us"]', '["@g.us"]', '"x@g.us"', '[1]'])
def test_config_recusa_id_que_nao_e_de_grupo(tmp_path, ids):
    (tmp_path / "config.toml").write_text(f"[grupos]\nids = {ids}\n", encoding="utf-8")
    with pytest.raises(ErroDeConfig):
        carregar(raiz=tmp_path)


def test_listar_esconde_grupo_fora_da_lista():
    f = _ferr({"listar": {"ok": True, "dados": {"conversas": [
        _g(GA, "Familia"), _g(GB, "Condominio"), {"id": PESSOA, "nome": "Renata Silva", "naoLidas": 1}]}}})
    t = f.listar({})
    assert "Familia" in t and "Renata Silva" in t and "Condominio" not in t and GB not in t


def test_sem_lista_nenhum_grupo_passa():
    f = _ferr({"listar": {"ok": True, "dados": {"conversas": [_g(GA, "Familia")]}}}, liberados=())
    assert "Familia" not in f.listar({})


def test_ler_grupo_liberado_funciona_e_fora_da_lista_recusa():
    f = _ferr({"ler": lambda a: _leitura(_g(a["alvo"], "Familia" if a["alvo"] == GA else "Condominio"))})
    assert "Familia" in f.ler({"conversa": GA})
    with pytest.raises(Recusa, match="nenhuma conversa liberada"):
        f.ler({"conversa": GB})
    assert GB not in f.leituras._por_id


def test_ler_por_nome_ambiguo_filtra_o_grupo_fora_da_lista():
    def resp(a):
        if a["alvo"] == "casa":
            return {"ok": True, "dados": {"ambiguo": [_g(GB, "Casa do Bairro"), _g(GA, "Casa Familia")]}}
        return _leitura(_g(a["alvo"], "Casa Familia"))
    f = _ferr({"ler": resp})
    assert "Casa Familia" in f.ler({"conversa": "casa"})
    assert [feito[1]["alvo"] for feito in f.pedidos.feitos] == ["casa", GA]  # leu so o liberado, pelo id


def test_ler_manda_a_lista_a_extensao_e_inexistente_responde_igual_a_nao_liberado():
    def resp(a):
        assert a["grupos"] == [GA]
        return {"ok": False, "tipo": "nao_achou", "erro": "nenhuma conversa com o id x"}
    f = _ferr({"ler": resp})
    with pytest.raises(Recusa) as e1:
        f.ler({"conversa": "120363999999999999@g.us"})
    f2 = _ferr({"ler": _leitura(_g(GB, "Condominio"))})
    with pytest.raises(Recusa) as e2:
        f2.ler({"conversa": GB})
    assert str(e1.value) == str(e2.value)


def test_ler_por_nome_so_com_grupo_fora_da_lista_recusa():
    f = _ferr({"ler": {"ok": True, "dados": {"ambiguo": [_g(GB, "Condominio")]}}})
    with pytest.raises(Recusa, match="nenhuma conversa liberada"):
        f.ler({"conversa": "cond"})


def test_enviar_a_grupo_fora_da_lista_nunca_chega_a_extensao():
    f = _ferr({"enviar": {"ok": True, "dados": {"resultado": "enviado"}}})
    # Leitura registrada de um grupo que depois saiu da lista (config editado): recusa e esquece.
    f.leituras.registrar(GB, "Condominio", [{"id": "m1", "t": 1}])
    with pytest.raises(Recusa, match="nenhuma conversa liberada"):
        f.enviar({"texto": "Oi", "conversa": GB, "nome_esperado": "Condominio"})
    assert f.pedidos.feitos == [] and GB not in f.leituras._por_id


def test_enviar_a_grupo_liberado_segue_as_mesmas_travas():
    f = _ferr({"ler": lambda a: _leitura(_g(GA, "Familia")),
               "enviar": {"ok": True, "dados": {"resultado": "enviado", "ack": 1}}})
    with pytest.raises(Recusa, match="nao foi lida"):
        f.enviar({"texto": "Oi", "conversa": GA, "nome_esperado": "Familia"})
    f.ler({"conversa": GA})
    with pytest.raises(Recusa, match="nao"):
        f.enviar({"texto": "Oi", "conversa": GA, "nome_esperado": "Outro nome"})
    assert "ENVIADO" in f.enviar({"texto": "Oi", "conversa": GA, "nome_esperado": "Familia"})


def test_grupo_sem_flag_mas_com_sufixo_g_us_tambem_e_filtrado():
    f = _ferr({"listar": {"ok": True, "dados": {"conversas": [{"id": GB, "nome": "Condominio"}]}}})
    assert "Condominio" not in f.listar({})


def test_formatar_leitura_de_grupo_segue_igual():
    assert "👥 Familia" in formatar_leitura(_g(GA, "Familia"), [])



def test_status_do_servidor_leva_so_os_ids_dos_grupos(tmp_path):
    import http.client
    import json

    from wacervo.cache import Cache
    from wacervo.config import Config, ConfigGrupos
    from wacervo.servidor import Motores, ServidorLocal

    (tmp_path / "config.toml").write_text(f'[grupos]\nids = ["{GA}"]\n', encoding="utf-8")
    cfg = Config(raiz=tmp_path, grupos=ConfigGrupos(ids=()))
    cache = Cache(cfg.caminho_cache)
    s = ServidorLocal(cfg, cache, Motores(transcrever=lambda d: "", ler_imagem=lambda d: ""), porta=0)
    s.em_segundo_plano()
    try:
        c = http.client.HTTPConnection("127.0.0.1", s.porta, timeout=10)
        c.request("GET", "/status", headers={"Origin": "chrome-extension://" + "a" * 32})
        dados = json.loads(c.getresponse().read())
        c.close()
    finally:
        s.shutdown()
        s.server_close()
        cache.fechar()
    assert dados["grupos"] == [GA]  # vem do arquivo, nao da config carregada na partida
