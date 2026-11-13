"""Transcricao pelo Gemini (agy) com o VAD da distro, sem reserva, sem rede nem agy de verdade."""

import http.client
import json
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from wacervo.cache import (
    Cache, Prefixo, assinatura_gemini, prefixo_gemini,
)
from wacervo.config import Config, ConfigGemini, ConfigTranscricao
from wacervo.servidor import Motores, ServidorLocal
from wacervo.transcricao import (
    CotaEsgotada, EmPausa, ErroDeTranscricao, FalhaDoAgy, Gemini, Indisponivel, Resultado,
    SemLogin,
    TranscricaoGemini, _interpretar, limpar_conversas_agy, prompt_gemini, suspeito,
)


def _cfg(tmp_path, reserva=True, **g):
    agy = tmp_path / "agy.exe"
    agy.write_bytes(b"")
    return Config(raiz=tmp_path, transcricao=ConfigTranscricao(
        reserva=reserva, gemini=ConfigGemini(agy=str(agy), **g)))


def _proc(stdout="", stderr="", rc=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=rc)


def _sucesso(conteudo="fala", texto="O bloqueio pelo SISBAJUD saiu ontem."):
    return json.dumps({"status": "SUCCESS", "response": "...",
                       "structured_output": {"conteudo": conteudo, "transcricao": texto}})


# --- saida do agy ---------------------------------------------------------------


def test_interpreta_o_json_final_mesmo_com_andamento_antes():
    saida = "root agent idle; waiting up to 3m20s\n" + _sucesso(texto="  Vi sim.  ") + "\n"
    assert _interpretar(_proc(saida)) == ("fala", "Vi sim.")


def test_resposta_fora_do_esquema_e_falha_do_agy():
    saida = json.dumps({"status": "SUCCESS", "structured_output": {"conteudo": "risada"}})
    with pytest.raises(FalhaDoAgy, match="esquema"):
        _interpretar(_proc(saida))


def test_cota_login_e_falha_generica_se_distinguem():
    with pytest.raises(CotaEsgotada):
        _interpretar(_proc(json.dumps({"status": "ERROR", "error": "RESOURCE_EXHAUSTED: quota"}), rc=1))
    with pytest.raises(SemLogin):
        _interpretar(_proc("", "You are not logged into Antigravity.", rc=1))
    with pytest.raises(FalhaDoAgy, match="rc=2") as erro:
        _interpretar(_proc("", "panic: runtime error", rc=2))
    assert not isinstance(erro.value, Indisponivel)
    assert isinstance(CotaEsgotada("x"), Indisponivel)


def test_prompt_leva_contexto_e_termos_da_config(tmp_path):
    p = prompt_gemini(_cfg(tmp_path, contexto="Audio de reuniao.", termos=("RENAJUD", "Projudi")))
    assert "Contexto: Audio de reuniao." in p
    assert "RENAJUD, Projudi" in p
    assert "não acrescente nada" in p
    sem = prompt_gemini(_cfg(tmp_path, contexto="", termos=()))
    assert "Contexto" not in sem and "Termos" not in sem


def test_suspeito_pela_fala_medida():
    assert suspeito(" ".join(["palavra"] * 30), 1.0)
    assert not suspeito(" ".join(["palavra"] * 30), 6.0)  # 5 por segundo mais a folga
    assert not suspeito("", 0.0)


# --- chamada ao agy -------------------------------------------------------------


def test_chama_o_agy_numa_pasta_so_com_o_audio_e_apaga_depois(tmp_path):
    vistos = {}

    def rodar(comando, cwd, **kw):
        pasta = Path(cwd)
        vistos.update(comando=comando, arquivos=sorted(p.name for p in pasta.iterdir()),
                      pasta=pasta, kw=kw)
        return _proc(_sucesso())

    cfg = _cfg(tmp_path, modelo="gemini-3.8-flash-low", prazo_s=90)
    assert Gemini(cfg, rodar=rodar)(b"OggS-audio-falso") == ("fala", "O bloqueio pelo SISBAJUD saiu ontem.")
    c = vistos["comando"]
    assert vistos["arquivos"] == ["audio.opus"]
    assert not vistos["pasta"].exists()
    assert c[c.index("--model") + 1] == "gemini-3.8-flash-low"
    assert c[c.index("--mode") + 1] == "plan"
    assert "--sandbox" in c
    assert c[c.index("--print-timeout") + 1] == "90s"
    assert json.loads(c[c.index("--json-schema") + 1])["required"] == ["conteudo", "transcricao"]
    assert "audio.opus" in c[c.index("-p") + 1]
    assert vistos["kw"]["timeout"] == 120
    assert vistos["kw"]["stdin"] is subprocess.DEVNULL


