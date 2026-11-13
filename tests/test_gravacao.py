"""Gravacao do Exportar todas pelo `wa servir` (0.16.0): so dentro da raiz, so
pela extensao, nunca por cima do que ja existe. Pasta temporaria, dados ficticios."""

import http.client
import json
import os
from urllib.parse import quote

import pytest

from wacervo.cache import Cache
from wacervo.config import Config, ConfigExportar
from wacervo.gravacao import ErroDeGravacao, Gravador, caminho_relativo
from wacervo.servidor import Motores, ServidorLocal

EXTENSAO = {"Origin": "chrome-extension://" + "a" * 32}


@pytest.fixture
def raiz(tmp_path):
    r = tmp_path / "70-WHATSAPP"
    r.mkdir()
    return r


@pytest.fixture
def servidor(tmp_path, raiz):
    cfg = Config(raiz=tmp_path / "projeto", exportar=ConfigExportar(raiz=str(raiz)))
    cache = Cache(cfg.caminho_cache)
    motores = Motores(transcrever=lambda d: "", ler_imagem=lambda d: "")
    s = ServidorLocal(cfg, cache, motores, porta=0)
    s.em_segundo_plano()
    yield s
    s.shutdown()
    s.server_close()
    cache.fechar()


def pedir(s, metodo, rota, corpo=None, cabecalhos=EXTENSAO):
    con = http.client.HTTPConnection("127.0.0.1", s.porta, timeout=5)
    con.request(metodo, rota, body=corpo, headers=cabecalhos)
    resp = con.getresponse()
    dados = json.loads(resp.read() or b"{}")
    con.close()
    return resp.status, dados


def gravar(s, caminho, corpo, lote="t1", extra="", cabecalhos=EXTENSAO):
    return pedir(s, "POST", f"/gravar?caminho={quote(caminho, safe='')}&lote={lote}{extra}", corpo, cabecalhos)


def test_grava_o_md_e_o_anexo_dentro_da_raiz(servidor, raiz):
    assert gravar(servidor, "renata-silva/anexos/2026-09-17_142509_imagem.jpg", b"jpg")[0] == 200
    codigo, r = gravar(servidor, "renata-silva/renata-silva_2026-09-17.md", "# Renata Silva\n".encode(), extra="&livre=1")
    assert codigo == 200 and r["caminho"] == "renata-silva/renata-silva_2026-09-17.md"
    assert (raiz / "renata-silva" / "renata-silva_2026-09-17.md").read_text(encoding="utf-8") == "# Renata Silva\n"
    assert (raiz / "renata-silva" / "anexos" / "2026-09-17_142509_imagem.jpg").read_bytes() == b"jpg"
    assert pedir(servidor, "GET", "/arquivo?caminho=" + quote("renata-silva/anexos/2026-09-17_142509_imagem.jpg"))[1] == \
        {"existe": True, "tamanho": 3}


def test_md_com_nome_tomado_ganha_sufixo_e_anexo_nao_e_sobrescrito(servidor, raiz):
    (raiz / "carla-mendes" / "anexos").mkdir(parents=True)
    (raiz / "carla-mendes" / "carla-mendes_2026-10-01.md").write_text("antigo", encoding="utf-8")
    (raiz / "carla-mendes" / "anexos" / "foto.jpg").write_bytes(b"antiga")
    codigo, r = gravar(servidor, "carla-mendes/carla-mendes_2026-10-01.md", b"novo", extra="&livre=1")
    assert codigo == 200 and r["caminho"] == "carla-mendes/carla-mendes_2026-10-01-2.md"
    assert gravar(servidor, "carla-mendes/anexos/foto.jpg", b"nova")[0] == 409
    assert (raiz / "carla-mendes" / "carla-mendes_2026-10-01.md").read_text(encoding="utf-8") == "antigo"
    assert (raiz / "carla-mendes" / "anexos" / "foto.jpg").read_bytes() == b"antiga"


@pytest.mark.parametrize("caminho", [
    "../fora.md",
    "renata-silva/../../fora.md",
    "renata-silva/..",
    "/etc/fora.md",
    "C:/fora.md",
    "C:\\fora.md",
    "renata-silva\\..\\..\\fora.md",
    "renata-silva/sub/fora.md",          # 3 partes so com anexos/ no meio
    "a/anexos/b/c.jpg",
    "fora.md",                            # solto na raiz: o Pepita leria como conversa
    "renata-silva/CON",
    "renata-silva/nul.txt",
    "renata-silva/x.md.parcial",
    "renata-silva/fim.",
    "renata-silva/a\x00b.md",
    "renata-silva//x.md",
])
def test_caminho_que_sai_da_raiz_e_recusado(servidor, raiz, caminho):
    codigo, _ = gravar(servidor, caminho, b"x")
    assert codigo in (400, 403)
    fora = raiz.parent
    assert not (fora / "fora.md").exists()
    assert sorted(p.name for p in fora.iterdir()) == ["70-WHATSAPP", "projeto"]
    assert not any(raiz.rglob("*"))


def test_juncao_dentro_da_raiz_que_aponta_para_fora_e_recusada(servidor, raiz, tmp_path):
    fora = tmp_path / "fora"
    fora.mkdir()
    try:
        os.symlink(fora, raiz / "atalho", target_is_directory=True)
    except OSError:
        pytest.skip("sem permissao para criar atalho")
    assert gravar(servidor, "atalho/x.md", b"x")[0] == 403
    assert not any(fora.iterdir())


