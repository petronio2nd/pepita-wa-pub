// Service worker: unico lugar que fala com o pepita-wa local (http://127.0.0.1).
// O conteudo da pagina nao pode: o fetch dele sai com a origem do WhatsApp,
// e o servidor recusa qualquer origem que nao seja chrome-extension://.
// Desde a 0.16.0 tambem cuida da rodada automatica do "Exportar todas" e da
// trava unica entre ela e o clique (chrome.storage.session).

importScripts('nucleo.js');
const N = globalThis.wacervoNucleo;

const PORTA_PADRAO = 8765;
// SEG-10: todo pedido ao servidor leva este cabecalho. Pagina web nao consegue mandar cabecalho
// proprio sem preflight (que o servidor recusa), entao o `wa servir` o exige de quem vem sem `Origin`.
const CABECALHO_PROPRIO = { 'X-Pepita-WA': '1' };
// A versao "so exportar" (ferramentas/pacote_so_exportar.py) nao tem servidor: troca para false.
const ROTINA = true;
// Ponte do agente (`wa mcp`, 0.19.0). A versao "so exportar" troca para false.
const AGENTE = true;
const WHATSAPP = 'https://web.whatsapp.com/';
const TRAVA_VALIDADE_MS = 5 * 60 * 1000; // renovada a cada 20 s por quem roda

async function porta() {
  const { porta } = await chrome.storage.local.get({ porta: PORTA_PADRAO });
  return Number(porta) || PORTA_PADRAO;
}

