import http.client
import json
import threading
import time

import pytest

from wacervo.cache import Cache
from wacervo.config import Config, ConfigGemini, ConfigOcr, ConfigServidor, ConfigTranscricao
from wacervo.servidor import DESCARTE_MAXIMO, Motores, ServidorLocal


class MotorFalso:
    def __init__(self, texto="Vi sim, ta tudo certo.", erro=None):
        self.texto = texto
        self.erro = erro
        self.chamadas = []

    def __call__(self, dados):
        self.chamadas.append(dados)
        if self.erro:
            raise self.erro
        return self.texto


@pytest.fixture
def ambiente(tmp_path):
    def criar(extensao_id="", transcrever=None, ler_imagem=None):
        cfg = Config(raiz=tmp_path, servidor=ConfigServidor(extensao_id=extensao_id))
        cache = Cache(cfg.caminho_cache)
        motores = Motores(
            transcrever=transcrever or MotorFalso(),
            ler_imagem=ler_imagem or MotorFalso("Reuniao na quinta as 15h"),
        )
        servidor = ServidorLocal(cfg, cache, motores, porta=0)
        servidor.em_segundo_plano()
        criados.append((servidor, cache))
        return servidor, motores

    criados = []
    yield criar
    for servidor, cache in criados:
        servidor.shutdown()
        servidor.server_close()
        cache.fechar()


def pedir(servidor, metodo, rota, corpo=None, cabecalhos=None):
    con = http.client.HTTPConnection("127.0.0.1", servidor.porta, timeout=5)
    con.request(metodo, rota, body=corpo, headers=cabecalhos or {})
    resp = con.getresponse()
    dados = json.loads(resp.read() or b"{}")
    con.close()
    return resp.status, dados


def test_status(ambiente):
    servidor, _ = ambiente()
    codigo, dados = pedir(servidor, "GET", "/status")
    assert codigo == 200
    assert dados["ok"] is True
    assert dados["cache"] == {}


def test_transcreve_e_usa_cache_na_segunda_vez(ambiente):
    servidor, motores = ambiente()
    codigo, dados = pedir(servidor, "POST", "/transcrever", b"ogg-falso")
    assert codigo == 200
    assert dados == {"texto": "Vi sim, ta tudo certo.", "cache": False, "motor": "whisper"}

    codigo, dados = pedir(servidor, "POST", "/transcrever", b"ogg-falso")
    assert dados == {"texto": "Vi sim, ta tudo certo.", "cache": True, "motor": "whisper"}
    assert len(motores.transcrever.chamadas) == 1


def test_ocr_tem_cache_separado_do_audio(ambiente):
    servidor, motores = ambiente()
    pedir(servidor, "POST", "/transcrever", b"mesmos-bytes")
    codigo, dados = pedir(servidor, "POST", "/ocr", b"mesmos-bytes")
    assert codigo == 200
    assert dados == {"texto": "Reuniao na quinta as 15h", "cache": False, "motor": "windows-ocr"}
    assert len(motores.ler_imagem.chamadas) == 1


def test_falha_do_motor_vira_500_e_nao_entra_no_cache(ambiente):
    ruim = MotorFalso(erro=RuntimeError("audio corrompido"))
    servidor, _ = ambiente(transcrever=ruim)
    codigo, dados = pedir(servidor, "POST", "/transcrever", b"ogg-quebrado")
    assert codigo == 500
    assert "audio corrompido" in dados["erro"]
    pedir(servidor, "POST", "/transcrever", b"ogg-quebrado")
    assert len(ruim.chamadas) == 2


def test_corpo_vazio_e_rota_desconhecida(ambiente):
    servidor, _ = ambiente()
    assert pedir(servidor, "POST", "/transcrever", b"")[0] == 400
    assert pedir(servidor, "POST", "/outra", b"x")[0] == 404
    assert pedir(servidor, "GET", "/outra")[0] == 404


