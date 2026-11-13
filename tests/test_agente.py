"""Ponte do agente (0.19.0): fila no wa servir, rotas, travas do envio e o MCP.

So dado ficticio, porta 0 e pasta temporaria. Nada de rede.
"""

import http.client
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from wacervo.agente import ErroDoAgente, FilaDoAgente, criar_token, ler_token
from wacervo.cache import Cache
from wacervo.config import Config, ConfigServidor
from wacervo.mcp_whatsapp import (
    LEITURA_VALE_S, Ferramentas, Leituras, Recusa, formatar_leitura, formatar_lista, responder,
)
from wacervo.servidor import Motores, ServidorLocal

RAIZ = Path(__file__).resolve().parents[1]
ORIGEM = "chrome-extension://" + "a" * 32
ID = "556290000000@c.us"
NOME = "Renata Silva"


# ---------- fila ----------
def _rapido(fila, op="enviar"):
    return fila.pedir(op, {"x": 1}, prazos=(0.3, 0.3, 15))


def test_sem_extensao_recusa_na_hora():
    with pytest.raises(ErroDoAgente) as e:
        FilaDoAgente().pedir("listar", {})
    assert e.value.tipo == "sem_extensao"


def test_pedido_nao_pego_sai_da_fila_e_nunca_roda():
    fila = FilaDoAgente()
    fila.proximo(0)  # extensao vista
    with pytest.raises(ErroDoAgente) as e:
        _rapido(fila)
    assert e.value.tipo == "nao_entregue"
    assert fila.proximo(0) is None


def test_pedido_respondido_volta_ao_mcp():
    fila = FilaDoAgente()
    fila.proximo(0)
    resultado = {}

    def mcp():
        resultado["r"] = fila.pedir("ler", {"alvo": NOME, "n": 5}, prazos=(2, 2, 15))

    t = threading.Thread(target=mcp)
    t.start()
    p = fila.proximo(2)
    assert p["op"] == "ler" and p["args"] == {"alvo": NOME, "n": 5}
    assert p["executar_ate"] > time.time() * 1000
    assert not fila.responder(p["id"], {"ok": True})  # sem aceite, resposta nao vale
    assert fila.aceitar(p["id"]) and not fila.aceitar(p["id"])
    assert fila.responder(p["id"], {"ok": True, "dados": {"a": 1}})
    t.join(3)
    assert resultado["r"] == {"ok": True, "dados": {"a": 1}}
    assert not fila.responder(p["id"], {"ok": True})  # segunda resposta nao vale


def test_pedido_entregue_sem_resposta_nao_volta_para_a_fila():
    fila = FilaDoAgente()
    fila.proximo(0)
    erros = []

    def mcp():
        try:
            fila.pedir("enviar", {}, prazos=(2, 0.3, 15))
        except ErroDoAgente as e:
            erros.append(e.tipo)

    t = threading.Thread(target=mcp)
    t.start()
    p = fila.proximo(2)
    assert fila.aceitar(p["id"])
    t.join(3)
    assert erros == ["sem_resposta"]
    assert fila.proximo(0) is None  # nunca roda duas vezes
    assert not fila.responder(p["id"], {"ok": True})


def test_pedido_pego_sem_aceite_conta_como_nao_rodado():
    # O long-poll caiu depois de o servidor tirar o pedido da fila: ninguem aceita.
    fila = FilaDoAgente()
    fila.proximo(0)
    erros = []

    def mcp():
        try:
            fila.pedir("enviar", {}, prazos=(2, 2, 15), aceite_s=0.3)
        except ErroDoAgente as e:
            erros.append(e.tipo)

    t = threading.Thread(target=mcp)
    t.start()
    p = fila.proximo(2)
    t.join(3)
    assert erros == ["nao_entregue"]
    assert not fila.aceitar(p["id"])  # aceite tardio: a extensao nao executa
    assert not fila.responder(p["id"], {"ok": True})


def test_extensao_sumida_conta_como_desligada():
    agora = [1000.0]
    fila = FilaDoAgente(relogio=lambda: agora[0])
    fila.proximo(0)
    agora[0] += 91
    assert not fila.situacao()["conectada"]
    with pytest.raises(ErroDoAgente):
        fila.pedir("listar", {})


def test_token_criado_uma_vez(tmp_path):
    caminho = tmp_path / "dados" / "agente.token"
    assert ler_token(caminho) is None
    t = criar_token(caminho)
    assert len(t) >= 32 and criar_token(caminho) == t and ler_token(caminho) == t


