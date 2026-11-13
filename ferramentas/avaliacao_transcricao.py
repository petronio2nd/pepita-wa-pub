"""Preparo e avaliação OFFLINE de ASR; não chama modelos, serviços ou provedores.

selecionar --tsv TEST.tsv --saida selecao.json
controles --diretorio CORPUS/controles  (diretório novo)
selar --manifesto selecao.json --corpus CORPUS --saida manifesto.json
avaliar --manifesto manifesto.json --corpus CORPUS --vad MEDIDAS.json --rodadas atual.json
        flash_high.json whisper.json --saida relatorio.json

Mantenha corpus, referências e saídas fora do git, em dados/avaliacao-transcricao-...,
no Windows. A aquisição dos WAVs e a execução das rodadas são passos externos.
Cada rodada é JSON {schema:1, manifesto_sha256, vad_sha256, candidato, parametros, resultados}.
parametros deve ser exatamente o contrato público do candidato no manifesto;
resultados contém {id, audio_sha256, status:"ok", origem, fala_s, transcricao:<string>}.
Origem gemini também exige conteudo: fala/silencio/ruido/musica/inaudivel.
VAD é JSON {schema:1, manifesto_sha256, parametros:COMUM.vad_previo, medidas:[
{id, audio_sha256, duracao_s, fala_s, trechos}]}; 28 medidas comuns a todas as rodadas.
Origem vad exige fala_s finito entre zero (inclusive) e 0,3 (exclusive) e texto vazio.
O corpus deve ter fonte/test.tsv e fonte/origem.json; este último contém exatamente
os campos FONTE e tsv_sha256. Selamento/avaliação exigem o hash público fixado.
As três rodadas só são comparadas após todos os gates. Este módulo não mede VAD
real, não reproduz os termos pessoais da configuração ativa e não decide promoção.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath, PureWindowsPath
import random
import re
import struct
import unicodedata
import wave

REVISAO = "70bb2e84b976b7e960aa89f1c648e09c59f894dd"
TSV_SHA = "4a7b8ae8ce07c355dcd2cf087a6486c7c3ab931d751b9a9218a9ee0cd798964b"
FONTE = {
    "dataset": "google/fleurs", "config": "pt_br", "split": "test",
    "revision": REVISAO, "licenca": "CC-BY-4.0",
    "builder": "https://huggingface.co/datasets/google/fleurs/blob/"
    "d74c1f182ca32a92215e33f39aa25543ae5a5647/fleurs.py",
}
SEED = 20261101
TAXA = 16000
TERMOS = [
    "SISBAJUD", "RENAJUD", "SERASAJUD", "Bacenjud", "teimosinha",
    "exceção de pré-executividade", "prescrição intercorrente", "Projudi", "PJe",
    "alvará", "conta judicial", "IA", "LGPD", "Pix", "CPF", "CNPJ",
]
COMUM = {"vad_previo": {"motor": "silero", "fala_minima_s": 0.3},
         "reserva_automatica": False}
PUBLICO = {**COMUM, "idioma": "pt", "contexto": "Mensagem de voz de WhatsApp.",
           "termos": TERMOS,
           "texto_suspeito": {"palavras_por_segundo": 5, "folga_palavras": 10,
                              "acao": "falha_explicita"}}
CANDIDATOS = {
    "atual": {**PUBLICO, "motor": "agy", "modelo": "gemini-3.8-flash-low"},
    "flash_high": {**PUBLICO, "motor": "agy", "modelo": "gemini-3.8-flash-high"},
    "whisper": {
        **COMUM,
        "motor": "whisper", "modelo": "deepdml/faster-whisper-large-v3-turbo-ct2",
        "idioma": "pt", "tipo_computacao": "float16", "dispositivo": "cuda",
        "vad_filter": True, "condition_on_previous_text": False,
    },
}
CONTROLES = (("silencio_curto", 1600), ("silencio", 48000),
             ("ruido_branco", 48000), ("tom_440hz", 48000))


class Invalido(ValueError):
    """Entrada recusada antes de qualquer comparação."""


def exigir(condicao, mensagem):
    if not condicao:
        raise Invalido(mensagem)


def sha(dados):
    return hashlib.sha256(dados).hexdigest()


def normalizar(texto):
    texto = unicodedata.normalize("NFKC", texto).casefold()
    return " ".join("".join(" " if unicodedata.category(c).startswith("P")
                            else c for c in texto).split())


def ler_tsv(dados):
    """TSV oficial de sete campos; aspas são literais, linhas vazias ignoradas."""
    linhas = csv.reader(io.StringIO(dados.decode("utf-8"), newline=""), delimiter="\t",
                        quoting=csv.QUOTE_NONE)
    registros = []
    vistos = set()
    for numero, campos in enumerate(linhas, 1):
        if not campos or all(not c.strip() for c in campos):
            continue
        exigir(len(campos) == 7, f"TSV linha {numero}: esperados sete campos")
        ident, nome, raw, transcricao, _ignorado, amostras, genero = campos
        exigir(genero in ("MALE", "FEMALE", "OTHER"), f"TSV linha {numero}: gênero inválido")
        exigir(ident.isascii() and ident.isdigit(), f"TSV linha {numero}: id inválido")
        exigir(re.fullmatch(r"[0-9]+\.wav", nome) is not None,
               f"TSV linha {numero}: filename inválido")
        exigir(amostras.isascii() and amostras.isdigit() and int(amostras) > 0,
               f"TSV linha {numero}: num_samples inválido")
        referencia = normalizar(raw)
        exigir(bool(referencia), f"TSV linha {numero}: raw_transcription vazia")
        exigir(nome not in vistos, f"TSV linha {numero}: filename duplicado")
        vistos.add(nome)
        registros.append({"frase_id": ident, "arquivo": f"fala/{nome}",
                          "referencia_bruta": raw,
                          "referencia": referencia, "num_samples": int(amostras),
                          "genero": genero})
    exigir(bool(registros), "TSV sem registros")
    return registros


def selecionar(dados, quantidade=24, seed=SEED):
    grupos = {}
    for r in sorted(ler_tsv(dados), key=lambda r: (int(r["frase_id"]), r["arquivo"])):
        if 3 * TAXA <= r["num_samples"] <= 30 * TAXA:
            grupos.setdefault(r["frase_id"], []).append(r)
    exigir(len(grupos) >= quantidade > 0, "Frases elegíveis insuficientes")
    rng = random.Random(seed)
    por_frase = {ident: rng.choice(candidatos) for ident, candidatos in grupos.items()}
    escolhidos = rng.sample(list(por_frase), quantidade)
    fala = [{"id": f"fala:{ident}", **por_frase[ident]} for ident in escolhidos]
    fala.sort(key=lambda r: int(r["frase_id"]))
    controles = [{"id": f"controle:{nome}", "arquivo": f"controles/{nome}.wav",
                  "referencia": "", "expectativa": "sem_fala", "num_samples": n}
                 for nome, n in CONTROLES]
    return {"schema": 1, "selado": False, "fonte": dict(FONTE),
            "tsv_sha256": sha(dados), "seed": seed, "quantidade_fala": quantidade,
            "amostragem": "um áudio por frase_id; sem balanceamento de gênero",
            "candidatos": json.loads(json.dumps(CANDIDATOS)),
            "fala": fala, "controles": controles}


def caminho_audio(raiz, relativo):
    exigir(isinstance(relativo, str) and "\\" not in relativo, "Caminho de áudio inválido")
    p = PurePosixPath(relativo)
    exigir(not p.is_absolute() and not PureWindowsPath(relativo).drive
           and all(x not in (".", "..", "") for x in relativo.split("/")),
           "Caminho de áudio absoluto ou traversal")
    raiz = Path(raiz).absolute()
    # Inclui os ancestrais da raiz: nenhum symlink/junction pode redirecionar o corpus.
    for parte in (raiz, *raiz.parents):
        exigir(not parte.is_symlink() and not parte.is_junction(), "Raiz com link")
    atual = raiz
    for parte in p.parts:
        atual /= parte
        exigir(not atual.is_symlink() and not atual.is_junction(), "Áudio com link")
    exigir(atual.is_file(), f"Áudio ausente: {relativo}")
    exigir(atual.resolve().is_relative_to(raiz.resolve()), "Áudio fora do corpus")
    return atual


def info_wav(dados):
    """Lê RIFF/WAVE PCM1 ou IEEE-float3, sem decodificar/modelo; valida frames."""
    exigir(len(dados) >= 12 and dados[:4] == b"RIFF" and dados[8:12] == b"WAVE",
           "WAV RIFF inválido")
    exigir(struct.unpack_from("<I", dados, 4)[0] + 8 == len(dados), "Tamanho RIFF inválido")
    offset, formato, audio = 12, None, None
    while offset < len(dados):
        exigir(offset + 8 <= len(dados), "Chunk WAV truncado")
        tipo, tamanho = struct.unpack_from("<4sI", dados, offset)
        inicio = offset + 8
        fim = inicio + tamanho
        exigir(fim + tamanho % 2 <= len(dados), "Chunk WAV truncado")
        if tipo == b"fmt ":
            exigir(formato is None and tamanho >= 16, "fmt WAV inválido/duplicado")
            formato = struct.unpack_from("<HHIIHH", dados, inicio)
        elif tipo == b"data":
            exigir(audio is None, "data WAV duplicado")
            audio = tamanho
        offset = fim + tamanho % 2
    exigir(formato is not None and audio is not None, "WAV sem fmt/data")
    codec, canais, taxa, bytes_s, bloco, bits = formato
    exigir(codec in (1, 3) and canais == 1 and taxa == TAXA,
           "WAV exige PCM1/float3 mono 16 kHz")
    exigir((codec == 1 and bits in (8, 16, 24, 32)) or (codec == 3 and bits in (32, 64)),
           "Bits WAV não suportados")
    exigir(bloco == canais * bits // 8 and bytes_s == taxa * bloco
           and audio > 0 and audio % bloco == 0, "Alinhamento WAV inválido")
    return {"codec": codec, "bits": bits, "num_samples": audio // bloco}


def _json_sem_duplicatas(pares):
    d = {}
    for k, v in pares:
        exigir(k not in d, f"Chave JSON duplicada: {k}")
        d[k] = v
    return d


def ler_json(caminho):
    dados = Path(caminho).read_bytes()
    return json.loads(dados, object_pairs_hook=_json_sem_duplicatas), sha(dados)


def escrever_novo(caminho, objeto):
    # x evita sobrescrever inclusive em concorrência. Nunca cria o diretório do corpus.
    with Path(caminho).open("x", encoding="utf-8", newline="\n") as f:
        json.dump(objeto, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")


def validar_manifesto(m, exigir_selado=False):
    exigir(isinstance(m, dict), "Manifesto deve ser objeto")
    exigir(all(isinstance(m.get(grupo), list)
               and all(isinstance(r, dict) for r in m[grupo]) for grupo in ("fala", "controles")),
           "Fala/controles devem ser listas de objetos")
    exigir(m.get("schema") == 1 and m.get("fonte") == FONTE, "Fonte/schema diferente")
    exigir(m.get("candidatos") == CANDIDATOS, "Contrato público diferente")
    exigir(m.get("seed") == SEED and m.get("quantidade_fala") == 24,
           "Seleção exige seed fixo e 24 frases")
    exigir(re.fullmatch(r"[0-9a-f]{64}", m.get("tsv_sha256", "")) is not None,
           "Hash TSV inválido")
    exigir(not exigir_selado or m.get("selado") is True, "Manifesto não selado")
    exigir(len(m.get("fala", [])) == 24 and len(m.get("controles", [])) == 4,
           "Manifesto exige 24 falas e quatro controles")
    ids, frases, arquivos = set(), set(), set()
    for r in m["fala"]:
        ident = r.get("frase_id", "")
        exigir(isinstance(ident, str) and ident.isascii() and ident.isdigit()
               and ident not in frases and r.get("id") == f"fala:{ident}", "Frase repetida/inválida")
        frases.add(ident)
        exigir(r.get("genero") in ("MALE", "FEMALE", "OTHER"), "Gênero inválido")
        exigir(isinstance(r.get("referencia"), str) and bool(r["referencia"])
               and normalizar(r["referencia"]) == r["referencia"], "Referência inválida")
        exigir(isinstance(r.get("referencia_bruta"), str)
               and normalizar(r["referencia_bruta"]) == r["referencia"],
               "Referência bruta inconsistente")
        exigir(re.fullmatch(r"fala/[0-9]+\.wav", r.get("arquivo", "")) is not None,
               "Filename de fala inválido")
        exigir(type(r.get("num_samples")) is int and 3*TAXA <= r["num_samples"] <= 30*TAXA,
               "Duração fora do recorte")
    for r, (nome, n) in zip(m["controles"], CONTROLES):
        exigir(r.get("id") == f"controle:{nome}" and r.get("arquivo") == f"controles/{nome}.wav"
               and r.get("num_samples") == n and r.get("referencia") == ""
               and r.get("expectativa") == "sem_fala", "Controle diferente")
    for r in m["fala"] + m["controles"]:
        exigir(r["id"] not in ids and r["arquivo"] not in arquivos, "Item duplicado")
        ids.add(r["id"])
        arquivos.add(r["arquivo"])
        if exigir_selado:
            exigir(re.fullmatch(r"[0-9a-f]{64}", r.get("audio_sha256", "")) is not None
                   and type(r.get("bytes")) is int and r["bytes"] > 0, "Integridade ausente")


def verificar_fonte(m, corpus):
    dados = caminho_audio(corpus, "fonte/test.tsv").read_bytes()
    exigir(sha(dados) == TSV_SHA, "Hash do TSV fonte diferente do fixado")
    origem, _ = ler_json(caminho_audio(corpus, "fonte/origem.json"))
    exigir(origem == {**FONTE, "tsv_sha256": TSV_SHA}, "JSON de origem diferente")
    esperado = selecionar(dados)
    recebido = json.loads(json.dumps(m))
    recebido["selado"] = False
    for r in recebido["fala"] + recebido["controles"]:
        for campo in ("audio_sha256", "bytes", "wav"):
            r.pop(campo, None)
    exigir(recebido == esperado, "Seleção diverge do TSV fonte: referência/metadados adulterados")


def selar(m, corpus):
    validar_manifesto(m)
    exigir(m.get("selado") is False, "Seleção já selada")
    verificar_fonte(m, corpus)
    novo = json.loads(json.dumps(m))
    hashes = set()
    for r in novo["fala"] + novo["controles"]:
        dados = caminho_audio(corpus, r["arquivo"]).read_bytes()
        info = info_wav(dados)
        exigir(info["num_samples"] == r["num_samples"], "Frames divergem do TSV/controle")
        if r["id"].startswith("controle:"):
            exigir(info["codec"] == 1 and info["bits"] == 16, "Controle exige PCM16")
            nome = r["id"].split(":", 1)[1]
            exigir(dados == audio_controle(nome, r["num_samples"]), "Controle não reproduzível")
        exigir(sha(dados) not in hashes, "Áudio repetido no corpus")
        hashes.add(sha(dados))
        r.update(audio_sha256=sha(dados), bytes=len(dados), wav=info)
    novo["selado"] = True
    return novo


def verificar_corpus(m, corpus):
    validar_manifesto(m, exigir_selado=True)
    verificar_fonte(m, corpus)
    hashes = set()
    for r in m["fala"] + m["controles"]:
        dados = caminho_audio(corpus, r["arquivo"]).read_bytes()
        exigir(len(dados) == r["bytes"] and sha(dados) == r["audio_sha256"],
               f"Áudio alterado: {r['id']}")
        info = info_wav(dados)
        exigir(info == r.get("wav") and info["num_samples"] == r["num_samples"],
               "Metadados WAV divergentes")
        exigir(r["audio_sha256"] not in hashes, "Áudio repetido no corpus")
        hashes.add(r["audio_sha256"])
        if r["id"].startswith("controle:"):
            exigir(dados == audio_controle(r["id"].split(":", 1)[1], r["num_samples"]),
                   "Controle não reproduzível")


def audio_controle(nome, n):
    rng = random.Random(SEED)
    if nome == "ruido_branco":
        valores = (rng.randint(-8192, 8192) for _ in range(n))
    elif nome == "tom_440hz":
        valores = (round(8192 * math.sin(2 * math.pi * 440 * i / TAXA)) for i in range(n))
    else:
        valores = (0 for _ in range(n))
    arquivo = io.BytesIO()
    with wave.open(arquivo, "wb") as w:
        w.setparams((1, 2, TAXA, 0, "NONE", "not compressed"))
        w.writeframes(b"".join(struct.pack("<h", v) for v in valores))
    return arquivo.getvalue()


def gerar_controles(diretorio):
    pasta = Path(diretorio)
    pasta.mkdir(exist_ok=False)
    for nome, n in CONTROLES:
        with (pasta / f"{nome}.wav").open("xb") as arquivo:
            arquivo.write(audio_controle(nome, n))


def distancia(a, b):
    anterior = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        atual = [i]
        for j, y in enumerate(b, 1):
            atual.append(min(atual[-1] + 1, anterior[j] + 1, anterior[j-1] + (x != y)))
        anterior = atual
    return anterior[-1]


def metricas(referencia, texto):
    ref, hip = normalizar(referencia), normalizar(texto)
    palavras, caracteres = ref.split(), "".join(ref.split())
    ew = distancia(palavras, hip.split())
    ec = distancia(caracteres, "".join(hip.split()))
    return {"erros_palavras": ew, "palavras": len(palavras),
            "erros_caracteres": ec, "caracteres": len(caracteres),
            "wer": ew / len(palavras) if palavras else None,
            "cer": ec / len(caracteres) if caracteres else None}


def numero_finito(valor):
    return type(valor) in (int, float) and math.isfinite(valor)


def validar_vad(dados, digest, manifesto_sha, m):
    """Confere os bytes originais: trocar medidas sem trocar digest é recusado."""
    exigir(isinstance(dados, bytes) and sha(dados) == digest, "Hash do VAD diferente dos bytes")
    v = json.loads(dados, object_pairs_hook=_json_sem_duplicatas)
    exigir(isinstance(v, dict) and v.get("schema") == 1
           and v.get("manifesto_sha256") == manifesto_sha, "VAD de outro manifesto/schema")
    exigir(v.get("parametros") == COMUM["vad_previo"], "Parâmetros VAD diferentes")
    medidas = v.get("medidas")
    exigir(isinstance(medidas, list) and len(medidas) == 28, "VAD exige 28 medidas")
    esperados = {r["id"]: r for r in m["fala"] + m["controles"]}
    vistos = {}
    for r in medidas:
        exigir(isinstance(r, dict), "Medida VAD deve ser objeto")
        ident = r.get("id")
        exigir(isinstance(ident, str) and ident in esperados and ident not in vistos,
               "ID VAD repetido/desconhecido")
        exigir(r.get("audio_sha256") == esperados[ident]["audio_sha256"], "Hash áudio VAD diferente")
        duracao, fala, trechos = r.get("duracao_s"), r.get("fala_s"), r.get("trechos")
        exigir(numero_finito(duracao) and abs(duracao - esperados[ident]["num_samples"] / TAXA) <= .01,
               "Duração VAD diferente dos frames")
        exigir(numero_finito(fala) and 0 <= fala <= duracao
               and type(trechos) is int and trechos >= 0, "Medida VAD inválida")
        vistos[ident] = r
    exigir(vistos.keys() == esperados.keys(), "Cobertura VAD incompleta")
    return vistos


def validar_rodada(rodada, manifesto_sha, m, vad_dados, vad_sha):
    medidas = validar_vad(vad_dados, vad_sha, manifesto_sha, m)
    exigir(isinstance(rodada, dict), "Rodada deve ser objeto")
    exigir(rodada.get("schema") == 1 and rodada.get("manifesto_sha256") == manifesto_sha,
           "Rodada de outro manifesto/schema")
    exigir(rodada.get("vad_sha256") == vad_sha, "Rodada de outro VAD")
    candidato = rodada.get("candidato")
    exigir(candidato in CANDIDATOS and rodada.get("parametros") == CANDIDATOS[candidato],
           "Candidato/parâmetros diferentes do contrato público")
    esperados = {r["id"]: r for r in m["fala"] + m["controles"]}
    resultados = rodada.get("resultados")
    exigir(isinstance(resultados, list) and len(resultados) == 28, "Rodada exige 28 resultados")
    vistos = {}
    for r in resultados:
        exigir(isinstance(r, dict), "Resultado inválido")
        ident = r.get("id")
        exigir(isinstance(ident, str) and ident in esperados and ident not in vistos,
               "Resultado repetido ou desconhecido")
        exigir(r.get("status") == "ok" and isinstance(r.get("transcricao"), str),
               "Falha de execução não equivale a transcrição vazia")
        origem = r.get("origem")
        permitida = "whisper" if candidato == "whisper" else "gemini"
        exigir(origem in (permitida, "vad"), "Origem incompatível: reserva disfarçada")
        fala = r.get("fala_s")
        exigir(numero_finito(fala) and fala == medidas[ident]["fala_s"], "Resultado diverge do VAD comum")
        exigir((origem == "vad") == (fala < .3), "Origem diverge do limiar VAD comum")
        if origem == "vad":
            exigir(r["transcricao"] == "", "Resultado VAD inválido")
        elif origem == "gemini":
            conteudo = r.get("conteudo")
            exigir(conteudo in ("fala", "silencio", "ruido", "musica", "inaudivel"),
                   "Conteúdo Gemini inválido")
            exigir(conteudo == "fala" or r["transcricao"] == "", "Gemini sem fala deve retornar texto vazio")
        exigir(r.get("audio_sha256") == esperados[ident]["audio_sha256"], "Hash de áudio diferente")
        vistos[ident] = r["transcricao"]
    exigir(vistos.keys() == esperados.keys(), "Cobertura incompleta")
    return candidato, vistos


def resumir(m, textos, metadados=()):
    fala = {r["id"]: metricas(r["referencia"], textos[r["id"]]) for r in m["fala"]}
    valores = list(fala.values())
    resumo = {}
    for taxa, erros, total in (("wer", "erros_palavras", "palavras"),
                               ("cer", "erros_caracteres", "caracteres")):
        resumo[f"micro_{taxa}"] = sum(v[erros] for v in valores) / sum(v[total] for v in valores)
        resumo[f"macro_{taxa}"] = sum(v[taxa] for v in valores) / len(valores)
    controles = {}
    for r in m["controles"]:
        texto = normalizar(textos[r["id"]])
        controles[r["id"]] = {**metricas("", texto), "insercoes": len(texto.split()),
                                "falso_positivo": bool(texto)}
    contagens = {}
    por_id = {r["id"]: r for r in metadados}
    for grupo in ("fala", "controles"):
        origens, conteudos = {}, {}
        vazios = 0
        for r in m[grupo]:
            meta = por_id.get(r["id"], {})
            origem = meta.get("origem", "nao_informada")
            conteudo = (meta["conteudo"] if origem == "gemini" else
                        "sem_fala" if origem == "vad" else "nao_informado")
            origens[origem] = origens.get(origem, 0) + 1
            conteudos[conteudo] = conteudos.get(conteudo, 0) + 1
            vazios += not normalizar(textos[r["id"]])
        contagens[grupo] = {"por_origem": origens, "por_conteudo": conteudos, "vazios": vazios}
    return {**resumo, "fala": fala, "controles": controles, "contagens": contagens,
            "falsos_positivos_controles": sum(c["falso_positivo"] for c in controles.values())}


def bootstrap(atual, candidato, repeticoes=10000, seed=SEED):
    """Pareado por frase: reamostra contagens e calcula delta micro WER."""
    exigir(atual.keys() == candidato.keys() and len(atual) == 24, "Bootstrap exige 24 pares")
    pares = [(atual[k], candidato[k]) for k in sorted(atual)]
    rng = random.Random(seed)
    deltas = []
    for _ in range(repeticoes):
        amostra = [pares[rng.randrange(24)] for _ in range(24)]
        denominador = sum(a["palavras"] for a, _ in amostra)
        deltas.append(sum(b["erros_palavras"] - a["erros_palavras"] for a, b in amostra)
                      / denominador)
    deltas.sort()
    def quantil(p):
        indice = (len(deltas) - 1) * p
        baixo = int(indice)
        alto = min(baixo + 1, len(deltas) - 1)
        return deltas[baixo] + (deltas[alto] - deltas[baixo]) * (indice - baixo)
    return {"n_frases": 24, "repeticoes": repeticoes, "seed": seed,
            "delta_micro_wer": sum(b["erros_palavras"] - a["erros_palavras"] for a, b in pares)
            / sum(a["palavras"] for a, _ in pares),
            "ic95_percentil": [quantil(.025), quantil(.975)],
            "ganhou": sum(b["erros_palavras"] < a["erros_palavras"] for a, b in pares),
            "perdeu": sum(b["erros_palavras"] > a["erros_palavras"] for a, b in pares),
            "empatou": sum(b["erros_palavras"] == a["erros_palavras"] for a, b in pares)}


def avaliar(m, manifesto_sha, corpus, rodadas, vad_dados, vad_sha):
    verificar_corpus(m, corpus)
    medidas = validar_vad(vad_dados, vad_sha, manifesto_sha, m)
    entradas = {}
    metadados = {}
    for rodada in rodadas:
        nome, textos = validar_rodada(rodada, manifesto_sha, m, vad_dados, vad_sha)
        exigir(nome not in entradas, "Candidato duplicado")
        entradas[nome] = textos
        metadados[nome] = rodada["resultados"]
    exigir(entradas.keys() == CANDIDATOS.keys(), "Exige atual, flash_high e whisper")
    resultados = {nome: resumir(m, textos, metadados[nome]) for nome, textos in entradas.items()}
    comparacoes = {nome: bootstrap(resultados["atual"]["fala"], resultados[nome]["fala"])
                   for nome in ("flash_high", "whisper")}
    return {"schema": 1, "manifesto_sha256": manifesto_sha, "rodadas": resultados,
            "vad_comum": {"sha256": vad_sha, "parametros": dict(COMUM["vad_previo"]),
                          "cobertura": len(medidas), "abaixo_limiar": sum(r["fala_s"] < .3 for r in medidas.values())},
            "comparacoes": comparacoes, "custos": None, "latencias": None,
            "limite_custos_latencias": "não medidos; entradas contêm apenas transcrições, sem tempo/RTF",
            "promocao": "não decidida; avaliação pública limitada"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="acao", required=True)
    s = sub.add_parser("selecionar")
    s.add_argument("--tsv", type=Path, required=True)
    s.add_argument("--saida", type=Path, required=True)
    c = sub.add_parser("controles")
    c.add_argument("--diretorio", type=Path, required=True)
    for nome in ("selar", "avaliar"):
        p = sub.add_parser(nome)
        p.add_argument("--manifesto", type=Path, required=True)
        p.add_argument("--corpus", type=Path, required=True)
        p.add_argument("--saida", type=Path, required=True)
        if nome == "avaliar":
            p.add_argument("--rodadas", type=Path, nargs=3, required=True)
            p.add_argument("--vad", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if hasattr(args, "saida"):
            exigir(not args.saida.exists() and not args.saida.is_symlink(), "Saída já existe")
        if args.acao == "selecionar":
            escrever_novo(args.saida, selecionar(args.tsv.read_bytes()))
        elif args.acao == "controles":
            gerar_controles(args.diretorio)
        else:
            m, digest = ler_json(args.manifesto)
            vad_dados = args.vad.read_bytes() if args.acao == "avaliar" else None
            resultado = (selar(m, args.corpus) if args.acao == "selar" else
                         avaliar(m, digest, args.corpus, [ler_json(p)[0] for p in args.rodadas],
                                 vad_dados, sha(vad_dados)))
            escrever_novo(args.saida, resultado)
    except (Invalido, OSError, ValueError, TypeError, KeyError) as erro:
        parser.exit(2, f"Recusado: {erro}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