def test_pagina_web_e_recusada(ambiente):
    servidor, motores = ambiente()
    codigo, _ = pedir(
        servidor, "POST", "/transcrever", b"ogg", {"Origin": "https://exemplo.com"}
    )
    assert codigo == 403
    assert motores.transcrever.chamadas == []
    assert pedir(servidor, "OPTIONS", "/transcrever")[0] == 403


def test_host_de_fora_e_recusado(ambiente):
    """DNS rebinding: o nome resolve para 127.0.0.1, mas o Host denuncia."""
    servidor, _ = ambiente()
    codigo, _ = pedir(servidor, "GET", "/status", cabecalhos={"Host": "atacante.com:8765"})
    assert codigo == 403


def test_extensao_aceita_e_id_restringe(ambiente):
    servidor, _ = ambiente()
    origem = {"Origin": "chrome-extension://abcdefghijklmnop"}
    assert pedir(servidor, "GET", "/status", cabecalhos=origem)[0] == 200

    restrito, _ = ambiente(extensao_id="outraextensao")
    assert pedir(restrito, "GET", "/status", cabecalhos=origem)[0] == 403
    certa = {"Origin": "chrome-extension://outraextensao"}
    assert pedir(restrito, "GET", "/status", cabecalhos=certa)[0] == 200


def test_status_traz_estado_do_motor_e_retencao(ambiente):
    servidor, _ = ambiente()
    _, dados = pedir(servidor, "GET", "/status")
    assert dados["whisper"] == "em_espera"
    assert dados["cache_legado"] == 0
    assert dados["ocr_minimo"] == 15
    assert dados["cache_manter_dias"] == 0
    assert dados["cache_arquivo"].endswith("cache.sqlite3")


def test_ocr_curto_volta_marcado_e_limiar_nao_reprocessa(tmp_path):
    cache = Cache(tmp_path / "dados" / "cache.sqlite3")
    leitor = MotorFalso("R$ 40")
    respostas = []
    for minimo in (15, 3):
        cfg = Config(raiz=tmp_path, ocr=ConfigOcr(minimo_caracteres=minimo))
        servidor = ServidorLocal(cfg, cache, Motores(MotorFalso(), leitor), porta=0)
        servidor.em_segundo_plano()
        try:
            respostas.append(pedir(servidor, "POST", "/ocr", b"png-falso")[1])
        finally:
            servidor.shutdown()
            servidor.server_close()
    assert respostas[0] == {"texto": "", "curto": "R$ 40", "cache": False, "motor": "windows-ocr"}
    assert respostas[1] == {"texto": "R$ 40", "cache": True, "motor": "windows-ocr"}
    assert len(leitor.chamadas) == 1
    cache.fechar()


def test_mudar_configuracao_nao_devolve_o_texto_antigo(tmp_path):
    """Mesmo arquivo, outro modelo: processa de novo em vez de reaproveitar."""
    cache = Cache(tmp_path / "dados" / "cache.sqlite3")
    motor = MotorFalso()
    for modelo, esperado in (("turbo", False), ("turbo", True), ("medium", False)):
        cfg = Config(raiz=tmp_path, transcricao=ConfigTranscricao(gemini=ConfigGemini(modelo=modelo)))
        servidor = ServidorLocal(cfg, cache, Motores(motor, MotorFalso()), porta=0)
        servidor.em_segundo_plano()
        try:
            _, dados = pedir(servidor, "POST", "/transcrever", b"ogg-falso")
        finally:
            servidor.shutdown()
            servidor.server_close()
        assert dados["cache"] is esperado, modelo
    assert len(motor.chamadas) == 2
    cache.fechar()


