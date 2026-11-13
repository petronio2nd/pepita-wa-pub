"""Contrato público de avaliação ASR: somente fixtures fictícias e CPU, sem ASR."""

import copy
import importlib.util
import io
import json
from pathlib import Path
import struct
import sys
import wave

import pytest


SPEC = importlib.util.spec_from_file_location(
    "avaliacao_transcricao", Path(__file__).parents[1] / "ferramentas" / "avaliacao_transcricao.py")
av = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(av)


def tsv(n=30, repeticoes=3):
    return "\n".join(
        f'{i}\t{i*10+j}.wav\tFrase fictícia {i}.\tfrase fictícia {i}\tIGNORADO\t48000\tMALE'
        for i in range(n) for j in range(repeticoes)).encode()


def pcm(n, valor=100):
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        w.writeframes(struct.pack("<h", valor) * n)
    return b.getvalue()


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    dados = tsv()
    monkeypatch.setattr(av, "TSV_SHA", av.sha(dados))
    m = av.selecionar(tsv())
    pasta = tmp_path / "corpus"
    (pasta / "fala").mkdir(parents=True)
    (pasta / "fonte").mkdir()
    (pasta / "fonte" / "test.tsv").write_bytes(dados)
    av.escrever_novo(pasta / "fonte" / "origem.json", {**av.FONTE, "tsv_sha256": av.TSV_SHA})
    for i, r in enumerate(m["fala"], 1):
        (pasta / r["arquivo"]).write_bytes(pcm(r["num_samples"], valor=i))
    av.gerar_controles(pasta / "controles")
    return pasta, av.selar(m, pasta)


def medidas_vad(m, digest="a" * 64):
    return {"schema": 1, "manifesto_sha256": digest,
            "parametros": copy.deepcopy(av.COMUM["vad_previo"]),
            "medidas": [{"id": r["id"], "audio_sha256": r["audio_sha256"],
                         "duracao_s": r["num_samples"] / 16000,
                         "fala_s": 0 if r["num_samples"] == 1600 else .5,
                         "trechos": 0 if r["num_samples"] == 1600 else 1}
                        for r in m["fala"] + m["controles"]]}


def vad_fixture(m, digest="a" * 64):
    dados = json.dumps(medidas_vad(m, digest)).encode()
    return dados, av.sha(dados)


def rodadas(m, digest="a" * 64):
    dados, vad_sha = vad_fixture(m, digest)
    medidas = av.validar_vad(dados, vad_sha, digest, m)
    return [{"schema": 1, "manifesto_sha256": digest, "candidato": nome,
             "vad_sha256": vad_sha,
             "parametros": copy.deepcopy(parametros),
             "resultados": [{"id": r["id"], "audio_sha256": r["audio_sha256"],
                             "status": "ok", "transcricao": r["referencia"],
                             "fala_s": medidas[r["id"]]["fala_s"],
                             "origem": "vad" if medidas[r["id"]]["fala_s"] < .3 else
                             "whisper" if nome == "whisper" else "gemini",
                             **({"conteudo": "fala"} if nome != "whisper" and medidas[r["id"]]["fala_s"] >= .3 else {})}
                            for r in m["fala"] + m["controles"]]}
            for nome, parametros in av.CANDIDATOS.items()]


def test_tsv_oficial_tem_sete_campos_aspas_literais_e_linhas_vazias():
    dados = b'\r\n\t\t\t\t\t\t\r\n1\t12.wav\t"Frase ficticia.\tfrase\tIGN\t48000\tMALE\r\n'
    r = av.ler_tsv(dados)
    assert len(r) == 1 and r[0]["referencia"] == "frase ficticia"


@pytest.mark.parametrize("linha", [
    "1\t12.wav\traw", "x\t12.wav\traw\tt\tx\t48000\tMALE",
    "1\t../12.wav\traw\tt\tx\t48000\tMALE",
    "1\tC:\\12.wav\traw\tt\tx\t48000\tMALE",
    "1\t12.wav\t...\tt\tx\t48000\tMALE",
    "1\t12.wav\traw\tt\tx\t-1\tMALE",
    "1\t12.wav\traw\tt\tx\t48000\tINVALIDO",
])
def test_tsv_malformado_nao_vazio_e_recusado(linha):
    with pytest.raises(av.Invalido):
        av.ler_tsv(linha.encode())