# ---------- rotas ----------
@pytest.fixture
def servidor(tmp_path):
    cfg = Config(raiz=tmp_path, servidor=ConfigServidor(extensao_id="a" * 32))
    cache = Cache(cfg.caminho_cache)
    s = ServidorLocal(cfg, cache, Motores(transcrever=lambda d: "", ler_imagem=lambda d: ""), porta=0)
    s.em_segundo_plano()
    yield s
    s.shutdown()
    s.server_close()
    cache.fechar()


def _post(s, rota, corpo, cabecalhos=None):
    c = http.client.HTTPConnection("127.0.0.1", s.porta, timeout=10)
    c.request("POST", rota, body=json.dumps(corpo).encode(), headers={
        "Content-Type": "application/json", **(cabecalhos or {}),
    })
    r = c.getresponse()
    dados = json.loads(r.read() or b"{}")
    c.close()
    return r.status, dados


def test_rota_do_mcp_exige_token_e_nenhuma_origem(servidor):
    token = servidor.app.token_agente
    assert _post(servidor, "/agente/pedir", {"op": "listar"})[0] == 403
    assert _post(servidor, "/agente/pedir", {"op": "listar"}, {"X-Wacervo-Token": "x" * 43})[0] == 403
    com_origem = {"X-Wacervo-Token": token, "Origin": ORIGEM}
    assert _post(servidor, "/agente/pedir", {"op": "listar"}, com_origem)[0] == 403
    status, r = _post(servidor, "/agente/pedir", {"op": "listar"}, {"X-Wacervo-Token": token})
    assert status == 200 and r["tipo"] == "sem_extensao"
    assert _post(servidor, "/agente/pedir", {"op": "apagar"}, {"X-Wacervo-Token": token})[0] == 400


def test_rotas_da_extensao_exigem_origem_de_extensao(servidor):
    assert _post(servidor, "/agente/proximo", {"espera_s": 0})[0] == 403
    assert _post(servidor, "/agente/proximo", {"espera_s": 0}, {"Origin": "https://web.whatsapp.com"})[0] == 403
    assert _post(servidor, "/agente/proximo", {"espera_s": 0}, {"Origin": ORIGEM}) == (200, {"pedido": None})
    c = http.client.HTTPConnection("127.0.0.1", servidor.porta, timeout=5)
    c.request("GET", "/agente/proximo")  # GET nunca (uma <img> de pagina qualquer faria GET)
    assert c.getresponse().status == 404


def test_agente_desligado_sem_extensao_id(tmp_path):
    cfg = Config(raiz=tmp_path)  # extensao_id vazio: qualquer extensao passaria no _autorizado
    cache = Cache(cfg.caminho_cache)
    s = ServidorLocal(cfg, cache, Motores(transcrever=lambda d: "", ler_imagem=lambda d: ""), porta=0)
    s.em_segundo_plano()
    try:
        status, r = _post(s, "/agente/proximo", {"espera_s": 0}, {"Origin": ORIGEM})
        assert status == 403 and "extensao_id" in r["erro"]
        assert _post(s, "/agente/aceite", {"id": "x"}, {"Origin": ORIGEM})[0] == 403
    finally:
        s.shutdown()
        s.server_close()
        cache.fechar()


def test_rota_da_extensao_recusa_outra_extensao(servidor):
    outra = "chrome-extension://" + "b" * 32
    assert _post(servidor, "/agente/proximo", {"espera_s": 0}, {"Origin": outra})[0] == 403


def test_ida_e_volta_pelas_rotas(servidor):
    _post(servidor, "/agente/proximo", {"espera_s": 0}, {"Origin": ORIGEM})
    saida = {}

    def mcp():
        saida["r"] = _post(servidor, "/agente/pedir", {"op": "listar", "args": {}},
                           {"X-Wacervo-Token": servidor.app.token_agente})

    t = threading.Thread(target=mcp)
    t.start()
    _, r = _post(servidor, "/agente/proximo", {"espera_s": 5}, {"Origin": ORIGEM})
    p = r["pedido"]
    assert p["op"] == "listar"
    assert _post(servidor, "/agente/aceite", {"id": p["id"]})[0] == 403
    assert _post(servidor, "/agente/aceite", {"id": p["id"]}, {"Origin": ORIGEM}) == (200, {"aceita": True})
    resposta = {"id": p["id"], "ok": True, "dados": {"conversas": []}}
    assert _post(servidor, "/agente/resposta", resposta, {"Origin": ORIGEM}) == (200, {"aceita": True})
    t.join(5)
    assert saida["r"] == (200, {"ok": True, "dados": {"conversas": []}})
    _, status = None, None
    c = http.client.HTTPConnection("127.0.0.1", servidor.porta, timeout=5)
    c.request("GET", "/status")
    assert json.loads(c.getresponse().read())["agente"]["conectada"] is True


