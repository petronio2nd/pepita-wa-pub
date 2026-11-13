import importlib.util
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent


def carregar():
    spec = importlib.util.spec_from_file_location("pacote", RAIZ / "ferramentas" / "pacote_so_exportar.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def pacote(tmp_path_factory):
    destino = tmp_path_factory.mktemp("dist")
    original = {p.name: p.read_bytes() for p in (RAIZ / "extensao").glob("*.js*")}
    pasta, arquivo_zip = carregar().gerar(destino, "pepita-wa-so-exportar-teste")
    # A extensao do usuario nao muda.
    assert {p.name: p.read_bytes() for p in (RAIZ / "extensao").glob("*.js*")} == original
    return pasta, arquivo_zip


def test_pacote_tira_o_servidor_e_as_opcoes(pacote):
    pasta, arquivo_zip = pacote
    manifesto = json.loads((pasta / "manifest.json").read_text(encoding="utf-8"))
    assert manifesto["host_permissions"] == ["https://web.whatsapp.com/*"]
    assert manifesto["permissions"] == ["storage"]
    assert "const ROTINA = false;" in (pasta / "fundo.js").read_text(encoding="utf-8")
    ponte = (pasta / "ponte.js").read_text(encoding="utf-8")
    assert "audio: false, imagem: false, anexos: true" in ponte
    assert "let auto = false;" in ponte
    assert "fieldset:has([data-r=auto])" in ponte
    assert (pasta / "nucleo.js").read_bytes() == (pasta / "nucleo-pagina.js").read_bytes()
    assert not (pasta / "testes").exists()
    assert not (pasta / "icones" / "gerar.py").exists()
    assert "Carregar sem compactacao" in (pasta / "LEIA-ME.txt").read_text(encoding="utf-8-sig")
    with zipfile.ZipFile(arquivo_zip) as z:
        nomes = z.namelist()
    assert "pepita-wa-so-exportar-teste/manifest.json" in nomes
    assert "pepita-wa-so-exportar-teste/vendor/wppconnect-wa.js" in nomes


JS = r"""
process.env.TZ = 'America/Sao_Paulo';
const N = require(process.argv[1] + '/nucleo.js');
const MD = require(process.argv[1] + '/markdown.js');
const t = Date.UTC(2026, 8, 17, 17, 25, 9) / 1000;
const msgs = [
  { id: 'a1', tipo: 'ptt', t, eu: false, autor: 'Renata Silva', subtipo: '', texto: '', duracao: 12, mimetype: 'audio/ogg; codecs=opus' },
  { id: 'i1', tipo: 'image', t, eu: false, autor: 'Renata Silva', subtipo: '', texto: '', mimetype: 'image/jpeg' },
];
const nomes = N.nomesDosAnexos(msgs);
const anexos = new Map([...nomes].map(([id, arquivo]) => [id, { arquivo }]));
const chat = { id: '556290000000@c.us', nome: 'Renata Silva', grupo: false };
const md = MD.gerar(chat, msgs, new Map(), new Date(), { modo: 'carregadas', anexos, audiosDesligados: 1 });
console.log(JSON.stringify({ nomes: Object.fromEntries(nomes), md }));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="sem node")
def test_pacote_anexa_o_audio(pacote):
    pasta, _ = pacote
    for js in ("ponte.js", "nucleo.js", "markdown.js", "fundo.js"):
        subprocess.run(["node", "--check", str(pasta / js)], check=True)
    saida = subprocess.run(["node", "-e", JS, str(pasta)], capture_output=True, text=True, encoding="utf-8", check=True)
    r = json.loads(saida.stdout)
    assert r["nomes"] == {
        "a1": "anexos/2026-09-17_142509_audio.opus",
        "i1": "anexos/2026-09-17_142509_imagem.jpg",
    }
    assert "🎙️ *audio 0:12* · [anexos/2026-09-17_142509_audio.opus]" in r["md"]
    assert "(fotos, videos, audios e documentos)" in r["md"]
    assert "versao so exportar, sem transcricao: 1 de 1 audio(s) sem texto" in r["md"]


JS_PARALELO = r"""
const N = require(process.argv[1] + '/nucleo.js');
const msgs = Array.from({ length: 10 }, (_, i) => ({ id: 'i' + i, tipo: 'image', t: 1000 + i, mimetype: 'image/jpeg' }));
let ativos = 0, pico = 0;
const deps = {
  coletar: async () => ({ chat: { nome: 'Renata Silva' }, mensagens: msgs }),
  anexar: async (m, caminho) => {
    pico = Math.max(pico, ++ativos);
    await new Promise((ok) => setTimeout(ok, 5));
    ativos--;
    return m.id === 'i3' ? { erro: 'nao disponivel' } : { arquivo: caminho };
  },
  gerar: (chat, mensagens, textos, info) => info,
  salvar: async () => 'ok',
};
(async () => {
  const r = await N.exportar(deps, { args: {}, audio: false, imagem: false, anexos: true });
  let salvo = null;
  const op = N.criarOperacao();
  const p = N.exportar({ ...deps, salvar: async () => (salvo = 'nao devia') }, { args: {}, audio: false, imagem: false, anexos: true }, op);
  op.cancelar();
  const cancelado = await p.then(() => false, (e) => e.tipo === 'cancelado');
  console.log(JSON.stringify({ pico, anexos: r.resumo.anexos, falhas: r.resumo.anexosFalhas, cancelado, salvo }));
})();
"""


@pytest.mark.skipif(not shutil.which("node"), reason="sem node")
def test_pacote_baixa_4_anexos_ao_mesmo_tempo(pacote):
    pasta, _ = pacote
    saida = subprocess.run(["node", "-e", JS_PARALELO, str(pasta)], capture_output=True, text=True, encoding="utf-8", check=True)
    r = json.loads(saida.stdout)
    assert r == {"pico": 4, "anexos": 9, "falhas": 1, "cancelado": True, "salvo": None}