def test_selecao_deterministica_por_frase_independente_da_ordem_tsv():
    dados = tsv()
    m = av.selecionar(dados)
    invertido = av.selecionar(b"\n".join(reversed(dados.splitlines())))
    assert m["fala"] == invertido["fala"]
    assert len({r["frase_id"] for r in m["fala"]}) == 24
    assert len(m["fala"]) == 24 and len(m["controles"]) == 4
    assert m["selado"] is False
    assert m["tsv_sha256"] == av.sha(dados)
    assert all(r["genero"] == "MALE" for r in m["fala"])
    assert "sem balanceamento" in m["amostragem"]


def test_manifesto_nao_compartilha_contrato_mutavel_com_constantes():
    m = av.selecionar(tsv())
    m["candidatos"]["atual"]["termos"].append("forjado")
    m["candidatos"]["atual"]["vad_previo"]["fala_minima_s"] = 999
    assert "forjado" not in av.CANDIDATOS["atual"]["termos"]
    assert av.CANDIDATOS["atual"]["vad_previo"]["fala_minima_s"] == .3
    with pytest.raises(av.Invalido, match="Contrato público"):
        av.validar_manifesto(m)


def test_duracao_e_frases_insuficientes_ou_filename_duplicado():
    dados = tsv(n=24).replace(b"48000", b"47999", 3)
    with pytest.raises(av.Invalido, match="insuficientes"):
        av.selecionar(dados)
    with pytest.raises(av.Invalido, match="duplicado"):
        av.ler_tsv(tsv() + b"\n" + tsv().splitlines()[0])


def test_wav_pcm_float_e_frames_sem_decoder():
    assert av.info_wav(pcm(1600)) == {"codec": 1, "bits": 16, "num_samples": 1600}
    fmt = struct.pack("<HHIIHH", 3, 1, 16000, 64000, 4, 32)
    corpo = b"WAVEfmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", 12) + b"\0"*12
    assert av.info_wav(b"RIFF" + struct.pack("<I", len(corpo)) + corpo)["num_samples"] == 3
    with pytest.raises(av.Invalido):
        av.info_wav(pcm(10)[:-1])
    with pytest.raises(av.Invalido):
        av.info_wav(pcm(10).replace(struct.pack("<I", 16000), struct.pack("<I", 8000), 1))


def test_controles_reproduziveis_e_recusa_sobrescrever(tmp_path):
    for nome in ("a", "b"):
        av.gerar_controles(tmp_path / nome)
    for nome, n in av.CONTROLES:
        a = (tmp_path / "a" / f"{nome}.wav").read_bytes()
        assert a == (tmp_path / "b" / f"{nome}.wav").read_bytes()
        assert av.info_wav(a) == {"codec": 1, "bits": 16, "num_samples": n}
    with pytest.raises(FileExistsError):
        av.gerar_controles(tmp_path / "a")


def test_selar_verifica_28_itens_e_gate_pega_tamper(corpus):
    pasta, m = corpus
    assert m["selado"] is True
    assert len({r["audio_sha256"] for r in m["fala"] + m["controles"]}) == 28
    av.verificar_corpus(m, pasta)
    arq = pasta / m["fala"][0]["arquivo"]
    arq.write_bytes(pcm(48000, 600))
    with pytest.raises(av.Invalido, match="alterado"):
        av.verificar_corpus(m, pasta)


def test_selar_rejeita_frames_errados_duplicata_e_controle_falso(corpus):
    pasta, selado = corpus
    m = copy.deepcopy(selado)
    m["selado"] = False
    arquivo = pasta / m["fala"][0]["arquivo"]
    original = arquivo.read_bytes()
    arquivo.write_bytes(pcm(47999))
    with pytest.raises(av.Invalido, match="Frames"):
        av.selar(m, pasta)
    arquivo.write_bytes((pasta / m["fala"][1]["arquivo"]).read_bytes())
    with pytest.raises(av.Invalido, match="repetido"):
        av.selar(m, pasta)
    arquivo.write_bytes(original)
    (pasta / m["controles"][1]["arquivo"]).write_bytes(pcm(48000))
    with pytest.raises(av.Invalido, match="reproduzível"):
        av.selar(m, pasta)


@pytest.mark.parametrize("relativo", ["../a.wav", "/a.wav", "C:/a.wav", "fala/../a.wav", "fala\\a.wav"])
def test_paths_sem_traversal_ou_absolutos(tmp_path, relativo):
    with pytest.raises(av.Invalido):
        av.caminho_audio(tmp_path, relativo)


def test_symlink_recusado_quando_suportado(tmp_path):
    original = tmp_path / "original.wav"
    original.write_bytes(pcm(1))
    link = tmp_path / "link.wav"
    try:
        link.symlink_to(original)
    except OSError:
        pytest.skip("Windows sem permissão de criar symlink")
    with pytest.raises(av.Invalido, match="link"):
        av.caminho_audio(tmp_path, "link.wav")