# ---------- travas ----------
def _msg(id_, t, eu=False, tipo="chat", texto="Oi, conseguiu ver?", **extra):
    return {"id": id_, "t": t, "eu": eu, "autor": "Eu" if eu else NOME, "tipo": tipo, "texto": texto, **extra}


class Pedidos:
    def __init__(self):
        self.feitos = []
        self.respostas = {}

    def __call__(self, op, args):
        self.feitos.append((op, args))
        r = self.respostas[op]
        return r(args) if callable(r) else r


def _leitura_ok(chat=None):
    return {"ok": True, "dados": {
        "chat": chat or {"id": ID, "nome": NOME, "grupo": False, "telefone": "556290000000"},
        "mensagens": [_msg("m1", 1_800_000_000), _msg("m2", 1_800_000_060, eu=True, texto="Vi sim")],
    }}


@pytest.fixture
def ferramentas():
    agora = [1_800_000_100.0]
    pedidos = Pedidos()
    f = Ferramentas(pedidos, Leituras(relogio=lambda: agora[0]))
    f.agora = agora
    f.pedidos = pedidos
    return f


def test_enviar_sem_leitura_recusa(ferramentas):
    with pytest.raises(Recusa, match="nao foi lida"):
        ferramentas.enviar({"texto": "Oi", "conversa": ID, "nome_esperado": NOME})
    assert ferramentas.pedidos.feitos == []


def test_enviar_exige_id_nome_e_texto(ferramentas):
    ferramentas.pedidos.respostas["ler"] = _leitura_ok()
    ferramentas.ler({"conversa": "Renata"})
    for args, erro in (
        ({"texto": "Oi", "conversa": NOME, "nome_esperado": NOME}, "id que o whatsapp_ler"),
        ({"texto": "Oi", "conversa": ID, "nome_esperado": "Carla Mendes"}, "nao \"Carla Mendes\""),
        ({"texto": "   ", "conversa": ID, "nome_esperado": NOME}, "texto vazio"),
    ):
        with pytest.raises(Recusa, match=erro):
            ferramentas.enviar(args)
    assert [op for op, _ in ferramentas.pedidos.feitos] == ["ler"]


def test_leitura_velha_nao_vale(ferramentas):
    ferramentas.pedidos.respostas["ler"] = _leitura_ok()
    ferramentas.ler({"conversa": ID})
    ferramentas.agora[0] += LEITURA_VALE_S + 1
    with pytest.raises(Recusa, match="30 min"):
        ferramentas.enviar({"texto": "Oi", "conversa": ID, "nome_esperado": NOME})


def test_leitura_que_falha_ou_fica_ambigua_apaga_o_alvo(ferramentas):
    ferramentas.pedidos.respostas["ler"] = _leitura_ok()
    ferramentas.ler({"conversa": ID})
    ferramentas.pedidos.respostas["ler"] = {"ok": False, "tipo": "nao_achou", "erro": "nenhuma conversa"}
    with pytest.raises(Recusa):
        ferramentas.ler({"conversa": "Carla"})
    with pytest.raises(Recusa, match="nao foi lida"):
        ferramentas.enviar({"texto": "Oi", "conversa": ID, "nome_esperado": NOME})

    ferramentas.pedidos.respostas["ler"] = _leitura_ok()
    ferramentas.ler({"conversa": ID})
    ferramentas.pedidos.respostas["ler"] = {"ok": True, "dados": {"ambiguo": [
        {"id": ID, "nome": NOME, "grupo": False}, {"id": "199999999999999@lid", "nome": "Renata S.", "grupo": False},
    ]}}
    texto = ferramentas.ler({"conversa": "Renata"})
    assert texto.startswith("AMBIGUO: 2") and "199999999999999@lid" in texto
    with pytest.raises(Recusa, match="nao foi lida"):
        ferramentas.enviar({"texto": "Oi", "conversa": ID, "nome_esperado": NOME})


def test_enviar_manda_o_que_a_leitura_viu(ferramentas):
    ferramentas.pedidos.respostas["ler"] = _leitura_ok()
    ferramentas.pedidos.respostas["enviar"] = {"ok": True, "dados": {"resultado": "enviado", "ack": 1}}
    ferramentas.ler({"conversa": ID})
    assert ferramentas.enviar({"texto": "Combinado.", "conversa": ID, "nome_esperado": NOME}).startswith("ENVIADO")
    op, args = ferramentas.pedidos.feitos[-1]
    assert op == "enviar" and args == {
        "chatId": ID, "texto": "Combinado.", "ultimaT": 1_800_000_060, "vistos": ["m1", "m2"],
    }