def test_agy_que_estoura_o_prazo_e_falha_e_a_pasta_some(tmp_path):
    pastas = []

    def rodar(comando, cwd, **kw):
        pastas.append(Path(cwd))
        raise subprocess.TimeoutExpired(comando, kw["timeout"])

    with pytest.raises(FalhaDoAgy, match="nao respondeu"):
        Gemini(_cfg(tmp_path), rodar=rodar)(b"ID3-mp3")
    assert not pastas[0].exists()


def test_sem_agy_e_indisponivel(tmp_path):
    cfg = Config(raiz=tmp_path, transcricao=ConfigTranscricao(
        gemini=ConfigGemini(agy=str(tmp_path / "nao-existe.exe"))))
    with pytest.raises(Indisponivel, match="agy nao encontrado"):
        Gemini(cfg, rodar=lambda *a, **k: pytest.fail("nao devia rodar"))(b"OggS")


# --- orquestrador: VAD, Gemini, reserva -----------------------------------------


class DistroFalsa:
    def __init__(self, fala_s=8.0, estado="em_espera", erro=None):
        self.fala_s = fala_s
        self.estado = estado
        self.erro = erro
        self.whisper = []

    def fala(self, dados):
        if self.estado == "fora":
            raise Indisponivel("servico de transcricao fora do ar")
        return {"duracao_s": 9.0, "fala_s": self.fala_s, "trechos": 1}

    def __call__(self, dados):
        self.whisper.append(dados)
        return "texto do whisper"

    def situacao(self):
        return {"estado": self.estado, "erro": self.erro, "carregado": False}


class GeminiFalso:
    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.chamadas = 0

    def __call__(self, dados, pausado=None, pausar=None):
        self.chamadas += 1
        r = self.respostas.pop(0) if len(self.respostas) > 1 else self.respostas[0]
        if isinstance(r, Exception):
            raise r
        return r


class Relogio:
    agora = 1000.0

    def __call__(self):
        return self.agora


def _orq(tmp_path, gemini, distro=None, reserva=True, relogio=None):
    cfg = _cfg(tmp_path, reserva=reserva, pausa_min=30)
    return TranscricaoGemini(cfg, distro or DistroFalsa(), gemini, relogio or Relogio()), cfg


def test_sem_fala_nao_chama_o_gemini(tmp_path):
    g = GeminiFalso(("fala", "inventado"))
    tr, _ = _orq(tmp_path, g, DistroFalsa(fala_s=0.0))
    assert tr(b"ogg") == Resultado("", "vad:sem-fala")
    assert g.chamadas == 0


def test_fala_vai_ao_gemini_e_entra_com_a_assinatura_dele(tmp_path):
    tr, cfg = _orq(tmp_path, GeminiFalso(("fala", "Vi sim.")))
    r = tr(b"ogg")
    assert r == Resultado("Vi sim.", "agy:gemini-3.8-flash-low")
    assert tr.assinaturas() == [assinatura_gemini(cfg), prefixo_gemini(cfg)]


def test_gemini_que_nao_ouve_fala_devolve_vazio_sem_reserva(tmp_path):
    distro = DistroFalsa()
    tr, _ = _orq(tmp_path, GeminiFalso(("ruido", "")), distro)
    assert tr(b"ogg").texto == ""
    assert distro.whisper == []


def test_cota_pausa_sem_reserva_mesmo_com_config_antiga(tmp_path):
    relogio = Relogio()
    distro = DistroFalsa()
    g = GeminiFalso(CotaEsgotada("cota do agy esgotada"), ("fala", "Vi sim."))
    tr, cfg = _orq(tmp_path, g, distro, reserva=True, relogio=relogio)
    for audio in (b"ogg-1", b"ogg-2"):
        with pytest.raises(Indisponivel, match="cota"):
            tr(audio)
    assert g.chamadas == 1 and distro.whisper == []
    assert tr.assinaturas() == [assinatura_gemini(cfg), prefixo_gemini(cfg)]
    assert tr.situacao()["bloqueio"] and tr.situacao()["reserva"] is False
    relogio.agora += 31 * 60
    assert tr(b"ogg-3") == Resultado("Vi sim.", tr.nome)