def test_normalizacao_metricas_e_vazio():
    assert av.normalizar("  ＣPF: ÁGUA—12, Pix! ") == "cpf água 12 pix"
    r = av.metricas("um dois", "um três quatro")
    assert r["erros_palavras"] == 2 and r["wer"] == 1
    assert av.metricas("um", "um dois três")["wer"] == 2
    assert av.metricas("á b", "a b")["cer"] == .5
    vazio = av.metricas("", "fala falsa")
    assert vazio["wer"] is None and vazio["cer"] is None
    assert vazio["erros_palavras"] == 2
    assert av.metricas("fala", "")["wer"] == 1


def test_micro_por_contagens_macro_por_fala_e_controles_separados(corpus):
    _, m = corpus
    m = copy.deepcopy(m)
    for r in m["fala"]:
        r["referencia"] = "a"
    m["fala"][-1]["referencia"] = " ".join(["a"] * 100)
    textos = {r["id"]: r["referencia"] for r in m["fala"] + m["controles"]}
    textos[m["fala"][0]["id"]] = ""
    textos[m["controles"][0]["id"]] = "inserção indevida"
    resumo = av.resumir(m, textos)
    assert resumo["micro_wer"] == 1 / 123
    assert resumo["macro_wer"] == 1 / 24
    assert resumo["micro_cer"] == 1 / 123
    assert resumo["macro_cer"] == 1 / 24
    assert resumo["falsos_positivos_controles"] == 1


def test_gate_manifesto_recusa_troca_de_fonte_e_frase_duplicada(corpus):
    _, original = corpus
    m = copy.deepcopy(original)
    m["fonte"]["revision"] = "outra"
    with pytest.raises(av.Invalido, match="Fonte"):
        av.validar_manifesto(m, True)
    m = copy.deepcopy(original)
    m["fala"][1] = copy.deepcopy(m["fala"][0])
    with pytest.raises(av.Invalido, match="Frase"):
        av.validar_manifesto(m, True)


@pytest.mark.parametrize("valor", [None, {}, "lista", [1], [None]])
def test_manifesto_malformado_tem_erro_padronizado(valor):
    m = av.selecionar(tsv())
    m["fala"] = valor
    with pytest.raises(av.Invalido, match="listas de objetos"):
        av.validar_manifesto(m)


def test_vad_bytes_hash_e_rodadas_precisam_da_mesma_medida(corpus):
    _, m = corpus
    dados, digest = vad_fixture(m)
    r = rodadas(m)[0]
    med = json.loads(dados)
    med["medidas"][0]["fala_s"] = .6
    adulterado = json.dumps(med).encode()
    with pytest.raises(av.Invalido, match="Hash do VAD"):
        av.validar_vad(adulterado, digest, "a" * 64, m)
    with pytest.raises(av.Invalido, match="outro VAD"):
        av.validar_rodada(r, "a" * 64, m, adulterado, av.sha(adulterado))
    r["vad_sha256"] = av.sha(adulterado)
    with pytest.raises(av.Invalido, match="VAD comum"):
        av.validar_rodada(r, "a" * 64, m, adulterado, av.sha(adulterado))


@pytest.mark.parametrize("defeito", ["faltante", "duplicado", "hash", "manifesto", "parametros",
                                     "duracao", "fala_nan", "fala_excessiva", "trechos"])
def test_vad_valida_cobertura_e_medidas(corpus, defeito):
    _, m = corpus
    v = medidas_vad(m)
    medida = v["medidas"][0]
    if defeito == "faltante":
        v["medidas"].pop()
    elif defeito == "duplicado":
        v["medidas"][-1] = copy.deepcopy(medida)
    elif defeito == "hash":
        medida["audio_sha256"] = "0" * 64
    elif defeito == "manifesto":
        v["manifesto_sha256"] = "b" * 64
    elif defeito == "parametros":
        v["parametros"]["fala_minima_s"] = .4
    elif defeito == "duracao":
        medida["duracao_s"] += .02
    elif defeito == "fala_nan":
        medida["fala_s"] = float("nan")
    elif defeito == "fala_excessiva":
        medida["fala_s"] = medida["duracao_s"] + 1
    else:
        medida["trechos"] = True
    dados = json.dumps(v).encode()
    with pytest.raises(av.Invalido):
        av.validar_vad(dados, av.sha(dados), "a" * 64, m)


