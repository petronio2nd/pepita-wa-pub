"""Midia na leitura do MCP: caminho do anexo, OCR do .md e pendencias. Dado ficticio, pasta temporaria."""

from datetime import datetime

from wacervo.anexos_leitura import BRASILIA, Anexos, ocr_do_md
from wacervo.mcp_whatsapp import INSTRUCOES, FERRAMENTAS, Ferramentas, formatar_leitura

ID = "199999999999999@lid"
CHAT = {"id": ID, "nome": "Renata Silva", "telefone": "556290000000"}


def _t(hora: str) -> int:
    return int(datetime.strptime(f"2026-11-11 {hora}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=BRASILIA).timestamp())


def _msg(hora, tipo="image", **extra):
    return {"id": f"m{hora}", "t": _t(hora), "tipo": tipo, "autor": "Renata Silva", **extra}


MD = f"""# Renata Silva

> contato · `{ID}` · 2026-11-11

## 2026-11-11

**12:43 · Renata Silva**
🖼️ *imagem* · [anexos/2026-11-11_124311_imagem.jpg](anexos/2026-11-11_124311_imagem.jpg)
*texto na imagem:*
> Conta bloqueada
> Esqueceu a senha?

**12:44 · Renata Silva**
🖼️ *imagem* · [anexos/2026-11-11_124400_imagem.jpg](anexos/2026-11-11_124400_imagem.jpg) — *legenda:* olha
*texto curto na imagem (pode ser ruido):* OK

**12:45 · Renata Silva**
🖼️ *imagem* · [anexos/2026-11-11_124500_imagem.jpg](anexos/2026-11-11_124500_imagem.jpg)
"""


def _pasta(tmp_path, nomes=("2026-11-11_124311_imagem.jpg", "2026-11-11_124400_imagem.jpg",
                           "2026-11-11_124500_imagem.jpg", "2026-11-11_124311_imagem-2.jpg")):
    d = tmp_path / "renata-silva"
    (d / "anexos").mkdir(parents=True)
    (d / "renata-silva_2026-11-11.md").write_text(MD, encoding="utf-8")
    for n in nomes:
        (d / "anexos" / n).write_bytes(b"x")
    return d


def test_ocr_do_md_le_texto_curto_e_sem_texto():
    o = ocr_do_md(MD)
    assert o["2026-11-11_124311_imagem.jpg"]["texto"] == "Conta bloqueada\nEsqueceu a senha?"
    assert o["2026-11-11_124400_imagem.jpg"]["curto"] == "OK"
    assert o["2026-11-11_124500_imagem.jpg"] == {}


def test_leitura_traz_caminho_e_ocr(tmp_path):
    d = _pasta(tmp_path)
    ms = [_msg("12:43:11"), _msg("12:44:00"), _msg("12:45:00")]
    Anexos(lambda: tmp_path).juntar(CHAT, ms)
    saida = formatar_leitura(CHAT, ms)
    assert str(d / "anexos" / "2026-11-11_124311_imagem.jpg") in saida
    assert "> Conta bloqueada" in saida and "pode errar" in saida
    assert "texto curto na imagem" in saida and "[nenhum texto lido]" in saida
    assert "PENDENTE" not in saida


def test_mesma_hora_pega_arquivos_em_ordem(tmp_path):
    _pasta(tmp_path)
    ms = [_msg("12:43:11"), _msg("12:43:11")]
    Anexos(lambda: tmp_path).juntar(CHAT, ms)
    assert ms[0]["anexo"]["arquivo"].endswith("124311_imagem.jpg")
    assert ms[1]["anexo"]["arquivo"].endswith("124311_imagem-2.jpg")


def test_arquivo_sem_bloco_no_md_marca_ocr_pendente(tmp_path):
    d = _pasta(tmp_path)
    (d / "anexos" / "2026-11-11_130000_imagem.jpg").write_bytes(b"x")
    ms = [_msg("13:00:00")]
    Anexos(lambda: tmp_path).juntar(CHAT, ms)
    assert "[OCR PENDENTE" in formatar_leitura(CHAT, ms)


def test_midia_nao_exportada_vira_pendente_sem_quebrar(tmp_path):
    _pasta(tmp_path)
    ms = [_msg("14:00:00"), _msg("14:01:00", tipo="chat", texto="oi"),
          _msg("14:02:00", tipo="video"), _msg("14:03:00", visualizacaoUnica=True)]
    Anexos(lambda: tmp_path).juntar(CHAT, ms)
    saida = formatar_leitura(CHAT, ms)
    assert "[IMAGEM PENDENTE: ainda nao exportada]" in saida
    assert "[VIDEO PENDENTE: ainda nao exportada]" in saida
    assert "2 midia(s) ainda nao exportada(s)" in saida
    assert "anexo" not in ms[3]  # visualizacao unica nao entra


def test_sem_pasta_ou_raiz_invalida_nao_quebra(tmp_path):
    ms = [_msg("12:43:11")]
    Anexos(lambda: tmp_path).juntar(CHAT, ms)
    assert "PENDENTE" in formatar_leitura(CHAT, ms)
    ms = [_msg("12:43:11")]
    Anexos(lambda: None).juntar(CHAT, ms)
    assert ms[0]["anexo"]["pendente"].startswith("sem pasta")


def test_documento_acha_pelo_nome(tmp_path):
    d = _pasta(tmp_path)
    (d / "anexos" / "2026-11-11_150000_contrato.pdf").write_bytes(b"x")
    ms = [_msg("15:00:00", tipo="document", arquivo="contrato.pdf")]
    Anexos(lambda: tmp_path).juntar(CHAT, ms)
    assert ms[0]["anexo"]["arquivo"].endswith("150000_contrato.pdf")
    assert "arquivo (abra com Read)" in formatar_leitura(CHAT, ms)


def test_ferramenta_ler_junta_midia_e_sobrevive_a_falha(tmp_path):
    _pasta(tmp_path)
    dados = {"chat": CHAT, "mensagens": [_msg("12:43:11")]}
    f = Ferramentas(lambda op, a: {"ok": True, "dados": dados}, anexos=Anexos(lambda: tmp_path))
    assert "> Conta bloqueada" in f.ler({"conversa": ID})

    class Quebrado:
        def juntar(self, *_):
            raise RuntimeError("x")

    f = Ferramentas(lambda op, a: {"ok": True, "dados": {"chat": CHAT, "mensagens": [_msg("12:43:11")]}},
                    anexos=Quebrado())
    assert "[🖼️ imagem]" in f.ler({"conversa": ID})


def test_instrucoes_e_descricao_falam_de_ocr():
    assert "OCR" in INSTRUCOES and "pode errar" in INSTRUCOES
    ler = next(t for t in FERRAMENTAS if t["name"] == "whatsapp_ler")
    assert "OCR" in ler["description"] and "PENDENTE" in ler["description"]