def test_sem_reserva_cota_e_503_e_texto_suspeito_e_500(tmp_path):
    tr, _ = _orq(tmp_path, GeminiFalso(CotaEsgotada("cota")), reserva=False)
    with pytest.raises(Indisponivel):
        tr(b"ogg")
    assert tr.situacao()["bloqueio"] == "cota"

    tr, _ = _orq(tmp_path, GeminiFalso(("fala", " ".join(["bla"] * 80))), DistroFalsa(fala_s=2.0),
                 reserva=False)
    with pytest.raises(ErroDeTranscricao) as erro:
        tr(b"ogg")
    assert not isinstance(erro.value, Indisponivel)
    assert "suspeito" in str(erro.value)


@pytest.mark.parametrize("resposta, classe", [
    (FalhaDoAgy("agy falhou"), Indisponivel),
    (("fala", " ".join(["bla"] * 80)), ErroDeTranscricao),
    (SemLogin("sem login"), Indisponivel),
])
def test_falhas_nunca_chamam_whisper(tmp_path, resposta, classe):
    distro = DistroFalsa(fala_s=2.0)
    tr, _ = _orq(tmp_path, GeminiFalso(resposta), distro, reserva=True)
    with pytest.raises(classe):
        tr(b"ogg")
    assert distro.whisper == []


def test_falha_do_agy_nao_pausa_e_proximo_audio_tenta_gemini(tmp_path):
    g = GeminiFalso(FalhaDoAgy("agy falhou (rc=2)"), ("fala", "Vi sim."))
    tr, _ = _orq(tmp_path, g)
    with pytest.raises(Indisponivel):
        tr(b"ogg-1")
    assert tr.situacao()["erro"] == "agy falhou (rc=2)"
    assert tr(b"ogg-2").texto == "Vi sim."
    assert tr.situacao()["erro"] is None


def test_pausa_que_comeca_na_fila_nao_chama_o_agy(tmp_path):
    cfg = _cfg(tmp_path, paralelo=1)
    g = Gemini(cfg, rodar=lambda *a, **k: pytest.fail("em pausa: nao devia rodar"))
    with pytest.raises(EmPausa, match="cota"):
        g(b"OggS", lambda: "cota do agy esgotada")

    class GeminiQueEsperou:  # outro audio estourou a cota enquanto este esperava a vaga
        def __call__(self, dados, pausado, pausar=None):
            tr._pausar("cota do agy esgotada")
            raise EmPausa(pausado())

    distro = DistroFalsa()
    tr = TranscricaoGemini(cfg, distro, GeminiQueEsperou(), Relogio())
    with pytest.raises(Indisponivel, match="cota"):
        tr(b"ogg")
    assert distro.whisper == []
    assert tr.situacao()["erro"] == "cota do agy esgotada"  # o motivo da pausa, nao falha


COTA = '{"status": "ERROR", "error": "RESOURCE_EXHAUSTED: quota"}'


def test_cota_pausa_com_a_vaga_ainda_na_mao(tmp_path):
    # Revisao do Codex: se a pausa so entrasse depois de soltar a vaga, o audio que
    # esperava pegaria a vaga, veria o Gemini valendo e chamaria o agy de novo.
    cfg = _cfg(tmp_path, paralelo=1)
    g = Gemini(cfg, rodar=lambda *a, **k: _proc(stdout=COTA, rc=1))
    vagas_livres = []
    with pytest.raises(CotaEsgotada):
        g(b"OggS", lambda: None, lambda motivo: vagas_livres.append(g._vagas._value))
    assert vagas_livres == [0]
    assert g._vagas._value == 1


