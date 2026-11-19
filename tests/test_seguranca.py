"""Testes de seguranca do `wa servir`, do servico da distro e do .md exportado.

Ver docs/seguranca/SECURITY_REPORT.md. Cada teste cita o achado (SEG-NN). O que
ainda nao esta protegido fica como `xfail(strict=True)`: quando a correcao entrar,
o teste passa a passar, o pytest acusa XPASS e o marcador tem de sair.

So dados ficticios, porta 0, motores falsos e pasta temporaria. Nada de rede.
"""

import http.client
import json
import re
import logging
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

import wacervo.servidor as modulo_servidor
from wacervo.cache import Cache
from wacervo.config import Config, ConfigServidor
from wacervo.servidor import Motores, ServidorLocal

from conftest import TOKEN_TESTE
from test_transcricao import servico

RAIZ = Path(__file__).resolve().parents[1]
EXTENSAO_FICTICIA = "a" * 32  # formato de id do Chrome: 32 letras de a a p.


class Motor:
    def __init__(self, texto):
        self.texto = texto
        self.chamadas = 0

    def __call__(self, dados):
        self.chamadas += 1
        return self.texto


@pytest.fixture
def servidor(tmp_path):
    criados = []

    def criar(extensao_id="", texto="Vi sim, ta tudo certo."):
        cfg = Config(raiz=tmp_path, servidor=ConfigServidor(extensao_id=extensao_id))
        cache = Cache(cfg.caminho_cache)
        motores = Motores(transcrever=Motor(texto), ler_imagem=Motor(texto))
        s = ServidorLocal(cfg, cache, motores, porta=0)
        s.em_segundo_plano()
        criados.append((s, cache))
        return s, motores

    yield criar
    for s, cache in criados:
        s.shutdown()
        s.server_close()
        cache.fechar()


@pytest.fixture
def servico_no_ar(monkeypatch):
    criados = []

    def criar():
        s = servico.Servico(("127.0.0.1", 0), medir=lambda dados: {
            "duracao_s": 1.0, "fala_s": 0.5, "trechos": 1})
        s.em_segundo_plano()
        criados.append(s)
        return s

    yield criar
    for s in criados:
        s.shutdown()
        s.server_close()


def pedir(porta, metodo, rota, corpo=None, cabecalhos=None):
    con = http.client.HTTPConnection("127.0.0.1", porta, timeout=5)
    con.request(metodo, rota, body=corpo, headers=cabecalhos or {})
    resp = con.getresponse()
    bruto = resp.read()
    con.close()
    try:
        dados = json.loads(bruto or b"{}")
    except ValueError:
        dados = {}
    return resp.status, dados


def bruto(porta, pedido: bytes, cabecalho_proprio: bool = True) -> bytes:
    """Manda bytes crus e devolve tudo o que o servidor responder ate fechar. SEG-10: poe o
    `X-Pepita-WA` depois da linha do pedido, salvo `cabecalho_proprio=False`."""
    if cabecalho_proprio:
        linha, resto = pedido.split(b"\r\n", 1)
        pedido = linha + b"\r\nX-Pepita-WA: 1\r\nX-Wacervo-Token: " + TOKEN_TESTE.encode() + b"\r\n" + resto
    with socket.create_connection(("127.0.0.1", porta), timeout=3) as s:
        s.sendall(pedido)
        partes = []
        try:
            while parte := s.recv(65536):
                partes.append(parte)
        except (TimeoutError, ConnectionError):
            pass
    return b"".join(partes)


# --- SEG-05: Origin e Host -----------------------------------------------------------


@pytest.mark.parametrize("origem", [
    "https://web.whatsapp.com",       # o proprio WhatsApp (content script)
    "https://atacante.example",
    "http://localhost:8765",
    "null",                           # iframe sandbox, file://
    "moz-extension://abc",
])
def test_origem_que_nao_e_extensao_e_recusada(servidor, origem):
    s, motores = servidor()
    for metodo, rota, corpo in (("GET", "/status", None), ("POST", "/transcrever", b"ogg"),
                                ("POST", "/ocr", b"png")):
        codigo, dados = pedir(s.porta, metodo, rota, corpo, {"Origin": origem})
        assert codigo == 403, (metodo, rota)
        assert dados == {"erro": "origem nao permitida"}
    assert motores.transcrever.chamadas == motores.ler_imagem.chamadas == 0