@pytest.mark.parametrize("origem", ["https://web.whatsapp.com", "https://atacante.example", "null"])
def test_origin_que_nao_e_extensao_nao_grava(servidor, raiz, origem):
    cab = {"Origin": origem}
    assert gravar(servidor, "renata-silva/x.md", b"x", cabecalhos=cab)[0] == 403
    assert pedir(servidor, "GET", "/arquivo?caminho=renata-silva/x.md", cabecalhos=cab)[0] == 403
    assert pedir(servidor, "POST", "/lote", b'{"lote": "t1", "desfazer": true}', cabecalhos=cab)[0] == 403
    assert not any(raiz.rglob("*"))


def test_host_de_fora_nao_grava(servidor, raiz):
    cab = {**EXTENSAO, "Host": f"atacante.example:{servidor.porta}"}
    assert gravar(servidor, "renata-silva/x.md", b"x", cabecalhos=cab)[0] == 403
    assert not any(raiz.rglob("*"))


def test_corpo_chunked_nao_vira_arquivo_vazio(servidor, raiz):
    con = http.client.HTTPConnection("127.0.0.1", servidor.porta, timeout=5)
    con.putrequest("POST", "/gravar?caminho=renata-silva%2Fx.md&lote=t1")
    con.putheader("Origin", EXTENSAO["Origin"])
    con.putheader("Transfer-Encoding", "chunked")
    con.endheaders()
    con.send(b"3\r\nabc\r\n0\r\n\r\n")
    resp = con.getresponse()
    resp.read()
    con.close()
    assert resp.status == 411
    assert not any(raiz.rglob("*"))


def test_md_grande_em_partes_segue_o_nome_livre(servidor, raiz):
    (raiz / "renata-silva").mkdir()
    (raiz / "renata-silva" / "x.md").write_text("antigo", encoding="utf-8")
    codigo, r = gravar(servidor, "renata-silva/x.md", b"aaa", extra="&livre=1&fim=0")
    assert codigo == 200 and r["caminho"] == "renata-silva/x-2.md"
    # A ponte manda as outras partes no nome que voltou.
    assert gravar(servidor, r["caminho"], b"bbb", extra="&deslocamento=3&fim=1")[0] == 200
    assert (raiz / "renata-silva" / "x-2.md").read_bytes() == b"aaabbb"
    assert (raiz / "renata-silva" / "x.md").read_text(encoding="utf-8") == "antigo"


def test_anexo_em_partes_so_aparece_no_fim(servidor, raiz):
    caminho = "renata-silva/anexos/video.mp4"
    assert gravar(servidor, caminho, b"aaa", extra="&fim=0")[0] == 200
    assert not (raiz / caminho).exists()
    assert gravar(servidor, caminho, b"bbb", extra="&deslocamento=9&fim=0")[0] == 409  # parte fora de ordem
    assert gravar(servidor, caminho, b"bbb", extra="&deslocamento=3&fim=1")[1]["tamanho"] == 6
    assert (raiz / caminho).read_bytes() == b"aaabbb"
    assert not list(raiz.rglob("*.parcial"))


def test_desfazer_o_lote_tira_so_o_que_ele_criou(servidor, raiz):
    (raiz / "carla-mendes" / "anexos").mkdir(parents=True)
    (raiz / "carla-mendes" / "anexos" / "antiga.jpg").write_bytes(b"antiga")
    gravar(servidor, "carla-mendes/anexos/nova.jpg", b"nova", lote="c1")
    gravar(servidor, "renata-silva/anexos/foto.jpg", b"foto", lote="c1")
    gravar(servidor, "renata-silva/anexos/meio.mp4", b"meio", lote="c1", extra="&fim=0")
    gravar(servidor, "renata-silva/outra.md", b"md", lote="c2")
    codigo, r = pedir(servidor, "POST", "/lote", b'{"lote": "c1", "desfazer": true}')
    assert codigo == 200 and r["removidos"] == 2
    assert sorted(p.relative_to(raiz).as_posix() for p in raiz.rglob("*")) == [
        "carla-mendes", "carla-mendes/anexos", "carla-mendes/anexos/antiga.jpg",
        "renata-silva", "renata-silva/outra.md",
    ]
    # Concluir so esquece: nada sai.
    assert pedir(servidor, "POST", "/lote", b'{"lote": "c2", "desfazer": false}')[1]["removidos"] == 0
    assert (raiz / "renata-silva" / "outra.md").exists()


def test_raiz_que_nao_existe_nao_e_criada(tmp_path):
    g = Gravador(tmp_path / "nao-existe")
    with pytest.raises(ErroDeGravacao) as erro:
        g.gravar("renata-silva/x.md", b"x", lote="t1")
    assert erro.value.codigo == 409
    assert not (tmp_path / "nao-existe").exists()


def test_status_diz_onde_grava(servidor, raiz):
    assert pedir(servidor, "GET", "/status")[1]["exportar"] == {"raiz": str(raiz), "existe": True}


def test_rotas_antigas_continuam_sem_consulta(servidor):
    assert pedir(servidor, "POST", "/transcrever?caminho=x", b"ogg")[0] == 404
    assert pedir(servidor, "POST", "/gravar/../marca?caminho=a/b.md&lote=t", b"x")[0] == 404


def test_caminho_relativo_aceita_os_formatos_do_exportar_todas():
    assert caminho_relativo("renata-silva-556290000000/renata-silva_2026-09-17_a_2026-10-31.md") == (
        "renata-silva-556290000000", "renata-silva_2026-09-17_a_2026-10-31.md")
    assert caminho_relativo("renata-silva/anexos/2026-09-17_142509_Contrato social.pdf")[2] == \
        "2026-09-17_142509_Contrato social.pdf"