def test_origem_obedece_limiar_comum_e_inaudivel_e_omissao_semantica(corpus):
    _, m = corpus
    r = rodadas(m)[0]
    resultado = r["resultados"][0]
    resultado.update(origem="vad", transcricao="")
    with pytest.raises(av.Invalido, match="limiar VAD"):
        av.validar_rodada(r, "a" * 64, m, *vad_fixture(m))
    resultado.update(origem="gemini", conteudo="inaudivel")
    _, textos = av.validar_rodada(r, "a" * 64, m, *vad_fixture(m))
    resumo = av.resumir(m, textos, r["resultados"])
    assert resumo["fala"][resultado["id"]]["wer"] == 1
    assert resumo["contagens"]["fala"]["por_conteudo"]["inaudivel"] == 1
    assert resumo["contagens"]["fala"]["vazios"] == 1
    resultado["transcricao"] = "texto indevido"
    with pytest.raises(av.Invalido, match="sem fala"):
        av.validar_rodada(r, "a" * 64, m, *vad_fixture(m))
    resultado.update(conteudo="desconhecido", transcricao="")
    with pytest.raises(av.Invalido, match="Conteúdo Gemini"):
        av.validar_rodada(r, "a" * 64, m, *vad_fixture(m))


@pytest.mark.parametrize("campo", ["referencia", "arquivo", "genero", "num_samples"])
def test_selecao_vinculada_ao_tsv_recusa_adulteracao(corpus, campo):
    pasta, original = corpus
    m = copy.deepcopy(original)
    m["selado"] = False
    r = m["fala"][0]
    if campo == "referencia":
        r["referencia"] = "referência forjada"
        r["referencia_bruta"] = "Referência forjada."
    elif campo == "arquivo":
        r[campo] = "fala/999999999.wav"
    elif campo == "genero":
        r[campo] = "FEMALE"
    else:
        r[campo] += 16000
    with pytest.raises(av.Invalido, match="diverge do TSV"):
        av.selar(m, pasta)
    m["selado"] = True
    with pytest.raises(av.Invalido, match="diverge do TSV"):
        av.verificar_corpus(m, pasta)


def test_tsv_de_outro_hash_e_json_origem_errado_sao_recusados(corpus):
    pasta, m = corpus
    origem = pasta / "fonte" / "origem.json"
    origem.write_text("{}", encoding="utf-8")
    with pytest.raises(av.Invalido, match="JSON de origem"):
        av.verificar_corpus(m, pasta)
    (pasta / "fonte" / "test.tsv").write_bytes(tsv() + b"\n")
    with pytest.raises(av.Invalido, match="Hash do TSV"):
        av.verificar_corpus(m, pasta)


def test_fallback_disfarcado_e_rejeitado_e_vad_exige_medida_e_texto_vazio(corpus):
    _, m = corpus
    r = rodadas(m)[0]
    resultado = r["resultados"][0]
    resultado["origem"] = "whisper"
    with pytest.raises(av.Invalido, match="reserva disfarçada"):
        av.validar_rodada(r, "a" * 64, m, *vad_fixture(m))
    resultado.update(origem="gemini")
    for fala_s in (None, True, -.1, .3, float("nan"), float("inf")):
        resultado["fala_s"] = fala_s
        with pytest.raises(av.Invalido, match="VAD comum"):
            av.validar_rodada(r, "a" * 64, m, *vad_fixture(m))
    resultado.update(fala_s=.5)
    resultado = next(x for x in r["resultados"] if x["origem"] == "vad")
    resultado["transcricao"] = "fala indevida"
    with pytest.raises(av.Invalido, match="VAD inválido"):
        av.validar_rodada(r, "a" * 64, m, *vad_fixture(m))


@pytest.mark.parametrize("defeito", ["faltante", "duplicado", "hash", "manifesto", "erro", "nao_string", "parametros"])
def test_gate_rodadas_falha_fechado(corpus, defeito):
    _, m = corpus
    r = rodadas(m)[0]
    if defeito == "faltante":
        r["resultados"].pop()
    elif defeito == "duplicado":
        r["resultados"][-1] = copy.deepcopy(r["resultados"][0])
    elif defeito == "hash":
        r["resultados"][0]["audio_sha256"] = "0" * 64
    elif defeito == "manifesto":
        r["manifesto_sha256"] = "b" * 64
    elif defeito == "erro":
        r["resultados"][0].update(status="erro", transcricao="")
    elif defeito == "nao_string":
        r["resultados"][0]["transcricao"] = None
    else:
        r["parametros"]["termos"] = ["termo diferente"]
    with pytest.raises(av.Invalido):
        av.validar_rodada(r, "a" * 64, m, *vad_fixture(m))