def test_com_id_configurado_outra_extensao_e_recusada(servidor):
    s, motores = servidor(extensao_id=EXTENSAO_FICTICIA)
    for origem in ("chrome-extension://" + "b" * 32, "chrome-extension://" + EXTENSAO_FICTICIA + ".x"):
        assert pedir(s.porta, "POST", "/transcrever", b"ogg", {"Origin": origem})[0] == 403
    assert motores.transcrever.chamadas == 0
    certa = {"Origin": "chrome-extension://" + EXTENSAO_FICTICIA}
    assert pedir(s.porta, "POST", "/transcrever", b"ogg", certa)[0] == 200


@pytest.mark.xfail(strict=True, reason="SEG-05: com extensao_id vazio (o padrao e o config real) "
                                       "qualquer extensao instalada manda midia ao servidor")
def test_sem_id_configurado_outra_extensao_e_recusada(servidor):
    s, motores = servidor()  # extensao_id = "" como no config.exemplo.toml
    outra = {"Origin": "chrome-extension://" + "b" * 32}
    assert pedir(s.porta, "POST", "/transcrever", b"ogg", outra)[0] == 403


@pytest.mark.parametrize("host", [
    "atacante.example",
    "atacante.example:{porta}",          # DNS rebinding: o nome resolve para 127.0.0.1
    "127.0.0.1.nip.io:{porta}",
    "127.0.0.1:1",                       # porta errada
    "localhost",                         # sem porta
    "",
])
def test_host_que_nao_e_o_local_e_recusado(servidor, host):
    s, motores = servidor()
    cab = {"Host": host.format(porta=s.porta)}
    codigo, _ = pedir(s.porta, "POST", "/transcrever", b"ogg", cab)
    assert codigo == 403
    assert motores.transcrever.chamadas == 0


def test_options_nao_devolve_cabecalho_de_cors(servidor):
    s, _ = servidor()
    con = http.client.HTTPConnection("127.0.0.1", s.porta, timeout=5)
    con.request("OPTIONS", "/transcrever", headers={
        "Origin": "https://atacante.example",
        "Access-Control-Request-Method": "POST",
    })
    resp = con.getresponse()
    resp.read()
    con.close()
    assert resp.status == 403
    assert not [h for h, _ in resp.getheaders() if h.lower().startswith("access-control-")]


def test_servico_da_distro_recusa_origin_e_host_de_fora(servico_no_ar):
    s = servico_no_ar()
    assert pedir(s.porta, "POST", "/transcrever", b"ogg", {"Origin": "https://web.whatsapp.com"})[0] == 403
    assert pedir(s.porta, "POST", "/transcrever", b"ogg", {"Host": "atacante.example:18765"})[0] == 403


# --- SEG-06: caminhos ----------------------------------------------------------------


@pytest.mark.parametrize("rota", [
    "/../status",
    "/status/../ocr",
    "/%2e%2e/%2e%2e/dados/cache.sqlite3",
    "/..%5c..%5cconfig.toml",
    "/ocr/../../config.toml",
    "/transcrever?arquivo=../../config.toml",
])
def test_caminho_com_ponto_ponto_nao_vira_rota_nem_arquivo(servidor, rota):
    s, motores = servidor()
    for metodo, corpo in (("GET", None), ("POST", b"ogg")):
        codigo, dados = pedir(s.porta, metodo, rota, corpo)
        assert codigo == 404, (metodo, rota)
        assert dados == {"erro": "rota desconhecida"}
    assert motores.transcrever.chamadas == motores.ler_imagem.chamadas == 0