def test_cota_com_outro_audio_na_fila_chama_o_agy_uma_vez(tmp_path):
    cfg = _cfg(tmp_path, paralelo=1)
    entrou, soltar = threading.Event(), threading.Event()
    chamadas = []

    def rodar(comando, cwd, **kw):
        chamadas.append(cwd)
        entrou.set()
        assert soltar.wait(5)
        return _proc(stdout=COTA, rc=1)

    tr = TranscricaoGemini(cfg, DistroFalsa(), Gemini(cfg, rodar=rodar), Relogio())
    resultados = {}
    def tentar(chave):
        try:
            tr(chave.encode())
        except Indisponivel as erro:
            resultados[chave] = erro

    a = threading.Thread(target=tentar, args=("a",))
    a.start()
    assert entrou.wait(5)
    b = threading.Thread(target=tentar, args=("b",))
    b.start()
    time.sleep(0.3)  # b na fila da vaga
    soltar.set()
    a.join(5)
    b.join(5)
    assert len(chamadas) == 1
    assert sorted(resultados) == ["a", "b"]
    assert all(isinstance(r, Indisponivel) for r in resultados.values())


def test_distro_fora_bloqueia(tmp_path):
    tr, _ = _orq(tmp_path, GeminiFalso(("fala", "x")), DistroFalsa(estado="fora", erro="fora do ar"))
    assert tr.situacao()["bloqueio"] == "fora do ar"
    with pytest.raises(Indisponivel):
        tr(b"ogg")


# --- pelo wa servir -------------------------------------------------------------


def _pedir(servidor, rota, corpo):
    con = http.client.HTTPConnection("127.0.0.1", servidor.porta, timeout=5)
    con.request("POST", rota, body=corpo)
    resp = con.getresponse()
    dados = json.loads(resp.read() or b"{}")
    con.close()
    return resp.status, dados


@pytest.fixture
def servir(tmp_path):
    criados = []

    def criar(tr, cfg):
        cache = Cache(cfg.caminho_cache)
        motores = Motores(transcrever=tr, ler_imagem=lambda d: "", nome_transcricao=tr.nome,
                          situacao_transcricao=tr.situacao, assinaturas_audio=tr.assinaturas)
        s = ServidorLocal(cfg, cache, motores, porta=0)
        s.em_segundo_plano()
        criados.append((s, cache))
        return s

    yield criar
    for s, cache in criados:
        s.shutdown()
        s.server_close()
        cache.fechar()


def test_servidor_responde_503_com_o_motor_fora_e_500_com_falha_do_audio(tmp_path, servir):
    tr, cfg = _orq(tmp_path, GeminiFalso(CotaEsgotada("cota")), reserva=False)
    s = servir(tr, cfg)
    codigo, dados = _pedir(s, "/transcrever", b"ogg")
    assert codigo == 503 and "cota" in dados["erro"]

    tr.gemini = GeminiFalso(FalhaDoAgy("x"))
    tr._pausa_motivo = None  # sem reserva, falha do agy tambem e passageira
    assert _pedir(s, "/transcrever", b"ogg-2")[0] == 503


def test_servidor_sem_reserva_responde_503_ate_gemini_voltar(tmp_path, servir):
    relogio = Relogio()
    g = GeminiFalso(CotaEsgotada("cota"), ("fala", "Vi sim."))
    distro = DistroFalsa()
    tr, cfg = _orq(tmp_path, g, distro, relogio=relogio)
    s = servir(tr, cfg)
    assert _pedir(s, "/transcrever", b"ogg")[0] == 503
    assert _pedir(s, "/transcrever", b"ogg")[0] == 503
    assert distro.whisper == []
    relogio.agora += 31 * 60
    codigo, dados = _pedir(s, "/transcrever", b"ogg")
    assert codigo == 200 and dados["texto"] == "Vi sim." and dados["cache"] is False
    assert _pedir(s, "/transcrever", b"ogg")[1]["cache"] is True


