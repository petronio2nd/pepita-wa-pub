"""Rodada automatica do Exportar todas (0.16.0): relogio no `wa servir`, extensao
pergunta a cada minuto, Chrome aberto pela rotina so quando a extensao some."""

import http.client
import json

import pytest

from wacervo.cache import Cache
from wacervo.config import Config, ConfigRotina, carregar
from wacervo.rotina import ESPERA_EXTENSAO_S, ESPERA_MAXIMA_S, Rotina, achar_chrome
from wacervo.servidor import Motores, ServidorLocal


class Relogio:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t

    def andar(self, s):
        self.t += s


@pytest.fixture
def criar(tmp_path):
    def fazer(cfg=ConfigRotina(), lancar_erro=None):
        relogio = Relogio()
        lancados = []

        def lancar(c):
            lancados.append(c)
            return lancar_erro

        r = Rotina(cfg, tmp_path / "dados" / "rotina.json", lancar=lancar, relogio=relogio)
        return r, relogio, lancados

    return fazer


def test_primeira_rodada_depois_de_ligar_e_a_cada_30_min(criar):
    r, relogio, lancados = criar()
    r.vez()  # extensao viva
    r.passo()
    assert r.vez() == {"rodar": False}
    relogio.andar(5 * 60)
    r.passo()
    vez = r.vez()
    assert vez["rodar"] and vez["abriuChrome"] is False
    assert r.vez() == {"rodar": False}  # entregue uma vez so
    relogio.andar(29 * 60)
    r.passo()
    assert r.vez() == {"rodar": False}
    relogio.andar(60)
    r.passo()
    assert r.vez()["rodar"]
    assert lancados == []


def test_sem_sinal_da_extensao_abre_o_chrome_uma_vez(criar):
    r, relogio, lancados = criar()
    relogio.andar(5 * 60)
    r.passo()
    assert lancados == []  # espera a extensao antes: o computador pode ter acabado de acordar
    relogio.andar(ESPERA_EXTENSAO_S)
    r.passo()
    r.passo()
    assert len(lancados) == 1
    # O Chrome que a rotina abriu pergunta: a rodada vai com o aviso para fechar no fim.
    relogio.andar(40)
    vez = r.vez()
    assert vez["rodar"] is True and vez["abriuChrome"] is True
    # A proxima rodada, com a extensao viva, nao abre outro Chrome.
    relogio.andar(30 * 60)
    r.vez()
    r.passo()
    relogio.andar(ESPERA_EXTENSAO_S)
    r.passo()
    assert len(lancados) == 1


def test_extensao_que_responde_logo_nao_abre_o_chrome(criar):
    r, relogio, lancados = criar()
    relogio.andar(5 * 60)
    r.passo()
    relogio.andar(60)
    assert r.vez()["abriuChrome"] is False
    relogio.andar(ESPERA_EXTENSAO_S)
    r.passo()
    assert lancados == []


def test_rodada_que_ninguem_pega_vira_pulada(criar):
    r, relogio, lancados = criar(ConfigRotina(abrir_chrome=False))
    relogio.andar(5 * 60)
    r.passo()
    relogio.andar(ESPERA_MAXIMA_S + 1)
    r.passo()
    assert lancados == []
    assert r.situacao()["ultima"]["resultado"] == "pulada"
    assert r.vez() == {"rodar": False}


def test_chrome_que_nao_abre_vira_falha(criar):
    r, relogio, _ = criar(lancar_erro="chrome.exe nao encontrado; ponha o caminho em [rotina] chrome")
    relogio.andar(5 * 60)
    r.passo()
    relogio.andar(ESPERA_EXTENSAO_S)
    r.passo()
    u = r.situacao()["ultima"]
    assert u["resultado"] == "falha" and "chrome.exe" in u["detalhe"]
    assert r.vez() == {"rodar": False}


def test_desligada_nao_roda_e_fica_desligada_depois_de_reiniciar(criar, tmp_path):
    r, relogio, lancados = criar()
    r.ligar(False)
    relogio.andar(3 * 3600)
    r.passo()
    assert r.vez() == {"rodar": False} and lancados == []
    assert r.situacao()["proxima"] is None
    outra = Rotina(ConfigRotina(), tmp_path / "dados" / "rotina.json", lancar=lambda c: None)
    assert outra.ligada is False