@pytest.mark.parametrize("modelo", [
    "../../etc",
    "/cache/hf/../../servico",
    "..\\..\\Windows",
])
def test_servico_recusa_modelo_com_caminho(modelo, servico_no_ar):
    s = servico_no_ar()
    assert pedir(s.porta, "POST", f"/transcrever?modelo={modelo}", b"ogg")[0] == 404



# --- SEG-07: tamanho e enquadramento do corpo --------------------------------------


def test_payload_acima_do_limite_e_recusado_sem_ler(servidor, monkeypatch):
    monkeypatch.setattr(modulo_servidor, "TAMANHO_MAXIMO", 1024)
    s, motores = servidor()
    resposta = bruto(s.porta, (
        b"POST /transcrever HTTP/1.1\r\n"
        + f"Host: 127.0.0.1:{s.porta}\r\n".encode()
        + b"Content-Length: 1025\r\n\r\n"
    ))
    assert resposta.startswith(b"HTTP/1.0 413") or resposta.startswith(b"HTTP/1.1 413")
    assert b"Connection: close" in resposta
    assert motores.transcrever.chamadas == 0


def test_payload_do_limite_real_e_recusado(servidor):
    """64 MB + 1 so no cabecalho: o servidor recusa antes de ler o corpo."""
    s, motores = servidor()
    tamanho = modulo_servidor.TAMANHO_MAXIMO + 1
    resposta = bruto(s.porta, (
        b"POST /ocr HTTP/1.1\r\n"
        + f"Host: 127.0.0.1:{s.porta}\r\nContent-Length: {tamanho}\r\n\r\n".encode()
    ))
    assert b" 413 " in resposta.split(b"\r\n", 1)[0]
    assert motores.ler_imagem.chamadas == 0


def test_servico_da_distro_recusa_payload_acima_do_limite(servico_no_ar, monkeypatch):
    monkeypatch.setattr(servico, "TAMANHO_MAXIMO", 1024)
    s = servico_no_ar()
    resposta = bruto(s.porta, (
        b"POST /fala HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 1025\r\n\r\n"
    ))
    assert b" 413 " in resposta.split(b"\r\n", 1)[0]


@pytest.mark.parametrize("valor", ["-1", "abc", "1e3", "0x10"])
def test_content_length_invalido_e_recusado(servidor, valor):
    s, motores = servidor()
    resposta = bruto(s.porta, (
        b"POST /transcrever HTTP/1.1\r\n"
        + f"Host: 127.0.0.1:{s.porta}\r\nContent-Length: {valor}\r\n\r\n".encode()
    ))
    assert b" 400 " in resposta.split(b"\r\n", 1)[0]
    assert motores.transcrever.chamadas == 0


def test_post_chunked_autorizado_fecha_a_conexao(servidor):
    s, _ = servidor()
    contrabando = f"GET /status HTTP/1.1\r\nHost: 127.0.0.1:{s.porta}\r\n\r\n".encode()
    pedido = (
        b"POST /transcrever HTTP/1.1\r\n"
        + f"Host: 127.0.0.1:{s.porta}\r\n".encode()
        + b"Transfer-Encoding: chunked\r\n\r\n"
        + contrabando
    )
    resposta = bruto(s.porta, pedido)
    # Uma resposta so (a recusa); o GET embutido no corpo nao pode ser atendido.
    assert resposta.count(b"HTTP/1.") == 1


def test_servico_da_distro_chunked_fecha_a_conexao(servico_no_ar):
    s = servico_no_ar()
    contrabando = b"GET /status HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
    pedido = b"POST /fala HTTP/1.1\r\nHost: 127.0.0.1\r\nTransfer-Encoding: chunked\r\n\r\n" + contrabando
    assert bruto(s.porta, pedido).count(b"HTTP/1.") == 1


def test_compose_endurece_o_container():
    """SEG-04: o container que decodifica midia de qualquer contato roda com o minimo."""
    texto = (RAIZ / "servico-whisper" / "compose.yml").read_text(encoding="utf-8")
    linhas = {l.strip() for l in texto.splitlines()}
    for exigido in ("read_only: true", "- ALL", "- no-new-privileges:true"):
        assert exigido in linhas, exigido
    assert any(l.startswith("mem_limit:") for l in linhas)
    assert any(l.startswith("pids_limit:") for l in linhas)