def test_bootstrap_micro_pareado_nao_media_wer():
    atual = {str(i): av.metricas("a", "erro") for i in range(23)}
    atual["longa"] = av.metricas(" ".join(["a"] * 100), " ".join(["a"] * 100))
    candidato = {k: {**v, "erros_palavras": 0} for k, v in atual.items()}
    b = av.bootstrap(atual, candidato)
    assert b["delta_micro_wer"] == -23 / 123
    assert b["ganhou"] == 23 and b["empatou"] == 1 and b["perdeu"] == 0
    assert b["repeticoes"] == 10000 and b["n_frases"] == 24
    assert b == av.bootstrap(atual, candidato)


def test_avaliacao_completa_controles_separados_e_sem_promocao(corpus):
    pasta, m = corpus
    r = rodadas(m)
    r[1]["resultados"][-1]["transcricao"] = "fala inventada"
    rel = av.avaliar(m, "a" * 64, pasta, r, *vad_fixture(m))
    assert rel["rodadas"]["atual"]["micro_wer"] == 0
    assert rel["rodadas"]["atual"]["macro_cer"] == 0
    assert rel["rodadas"]["flash_high"]["micro_wer"] == 0
    assert rel["rodadas"]["flash_high"]["falsos_positivos_controles"] == 1
    assert rel["rodadas"]["flash_high"]["controles"]["controle:tom_440hz"]["wer"] is None
    assert rel["comparacoes"]["whisper"]["ic95_percentil"] == [0, 0]
    assert rel["promocao"].startswith("não decidida")
    assert rel["custos"] is None and rel["latencias"] is None
    assert rel["vad_comum"]["sha256"] == vad_fixture(m)[1]
    assert rel["vad_comum"]["cobertura"] == 28
    assert rel["rodadas"]["atual"]["contagens"]["fala"]["por_origem"] == {"gemini": 24}
    with pytest.raises(av.Invalido, match="Candidato duplicado"):
        av.avaliar(m, "a" * 64, pasta, [r[0], r[0], r[2]], *vad_fixture(m))
    with pytest.raises(av.Invalido, match="não selado"):
        av.avaliar({**m, "selado": False}, "a" * 64, pasta, r, *vad_fixture(m))


def test_json_duplicado_e_saida_existente_sao_recusados(tmp_path):
    p = tmp_path / "saida.json"
    p.write_text('{"schema":1,"schema":2}', encoding="utf-8")
    with pytest.raises(av.Invalido, match="duplicada"):
        av.ler_json(p)
    with pytest.raises(FileExistsError):
        av.escrever_novo(p, {})


def test_cli_selecionar_ficticio_nao_importa_runtime(tmp_path):
    entrada, saida = tmp_path / "test.tsv", tmp_path / "selecao.json"
    entrada.write_bytes(tsv())
    antes = set(sys.modules)
    assert av.main(["selecionar", "--tsv", str(entrada), "--saida", str(saida)]) == 0
    assert json.loads(saida.read_text(encoding="utf-8"))["selado"] is False
    assert not any(x.startswith(("faster_whisper", "onnxruntime", "wacervo"))
                   for x in set(sys.modules) - antes)
    with pytest.raises(SystemExit) as erro:
        av.main(["selecionar", "--tsv", str(entrada), "--saida", str(saida)])
    assert erro.value.code == 2


def test_cli_saida_existente_recusada_antes_de_ler_e_calcular(tmp_path, monkeypatch):
    saida = tmp_path / "existe.json"
    saida.write_text("preservar", encoding="utf-8")
    def proibido(*args):
        raise AssertionError("Não deve ler manifesto nem calcular com saída existente")
    monkeypatch.setattr(av, "ler_json", proibido)
    with pytest.raises(SystemExit) as erro:
        av.main(["avaliar", "--manifesto", "nao-existe", "--corpus", "nao-existe",
                 "--vad", "nao-existe", "--rodadas", "a", "b", "c", "--saida", str(saida)])
    assert erro.value.code == 2
    assert saida.read_text(encoding="utf-8") == "preservar"


def test_ancestral_symlink_relativo_recusado(tmp_path, monkeypatch):
    destino = tmp_path / "destino"
    (destino / "subpasta").mkdir(parents=True)
    (destino / "subpasta" / "a.wav").write_bytes(pcm(1))
    link = tmp_path / "ligacao"
    try:
        link.symlink_to(destino, target_is_directory=True)
    except OSError:
        pytest.skip("Windows sem permissão de criar symlink")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(av.Invalido, match="Raiz com link"):
        av.caminho_audio(Path("ligacao/subpasta"), "a.wav")