def test_prefixo_casa_com_o_prompt_mais_recente_do_mesmo_modelo(tmp_path):
    cfg = _cfg(tmp_path)
    cache = Cache(cfg.caminho_cache)
    base = prefixo_gemini(cfg)
    linhas = [
        (f"{base}aaaaaaaaaaaa|vad", "antigo", "2026-10-20T10:00:00+00:00"),
        (f"{base}bbbbbbbbbbbb|vad", "recente", "2026-10-21T10:00:00+00:00"),
        ("g1|gemini|outro-modelo|cccccccccccc|vad", "outro", "2026-10-22T10:00:00+00:00"),
    ]
    for sig, texto, criado in linhas:
        cache._con.execute("INSERT INTO resultados VALUES ('sha', 'audio', ?, ?, 'agy:x', ?)",
                           (sig, texto, criado))
    assert isinstance(base, Prefixo)
    assert cache.obter_com_motor("sha", "audio", base) == ("recente", "agy:x")
    assert cache.obter("sha", "audio", str(base)) is None  # str comum: so a exata
    assert cache.obter("outro", "audio", base) is None
    cache.fechar()


def test_mudar_os_termos_nao_refaz_o_que_o_gemini_ja_transcreveu(tmp_path, servir):
    g = GeminiFalso(("fala", "Vi sim."), ("fala", "Outro audio."))
    tr, cfg = _orq(tmp_path, g)
    s = servir(tr, cfg)
    assert _pedir(s, "/transcrever", b"ogg")[1]["cache"] is False

    tr.cfg = _cfg(tmp_path, reserva=True, pausa_min=30, termos=("Renata Silva",))
    assert assinatura_gemini(tr.cfg) != assinatura_gemini(cfg)
    _, dados = _pedir(s, "/transcrever", b"ogg")
    assert dados == {"texto": "Vi sim.", "cache": True, "motor": tr.nome}
    _, dados = _pedir(s, "/transcrever", b"ogg-novo")  # audio novo vai com os termos novos
    assert dados["cache"] is False and g.chamadas == 2


def test_status_traz_o_motor_da_transcricao(tmp_path, servir):
    tr, cfg = _orq(tmp_path, GeminiFalso(("fala", "x")))
    s = servir(tr, cfg)
    con = http.client.HTTPConnection("127.0.0.1", s.porta, timeout=5)
    con.request("GET", "/status")
    dados = json.loads(con.getresponse().read())
    con.close()
    assert dados["transcricao"]["motor"] == "gemini"
    assert dados["transcricao"]["modelo"] == "gemini-3.8-flash-low"
    assert dados["transcricao"]["bloqueio"] is None


# --- limpeza do que o agy guarda ----------------------------------------------------

DIA = 86400
AGORA = 1_900_000_000.0


def _agy_falso(tmp_path):
    """A pasta do agy e o temp do wa. Conversas: do wa velha, do wa nova, revisao que cita
    o prefixo e o prompt, pasta de trabalho mista e um resumo sem nenhum arquivo."""
    import os
    import sqlite3

    pasta, temp = tmp_path / "antigravity-cli", tmp_path / "Temp"
    for sub in ("conversations", "annotations", "brain"):
        (pasta / sub).mkdir(parents=True)
    wa = f'["{temp.as_uri()}/wa-audio-abc"]'
    citacao = "Ouça o arquivo de áudio audio.opus nesta pasta. wa-audio-".encode()
    con = sqlite3.connect(pasta / "conversation_summaries.db")
    con.execute("CREATE TABLE conversation_summaries (conversation_id TEXT PRIMARY KEY,"
                " preview TEXT, workspace_uris TEXT)")
    conversas = {
        "wa-velha": (b"binario", wa, 100),
        "wa-nova": (b"binario", wa, 10),
        "revisao": (citacao, '["file:///C:/Temp/dupla/pepita-wa", "file:///D:/projeto"]', 100),
        "mista": (b"binario", f'["{temp.as_uri()}/wa-audio-x", "file:///D:/projeto"]', 100),
    }
    for cid, (conteudo, uris, idade) in conversas.items():
        db = pasta / "conversations" / f"{cid}.db"
        db.write_bytes(conteudo)
        titulo = pasta / "annotations" / f"{cid}.pbtxt"
        titulo.write_text('title:"Renata Silva"')
        brain = pasta / "brain" / cid
        (brain / "scratch").mkdir(parents=True)
        for c in (db, titulo, brain / "scratch", brain):
            os.utime(c, (AGORA - idade * DIA, AGORA - idade * DIA))
        con.execute("INSERT INTO conversation_summaries VALUES (?, ?, ?)", (cid, "previa", uris))
    con.execute("INSERT INTO conversation_summaries VALUES (?, ?, ?)", ("orfa", "previa", wa))
    con.commit()
    con.close()
    return pasta, temp