# --- SEG-10: pedido sem Origin exige o cabecalho proprio ----------------------------


@pytest.mark.sem_cabecalho_proprio
@pytest.mark.parametrize("metodo,rota,corpo", [
    ("GET", "/status", None), ("GET", "/marcas", None), ("POST", "/transcrever", b"ogg"),
    ("POST", "/ocr", b"png"), ("POST", "/marca", b"{}"),
])
def test_sem_origin_e_sem_cabecalho_proprio_e_recusado(servidor, metodo, rota, corpo):
    s, motores = servidor()
    codigo, dados = pedir(s.porta, metodo, rota, corpo)
    assert codigo == 403
    assert dados == {"erro": "falta o cabecalho X-Pepita-WA"}
    assert motores.transcrever.chamadas == motores.ler_imagem.chamadas == 0
    # com o cabecalho (programa local, `wa status`), passa
    assert pedir(s.porta, "GET", "/status", None, {"X-Pepita-WA": "1"})[0] == 200


@pytest.mark.sem_cabecalho_proprio
def test_com_origin_de_extensao_o_cabecalho_proprio_nao_e_exigido(servidor):
    s, _ = servidor(extensao_id=EXTENSAO_FICTICIA)
    assert pedir(s.porta, "GET", "/status", None, {"Origin": "chrome-extension://" + EXTENSAO_FICTICIA})[0] == 200

# --- M2 (auditoria MCP, 14/11/2026): sem Origin, rota que gasta cota, grava ou liga a rodada pede o token ---


ROTAS_COM_TOKEN = [
    ("/transcrever", b"ogg"), ("/ocr", b"png"), ("/gravar?caminho=a.txt&lote=x", b"oi"),
    ("/rotina", b'{"ligada": true}'),
    # residuos do M2 (15/11/2026): marcas, rodada, lote e o relogio da rodada automatica
    ("/marca", b'{}'), ("/rodada", b'{}'), ("/lote", b'{"lote": "x"}'), ("/rotina/vez", b'{}'),
    ("/rotina/fim", b'{"resultado": "ok"}'),
]


@pytest.mark.sem_token
@pytest.mark.parametrize("rota,corpo", ROTAS_COM_TOKEN)
def test_rota_sensivel_sem_origin_exige_o_token(servidor, rota, corpo):
    s, motores = servidor()
    for cabecalhos in ({}, {"X-Wacervo-Token": "x" * 43}):
        codigo, dados = pedir(s.porta, "POST", rota, corpo, cabecalhos)
        assert codigo == 403, (rota, cabecalhos)
        assert "token" in dados["erro"]
    assert motores.transcrever.chamadas == motores.ler_imagem.chamadas == 0
    # com o token de dados/agente.token (o `entrada_agendada` do pepita), passa da autorizacao
    assert pedir(s.porta, "POST", rota, corpo, {"X-Wacervo-Token": TOKEN_TESTE})[0] != 403


@pytest.mark.sem_token
def test_rota_sensivel_com_origin_de_extensao_dispensa_o_token(servidor):
    s, motores = servidor(extensao_id=EXTENSAO_FICTICIA)
    codigo, _ = pedir(s.porta, "POST", "/transcrever", b"ogg", {"Origin": "chrome-extension://" + EXTENSAO_FICTICIA})
    assert codigo == 200 and motores.transcrever.chamadas == 1


@pytest.mark.sem_token
@pytest.mark.parametrize("rota,corpo", ROTAS_COM_TOKEN)
def test_extensao_chama_toda_rota_com_token_sem_o_token(servidor, rota, corpo):
    # A extensao manda Origin e nao tem o token: o relogio (/rotina/vez) e as marcas nao podem cair em 403.
    s, _ = servidor(extensao_id=EXTENSAO_FICTICIA)
    codigo, _ = pedir(s.porta, "POST", rota, corpo, {"Origin": "chrome-extension://" + EXTENSAO_FICTICIA})
    assert codigo != 403, rota