class MotorLento(MotorFalso):
    """Segura o motor ate o teste soltar, para os pedidos se sobreporem."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.entrou = threading.Event()
        self.solta = threading.Event()

    def __call__(self, dados):
        self.chamadas.append(dados)
        self.entrou.set()
        assert self.solta.wait(5)
        if self.erro:
            raise self.erro
        return self.texto


def _em_paralelo(servidor, n, corpo=b"ogg-igual"):
    resultados = [None] * n

    def um(i):
        resultados[i] = pedir(servidor, "POST", "/transcrever", corpo)

    threads = [threading.Thread(target=um, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    return threads, resultados


def test_pedidos_simultaneos_da_mesma_midia_rodam_o_motor_uma_vez(ambiente):
    lento = MotorLento()
    servidor, _ = ambiente(transcrever=lento)
    threads, resultados = _em_paralelo(servidor, 3)
    assert lento.entrou.wait(5)
    time.sleep(0.2)  # os outros dois chegam e ficam esperando o primeiro
    lento.solta.set()
    for t in threads:
        t.join(5)
    assert len(lento.chamadas) == 1
    assert all(r[0] == 200 and r[1]["texto"] == "Vi sim, ta tudo certo." for r in resultados)
    assert servidor.app._em_andamento == {}


def test_falha_compartilhada_libera_a_chave_para_nova_tentativa(ambiente):
    lento = MotorLento(erro=RuntimeError("GPU sem memoria"))
    servidor, _ = ambiente(transcrever=lento)
    threads, resultados = _em_paralelo(servidor, 2)
    assert lento.entrou.wait(5)
    time.sleep(0.2)
    lento.solta.set()
    for t in threads:
        t.join(5)
    assert [r[0] for r in resultados] == [500, 500]
    assert len(lento.chamadas) == 1
    assert servidor.app._em_andamento == {}

    lento.erro = None
    codigo, dados = pedir(servidor, "POST", "/transcrever", b"ogg-igual")
    assert codigo == 200 and dados["cache"] is False
    assert len(lento.chamadas) == 2


def test_recusa_com_corpo_descarta_sem_confundir_o_proximo_pedido(ambiente):
    """HTTP/1.1: o corpo de um POST recusado nao pode virar o pedido seguinte."""
    servidor, _ = ambiente()
    con = http.client.HTTPConnection("127.0.0.1", servidor.porta, timeout=5)
    con.request("POST", "/outra", body=b"GET /status HTTP/1.1\r\n\r\n")
    resp = con.getresponse()
    assert resp.status == 404
    assert resp.getheader("Connection") is None
    resp.read()
    sock = con.sock
    # Mesma conexao: o corpo foi descartado, e o pedido seguinte sai inteiro.
    con.request("POST", "/transcrever", body=b"ogg-falso")
    resp = con.getresponse()
    assert resp.status == 200
    assert json.loads(resp.read())["texto"] == "Vi sim, ta tudo certo."
    assert con.sock is sock
    con.close()


def test_origem_recusada_com_corpo_descarta_e_responde(ambiente):
    """Fechar com o corpo no buffer faz o Windows mandar RST (WinError 10053)."""
    servidor, motores = ambiente()
    con = http.client.HTTPConnection("127.0.0.1", servidor.porta, timeout=5)
    con.request("POST", "/transcrever", body=b"x" * 100_000, headers={"Origin": "https://exemplo.com"})
    resp = con.getresponse()
    assert resp.status == 403
    assert resp.getheader("Connection") is None
    assert json.loads(resp.read())["erro"] == "origem nao permitida"
    assert motores.transcrever.chamadas == []
    con.close()


def _recusa_sem_corpo_enviado(servidor, cabecalhos):
    """Manda so os cabecalhos de um POST de origem recusada e le a resposta."""
    con = http.client.HTTPConnection("127.0.0.1", servidor.porta, timeout=5)
    con.putrequest("POST", "/transcrever")
    con.putheader("Origin", "https://exemplo.com")
    for nome, valor in cabecalhos.items():
        con.putheader(nome, valor)
    con.endheaders()
    resp = con.getresponse()
    resultado = resp.status, resp.getheader("Connection")
    con.close()
    return resultado


def test_recusa_de_corpo_acima_de_1_mb_fecha_sem_ler(ambiente):
    servidor, _ = ambiente()
    tamanho = str(DESCARTE_MAXIMO + 1)
    assert _recusa_sem_corpo_enviado(servidor, {"Content-Length": tamanho}) == (403, "close")


def test_recusa_de_corpo_chunked_fecha_sem_ler(ambiente):
    servidor, _ = ambiente()
    assert _recusa_sem_corpo_enviado(servidor, {"Transfer-Encoding": "chunked"}) == (403, "close")


def test_cliente_que_desiste_nao_vira_traceback(ambiente, capsys):
    """O fundo.js aborta fetches ao cancelar; isso nao pode sujar o console."""
    servidor, _ = ambiente()
    con = http.client.HTTPConnection("127.0.0.1", servidor.porta, timeout=5)
    con.connect()
    # Manda meio pedido e some: do outro lado vira ConnectionResetError.
    con.sock.sendall(b"POST /transcrever HTTP/1.1\r\nContent-Length: 100\r\n\r\n")
    con.sock.close()
    time.sleep(0.2)
    assert "Traceback" not in capsys.readouterr().err


def test_erro_de_verdade_continua_aparecendo(ambiente, capsys):
    servidor, _ = ambiente()
    servidor.handle_error(None, ("127.0.0.1", 0))  # sem excecao em curso
    assert "127.0.0.1" in capsys.readouterr().err


def test_envio_interrompido_nao_chega_no_motor(ambiente):
    """Cancelar uma exportacao aborta o fetch: o pedaco que chegou nao vale."""
    servidor, motores = ambiente()
    con = http.client.HTTPConnection("127.0.0.1", servidor.porta, timeout=5)
    con.connect()
    con.sock.sendall(
        b"POST /transcrever HTTP/1.1\r\n"
        b"Host: 127.0.0.1:%d\r\n"
        b"Content-Length: 500\r\n\r\nogg-pela-metade" % servidor.porta
    )
    con.sock.shutdown(1)  # fecha so o envio: o servidor le EOF antes dos 500.
    time.sleep(0.3)
    con.close()
    assert motores.transcrever.chamadas == []  # o modelo nem foi carregado


# ---------- marcas do "Exportar todas" ----------

def pedir_json(servidor, rota, corpo):
    return pedir(servidor, "POST", rota, json.dumps(corpo).encode("utf-8"),
                 {"Content-Type": "application/json"})


MARCA = {"id": "556290000000@c.us", "nome": "Renata Silva", "pasta": "renata-silva",
         "primeira": 1_700_000_000, "ultima": 1_700_100_000, "completaAntes": False}


def test_marcas_comeca_vazio(ambiente):
    servidor, _ = ambiente()
    assert pedir(servidor, "GET", "/marcas") == (200, {"conversas": {}, "rodada": None, "importadas": {}})


def test_importadas_chegam_a_extensao_sem_inventar_datas(ambiente):
    servidor, _ = ambiente()
    servidor.app.marcas.registrar_importadas(['199999999999999@lid'], ['FICTICIO1'],
                                           'renata', 'iphone.md', 'a' * 64)
    assert pedir(servidor, 'GET', '/marcas')[0] == 409
    assert pedir(servidor, 'GET', '/marcas?versao=0.28.0')[0] == 409
    assert pedir(servidor, 'GET', '/marcas?versao=')[0] == 409
    from wacervo import __version__
    codigo, dados = pedir(servidor, 'GET', '/marcas?versao='+__version__)
    assert codigo == 200
    assert dados['conversas'] == {}
    assert dados['importadas'] == {'199999999999999@lid': {'pasta': 'renata'}}
    codigo, recibos = pedir(servidor, 'GET', '/importadas?id=199999999999999%40lid')
    assert codigo == 200
    assert recibos['ids'] == ['FICTICIO1']
    assert pedir_json(servidor, '/marca', MARCA)[0] == 409
    corpo=json.dumps(MARCA).encode('utf-8')
    assert pedir(servidor,'POST','/marca',corpo,{'Content-Type':'application/json','X-Pepita-WA-Versao':__version__})[0]==200
    assert pedir(servidor,'POST','/gravar?caminho=renata/x.md&lote=t1',b'x')[0]==409


def test_marca_junta_o_trecho_e_guarda_a_pasta_da_primeira_vez(ambiente):
    servidor, _ = ambiente()
    assert pedir_json(servidor, "/marca", MARCA)[0] == 200
    # Rodada seguinte: o historico antigo ate o comeco e o que chegou depois.
    codigo, marca = pedir_json(servidor, "/marca", {
        **MARCA, "nome": "Renata S.", "pasta": "outra", "primeira": 1_600_000_000,
        "ultima": 1_800_000_000, "completaAntes": True,
    })
    assert codigo == 200
    assert (marca["pasta"], marca["primeira"], marca["ultima"], marca["completaAntes"]) == (
        "renata-silva", 1_600_000_000, 1_800_000_000, True)
    # Rodada que so pega o fim nao desfaz o comeco nem o "completa".
    pedir_json(servidor, "/marca", {**MARCA, "primeira": 1_800_000_001, "ultima": 1_900_000_000})
    _, dados = pedir(servidor, "GET", "/marcas")
    m = dados["conversas"]["556290000000@c.us"]
    assert (m["nome"], m["primeira"], m["ultima"], m["completaAntes"]) == (
        "Renata Silva", 1_600_000_000, 1_900_000_000, True)
    # Trecho solto, sem encostar no marcado: recusado, a marca fica igual.
    codigo, _ = pedir_json(servidor, "/marca", {**MARCA, "primeira": 1_950_000_000, "ultima": 1_960_000_000})
    assert codigo == 400
    _, dados = pedir(servidor, "GET", "/marcas")
    assert dados["conversas"]["556290000000@c.us"]["ultima"] == 1_900_000_000


@pytest.mark.parametrize("ruim", [
    {"id": "sem-arroba"}, {"pasta": "../fora"}, {"pasta": "a\\b"}, {"pasta": ""},
    {"pasta": "a\x08"}, {"pasta": "a:b"},
    {"primeira": "ontem"}, {"primeira": 2_000_000_000}, {"ultima": True},
])
def test_marca_recusa_campo_invalido(ambiente, ruim):
    servidor, _ = ambiente()
    codigo, dados = pedir_json(servidor, "/marca", {**MARCA, **ruim})
    assert codigo == 400 and dados["erro"]
    assert pedir(servidor, "GET", "/marcas")[1]["conversas"] == {}


def test_marca_recusa_json_quebrado_e_lista(ambiente):
    servidor, _ = ambiente()
    assert pedir(servidor, "POST", "/marca", b"{nao e json")[0] == 400
    assert pedir_json(servidor, "/marca", [MARCA])[0] == 400


def test_rodada_fica_a_ultima(ambiente):
    servidor, _ = ambiente()
    pedir_json(servidor, "/rodada", {"total": 3, "exportadas": 1, "sem_novidade": 2, "interrompida": True})
    pedir_json(servidor, "/rodada", {"total": 3, "exportadas": 0, "sem_novidade": 3})
    rodada = pedir(servidor, "GET", "/marcas")[1]["rodada"]
    assert rodada["quando"]
    assert {k: rodada[k] for k in ("total", "sem_novidade", "falhas", "interrompida")} == {
        "total": 3, "sem_novidade": 3, "falhas": 0, "interrompida": False}


def test_marcas_pede_origem_de_extensao(ambiente):
    servidor, _ = ambiente()
    codigo, _ = pedir(servidor, "GET", "/marcas", cabecalhos={"Origin": "https://web.whatsapp.com"})
    assert codigo == 403
    corpo = json.dumps(MARCA).encode("utf-8")
    codigo, _ = pedir(servidor, "POST", "/marca", corpo, {"Origin": "https://web.whatsapp.com"})
    assert codigo == 403
