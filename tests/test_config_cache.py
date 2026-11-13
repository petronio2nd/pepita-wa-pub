import re
import sqlite3

import pytest

from wacervo.cache import LEGADO, Cache, assinatura, assinatura_whisper, sha256
from wacervo.config import (
    Config, ConfigGemini, ConfigOcr, ConfigTranscricao, ErroDeConfig, carregar,
)
from wacervo.ocr import LeitorDeTexto, aplicar_minimo, caracteres_uteis


def test_sem_config_toml_valem_os_padroes(tmp_path):
    cfg = carregar(raiz=tmp_path)
    assert cfg.servidor.porta == 8765
    assert cfg.transcricao.modelo == "deepdml/faster-whisper-large-v3-turbo-ct2"
    assert cfg.transcricao.servico == "http://127.0.0.1:18765"
    assert cfg.ocr.idioma == "pt-BR"
    assert cfg.caminho_cache == tmp_path / "dados" / "cache.sqlite3"


def test_config_toml_sobrepoe_so_o_que_tem(tmp_path):
    (tmp_path / "config.toml").write_text(
        '[servidor]\nporta = 9000\n[transcricao]\nmodelo = "medium"\n', encoding="utf-8"
    )
    cfg = carregar(raiz=tmp_path)
    assert cfg.servidor.porta == 9000
    assert cfg.transcricao.modelo == "medium"
    assert cfg.transcricao.idioma == "pt"


def test_config_aceita_servico_local_em_outra_porta(tmp_path):
    (tmp_path / "config.toml").write_text(
        '[transcricao]\nservico = "http://localhost:19000/"\n', encoding="utf-8"
    )
    assert carregar(raiz=tmp_path).transcricao.servico == "http://localhost:19000/"


def test_config_toml_quebrado(tmp_path):
    (tmp_path / "config.toml").write_text("[servidor\n", encoding="utf-8")
    with pytest.raises(ErroDeConfig):
        carregar(raiz=tmp_path)


def test_cache_guarda_por_sha_tipo_e_assinatura(tmp_path):
    cache = Cache(tmp_path / "c.sqlite3")
    sha = sha256(b"ogg")
    assert cache.obter(sha, "audio", "a1") is None
    cache.guardar(sha, "audio", "a1", "texto", "whisper")
    assert cache.obter(sha, "audio", "a1") == "texto"
    assert cache.obter(sha, "imagem", "a1") is None
    assert cache.obter(sha, "audio", "a2") is None
    assert cache.contagem() == {"audio": 1}
    cache.fechar()


def test_assinatura_muda_com_a_configuracao_que_muda_o_texto(tmp_path):
    w = {}
    base = Config(raiz=tmp_path, transcricao=ConfigTranscricao(**w))
    outro_modelo = Config(raiz=tmp_path, transcricao=ConfigTranscricao(modelo="medium", **w))
    outro_idioma = Config(raiz=tmp_path, transcricao=ConfigTranscricao(idioma="en", **w))
    so_porta = Config(raiz=tmp_path, transcricao=ConfigTranscricao(descarregar_apos_min=0, **w))
    outro_limiar = Config(raiz=tmp_path, ocr=ConfigOcr(minimo_caracteres=3))
    outro_ocr = Config(raiz=tmp_path, ocr=ConfigOcr(idioma="en-US"))

    audio = assinatura_whisper(base)
    assert assinatura_whisper(outro_modelo) != audio
    assert assinatura_whisper(outro_idioma) != audio
    assert assinatura_whisper(so_porta) == audio  # nao muda o texto
    assert assinatura_whisper(Config(raiz=tmp_path, transcricao=ConfigTranscricao(**w),
                             ocr=ConfigOcr(minimo_caracteres=3))) == audio

    imagem = assinatura(base, "imagem")
    # O limiar so filtra a resposta: o cache guarda a leitura inteira.
    assert assinatura(outro_limiar, "imagem") == imagem
    assert assinatura(outro_ocr, "imagem") != imagem
    assert assinatura(outro_modelo, "imagem") == imagem