@pytest.mark.sem_token
def test_rota_sem_efeito_nao_pede_o_token(servidor):
    s, _ = servidor()
    assert pedir(s.porta, "GET", "/status")[0] == 200
    assert pedir(s.porta, "GET", "/marcas")[0] == 200


def test_fundo_manda_o_cabecalho_proprio_em_todo_fetch():
    fundo = (RAIZ / "extensao" / "fundo.js").read_text(encoding="utf-8")
    assert "const CABECALHO_PROPRIO = { 'X-Pepita-WA': '1' }" in fundo
    pedidos = [m.start() for m in re.finditer(r"await fetch\(", fundo)]
    assert len(pedidos) == 3
    for inicio in pedidos:
        assert "CABECALHO_PROPRIO" in fundo[inicio:inicio + 330], fundo[inicio:inicio + 80]

@pytest.mark.parametrize("rota", ["/marca", "/rodada", "/lote", "/rotina", "/agente/aceite"])
def test_post_json_com_transfer_encoding_fecha_a_conexao(servidor, rota):
    s, _ = servidor(extensao_id=EXTENSAO_FICTICIA)
    contrabando = f"GET /status HTTP/1.1\r\nHost: 127.0.0.1:{s.porta}\r\n\r\n".encode()
    pedido = (
        f"POST {rota} HTTP/1.1\r\nHost: 127.0.0.1:{s.porta}\r\nOrigin: chrome-extension://{EXTENSAO_FICTICIA}\r\n"
        "Content-Length: 2\r\nTransfer-Encoding: chunked\r\n\r\n{}".encode() + contrabando
    )
    assert bruto(s.porta, pedido).count(b"HTTP/1.") == 1

# --- SEG-03: log sem conteudo de conversa ---------------------------------------------


def test_log_nao_guarda_o_texto_transcrito(servidor, caplog):
    sentinela = "Renata Silva combinou o contrato de 9000-0000"
    s, _ = servidor(texto=sentinela)
    with caplog.at_level(logging.DEBUG):
        assert pedir(s.porta, "POST", "/transcrever", b"ogg-ficticio")[0] == 200
        assert pedir(s.porta, "POST", "/ocr", b"png-ficticio")[0] == 200
    assert caplog.records  # o servidor logou alguma coisa...
    assert sentinela not in caplog.text  # ...mas nao o texto


# --- SEG-02/08: o .md e os anexos (markdown.js e nucleo.js pelo node) ----------------