def test_fim_guarda_o_resultado_e_recusa_resultado_estranho(criar, tmp_path):
    r, relogio, _ = criar()
    relogio.andar(5 * 60)
    r.passo()
    ident = r.vez()["id"]
    r.fim({"id": ident, "resultado": "ok", "detalhe": "2 exportada(s), 300 sem novidade"})
    assert r.situacao()["ultima"]["detalhe"] == "2 exportada(s), 300 sem novidade"
    with pytest.raises(ValueError):
        r.fim({"resultado": "qualquer"})
    salvo = json.loads((tmp_path / "dados" / "rotina.json").read_text(encoding="utf-8"))
    assert salvo["ultima"]["resultado"] == "ok"


def test_fim_atrasado_de_outra_rodada_nao_apaga_o_resultado(criar):
    r, relogio, _ = criar()
    relogio.andar(5 * 60)
    r.passo()
    velha = r.vez()["id"]
    relogio.andar(30 * 60)
    r.passo()
    nova = r.vez()["id"]
    assert r.fim({"id": velha, "resultado": "falha", "detalhe": "a aba fechou"})["ignorado"] is True
    assert r.situacao()["ultima"] is None
    r.fim({"id": nova, "resultado": "ok"})
    assert r.situacao()["ultima"]["resultado"] == "ok"
    # O mesmo fim duas vezes nao conta de novo.
    assert r.fim({"id": nova, "resultado": "falha"}).get("ignorado") is True


def test_achar_chrome_configurado_que_nao_existe(tmp_path):
    assert achar_chrome(str(tmp_path / "chrome.exe")) is None
    exe = tmp_path / "chrome.exe"
    exe.write_bytes(b"")
    assert achar_chrome(str(exe)) == str(exe)


def test_config_da_rotina(tmp_path):
    (tmp_path / "config.toml").write_text(
        '[exportar]\nraiz = "E:/whatsapp"\n[rotina]\nintervalo_min = 60\nperfil = "Profile 1"\n', encoding="utf-8")
    cfg = carregar(raiz=tmp_path)
    assert cfg.exportar.raiz == "E:/whatsapp"
    assert cfg.rotina.intervalo_min == 60 and cfg.rotina.perfil == "Profile 1" and cfg.rotina.ligada is True


def test_rotas_da_rotina_pelo_servidor(tmp_path):
    relogio = Relogio()
    rotina = Rotina(ConfigRotina(), tmp_path / "rotina.json", lancar=lambda c: None, relogio=relogio)
    cfg = Config(raiz=tmp_path)
    cache = Cache(cfg.caminho_cache)
    s = ServidorLocal(cfg, cache, Motores(transcrever=lambda d: "", ler_imagem=lambda d: ""), porta=0, rotina=rotina)
    s.em_segundo_plano()
    ext = {"Origin": "chrome-extension://" + "a" * 32, "Content-Type": "application/json"}

    def pedir(metodo, rota, corpo=None, cab=ext):
        con = http.client.HTTPConnection("127.0.0.1", s.porta, timeout=5)
        con.request(metodo, rota, body=json.dumps(corpo).encode() if corpo is not None else None, headers=cab)
        resp = con.getresponse()
        dados = json.loads(resp.read() or b"{}")
        con.close()
        return resp.status, dados

    try:
        assert pedir("GET", "/rotina")[1]["ligada"] is True
        assert pedir("POST", "/rotina/vez", {})[1] == {"rodar": False}
        relogio.andar(5 * 60)
        rotina.passo()
        ident = pedir("POST", "/rotina/vez", {})[1]["id"]
        assert pedir("POST", "/rotina/fim", {"id": ident, "resultado": "pulada", "detalhe": "transcricao parada"})[0] == 200
        assert pedir("GET", "/rotina")[1]["ultima"]["detalhe"] == "transcricao parada"
        assert pedir("POST", "/rotina", {"ligada": "sim"})[0] == 400
        assert pedir("POST", "/rotina", {"ligada": False})[1]["ligada"] is False
        # Pagina web nao liga, nao desliga e nao pede rodada.
        fora = {"Origin": "https://web.whatsapp.com", "Content-Type": "application/json"}
        assert pedir("POST", "/rotina", {"ligada": True}, fora)[0] == 403
        assert pedir("POST", "/rotina/vez", {}, fora)[0] == 403
        assert rotina.ligada is False
    finally:
        s.shutdown()
        s.server_close()
        cache.fechar()