def test_assinatura_do_gemini_segue_modelo_e_prompt_e_nao_o_whisper(tmp_path):
    def cfg(**g):
        return Config(raiz=tmp_path, transcricao=ConfigTranscricao(gemini=ConfigGemini(**g)))

    base = assinatura(Config(raiz=tmp_path), "audio")
    assert base == assinatura(cfg(), "audio")
    assert base.split("|")[1] == "gemini"
    assert assinatura(cfg(modelo="gemini-3.8-flash-high"), "audio") != base
    assert assinatura(cfg(termos=("RENAJUD",)), "audio") != base
    assert assinatura(cfg(contexto="Audio de reuniao."), "audio") != base
    assert assinatura(cfg(paralelo=1, prazo_s=60, pausa_min=5), "audio") == base
    # Trocar o whisper (a reserva) nao transcreve de novo o que o Gemini fez.
    outro_whisper = Config(raiz=tmp_path, transcricao=ConfigTranscricao(modelo="medium"))
    assert assinatura(outro_whisper, "audio") == base
    assert assinatura_whisper(outro_whisper) != assinatura_whisper(Config(raiz=tmp_path))


def test_config_do_gemini(tmp_path):
    arquivo = tmp_path / "config.toml"
    arquivo.write_text(
        '[transcricao]\nmotor = "gemini"\nreserva = false\n'
        '[transcricao.gemini]\nmodelo = "gemini-3.8-flash-medium"\nparalelo = 2\n'
        'termos = ["RENAJUD", " Projudi "]\ncontexto = ""\n',
        encoding="utf-8",
    )
    t = carregar(arquivo, tmp_path).transcricao
    assert (t.motor, t.reserva) == ("gemini", False)
    assert t.gemini.modelo == "gemini-3.8-flash-medium"
    assert t.gemini.paralelo == 2
    assert t.gemini.termos == ("RENAJUD", "Projudi")
    assert t.gemini.contexto == ""
    assert t.gemini.prazo_s == ConfigGemini().prazo_s
    assert t.gemini.guardar_agy_dias == 90
    assert Config(raiz=tmp_path).transcricao.motor == "gemini"
    arquivo.write_text('[transcricao.gemini]\nguardar_agy_dias = 0\n', encoding="utf-8")
    assert carregar(arquivo, tmp_path).transcricao.gemini.guardar_agy_dias == 0


@pytest.mark.parametrize("trecho", [
    '[transcricao]\nmotor = "openai"\n',
    '[transcricao]\nreserva = "sim"\n',
    '[transcricao.gemini]\ntermos = "RENAJUD"\n',
    '[transcricao.gemini]\ntermos = ["RENAJUD", ""]\n',
    '[transcricao.gemini]\nparalelo = 0\n',
    '[transcricao.gemini]\nguardar_agy_dias = -1\n',
    '[transcricao]\ngemini = 3\n',
])
def test_config_do_gemini_recusa_valor_ruim(tmp_path, trecho):
    arquivo = tmp_path / "config.toml"
    arquivo.write_text(trecho, encoding="utf-8")
    with pytest.raises(ErroDeConfig):
        carregar(arquivo, tmp_path)


def _base_esquema_1(caminho):
    con = sqlite3.connect(caminho)
    con.execute(
        "CREATE TABLE resultados (sha256 TEXT NOT NULL, tipo TEXT NOT NULL, texto TEXT NOT NULL,"
        " motor TEXT NOT NULL, criado TEXT NOT NULL, PRIMARY KEY (sha256, tipo))"
    )
    con.execute(
        "INSERT INTO resultados VALUES (?, 'audio', 'Vi sim, ta tudo certo.', 'whisper-large-v3',"
        " '2020-01-01T12:00:00+00:00')",
        (sha256(b"ogg"),),
    )
    con.commit()
    con.close()


def test_migracao_preserva_a_base_antiga_sem_reutilizar(tmp_path):
    caminho = tmp_path / "c.sqlite3"
    _base_esquema_1(caminho)

    cache = Cache(caminho)
    sha = sha256(b"ogg")
    assinatura_atual = assinatura(Config(raiz=tmp_path), "audio")
    assert cache.obter(sha, "audio", assinatura_atual) is None  # nao volta em silencio
    assert cache.obter(sha, "audio", LEGADO) == "Vi sim, ta tudo certo."  # mas nao se perdeu
    assert cache.contagem() == {}
    assert cache.contagem_legado() == 1
    cache.fechar()

    # Abrir de novo nao migra duas vezes.
    cache = Cache(caminho)
    assert cache.contagem_legado() == 1
    cache.fechar()


def test_limpar_so_legado_e_por_idade(tmp_path):
    caminho = tmp_path / "c.sqlite3"
    _base_esquema_1(caminho)
    cache = Cache(caminho)
    cache.guardar(sha256(b"png"), "imagem", "i1", "Reuniao na quinta", "windows-ocr")

    assert cache.contar(so_legado=True) == 1
    assert cache.contar(dias=1) == 1  # so a linha legada e antiga
    assert cache.limpar(so_legado=True) == 1
    assert cache.contagem_legado() == 0
    assert cache.contagem() == {"imagem": 1}
    assert cache.limpar() == 1
    assert cache.contagem() == {}
    cache.fechar()