function deBase64(texto) {
  const bin = atob(texto);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

async function status() {
  const url = `http://127.0.0.1:${await porta()}/status`;
  try {
    const resp = await fetch(url, { headers: CABECALHO_PROPRIO, signal: AbortSignal.timeout(3000) });
    return { ok: resp.ok, ...(await resp.json()) };
  } catch (erro) {
    return { ok: false, erro: `pepita-wa fora do ar em ${url}` };
  }
}

// Prazo por pedido. Audio: o Gemini (ate [transcricao.gemini] prazo_s) e, se ele
// falhar, carregar o whisper de reserva (ate uns minutos) mais o dobro da duracao. Estourar o prazo nao para o servidor: ele termina e guarda no cache,
// e a proxima tentativa volta na hora.
function prazoDe(rota, duracao) {
  return rota === '/transcrever' ? (10 * 60 + 2 * (Number(duracao) || 0)) * 1000 : 2 * 60 * 1000;
}

const emCurso = new Map(); // op -> Set<AbortController>

async function processar({ rota, base64, duracao, op }) {
  const url = `http://127.0.0.1:${await porta()}${rota}`;
  const controle = new AbortController();
  const prazo = prazoDe(rota, duracao);
  const relogio = setTimeout(() => controle.abort('prazo'), prazo);
  if (op) {
    if (!emCurso.has(op)) emCurso.set(op, new Set());
    emCurso.get(op).add(controle);
  }
  let resp;
  try {
    resp = await fetch(url, {
      method: 'POST',
      headers: { ...CABECALHO_PROPRIO, 'Content-Type': 'application/octet-stream' },
      body: deBase64(base64),
      signal: controle.signal,
    });
  } catch (erro) {
    if (controle.signal.reason === 'prazo') {
      return {
        erro: `o servidor nao respondeu em ${Math.round(prazo / 60000)} min; ele continua e guarda o resultado no cache`,
        tipo: 'prazo',
      };
    }
    if (controle.signal.aborted) return { erro: 'cancelado', tipo: 'cancelado' };
    return { erro: 'pepita-wa fora do ar', tipo: 'fora' };
  } finally {
    clearTimeout(relogio);
    emCurso.get(op)?.delete(controle);
    if (op && !emCurso.get(op)?.size) emCurso.delete(op);
  }
  let dados = {};
  try {
    dados = await resp.json();
  } catch {}
  if (!resp.ok) return { erro: dados.erro || `HTTP ${resp.status}`, tipo: 'servidor' };
  const r = { texto: dados.texto || '', cache: !!dados.cache };
  if (dados.curto) r.curto = dados.curto;
  return r;
}

// Rotas JSON do servidor (marcas, rodada, lote, rotina) e a gravacao (corpo em
// bytes). Fora do ar ou recusado, devolve {erro, tipo} como o processar.
async function pedirServidor(rota, { corpo, bytes, prazo = 15000, versao = '' } = {}) {
  const url = `http://127.0.0.1:${await porta()}${rota}`;
  let resp;
  try {
    const comCorpo = corpo !== undefined || bytes !== undefined;
    resp = await fetch(url, {
      method: comCorpo ? 'POST' : 'GET',
      headers: { ...CABECALHO_PROPRIO, 'X-Pepita-WA-Versao': versao,
        ...(bytes !== undefined ? { 'Content-Type': 'application/octet-stream' }
          : corpo !== undefined ? { 'Content-Type': 'application/json' } : {}) },
      body: bytes !== undefined ? bytes : corpo !== undefined ? JSON.stringify(corpo) : undefined,
      signal: AbortSignal.timeout(prazo),
    });
  } catch {
    return { erro: 'pepita-wa fora do ar', tipo: 'fora' };
  }
  let dados = {};
  try {
    dados = await resp.json();
  } catch {}
  if (!resp.ok) return { erro: dados.erro || `HTTP ${resp.status}`, tipo: 'servidor' };
  return { ok: true, dados };
}

const marcas = (rota, corpo, versao) => pedirServidor(rota, { corpo, versao });

// Uma parte de um arquivo do "Exportar todas" (o servidor grava dentro da raiz dele).
function gravar({ caminho, lote, base64, deslocamento = 0, fim = true, livre = false, versao }) {
  const q = new URLSearchParams({ caminho, lote, deslocamento: String(deslocamento), fim: fim ? '1' : '0', livre: livre ? '1' : '0' });
  return pedirServidor(`/gravar?${q}`, { bytes: deBase64(base64 || ''), prazo: 2 * 60 * 1000, versao });
}

// ---------- trava unica entre o clique e a rodada automatica ----------
// O service worker atende um evento por vez, mas cada um tem `await`: a fila
// abaixo faz o ler-decidir-gravar da trava sem outro pedido no meio.
let filaDaTrava = Promise.resolve();
function naFila(fn) {
  const p = filaDaTrava.then(fn, fn);
  filaDaTrava = p.catch(() => {});
  return p;
}
const lerTrava = async () => (await chrome.storage.session.get({ trava: null })).trava;
const gravarTrava = (trava) => (trava ? chrome.storage.session.set({ trava }) : chrome.storage.session.remove('trava'));

function travar(pedido) {
  return naFila(async () => {
    const d = N.decidirTrava(await lerTrava(), pedido, Date.now(), TRAVA_VALIDADE_MS);
    if (d.ok) await gravarTrava(d.trava);
    return d;
  });
}

function renovar(abaId) {
  return naFila(async () => {
    const nova = N.renovarTrava(await lerTrava(), abaId, Date.now(), TRAVA_VALIDADE_MS);
    if (nova) await gravarTrava(nova);
    return !!nova;
  });
}

// Solta a trava se ela e desta aba (ou de quem `teste` aceitar). Devolve a que saiu.
function destravar(teste) {
  return naFila(async () => {
    const atual = await lerTrava();
    if (!atual || !teste(atual)) return null;
    await gravarTrava(null);
    return atual;
  });
}

// ---------- rodada automatica ----------
// O relogio fica no wa servir (src/wacervo/rotina.py). A cada minuto o alarme
// pergunta se e hora; sem o Chrome aberto, o proprio servidor abre o chrome.exe no
// WhatsApp Web e diz `abriuChrome`. Aqui: acha a aba do WhatsApp (ou abre uma, sem
// duplicar), espera ela carregar e pede a rodada a ponte.js, que roda o mesmo
// "Exportar todas" do clique e avisa o fim (rotina-fim). No fim fecha so o que a
// rotina abriu.
const espera = (ms) => new Promise((ok) => setTimeout(ok, ms));

async function retratoDasJanelas() {
  const janelas = await chrome.windows.getAll({ populate: true });
  return janelas.map((j) => ({ id: j.id, abas: (j.tabs || []).map((a) => a.id) }));
}

async function fecharOQueAbriu(registro) {
  if (!registro?.abriuAba && !registro?.abriuChrome) return;
  const { janelas, abas } = N.oQueFechar(registro, await retratoDasJanelas().catch(() => []));
  for (const id of abas) await chrome.tabs.remove(id).catch(() => {});
  for (const id of janelas) await chrome.windows.remove(id).catch(() => {});
}

const avisarFim = (id, resultado, detalhe) =>
  marcas('/rotina/fim', { id, resultado, detalhe, automatica: true });

async function esperarCarregar(abaId, prazo = 90000) {
  for (const inicio = Date.now(); Date.now() - inicio < prazo; await espera(1000)) {
    const aba = await chrome.tabs.get(abaId).catch(() => null);
    if (!aba) return false;
    if (aba.status === 'complete') return true;
  }
  return false;
}

// O content script entra logo depois do carregamento: tenta por ate 1 min.
async function pedirAPonte(abaId, msg, prazo = 60000) {
  for (const inicio = Date.now(); Date.now() - inicio < prazo; await espera(2000)) {
    try {
      const r = await chrome.tabs.sendMessage(abaId, msg);
      if (r) return r;
    } catch {}
  }
  return null;
}

const abasDoWhatsApp = () => chrome.tabs.query({ url: 'https://web.whatsapp.com/*' });

// Chrome aberto pela rotina: o onStartup chega antes da janela que o chrome.exe abriu.
// Espera a aba do WhatsApp aparecer (ate 60 s) antes do retrato, senao a rotina
// abriria outra aba e no fim nao fecharia o Chrome.
async function esperarJanelaDoLancamento() {
  for (const inicio = Date.now(); Date.now() - inicio < 60000; await espera(1000)) {
    if ((await abasDoWhatsApp()).length) return;
  }
}

// Chrome vivo em segundo plano, sem nenhuma janela: tabs.create falha com "No current
// window" (rodada de 08/11/2026 14:38). Nesse caso abre uma janela sem foco com a aba;
// fecharOQueAbriu remove a aba e a janela some junto. Com janela aberta, o erro e outro
// e segue como falha.
async function abrirAbaDoWhatsApp() {
  try {
    return await chrome.tabs.create({ url: WHATSAPP, active: false });
  } catch (erro) {
    const abertas = await chrome.windows.getAll().catch(() => null);
    if (!abertas || abertas.length) throw erro;
    const janela = await chrome.windows.create({ url: WHATSAPP, focused: false });
    return janela.tabs[0];
  }
}

async function rodadaAutomatica({ id, abriuChrome }) {
  const motivo = N.motivoParaNaoExportarTodas(await status(), { automatica: true });
  if (motivo) return avisarFim(id, 'pulada', motivo);
  if (abriuChrome) await esperarJanelaDoLancamento();
  const janelasAntes = abriuChrome
    ? Object.fromEntries((await retratoDasJanelas()).map((j) => [j.id, j.abas]))
    : null;
  const [existente] = await abasDoWhatsApp();
  // A trava entra antes de abrir aba: com o clique em andamento, nada se abre.
  const registro = { quem: 'auto', idRodada: id, abaId: existente?.id ?? null, abriuAba: false, abriuChrome: !!abriuChrome, janelasAntes };
  const t = await travar(registro);
  // A rodada anterior ainda roda (a primeira pode passar de 30 min): esta nao conta.
  if (!t.ok && t.ocupadaPor === 'auto') return;
  if (!t.ok) return avisarFim(id, 'pulada', t.motivo);
  let aba = existente;
  let soltar = true;
  try {
    if (!aba) {
      // Aba nova sem foco: o usuario continua no que estava.
      aba = await abrirAbaDoWhatsApp();
      registro.abriuAba = true;
      registro.abaId = aba.id;
      await naFila(async () => gravarTrava({ ...(await lerTrava()), abaId: aba.id, abriuAba: true }));
    }
    // Aba descartada pelo Chrome (economia de memoria) nao tem pagina: recarrega,
    // o que nao atrapalha o usuario. E ela nao pode ser descartada no meio da rodada.
    if (aba.discarded || aba.status === 'unloaded') await chrome.tabs.reload(aba.id);
    await chrome.tabs.update(aba.id, { autoDiscardable: false }).catch(() => {});
    if (!(await esperarCarregar(aba.id))) {
      await avisarFim(id, 'falha', 'a aba do WhatsApp Web nao carregou');
      return;
    }
    const recemAberta = registro.abriuAba || registro.abriuChrome;
    const r = await pedirAPonte(aba.id, { tipo: 'rodada-auto', id, recemAberta });
    if (!r?.aceita) {
      await avisarFim(id, 'pulada', r?.motivo || 'a aba do WhatsApp nao respondeu: recarregue-a (a extensao foi recarregada depois dela)');
      return;
    }
    soltar = false; // agora e a ponte que termina (rotina-fim)
  } catch (erro) {
    await avisarFim(id, 'falha', String(erro?.message || erro).slice(0, 200));
  } finally {
    if (soltar) {
      const saiu = await destravar((x) => x.quem === 'auto' && x.idRodada === id);
      await fecharOQueAbriu(saiu || registro);
    }
  }
}

// A ponte terminou a rodada (ou desistiu: sem login, WhatsApp travado).
async function fimDaRodada(abaId, { resultado, detalhe }) {
  const saiu = await destravar((x) => x.quem === 'auto' && x.abaId === abaId);
  if (!saiu) return;
  await avisarFim(saiu.idRodada, resultado, detalhe);
  if (saiu.abaId != null) await chrome.tabs.update(saiu.abaId, { autoDiscardable: true }).catch(() => {});
  await fecharOQueAbriu(saiu);
}

// Trava vencida (aba fechada ou travada no meio): solta e, se era a automatica,
// avisa e fecha o que ela abriu.
async function limparTravaVencida() {
  const saiu = await destravar((x) => x.validade <= Date.now());
  if (saiu?.quem === 'auto') {
    await avisarFim(saiu.idRodada, 'falha', 'a rodada parou sem terminar (a aba fechou ou travou)');
    await fecharOQueAbriu(saiu);
  }
}

let perguntando = false;
async function perguntarRotina() {
  if (!ROTINA || perguntando) return;
  perguntando = true;
  try {
    await limparTravaVencida();
    const r = await marcas('/rotina/vez', {});
    if (r.ok && r.dados?.rodar) await rodadaAutomatica(r.dados);
  } catch {
  } finally {
    perguntando = false;
  }
}

function armarAlarme() {
  if (!ROTINA) return;
  chrome.alarms.get('rotina').then((a) => a || chrome.alarms.create('rotina', { periodInMinutes: 1 }));
}

if (ROTINA) {
  chrome.alarms.onAlarm.addListener((a) => a.name === 'rotina' && perguntarRotina());
  chrome.runtime.onStartup.addListener(() => {
    armarAlarme();
    perguntarRotina(); // Chrome aberto pela rotina: pergunta logo, sem esperar o alarme
  });
  chrome.runtime.onInstalled.addListener(armarAlarme);
  armarAlarme();
}

// ---------- agente (0.19.0) ----------
// O `wa mcp` pede ao wa servir (POST /agente/pedir); este laco pergunta ao servidor
// (long-poll de ate 20 s) e manda cada pedido para a aba do WhatsApp, sem esperar um
// terminar para pegar o proximo. Cada volta chama uma API da extensao, o que mantem
// o service worker acordado enquanto houver aba do WhatsApp; sem aba, o laco para, e
// o alarme de 1 min, a aba que carrega ou qualquer mensagem da ponte o religa.
const PRAZO_NA_ABA = { listar: 35000, ler: 145000, enviar: 40000, arquivar: 40000 };
let agenteRodando = false;

function comPrazo(promessa, ms) {
  let relogio;
  return Promise.race([
    promessa,
    new Promise((_, falha) => { relogio = setTimeout(() => falha(Object.assign(new Error('prazo'), { prazo: true })), ms); }),
  ]).finally(() => clearTimeout(relogio));
}

async function atenderPedido(pedido) {
  // Aceite antes de executar: se o servidor ja deu o pedido por perdido (ou nao
  // respondeu), nada roda, e o MCP ouviu "NAO rodou".
  const aceite = await pedirServidor('/agente/aceite', { corpo: { id: pedido.id }, prazo: 5000 });
  if (!aceite.ok || aceite.dados?.aceita !== true) return;
  let resposta = { ok: false, tipo: 'sem_aba', erro: 'nenhuma aba do WhatsApp Web aberta no Chrome' };
  for (const aba of await abasDoWhatsApp().catch(() => [])) {
    let r;
    try {
      r = await comPrazo(chrome.tabs.sendMessage(aba.id, { tipo: 'agente', pedido }), PRAZO_NA_ABA[pedido.op] || 30000);
    } catch (erro) {
      r = N.classificarFalhaNaAba(erro);
    }
    resposta = r || { ok: false, tipo: 'sem_resposta', erro: 'a aba do WhatsApp respondeu vazio' };
    // Outra aba pode estar pronta (o WhatsApp Web so funciona numa por vez).
    if (resposta.tipo !== 'sem_aba' && resposta.tipo !== 'nao_pronto') break;
  }
  await pedirServidor('/agente/resposta', { corpo: { id: pedido.id, ...resposta }, prazo: 15000 });
}

async function lacoDoAgente() {
  if (!AGENTE || agenteRodando) return;
  agenteRodando = true;
  try {
    for (;;) {
      if (!(await abasDoWhatsApp().catch(() => [])).length) return;
      const r = await pedirServidor('/agente/proximo', { corpo: { espera_s: 20 }, prazo: 30000 });
      if (!r.ok) {
        // Fora do ar: tenta de novo logo. Servidor sem a rota (versao antiga): espera mais.
        await espera(r.tipo === 'fora' ? 10000 : 60000);
        continue;
      }
      if (r.dados?.pedido) atenderPedido(r.dados.pedido).catch(() => {});
    }
  } finally {
    agenteRodando = false;
  }
}

if (AGENTE) {
  chrome.alarms.onAlarm.addListener(() => lacoDoAgente());
  chrome.runtime.onStartup.addListener(() => lacoDoAgente());
  chrome.tabs.onUpdated.addListener((_id, mudou, aba) => {
    if (mudou.status === 'complete' && aba.url?.startsWith(WHATSAPP)) lacoDoAgente();
  });
  if (!ROTINA) chrome.alarms.get('agente').then((a) => a || chrome.alarms.create('agente', { periodInMinutes: 1 }));
  lacoDoAgente();
}

function abortar(op) {
  for (const c of emCurso.get(op) || []) c.abort('cancelado');
  emCurso.delete(op);
}

// Indicador no icone da extensao, so na aba que pediu: o painel pode estar fechado.
function indicador(abaId, texto) {
  if (abaId == null) return;
  chrome.action.setBadgeBackgroundColor({ tabId: abaId, color: '#1f7a4d' }).catch(() => {});
  chrome.action.setBadgeText({ tabId: abaId, text: texto || '' }).catch(() => {});
}

chrome.runtime.onMessage.addListener((msg, remetente, responder) => {
  lacoDoAgente();
  if (msg?.tipo === 'agente-oi') {
    responder({ ok: true });
    return false;
  }
  if (msg?.tipo === 'status') {
    status().then(responder);
    return true;
  }
  if (msg?.tipo === 'processar' && (msg.rota === '/transcrever' || msg.rota === '/ocr')) {
    processar(msg).then(responder);
    return true;
  }
  if (msg?.tipo === 'marcas') {
    marcas(`/marcas?versao=${encodeURIComponent(msg.versao || '')}`).then(responder);
    return true;
  }
  if (msg?.tipo === 'importadas') {
    marcas(`/importadas?id=${encodeURIComponent(msg.id || '')}`).then(responder);
    return true;
  }
  // So a linha do painel: GET /rodada nao traz as marcas de todas as conversas.
  if (msg?.tipo === 'ultimaRodada') {
    marcas('/rodada').then(responder);
    return true;
  }
  if (msg?.tipo === 'marcar' || msg?.tipo === 'rodada') {
    marcas(msg.tipo === 'marcar' ? '/marca' : '/rodada', msg.corpo || {}, msg.versao).then(responder);
    return true;
  }
  if (msg?.tipo === 'abortar') {
    abortar(msg.op);
    responder({ ok: true });
    return false;
  }
  if (msg?.tipo === 'indicador') {
    indicador(remetente.tab?.id, String(msg.texto || '').slice(0, 4));
    responder({ ok: true });
    return false;
  }
  if (msg?.tipo === 'manter') {
    // Para o worker nao dormir no meio de uma exportacao ou da fila da tela; no
    // "Exportar todas", renova tambem a trava.
    if (msg.trava) {
      renovar(remetente.tab?.id).then((ok) => responder({ ok }));
      return true;
    }
    responder({ ok: true });
    return false;
  }
  if (msg?.tipo === 'gravar') {
    gravar(msg).then(responder);
    return true;
  }
  if (msg?.tipo === 'arquivo') {
    pedirServidor(`/arquivo?${new URLSearchParams({ caminho: String(msg.caminho || '') })}`).then(responder);
    return true;
  }
  if (msg?.tipo === 'lote') {
    marcas('/lote', { lote: msg.lote, desfazer: !!msg.desfazer }).then(responder);
    return true;
  }
  if (msg?.tipo === 'travar') {
    // So o clique pede por aqui; a automatica trava no proprio fundo.js.
    travar({ quem: 'manual', abaId: remetente.tab?.id ?? null }).then(responder);
    return true;
  }
  if (msg?.tipo === 'destravar') {
    destravar((x) => x.quem === 'manual' && x.abaId === remetente.tab?.id).then(() => responder({ ok: true }));
    return true;
  }
  if (msg?.tipo === 'rotina') {
    marcas('/rotina').then(responder);
    return true;
  }
  if (msg?.tipo === 'rotina-ligar') {
    marcas('/rotina', { ligada: !!msg.ligada }).then(responder);
    return true;
  }
  if (msg?.tipo === 'rotina-fim') {
    fimDaRodada(remetente.tab?.id, msg).then(() => responder({ ok: true }));
    return true;
  }
  return false;
});

chrome.action.onClicked.addListener(async (aba) => {
  if (aba.url?.startsWith('https://web.whatsapp.com/')) {
    try {
      await chrome.tabs.sendMessage(aba.id, { tipo: 'alternar-painel' });
      return;
    } catch {
      // Aba aberta antes de instalar/recarregar a extensao: sem content script.
      await chrome.tabs.reload(aba.id);
      return;
    }
  }
  const [existente] = await chrome.tabs.query({ url: 'https://web.whatsapp.com/*' });
  if (existente) {
    await chrome.tabs.update(existente.id, { active: true });
    await chrome.windows.update(existente.windowId, { focused: true });
  } else {
    await chrome.tabs.create({ url: 'https://web.whatsapp.com/' });
  }
});