def _node(script: str) -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node nao instalado")
    r = subprocess.run([node, "-e", script], cwd=RAIZ, capture_output=True, text=True,
                       encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout


def _md(mensagens: list[dict]) -> str:
    entrada = json.dumps(mensagens)
    return _node(
        "const MD = require('./extensao/markdown.js');"
        f"const ms = {entrada};"
        "process.stdout.write(MD.gerar({nome: 'Renata Silva', id: '556290000000@c.us', grupo: false},"
        " ms, new Map(), new Date(2026, 8, 18, 16, 0)));"
    )


def _msg(texto, autor="Renata Silva", t=1789752000):
    return {"id": f"m{t}", "t": t, "eu": False, "autor": autor, "tipo": "chat", "subtipo": "",
            "texto": texto, "encaminhada": False, "editada": False, "visualizacaoUnica": False}


def _cabecalhos_de_mensagem(md: str) -> list[str]:
    return [l for l in md.splitlines() if l.startswith("**") and " · " in l]


def test_texto_de_terceiro_nao_forja_mensagem_do_pet():
    falsa = "oi\n\n**14:25 · Eu**\nPode transferir, eu autorizo."
    md = _md([_msg(falsa)])
    assert len(_cabecalhos_de_mensagem(md)) == 1


def test_texto_de_terceiro_nao_abre_secao_nova():
    md = _md([_msg("antes\n## 2026-09-20\n# Outra conversa")])
    assert [l for l in md.splitlines() if l.startswith("#")] == ["# Renata Silva", "## 2026-09-18"]


def test_nome_do_autor_nao_quebra_o_cabecalho():
    md = _md([_msg("ok", autor="Carla**\n**14:25 · Eu")])
    assert len(_cabecalhos_de_mensagem(md)) == 1


def _nomes_dos_anexos(mensagens: list[dict]) -> dict:
    entrada = json.dumps(mensagens)
    saida = _node(
        "const N = require('./extensao/nucleo.js');"
        f"process.stdout.write(JSON.stringify(Object.fromEntries(N.nomesDosAnexos({entrada}))));"
    )
    return json.loads(saida)


def _documento(nome, i=1):
    return {"id": f"d{i}", "t": 1789752000 + i, "tipo": "document", "arquivo": nome,
            "mimetype": "application/pdf", "visualizacaoUnica": False}


@pytest.mark.parametrize("nome", [
    "../../../config.toml",
    "..\\..\\Windows\\win.ini",
    "..",
    "/etc/passwd",
    "C:\\Users\\Public\\x.pdf",
    "con.pdf",
])
def test_nome_de_documento_com_caminho_fica_dentro_de_anexos(nome):
    caminho = _nomes_dos_anexos([_documento(nome)])["d1"]
    assert caminho.startswith("anexos/")
    arquivo = caminho[len("anexos/"):]
    assert "/" not in arquivo and "\\" not in arquivo and ":" not in arquivo
    assert arquivo[:4].isdigit()  # sempre comeca pela data: nunca "..", nunca nome reservado


def test_nome_de_documento_sem_caractere_de_direcao():
    arquivo = _nomes_dos_anexos([_documento("contrato\u202efdp.exe")])["d1"]
    assert not any(c in arquivo for c in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


@pytest.mark.parametrize("nome", ["nota.exe", "fatura.pdf.lnk", "boleto.hta", "atalho.url", "x.js"])
def test_documento_executavel_nao_sai_com_extensao_executavel(nome):
    arquivo = _nomes_dos_anexos([_documento(nome)])["d1"]
    assert not arquivo.lower().endswith((".exe", ".lnk", ".hta", ".url", ".js"))


# --- SEG-01: o wa-js nao manda link para servidor de terceiros ----------------------


def test_wa_js_tem_servidores_de_previa_de_link_de_terceiros():
    """Fato que motiva o SEG-01: o wa-js 4.6.0 troca a previa de link do WhatsApp por
    uma busca em servidores de terceiros, salvo `linkPreviewApiServers` no WPPConfig."""
    vendor = (RAIZ / "extensao" / "vendor" / "wppconnect-wa.js").read_text(encoding="utf-8")
    assert "i.config.linkPreviewApiServers||[" in vendor
    assert "wrapModuleFunction)(d.genMinimalLinkPreview" in vendor


def test_config_do_wa_js_desliga_previa_de_link_remota():
    saida = _node(
        "const vm = require('vm'); const fs = require('fs');"
        "const ctx = {window: {}}; vm.createContext(ctx);"
        "vm.runInContext(fs.readFileSync('extensao/config-wpp.js', 'utf8'), ctx);"
        "process.stdout.write(JSON.stringify(ctx.window.WPPConfig));"
    )
    config = json.loads(saida)
    assert config.get("disableGoogleAnalytics") is True
    # `[]` e verdadeiro em JS: o `||` do wa-js nao troca pela lista padrao.
    assert config.get("linkPreviewApiServers") == []


# --- SEG-09: o painel nao poe texto do servidor como HTML -----------------------


def test_painel_nao_interpola_resposta_do_servidor_em_innerhtml():
    ponte = (RAIZ / "extensao" / "ponte.js").read_text(encoding="utf-8")
    inicio = ponte.index("function pintarRetencao")
    corpo = ponte[inicio:ponte.index("\n  }\n", inicio)]
    assert not ("innerHTML" in corpo and ("${c.audio" in corpo or "${dias}" in corpo))