@pytest.mark.parametrize(
    "conteudo, trecho",
    [
        ('[servidor]\nporta = "8765"\n', "porta precisa ser um numero inteiro"),
        ("[servidor]\nporta = true\n", "porta precisa ser um numero inteiro"),
        ("[servidor]\nporta = 70000\n", "porta precisa estar entre"),
        ("[transcricao]\ndescarregar_apos_min = -1\n", "descarregar_apos_min"),
        ('[transcricao]\ndispositivo = "gpu"\n', "dispositivo precisa ser um de"),
        ("[transcricao]\nmodelo = 3\n", "modelo precisa ser texto"),
        # A midia nao sai da maquina: so loopback, com porta, sem caminho.
        ('[transcricao]\nservico = "http://192.168.0.10:18765"\n', "servico precisa ser http://127.0.0.1"),
        ('[transcricao]\nservico = "https://127.0.0.1:18765"\n', "servico precisa ser"),
        ('[transcricao]\nservico = "http://127.0.0.1"\n', "servico precisa ser"),
        ('[transcricao]\nservico = "http://127.0.0.1:18765/x"\n', "servico precisa ser"),
        ('[transcricao]\nservico = "http://127.0.0.1.exemplo.com:18765"\n', "servico precisa ser"),
        ("[ocr]\nminimo_caracteres = 1.5\n", "minimo_caracteres"),
        ('servidor = "x"\n', "[servidor] precisa ser uma secao"),
    ],
)
def test_config_com_valor_errado_explica_o_campo(tmp_path, conteudo, trecho):
    (tmp_path / "config.toml").write_text(conteudo, encoding="utf-8")
    with pytest.raises(ErroDeConfig, match=re.escape(trecho)):
        carregar(raiz=tmp_path)


def test_caracteres_uteis_ignora_pontuacao():
    assert caracteres_uteis("  - . ; ") == 0
    assert caracteres_uteis("às 15h") == 5


def test_leitor_devolve_a_leitura_inteira_sem_limiar(tmp_path, monkeypatch):
    leitor = LeitorDeTexto(carregar(raiz=tmp_path))

    async def ler_falso(dados, idioma):
        return dados.decode()

    monkeypatch.setattr("wacervo.ocr._ler", ler_falso)
    assert leitor(b"OK 2") == "OK 2"


def test_texto_curto_e_registrado_a_parte_em_vez_de_sumir():
    assert aplicar_minimo("Reuniao marcada para quinta", 15) == {"texto": "Reuniao marcada para quinta"}
    assert aplicar_minimo("R$ 40", 15) == {"texto": "", "curto": "R$ 40"}
    assert aplicar_minimo("  ", 15) == {"texto": ""}
    assert aplicar_minimo("OK", 0) == {"texto": "OK"}


def test_config_de_retencao_do_cache(tmp_path):
    assert carregar(raiz=tmp_path).cache.manter_dias == 0
    (tmp_path / "config.toml").write_text("[cache]\nmanter_dias = 30\n", encoding="utf-8")
    assert carregar(raiz=tmp_path).cache.manter_dias == 30
    (tmp_path / "config.toml").write_text("[cache]\nmanter_dias = -1\n", encoding="utf-8")
    with pytest.raises(ErroDeConfig, match="manter_dias"):
        carregar(raiz=tmp_path)


def test_expirar_cache_apaga_so_o_que_passou_do_prazo(tmp_path, monkeypatch):
    from wacervo import cli

    caminho = tmp_path / "c.sqlite3"
    _base_esquema_1(caminho)  # linha de 2020
    cache = Cache(caminho)
    cache.guardar(sha256(b"png"), "imagem", "i1", "Reuniao na quinta", "windows-ocr")

    class Parar(Exception):
        pass

    def dormir(_):
        raise Parar

    monkeypatch.setattr(cli.time, "sleep", dormir)
    with pytest.raises(Parar):
        cli._expirar_cache(cache, 30)
    assert cache.contagem_legado() == 0
    assert cache.contagem() == {"imagem": 1}
    cache.fechar()


def test_config_antiga_nao_reativa_reserva(tmp_path):
    (tmp_path / "config.toml").write_text('[transcricao]\nreserva = true\n', encoding="utf-8")
    assert carregar(raiz=tmp_path).transcricao.reserva is False
    assert ConfigTranscricao(reserva=True).reserva is False
    with pytest.raises(ErroDeConfig, match="gemini"):
        ConfigTranscricao(motor="whisper")