def _restantes(pasta):
    import sqlite3

    con = sqlite3.connect(pasta / "conversation_summaries.db")
    resumos = sorted(i for (i,) in con.execute("SELECT conversation_id FROM conversation_summaries"))
    con.close()
    return {
        "conversas": sorted(p.stem for p in (pasta / "conversations").glob("*.db")),
        "titulos": sorted(p.stem for p in (pasta / "annotations").iterdir()),
        "brain": sorted(p.name for p in (pasta / "brain").iterdir()),
        "resumos": resumos,
    }


def test_limpeza_do_agy_apaga_so_as_do_wa_pelo_resumo_e_mais_velhas_que_o_prazo(tmp_path):
    pasta, temp = _agy_falso(tmp_path)
    assert limpar_conversas_agy(90, pasta, agora=AGORA, temp=temp) == 2  # wa-velha e orfa
    ficam = ["mista", "revisao", "wa-nova"]
    assert _restantes(pasta) == {"conversas": ficam, "titulos": ficam, "brain": ficam,
                                 "resumos": ficam}
    assert limpar_conversas_agy(90, pasta, agora=AGORA, temp=temp) == 0


def test_limpeza_do_agy_desligada_ou_sem_resumo_nao_apaga_nada(tmp_path):
    pasta, temp = _agy_falso(tmp_path)
    antes = _restantes(pasta)
    assert limpar_conversas_agy(0, pasta, agora=AGORA, temp=temp) == 0
    assert _restantes(pasta) == antes
    (pasta / "conversation_summaries.db").unlink()
    assert limpar_conversas_agy(90, pasta, agora=AGORA, temp=temp) == 0
    assert len(list((pasta / "conversations").glob("*.db"))) == 4
    assert limpar_conversas_agy(90, tmp_path / "nao-existe", agora=AGORA, temp=temp) == 0


def test_limpeza_do_agy_que_falha_no_meio_tenta_de_novo_pelo_resumo(tmp_path, monkeypatch):
    import wacervo.transcricao as modulo

    import os

    pasta, temp = _agy_falso(tmp_path)
    rmtree = modulo.shutil.rmtree

    def pela_metade(caminho, *a, **k):
        # Apaga um filho e falha no resto: a pasta fica com a data de hoje.
        rmtree(next(caminho.iterdir()))
        os.utime(caminho, (AGORA, AGORA))
        raise PermissionError("em uso")

    monkeypatch.setattr(modulo.shutil, "rmtree", pela_metade)
    assert limpar_conversas_agy(90, pasta, agora=AGORA, temp=temp) == 1  # so a orfa
    r = _restantes(pasta)
    assert "wa-velha" in r["brain"] and "wa-velha" in r["conversas"]
    assert "wa-velha" in r["resumos"]  # a pendencia fica no resumo

    monkeypatch.setattr(modulo.shutil, "rmtree", rmtree)
    amanha = AGORA + DIA  # a data nova do brain nao adia a tentativa por 90 dias
    assert limpar_conversas_agy(90, pasta, agora=amanha, temp=temp) == 1
    r = _restantes(pasta)
    assert all("wa-velha" not in v for v in r.values())


def test_limpeza_do_agy_que_falha_no_db_tenta_de_novo_no_dia_seguinte(tmp_path, monkeypatch):
    from pathlib import Path

    pasta, temp = _agy_falso(tmp_path)
    unlink = Path.unlink

    def db_preso(self, *a, **k):
        if self.name == "wa-velha.db":
            raise PermissionError("em uso")
        return unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", db_preso)
    assert limpar_conversas_agy(90, pasta, agora=AGORA, temp=temp) == 1  # so a orfa
    r = _restantes(pasta)
    assert "wa-velha" not in r["brain"] and "wa-velha" not in r["titulos"]
    assert "wa-velha" in r["conversas"] and "wa-velha" in r["resumos"]

    monkeypatch.setattr(Path, "unlink", unlink)
    assert limpar_conversas_agy(90, pasta, agora=AGORA + DIA, temp=temp) == 1
    assert all("wa-velha" not in v for v in _restantes(pasta).values())