@pytest.mark.parametrize("resposta, esperado, apaga", [
    ({"ok": True, "dados": {"resultado": "nova"}}, "chegou mensagem", True),
    ({"ok": True, "dados": {"resultado": "nao_confirmado"}}, "nao aparece", True),
    ({"ok": False, "tipo": "sem_resposta", "erro": "x"}, "RESULTADO DESCONHECIDO", True),
    ({"ok": False, "tipo": "prazo", "erro": "x"}, "RESULTADO DESCONHECIDO", True),
    ({"ok": False, "tipo": "nao_entregue", "erro": "x"}, "NAO ENVIADO", True),
    ({"ok": False, "tipo": "expirado", "erro": "x"}, "NAO ENVIADO", True),
])
def test_envio_que_nao_confirma_exige_nova_leitura(ferramentas, resposta, esperado, apaga):
    ferramentas.pedidos.respostas["ler"] = _leitura_ok()
    ferramentas.pedidos.respostas["enviar"] = resposta
    ferramentas.ler({"conversa": ID})
    with pytest.raises(Recusa, match=esperado):
        ferramentas.enviar({"texto": "Oi", "conversa": ID, "nome_esperado": NOME})
    with pytest.raises(Recusa, match="nao foi lida"):
        ferramentas.enviar({"texto": "Oi", "conversa": ID, "nome_esperado": NOME})


# ---------- texto ----------
def test_audio_sem_texto_sai_pendente_nunca_inventado():
    chat = {"id": ID, "nome": NOME, "grupo": False, "telefone": "556290000000"}
    msgs = [
        _msg("a1", 1_800_000_000, tipo="ptt", texto="", duracao=12, transcricao={"texto": "Vi sim, ta tudo certo."}),
        _msg("a2", 1_800_000_010, tipo="ptt", texto="", duracao=75, transcricao={"pendente": "nao terminou"}),
        _msg("a3", 1_800_000_020, tipo="ptt", texto="", duracao=3, transcricao={"texto": ""}),
        _msg("i1", 1_800_000_030, tipo="image", texto="o contrato"),
    ]
    t = formatar_leitura(chat, msgs)
    assert "⚠️ 1 audio(s) sem transcricao" in t
    assert "🎙️ audio 0:12 — Vi sim, ta tudo certo." in t
    assert "🎙️ audio 1:15 — [TRANSCRICAO PENDENTE (nao terminou): nao redija sobre este audio]" in t
    assert "[sem fala detectada]" in t
    assert "[🖼️ imagem] o contrato" in t
    assert f"conversa=\"{ID}\"" in t and "+556290000000" in t
    assert "[15/01 05:00]" in t  # 1_800_000_000 em Brasilia


def test_lista_vazia_e_cheia():
    assert formatar_lista([]) == "Nenhuma conversa com mensagem nao lida."
    t = formatar_lista([{"id": "120363000000000000@g.us", "nome": "Familia", "grupo": True, "naoLidas": 3, "t": 0}])
    assert "👥 Familia · 3 nao lida(s) · id: 120363000000000000@g.us" in t


# ---------- MCP ----------
def test_jsonrpc_basico(ferramentas):
    r = responder({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                  ferramentas)
    assert r["result"]["protocolVersion"] == "2025-06-18"
    nomes = [t["name"] for t in responder({"id": 2, "method": "tools/list"}, ferramentas)["result"]["tools"]]
    assert nomes == ["whatsapp_listar", "whatsapp_ler", "whatsapp_enviar"]
    assert responder({"method": "notifications/initialized"}, ferramentas) is None
    r = responder({"id": 3, "method": "tools/call", "params": {
        "name": "whatsapp_enviar", "arguments": {"texto": "Oi", "conversa": ID, "nome_esperado": NOME}}}, ferramentas)
    assert r["result"]["isError"] is True
    assert responder({"id": 4, "method": "x"}, ferramentas)["error"]["code"] == -32601


def test_descricao_do_enviar_exige_o_sim_do_usuario():
    from wacervo.mcp_whatsapp import FERRAMENTAS

    d = next(t for t in FERRAMENTAS if t["name"] == "whatsapp_enviar")["description"]
    assert "\"sim\"" in d and "texto exato" in d and "terceiros" in d


def test_mcp_roda_so_com_biblioteca_padrao(tmp_path):
    """`-S` tira o site-packages: o MCP sobe no Python do sistema, sem o .venv."""
    entrada = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n"
    env = {**os.environ, "PYTHONPATH": str(RAIZ / "src")}
    r = subprocess.run([sys.executable, "-S", "-B", "-m", "wacervo.mcp_whatsapp"], input=entrada.encode(),
                       capture_output=True, timeout=30, env=env, cwd=tmp_path)
    assert r.returncode == 0, r.stderr.decode()
    linha = json.loads(r.stdout.decode().splitlines()[0])
    assert linha["result"]["serverInfo"]["name"] == "whatsapp"
