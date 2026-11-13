// Content script isolado: desenha o painel, conversa com a pagina.js (mundo
// MAIN, via postMessage) e com o fundo.js (via chrome.runtime), monta o .md e
// salva onde o usuario escolher. A logica testavel (prazos, periodo, exportacao,
// fila) esta em nucleo.js; aqui fica o que depende do DOM e do chrome.*.
(() => {
  'use strict';

  const MD = globalThis.wacervoMarkdown;
  const N = globalThis.wacervoNucleo;
  const VERSAO = chrome.runtime.getManifest().version;
  const LIMITE_MIDIA = 40 * 1024 * 1024; // o servidor aceita ate 64 MB; base64 infla 4/3.
  // Prazos de inatividade de cada pedido a pagina.js (a coleta renova a cada pagina lida).
  const PRAZO = {
    chat: 5000, audio: 10000, coletar: 60000, coletarConversa: 60000, listarConversas: 60000,
    midia: 60000, midiaGrande: 300000, cancelar: 5000,
    agenteListar: 30000, agenteLer: 60000, agenteEnviar: 30000, agenteGrupos: 30000,
  };
  const FUSO = Intl.DateTimeFormat().resolvedOptions().timeZone || '';

  // ---------- pagina.js ----------
  const canal = N.criarCanal((msg) => window.postMessage(msg, location.origin));
  window.addEventListener('message', (ev) => {
    if (ev.source === window) canal.receber(ev.data);
  });
  const pedir = (acao, args, opcoes = {}) => canal.pedir(acao, args, { prazo: PRAZO[acao], ...opcoes });

  // ---------- fundo.js ----------
  const aoFundo = (msg) => chrome.runtime.sendMessage(msg);

  function base64(buffer) {
    const bytes = new Uint8Array(buffer);
    let bin = '';
    for (let i = 0; i < bytes.length; i += 0x8000) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return btoa(bin);
  }

  // Baixa a midia pela pagina. Nunca rejeita: devolve {midia} ou {erro, tipo}.
  // Video e documento podem ser grandes: prazo maior.
  async function baixarMidia(m) {
    const prazo = m.tipo === 'video' || m.tipo === 'document' ? PRAZO.midiaGrande : PRAZO.midia;
    // O wa-js as vezes pede o download e ainda nao acha o arquivo logo depois
    // ("Media not found"); esperar um pouco e pedir de novo costuma resolver.
    for (let tentativa = 1; ; tentativa++) {
      try {
        return { midia: await pedir('midia', { msgId: m.id }, { prazo }) };
      } catch (erro) {
        if (erro.tipo === 'prazo') {
          return { erro: `o WhatsApp nao entregou a midia em ${prazo / 1000} s`, tipo: 'prazo' };
        }
        if (tentativa >= 3 || !/not found/i.test(erro.message)) {
          return { erro: `nao disponivel no WhatsApp (${erro.message})`, tipo: 'indisponivel' };
        }
        await new Promise((ok) => setTimeout(ok, 1500 * tentativa));
      }
    }
  }

  // Na exportacao com anexos, a foto baixada para o OCR fica guardada ate o
  // anexo dela ser gravado logo em seguida: uma midia so, nunca a conversa toda.
  let guardada = null; // {id, promessa}
  function baixarGuardando(m) {
    if (guardada?.id === m.id) return guardada.promessa;
    guardada = { id: m.id, promessa: baixarMidia(m) };
    return guardada.promessa;
  }
  function baixarParaAnexo(m) {
    const p = guardada?.id === m.id ? guardada.promessa : baixarMidia(m);
    guardada = null;
    return p;
  }

  // Baixa a midia pela pagina e manda ao servidor. Nunca rejeita: devolve
  // {texto, curto?} ou {erro, tipo}, com o tipo separando midia indisponivel
  // no WhatsApp, pagina lenta, servidor fora do ar e servidor lento.
  async function processarMidia(m, rota, op, { guardar = false } = {}) {
    const baixada = await (guardar ? baixarGuardando(m) : baixarMidia(m));
    if (baixada.erro) return baixada;
    const { midia } = baixada;
    if (midia.bytes.byteLength > LIMITE_MIDIA) return { erro: 'arquivo grande demais', tipo: 'indisponivel' };
    try {
      return await aoFundo({ tipo: 'processar', rota, base64: base64(midia.bytes), duracao: m.duracao, op });
    } catch (erro) {
      return { erro: `extensao recarregada: recarregue a pagina (${erro.message})`, tipo: 'fora' };
    }
  }

  // Indicador no icone: com o painel fechado, e por ele que se ve o progresso.
  let exportacaoPct = null;
  let indicadorAtual = '';
  function atualizarIndicador() {
    let texto = '';
    if (exportacaoPct !== null) texto = `${exportacaoPct}%`;
    else if (fila.pendentes) texto = String(Math.min(fila.pendentes, 999));
    if (texto === indicadorAtual) return;
    indicadorAtual = texto;
    aoFundo({ tipo: 'indicador', texto }).catch(() => {});
  }

  // ---------- painel ----------
  let host = null;
  let el = {};
  let vigia = null;
  let focoAnterior = null;
  let operacao = null; // exportacao em curso
  let ultima = null; // {pedido, resultado} para "tentar de novo"

  const CSS = `
    :host { all: initial; }
    /* o all: initial acima anula o display:none do atributo hidden */
    :host([hidden]) { display: none !important; }
    [hidden] { display: none !important; }
    *, *::before, *::after { box-sizing: border-box; }
    .painel { position: fixed; top: 64px; right: 24px; z-index: 2147483647; width: min(330px, calc(100vw - 32px));
      max-height: calc(100vh - 88px); overflow: hidden auto; font: 13px/1.4 system-ui, sans-serif; color: #111b21; background: #fff;
      border: 1px solid #d1d7db; border-radius: 10px; box-shadow: 0 8px 28px rgba(0,0,0,.18); }
    @media (prefers-color-scheme: dark) {
      .painel { color: #e9edef; background: #222e35; border-color: #3b4a54; }
      input { color: #e9edef; background: #111b21; border-color: #3b4a54; }
      .suave { color: #8696a0 !important; }
    }
    header { display: flex; align-items: center; justify-content: space-between; padding: 10px 14px;
      border-bottom: 1px solid rgba(134,150,160,.3); }
    h2 { font-size: 14px; margin: 0; }
    header button { all: unset; cursor: pointer; font-size: 18px; line-height: 1; padding: 0 4px; border-radius: 4px; }
    button:focus-visible, input:focus-visible, summary:focus-visible { outline: 2px solid #00a884; outline-offset: 2px; }
    .corpo { padding: 12px 14px; display: grid; grid-template-columns: minmax(0, 1fr); gap: 10px; }
    fieldset { border: 1px solid rgba(134,150,160,.35); border-radius: 8px; padding: 8px 10px 10px; margin: 0;
      display: grid; grid-template-columns: minmax(0, 1fr); gap: 8px; min-width: 0; }
    legend { font-weight: 600; padding: 0 4px; }
    .suave { color: #667781; font-size: 12px; }
    .chat { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    label { display: flex; gap: 8px; align-items: flex-start; cursor: pointer; }
    input[type=date], input[type=number] { font: inherit; padding: 3px 6px; border: 1px solid #d1d7db; border-radius: 6px; }
    input[type=number] { width: 72px; }
    .datas { display: flex; gap: 6px; align-items: center; padding-left: 24px; }
    .datas input { flex: 1 1 0; min-width: 0; }
    .acoes { display: flex; gap: 8px; flex-wrap: wrap; }
    .acoes button { flex: 1; font: inherit; font-weight: 600; padding: 8px; border-radius: 8px; border: 0; cursor: pointer;
      background: #00a884; color: #fff; }
    .acoes button.sec { background: transparent; color: inherit; border: 1px solid #d1d7db; }
    .acoes button:disabled { opacity: .5; cursor: default; }
    .servidor::before { content: '● '; }
    .servidor.no-ar::before { color: #00a884; }
    .servidor.aviso::before { color: #d9a400; }
    .servidor.fora::before { color: #ea0038; }
    .progresso { white-space: pre-wrap; min-height: 1.4em; }
    details summary { cursor: pointer; }
    details p { margin: 6px 0 0; }
    code { font-size: 11.5px; }
  `;

  function criarPainel() {
    host = document.createElement('div');
    host.id = 'wacervo-painel';
    const raiz = host.attachShadow({ mode: 'closed' });
    raiz.innerHTML = `
      <style>${CSS}</style>
      <div class="painel" role="dialog" aria-labelledby="wacervo-titulo" data-r="painel">
        <header><h2 id="wacervo-titulo">pepita-wa <span class="suave">${VERSAO}</span></h2>
          <button type="button" title="Fechar (Esc). O que estiver em andamento continua." aria-label="Fechar painel; o que estiver em andamento continua" data-r="fechar">×</button></header>
        <div class="corpo">
          <div>
            <div class="chat" data-r="chat">—</div>
            <div class="suave" data-r="carregadas"></div>
          </div>
          <div class="servidor suave" data-r="servidor">verificando pepita-wa...</div>

          <fieldset>
            <legend>Exportar esta conversa</legend>
            <label><input type="radio" name="modo" value="carregado" checked>
              <span>Tudo o que esta carregado<br><span class="suave">role a conversa para cima ate onde quiser</span></span></label>
            <label><input type="radio" name="modo" value="periodo"><span>Periodo</span></label>
            <div class="datas"><input type="date" data-r="de" aria-label="Data inicial do periodo"> ate
              <input type="date" data-r="ate" aria-label="Data final do periodo"></div>
            <label><input type="checkbox" data-r="audio" checked><span>Transcrever audios</span></label>
            <label><input type="checkbox" data-r="imagem" checked><span>Ler texto das imagens</span></label>
            <label><input type="checkbox" data-r="anexos" checked><span>Baixar anexos (fotos, videos, documentos)<br><span class="suave">voce escolhe a pasta; dentro dela vai uma pasta com o .md e os anexos</span></span></label>
            <div class="acoes">
              <button type="button" data-r="exportar">Exportar</button>
              <button type="button" class="sec" data-r="cancelar" hidden>Cancelar</button>
            </div>
            <div class="acoes" data-r="alternativas" hidden>
              <button type="button" class="sec" data-r="tentarDeNovo">Tentar de novo</button>
              <button type="button" class="sec" data-r="semMidia">Exportar sem transcricao/OCR</button>
            </div>
            <div class="acoes" data-r="soFalhas" hidden>
              <button type="button" class="sec" data-r="refazerFalhas">Tentar de novo so as falhas</button>
            </div>
            <div class="progresso suave" role="status" aria-live="polite" data-r="progresso"></div>
          </fieldset>

          <fieldset>
            <legend>Audios da conversa aberta</legend>
            <label><input type="checkbox" data-r="auto"><span>Transcrever automaticamente<br><span class="suave">se desligado, cada audio ganha um botao Transcrever</span></span></label>
            <label><input type="checkbox" data-r="mostrar"><span>Mostrar o texto embaixo de cada audio</span></label>
            <div class="suave" role="status" aria-live="polite" data-r="filaTexto"></div>
          </fieldset>

          <fieldset>
            <legend>Todas as conversas</legend>
            <div class="suave" data-r="rodada"></div>
            <details class="suave"><summary>Conversas individuais, so o que falta, na pasta [exportar] raiz</summary>
              <p>Arquivadas incluidas; sem grupos, canais, listas e status. Uma por vez, com as opcoes de audio, imagem e anexos acima (o modo e o periodo nao valem aqui). Quem grava e o pepita-wa, na pasta do <code>[exportar] raiz</code> (padrao <code>D:\\70-WHATSAPP</code>, que o Pepita le); cada contato tem a sua pasta, e cada rodada grava nela um <code>.md</code> so com as mensagens que ainda nao tinham saido. O pepita-wa guarda ate onde cada conversa foi (so datas, nunca o texto); sem ele no ar, nada sai.</p>
              <p>A rodada automatica faz o mesmo sozinha, sempre com transcricao, texto das imagens e anexos. Sem o Chrome aberto, o pepita-wa abre o Chrome no WhatsApp Web; sem a aba, ela abre uma; no fim fecha so o que abriu. Pula (e avisa aqui) com o WhatsApp sem login ou a transcricao parada.</p>
            </details>
            <label><input type="checkbox" data-r="rotinaLigada"><span>Rodada automatica<br><span class="suave" data-r="rotinaTexto"></span></span></label>
            <label><input type="checkbox" data-r="capturaAoVivo"><span>Captura ao vivo (em teste)<br><span class="suave">conversa com mensagem nova sai uns 2 min depois da ultima, pelo mesmo caminho do Exportar todas, so com esta aba aberta</span></span></label>
            <div class="acoes">
              <button type="button" data-r="exportarTodas">Exportar todas</button>
              <button type="button" class="sec" data-r="cancelarTodas" hidden>Cancelar</button>
            </div>
            <div class="progresso suave" role="status" aria-live="polite" data-r="progressoTodas"></div>
          </fieldset>

          <details class="suave"><summary>O que fica guardado</summary><div data-r="retencao"></div></details>
          <details class="suave"><summary>Ajustes</summary>
            <label style="margin-top:6px">porta do pepita-wa <input type="number" data-r="porta" min="1" max="65535"></label>
          </details>
        </div>
      </div>`;
    raiz.querySelectorAll('[data-r]').forEach((n) => (el[n.dataset.r] = n));
    el.modos = raiz.querySelectorAll('input[name=modo]');

    const hoje = MD.dia(new Date());
    el.de.value = hoje;
    el.ate.value = hoje;
    el.de.addEventListener('focus', () => (el.modos[1].checked = true));
    el.ate.addEventListener('focus', () => (el.modos[1].checked = true));

    chrome.storage.local.get({ capturaAoVivo: false }).then((c) => (el.capturaAoVivo.checked = CAPTURA && c.capturaAoVivo));
    el.capturaAoVivo.disabled = !CAPTURA;
    el.capturaAoVivo.addEventListener('change', async () => {
      await chrome.storage.local.set({ capturaAoVivo: el.capturaAoVivo.checked });
      el.capturaAoVivo.checked ? ligarCaptura() : desligarCaptura();
    });
    chrome.storage.local.get({ porta: 8765, audio: true, imagem: true, anexos: true }).then((c) => {
      el.porta.value = c.porta;
      el.audio.checked = c.audio;
      el.imagem.checked = c.imagem;
      el.anexos.checked = c.anexos;
    });
    el.auto.checked = auto;
    el.mostrar.checked = mostrar;
    el.porta.addEventListener('change', async () => {
      await chrome.storage.local.set({ porta: Number(el.porta.value) || 8765 });
      verificarServidor();
    });
    el.audio.addEventListener('change', () => chrome.storage.local.set({ audio: el.audio.checked }));
    el.imagem.addEventListener('change', () => chrome.storage.local.set({ imagem: el.imagem.checked }));
    el.anexos.addEventListener('change', () => chrome.storage.local.set({ anexos: el.anexos.checked }));
    el.auto.addEventListener('change', () => chrome.storage.local.set({ auto: el.auto.checked }));
    el.mostrar.addEventListener('change', () => chrome.storage.local.set({ mostrar: el.mostrar.checked }));
    el.fechar.addEventListener('click', fechar);
    el.exportar.addEventListener('click', exportar);
    el.cancelar.addEventListener('click', () => {
      operacao?.cancelar();
      mostrarProgresso('cancelando...');
    });
    el.exportarTodas.addEventListener('click', exportarTodas);
    el.rotinaLigada.addEventListener('change', async () => {
      const r = await aoFundo({ tipo: 'rotina-ligar', ligada: el.rotinaLigada.checked }).catch(() => null);
      pintarRotina(r?.ok ? r.dados : null);
    });
    el.cancelarTodas.addEventListener('click', () => {
      operacao?.cancelar();
      mostrarTodas('cancelando...');
    });
    el.tentarDeNovo.addEventListener('click', () => repetir({}));
    el.semMidia.addEventListener('click', () => repetir({ semMidia: true }));
    el.refazerFalhas.addEventListener('click', () => repetir({}));
    el.painel.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        fechar();
      }
    });
    document.documentElement.appendChild(host);
    atualizarTextoDaFila();
  }

  function abrir() {
    if (!host) criarPainel();
    focoAnterior = document.activeElement;
    host.hidden = false;
    (operacao ? (el.cancelar.hidden ? el.cancelarTodas : el.cancelar) : el.exportar).focus();
    atualizarChat();
    verificarServidor();
    if (!operacao) atualizarRodada();
    clearInterval(vigia);
    vigia = setInterval(atualizarChat, 1500);
  }

  // Fechar so esconde: exportacao e fila continuam, com o progresso no icone.
  function fechar() {
    host.hidden = true;
    clearInterval(vigia);
    focoAnterior?.focus?.();
    focoAnterior = null;
  }

  const mostrarProgresso = (t) => el.progresso && (el.progresso.textContent = t);
  const mostrarTodas = (t) => el.progressoTodas && (el.progressoTodas.textContent = t);

  let chatInfo = null;
  async function atualizarChat() {
    try {
      chatInfo = await pedir('chat');
    } catch (erro) {
      chatInfo = null;
      el.chat.textContent = erro.tipo === 'prazo' ? 'WhatsApp Web nao respondeu' : erro.message;
      el.carregadas.textContent = '';
      return;
    }
    if (!chatInfo) {
      el.chat.textContent = 'abra uma conversa';
      el.carregadas.textContent = '';
      return;
    }
    el.chat.textContent = chatInfo.nome;
    el.carregadas.textContent = chatInfo.carregadas
      ? `${chatInfo.carregadas} mensagens carregadas, desde ${new Date(chatInfo.primeira * 1000).toLocaleString()}`
      : 'nenhuma mensagem carregada';
  }

  const ESTADO_WHISPER = {
    em_espera: ['no-ar', 'whisper em espera (carrega no primeiro audio, uns segundos)'],
    carregando: ['aviso', 'whisper carregando na GPU...'],
    pronto: ['no-ar', 'whisper pronto'],
    processando: ['no-ar', 'whisper transcrevendo'],
    erro: ['aviso', 'whisper nao carregou'],
    // O whisper roda num container da distro WSL2; o wa servir so repassa o audio.
    fora: ['aviso', 'servico de transcricao fora do ar'],
  };

  // Motor gemini (0.9.0): o agy transcreve e o whisper da distro so entra de reserva.
  function estadoGemini(t) {
    const reserva = t.reserva ? ' · whisper de reserva' : '';
    if (t.bloqueio) return ['aviso', `transcricao parada: ${t.bloqueio}`];
    if (t.estado === 'pausado') return ['aviso', `gemini pausado ate ${t.volta_em} (${t.erro})${reserva}`];
    if (t.estado === 'sem_agy') return ['aviso', `agy nao encontrado${reserva}`];
    return ['no-ar', `gemini pronto (${t.modelo})`];
  }

  async function verificarServidor() {
    let s;
    try {
      s = await aoFundo({ tipo: 'status' });
    } catch {
      s = { ok: false, erro: 'extensao recarregada: recarregue a pagina' };
    }
    if (!el.servidor) return !!s.ok;
    let classe = 'fora';
    let texto = `${s.erro} — rode \`uv run wa servir\``;
    if (s.ok) {
      if (s.transcricao?.motor === 'gemini') {
        const [c, t] = estadoGemini(s.transcricao);
        classe = c;
        texto = `pepita-wa no ar · ${t}`;
      } else {
        const estado = s.whisper || (s.whisper_carregado ? 'pronto' : 'em_espera');
        const [c, t] = ESTADO_WHISPER[estado] || ['no-ar', `whisper ${estado}`];
        classe = c;
        texto = `pepita-wa no ar · ${t}${estado === 'erro' && s.whisper_erro ? `: ${s.whisper_erro}` : ''}${estado === 'fora' ? ' (distro ubuntu-dev)' : ''}`;
      }
      const aviso = N.avisoDeVersao(VERSAO, s.versao);
      if (aviso) {
        classe = 'aviso';
        texto = `${texto} · ${aviso}`;
      }
    }
    el.servidor.className = `servidor suave ${classe}`;
    el.servidor.textContent = texto;
    pintarRetencao(s);
    return !!s.ok;
  }

  function pintarRetencao(s) {
    const partes = [];
    if (s.ok) {
      const c = s.cache || {};
      const dias = s.cache_manter_dias;
      partes.push(
        `O pepita-wa guarda so o texto de cada audio e imagem ja processados (nunca a midia), ` +
          `pelo sha256 do arquivo, em <code></code>: ${c.audio || 0} audio(s), ${c.imagem || 0} imagem(ns).`,
        dias
          ? `Cada texto e apagado ${dias} dias depois de gravado ([cache] manter_dias).`
          : 'Fica ate voce rodar <code>uv run wa limpar-cache --sim</code>, ou defina <code>[cache] manter_dias</code> no config.toml.',
      );
    } else {
      partes.push('O pepita-wa guarda so o texto de cada midia processada, nunca a midia (servidor fora do ar agora).');
    }
    partes.push('Nesta aba, os textos ficam na memoria ate recarregar a pagina. O .md fica so onde voce salvar.');
    el.retencao.innerHTML = partes.map((p) => `<p>${p}</p>`).join('');
    const arquivo = el.retencao.querySelector('code');
    if (s.ok && arquivo) arquivo.textContent = s.cache_arquivo || 'dados/cache.sqlite3';
  }

  // ---------- exportacao ----------
  function nomeSugerido(modo, chat) {
    const nome = { nome: chat.nome || 'conversa' };
    if (modo === 'periodo') {
      return MD.nomeDoArquivo(nome, new Date(el.de.value + 'T00:00'), new Date(el.ate.value + 'T00:00'));
    }
    const hoje = new Date();
    const a = chat.primeira ? new Date(chat.primeira * 1000) : hoje;
    const b = chat.ultima ? new Date(chat.ultima * 1000) : hoje;
    return MD.nomeDoArquivo(nome, a, b);
  }

  async function escolherDestino(nome) {
    // Tem que ser a primeira coisa do clique: o seletor exige gesto do usuario recente.
    if (!window.showSaveFilePicker) return null;
    try {
      return await window.showSaveFilePicker({
        id: 'wacervo',
        suggestedName: nome,
        types: [{ description: 'Markdown', accept: { 'text/markdown': ['.md'] } }],
      });
    } catch (erro) {
      if (erro.name === 'AbortError') throw erro;
      return null; // sem permissao para o seletor: cai no download comum.
    }
  }

  // Com anexos: o usuario escolhe a pasta (a do projeto onde vai usar a conversa) e
  // a exportacao cria dentro dela <nome>/, com <nome>.md e anexos/, como o
  // Salvar post. Tambem tem que ser a primeira coisa do clique.
  async function escolherPasta(semPicker = 'este Chrome nao deixa escolher pasta; desmarque "Baixar anexos" para salvar so o .md') {
    if (!window.showDirectoryPicker) throw new Error(semPicker);
    return window.showDirectoryPicker({ id: 'wacervo-pasta', mode: 'readwrite' });
  }

  async function subpasta(pai, nome) {
    try {
      return { dir: await pai.getDirectoryHandle(nome), criada: false };
    } catch {
      return { dir: await pai.getDirectoryHandle(nome, { create: true }), criada: true };
    }
  }

  async function gravarArquivo(dir, nome, conteudo) {
    const arquivo = await dir.getFileHandle(nome, { create: true });
    const w = await arquivo.createWritable();
    await w.write(conteudo);
    await w.close();
  }

  // Uma por execucao. Anotar o que foi criado deixa o cancelamento desfazer
  // so isso: arquivo que ja estava na pasta (exportacao anterior) fica.
  // `pasta` e `arquivo` saem de `base` (<base>/<base>.md); o Exportar todas da os dois.
  function criarGravacao({ raiz, base, pasta = base, arquivo = `${base}.md` }) {
    let pastas = null;
    let desfeita = false;
    const novos = [];
    const abrir = () =>
      (pastas ||= (async () => {
        const conversa = await subpasta(raiz, pasta);
        const anexos = await subpasta(conversa.dir, 'anexos');
        return { conversa, anexos };
      })());

    return {
      // Mesmo nome a cada exportacao: o que ja esta na pasta nao baixa de novo.
      async anexar(m, caminho) {
        const nome = caminho.split('/').pop();
        try {
          const { anexos } = await abrir();
          try {
            const atual = await (await anexos.dir.getFileHandle(nome)).getFile();
            if (atual.size > 0) return { arquivo: caminho, existia: true };
          } catch {}
          const baixada = await baixarParaAnexo(m);
          if (baixada.erro) return { erro: baixada.erro, tipo: baixada.tipo };
          // Cancelar solta a exportacao na hora, mas este download segue ate o fim:
          // depois de desfeita, nao grava mais nada.
          if (desfeita) return { erro: 'cancelado', tipo: 'cancelado' };
          await gravarArquivo(anexos.dir, nome, baixada.midia.bytes);
          novos.push(nome);
          if (desfeita) await anexos.dir.removeEntry(nome).catch(() => {});
          return { arquivo: caminho };
        } catch (erro) {
          return { erro: `nao gravou na pasta (${erro.message})`, tipo: 'disco' };
        }
      },
      async salvar(texto) {
        const { conversa } = await abrir();
        await gravarArquivo(conversa.dir, arquivo, new Blob([texto], { type: 'text/markdown;charset=utf-8' }));
        return `${raiz.name}/${pasta}/${arquivo}`;
      },
      async desfazer() {
        desfeita = true;
        if (!pastas) return;
        try {
          const { conversa, anexos } = await pastas;
          if (conversa.criada) await raiz.removeEntry(pasta, { recursive: true });
          else if (anexos.criada) await conversa.dir.removeEntry('anexos', { recursive: true });
          else for (const nome of novos) await anexos.dir.removeEntry(nome).catch(() => {});
        } catch {}
      },
    };
  }

  async function salvar(destino, nome, texto) {
    if (destino) {
      const w = await destino.createWritable();
      await w.write(texto);
      await w.close();
      return destino.name;
    }
    const url = URL.createObjectURL(new Blob([texto], { type: 'text/markdown;charset=utf-8' }));
    const a = Object.assign(document.createElement('a'), { href: url, download: nome });
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
    return `${nome} (Downloads)`;
  }

  async function exportar() {
    if (operacao) return;
    const modo = [...el.modos].find((m) => m.checked).value;
    if (modo === 'periodo' && (!el.de.value || !el.ate.value || el.de.value > el.ate.value)) {
      mostrarProgresso('periodo invalido');
      return;
    }
    // Congela a conversa no clique: se ela mudar ate a coleta, a pagina recusa.
    if (!chatInfo) {
      mostrarProgresso('abra uma conversa primeiro');
      return;
    }
    const congelado = { id: chatInfo.id, nome: chatInfo.nome };
    const nome = nomeSugerido(modo, chatInfo);
    const anexos = el.anexos.checked;
    let destino;
    let pasta = null;
    try {
      if (anexos) pasta = { raiz: await escolherPasta(), base: nome.replace(/\.md$/, '') };
      else destino = await escolherDestino(nome);
    } catch (erro) {
      if (erro.name !== 'AbortError') mostrarProgresso(`erro: ${erro.message}`);
      return; // cancelou o seletor ou o Chrome nao deixou.
    }
    const args = { modo };
    if (modo === 'periodo') {
      args.inicio = new Date(el.de.value + 'T00:00:00').getTime() / 1000;
      args.fim = new Date(el.ate.value + 'T23:59:59.999').getTime() / 1000;
    }
    await rodar({ args, congelado, destino, pasta, nome, anexos, audio: el.audio.checked, imagem: el.imagem.checked });
  }

  // "Tentar de novo" reusa a coleta e os textos que deram certo: so as falhas
  // (ou tudo, se o servidor estava fora) voltam para o pepita-wa.
  function repetir({ semMidia = false }) {
    if (operacao || !ultima) return;
    const r = ultima.resultado;
    const anterior = r?.coleta ? { coleta: r.coleta, textos: r.textos || new Map() } : null;
    return rodar({ ...ultima.pedido, semMidia, anterior });
  }

  async function rodar(pedido) {
    const op = N.criarOperacao();
    const opId = `e${Date.now()}`;
    operacao = op;
    op.aoCancelar(() => {
      pedir('cancelar').catch(() => {});
      aoFundo({ tipo: 'abortar', op: opId }).catch(() => {});
    });
    el.exportar.disabled = true;
    el.cancelar.hidden = false;
    el.alternativas.hidden = true;
    el.soFalhas.hidden = true;
    exportacaoPct = 0;
    atualizarIndicador();
    const manter = setInterval(() => aoFundo({ tipo: 'manter' }).catch(() => {}), 20000);
    const gravacao = pedido.pasta ? criarGravacao(pedido.pasta) : null;
    let salvou = false;

    const deps = {
      coletar: (args) =>
        pedir('coletar', { ...args, esperado: pedido.congelado }, { progresso: mostrarProgresso }),
      processar: (m, rota) => processarMidia(m, rota, opId, { guardar: !!gravacao && m.tipo === 'image' }),
      anexar: gravacao?.anexar,
      gerar: (chat, mensagens, textos, info) => MD.gerar(chat, mensagens, textos, new Date(), { ...info, fuso: FUSO }),
      salvar: async (md) => {
        const onde = gravacao ? await gravacao.salvar(md) : await salvar(pedido.destino, pedido.nome, md);
        salvou = true;
        return onde;
      },
      servidorNoAr: verificarServidor,
      fila,
      mostrar: mostrarProgresso,
      passo: (feitos, total) => {
        exportacaoPct = Math.min(99, Math.floor(((feitos - 1) / total) * 100));
        atualizarIndicador();
      },
    };

    try {
      const r = await N.exportar(deps, pedido, op);
      ultima = { pedido, resultado: r };
      mostrarProgresso(`salvo: ${r.salvoEm}\n${N.descreverResumo(r.resumo)}`);
      el.soFalhas.hidden = !r.resumo.falhas && !r.resumo.anexosFalhas;
    } catch (erro) {
      // Sem o .md, nada fica: os anexos gravados nesta execucao saem.
      if (gravacao && !salvou) await gravacao.desfazer();
      if (erro.tipo === 'fora') {
        ultima = { pedido, resultado: { coleta: erro.coleta, textos: erro.textos } };
        mostrarProgresso(
          `${erro.coleta.mensagens.length} mensagens lidas, mas o pepita-wa esta fora do ar.\n` +
            'Rode `uv run wa servir` e tente de novo, ou exporte sem transcricao/OCR.',
        );
        el.alternativas.hidden = false;
      } else if (erro.tipo === 'cancelado') {
        mostrarProgresso('cancelado: nada foi salvo');
      } else if (erro.tipo === 'prazo') {
        mostrarProgresso(`erro: ${erro.message}. O WhatsApp Web pode estar travado; recarregue a aba.`);
      } else {
        mostrarProgresso(`erro: ${erro.message}`);
      }
    } finally {
      clearInterval(manter);
      guardada = null;
      operacao = null;
      exportacaoPct = null;
      atualizarIndicador();
      el.exportar.disabled = false;
      el.cancelar.hidden = true;
    }
  }

  // ---------- todas as conversas ----------
  // Um clique, a [exportar] raiz (a pasta que o buscador le): cada conversa
  // individual tem a sua pasta (<contato>/), e cada rodada grava nela um .md so
  // com o que ainda nao tinha saido (<contato>/<contato>_<de>_a_<ate>.md, anexos/).
  // Ate onde cada conversa foi fica no pepita-wa (GET /marcas, POST /marca), so
  // datas: nada de texto de conversa. A logica (filtro, ordem, plano, recorte,
  // cobertura, resumo) esta no nucleo.js.
  // Desde a 0.16.0 quem grava e o wa servir (POST /gravar), dentro da raiz dele: sem
  // seletor de pasta, o clique e a rodada automatica fazem a mesma coisa.
  async function exportarTodas() {
    if (operacao) return;
    el.exportarTodas.disabled = true;
    try {
      const motivo = N.motivoParaNaoExportarTodas(await aoFundo({ tipo: 'status' }).catch(() => null));
      if (motivo) {
        mostrarTodas(`erro: ${motivo}`);
        return;
      }
      const t = await aoFundo({ tipo: 'travar' }).catch(() => null);
      if (!t?.ok) {
        mostrarTodas(`espere: ${t?.motivo || 'extensao recarregada: recarregue a pagina'}`);
        return;
      }
      try {
        await rodarTodas({ anexos: el.anexos.checked, audio: el.audio.checked, imagem: el.imagem.checked });
      } finally {
        await aoFundo({ tipo: 'destravar' }).catch(() => {});
      }
    } finally {
      if (!operacao) el.exportarTodas.disabled = false;
    }
  }

  // Rodada automatica: o fundo.js achou (ou abriu) esta aba e ja tem a trava. A
  // resposta sai na hora; o fim vai depois, por `rotina-fim`.
  const PARTE_ANEXO = 8 * 1024 * 1024; // base64 de cada parte cabe folgado numa mensagem
  async function rodadaAutomatica({ recemAberta }) {
    const fim = (resultado, detalhe) => aoFundo({ tipo: 'rotina-fim', resultado, detalhe }).catch(() => {});
    // O painel guarda os botoes e o progresso: existe mesmo fechado.
    if (!host) {
      criarPainel();
      host.hidden = true;
    }
    const manter = setInterval(() => aoFundo({ tipo: 'manter', trava: true }).catch(() => {}), 20000);
    try {
      // Espera o wa-js e o login: a aba pode ter acabado de abrir.
      let ultimo = null;
      for (const inicio = Date.now(); Date.now() - inicio < 3 * 60 * 1000; await new Promise((ok) => setTimeout(ok, 3000))) {
        try {
          ultimo = await pedir('situacao', {}, { prazo: 10000 });
          if (ultimo?.pronto && ultimo?.logado) break;
        } catch {}
      }
      if (!ultimo) return fim('falha', 'o WhatsApp Web nao respondeu em 3 min');
      if (!ultimo.logado) {
        mostrarTodas('rodada automatica pulada: o WhatsApp Web esta sem login (QR code na tela)');
        return fim('pulada', 'WhatsApp Web sem login (QR code na tela)');
      }
      if (!ultimo.pronto) return fim('falha', 'o WhatsApp Web nao terminou de carregar em 3 min');
      // Aba que acabou de abrir: a lista de conversas ainda sincroniza uns segundos.
      if (recemAberta) await new Promise((ok) => setTimeout(ok, 20000));
      // A transcricao pode ter parado enquanto a aba carregava.
      const motivo = N.motivoParaNaoExportarTodas(await aoFundo({ tipo: 'status' }).catch(() => null), { automatica: true });
      if (motivo) {
        mostrarTodas(`rodada automatica pulada: ${motivo}`);
        return fim('pulada', motivo);
      }
      if (operacao) return fim('pulada', 'outra exportacao em andamento nesta aba');
      const r = await rodarTodas({ anexos: true, audio: true, imagem: true, automatica: true });
      if (!r) return fim('falha', 'a rodada nao comecou (veja o painel)');
      const { resultado, detalhe } = N.resultadoDaRodada(r);
      return fim(resultado, detalhe);
    } catch (erro) {
      return fim('falha', String(erro?.message || erro).slice(0, 200));
    } finally {
      clearInterval(manter);
    }
  }

  // Igual ao criarGravacao, mas pelo wa servir: `pasta` e `arquivo` relativos a raiz
  // dele. O lote deixa o cancelamento desfazer so o que esta exportacao criou.
  function criarGravacaoNoServidor({ pasta, arquivo, lote }) {
    let desfeita = false;
    const falhou = (r) => N.erroDe(r?.tipo === 'fora' ? 'fora' : 'disco', `nao gravou na pasta (${r?.erro || 'sem resposta'})`);
    const enviar = async (caminho, bytes, { livre = false } = {}) => {
      const tudo = new Uint8Array(bytes);
      let r = null;
      for (const [ini, fimParte] of N.partesDoArquivo(tudo.byteLength, PARTE_ANEXO)) {
        if (desfeita) throw N.erroDe('cancelado', 'cancelado');
        r = await aoFundo({
          tipo: 'gravar', caminho, lote, livre, deslocamento: ini, fim: fimParte === tudo.byteLength,
          base64: base64(tudo.subarray(ini, fimParte)),
        }).catch((e) => ({ erro: `extensao recarregada (${e.message})`, tipo: 'fora' }));
        if (!r?.ok) throw falhou(r);
        // Com `livre`, o servidor pode ter escolhido outro nome (-2): as outras partes vao nele.
        caminho = r.dados.caminho;
        livre = false;
      }
      return r.dados;
    };
    return {
      // Mesmo nome a cada exportacao: o que ja esta na pasta nao baixa de novo.
      async anexar(m, caminho) {
        const rel = `${pasta}/${caminho}`;
        try {
          const existe = await aoFundo({ tipo: 'arquivo', caminho: rel });
          if (!existe?.ok) throw falhou(existe);
          if (existe.dados.existe && existe.dados.tamanho > 0) return { arquivo: caminho, existia: true };
          const baixada = await baixarParaAnexo(m);
          if (baixada.erro) return { erro: baixada.erro, tipo: baixada.tipo };
          if (desfeita) return { erro: 'cancelado', tipo: 'cancelado' };
          await enviar(rel, baixada.midia.bytes);
          return { arquivo: caminho };
        } catch (erro) {
          if (erro.tipo === 'cancelado') return { erro: 'cancelado', tipo: 'cancelado' };
          return { erro: erro.message, tipo: 'disco' };
        }
      },
      async salvar(texto) {
        const r = await enviar(`${pasta}/${arquivo}`, new TextEncoder().encode(texto), { livre: true });
        aoFundo({ tipo: 'lote', lote, desfazer: false }).catch(() => {});
        return r.caminho;
      },
      async desfazer() {
        desfeita = true;
        await aoFundo({ tipo: 'lote', lote, desfazer: true }).catch(() => {});
      },
    };
  }

  async function lerMarcas() {
    try {
      return await aoFundo({ tipo: 'marcas' });
    } catch {
      return { erro: 'extensao recarregada: recarregue a pagina', tipo: 'fora' };
    }
  }

  function pintarRotina(r) {
    if (!el.rotinaTexto) return;
    el.rotinaTexto.textContent = N.descreverRotina(r);
    el.rotinaLigada.checked = !!r?.ligada;
    el.rotinaLigada.disabled = !r;
  }

  // Marca que o pepita-wa nao guardou depois de o .md estar salvo (nucleo.marcaComPendente):
  // so id, nome, pasta e datas, ate a rodada seguinte conseguir manda-la.
  const PENDENTES = 'marcasPendentes';
  async function lerPendentes() {
    try {
      return (await chrome.storage.local.get({ [PENDENTES]: {} }))[PENDENTES] || {};
    } catch {
      return {};
    }
  }
  async function gravarPendentes(pendentes) {
    try {
      await chrome.storage.local.set({ [PENDENTES]: pendentes });
    } catch {}
  }

  async function atualizarRodada() {
    let r = null;
    try {
      r = await aoFundo({ tipo: 'ultimaRodada' });
    } catch {}
    if (el.rodada) el.rodada.textContent = r?.ok ? N.descreverRodada(r.dados.rodada) : '';
    const rotina = await aoFundo({ tipo: 'rotina' }).catch(() => null);
    pintarRotina(rotina?.ok ? rotina.dados : null);
  }

  // Ids dos grupos liberados pelo usuario ([grupos] ids do config.toml), pelo /status. Falha = nenhum.
  let gruposCache = { em: 0, ids: [] };
  async function gruposEscolhidos(maxIdadeMs = 0) {
    if (maxIdadeMs && Date.now() - gruposCache.em < maxIdadeMs) return gruposCache.ids;
    const s = await aoFundo({ tipo: 'status' }).catch(() => null);
    const ids = s?.ok && Array.isArray(s.grupos) ? s.grupos.filter((g) => typeof g === 'string' && g.endsWith('@g.us')) : [];
    gruposCache = { em: Date.now(), ids };
    return ids;
  }

  // Devolve o resumo do nucleo (exportarTodas), ou null se nem comecou.
  // `soIds`: captura ao vivo, so essas conversas e sem registrar como rodada.
  async function rodarTodas({ anexos, audio, imagem, automatica = false, soIds = null }) {
    const op = N.criarOperacao();
    operacao = op;
    let opAtual = null;
    op.aoCancelar(() => {
      pedir('cancelar').catch(() => {});
      if (opAtual) aoFundo({ tipo: 'abortar', op: opAtual }).catch(() => {});
    });
    el.exportar.disabled = true;
    el.exportarTodas.disabled = true;
    el.cancelarTodas.hidden = false;
    exportacaoPct = 0;
    atualizarIndicador();
    const manter = setInterval(() => aoFundo({ tipo: 'manter', trava: true }).catch(() => {}), 20000);
    let titulo = '';
    const prefixo = soIds ? 'captura ao vivo · ' : automatica ? 'rodada automatica · ' : '';
    const mostrarUma = (t) => mostrarTodas(`${prefixo}${titulo}\n${t}`);

    try {
      mostrarTodas(`${prefixo}lendo as marcas do pepita-wa...`);
      const lidas = await lerMarcas();
      if (!lidas?.ok) {
        mostrarTodas(`erro: ${lidas?.erro}; sem as marcas nada sai. Rode \`uv run wa servir\` e tente de novo.`);
        return null;
      }
      const marcas = new Map(Object.entries(lidas.dados.conversas || {}));
      const pendentes = await lerPendentes();
      if (await N.reenviarPendentes(pendentes, marcas, (corpo) => aoFundo({ tipo: 'marcar', corpo }))) {
        await gravarPendentes(pendentes);
      }
      // Pastas ja tomadas por outros contatos, em qualquer rodada.
      const usados = new Set([...marcas.values()].map((m) => m.pasta.toLowerCase()));
      const agora = Math.floor(Date.now() / 1000);
      mostrarTodas('listando as conversas...');

      // Grupos que o usuario escolheu: vem do /status do servidor (config.toml), so ids.
      const grupos = await gruposEscolhidos();
      const deps = {
        listar: () => pedir('listarConversas', { grupos }),
        aoComecar: (c, i, total) => {
          titulo = `${i}/${total} · ${c.nome}`;
          mostrarUma('lendo mensagens...');
          exportacaoPct = Math.min(99, Math.floor(((i - 1) / total) * 100));
          atualizarIndicador();
        },
        exportarUma: async (conversa, i) => {
          const idOp = `t${Date.now()}-${i}`;
          opAtual = idOp;
          const marca = marcas.get(conversa.id);
          let gravacao = null;
          let pasta = marca?.pasta || '';
          let cobertura = null;
          let salvou = false;
          const marcar = async () => {
            const corpo = { id: conversa.id, nome: conversa.nome, pasta, ...cobertura };
            let r;
            try {
              r = await aoFundo({ tipo: 'marcar', corpo });
            } catch {
              r = { erro: 'extensao recarregada', tipo: 'fora' };
            }
            if (r?.ok) {
              if (pendentes[conversa.id]) {
                delete pendentes[conversa.id];
                await gravarPendentes(pendentes);
              }
            } else {
              if (salvou) {
                pendentes[conversa.id] = corpo;
                await gravarPendentes(pendentes);
              }
              throw N.erroDe(r?.tipo === 'fora' ? 'fora' : 'marca',
                `${salvou ? 'gravou, mas ' : ''}a marca nao foi guardada (${r?.erro})`);
            }
            marcas.set(conversa.id, r.dados);
          };
          const deps1 = {
            coletar: async () => {
              const plano = N.planoDaConversa(marca, agora);
              const coleta = await pedir('coletarConversa', { ...plano, chatId: conversa.id }, { progresso: mostrarUma });
              const decisao = N.decidirConversa(marca, coleta);
              ({ cobertura } = decisao);
              const { novas } = decisao;
              if (decisao.acao === 'vazio') throw N.erroDe('vazio', 'nenhuma mensagem');
              if (decisao.acao === 'sem_novidade') {
                if (cobertura) await marcar();
                throw N.erroDe('sem_novidade', 'sem novidade');
              }
              if (decisao.acao === 'limite') {
                throw N.erroDe('limite', 'a busca parou no limite de paginas antes do trecho ja exportado; nada gravado');
              }
              if (!pasta) {
                pasta = N.nomeUnico(usados, MD.slug(conversa.nome), conversa.id, { forcarId: !!conversa.homonima });
              }
              const nome = MD.nomeDoArquivo({ nome: conversa.nome }, new Date(novas[0].t * 1000), new Date(novas.at(-1).t * 1000));
              gravacao = criarGravacaoNoServidor({ pasta, arquivo: nome, lote: idOp.replace(/[^A-Za-z0-9_-]/g, '') });
              return { ...coleta, mensagens: novas };
            },
            processar: (m, rota) => processarMidia(m, rota, idOp, { guardar: anexos && m.tipo === 'image' }),
            anexar: (m, caminho) => gravacao.anexar(m, caminho),
            gerar: (chat, mensagens, textos, info) => MD.gerar(chat, mensagens, textos, new Date(), { ...info, fuso: FUSO }),
            salvar: async (md) => {
              const onde = await gravacao.salvar(md);
              salvou = true;
              await marcar();
              return onde;
            },
            servidorNoAr: verificarServidor,
            fila,
            mostrar: mostrarUma,
          };
          const marcaAnterior = marca ? { primeira: marca.primeira, ultima: marca.ultima } : null;
          try {
            return await N.exportar(deps1, { args: { modo: 'incremental' }, audio, imagem, anexos, marcaAnterior }, op);
          } catch (erro) {
            // Sem o .md desta conversa, a pasta e os anexos gravados agora saem; as outras ficam.
            if (gravacao && !salvou) await gravacao.desfazer();
            throw erro;
          } finally {
            guardada = null;
          }
        },
      };

      const r = await N.exportarTodas(deps, op, { soIds, grupos });
      mostrarTodas(`${prefixo}${N.descreverResumoTodas(r)}`);
      if (soIds) return r;
      await aoFundo({
        tipo: 'rodada',
        corpo: {
          total: r.total, exportadas: r.exportadas, sem_novidade: r.semNovidade + r.puladas,
          parciais: r.parciais, falhas: r.falhas, interrompida: r.cancelado || !!r.parou,
        },
      }).catch(() => {});
      atualizarRodada();
      return r;
    } catch (erro) {
      mostrarTodas(erro.tipo === 'cancelado' ? 'cancelado antes de comecar: nada foi gravado' : `erro: ${erro.message}`);
      return null;
    } finally {
      clearInterval(manter);
      guardada = null;
      operacao = null;
      exportacaoPct = null;
      atualizarIndicador();
      el.exportar.disabled = false;
      el.exportarTodas.disabled = false;
      el.cancelarTodas.hidden = true;
    }
  }

  // ---------- captura ao vivo (0.20.0) ----------
  // Ligada no painel (chrome.storage `capturaAoVivo`, desligada por padrao). A pagina
  // avisa cada mensagem nova; o agendador do nucleo junta as conversas e, uns minutos
  // depois, exporta so elas pelo mesmo fluxo do Exportar todas (marcas, /gravar), com a
  // mesma trava do fundo.js. Sem esta aba aberta nao ha captura: a rodada de 30 min cobre.
  const CAPTURA = true;
  let captura = null;

  async function capturarAgora(ids) {
    if (operacao) return false;
    const motivo = N.motivoParaNaoExportarTodas(await aoFundo({ tipo: 'status' }).catch(() => null), { automatica: true });
    if (motivo) return false;
    const t = await aoFundo({ tipo: 'travar' }).catch(() => null);
    if (!t?.ok) return false;
    try {
      if (!host) {
        criarPainel();
        host.hidden = true;
      }
      return !!(await rodarTodas({ anexos: true, audio: true, imagem: true, soIds: ids }));
    } finally {
      await aoFundo({ tipo: 'destravar' }).catch(() => {});
    }
  }

  async function ligarCaptura() {
    if (!CAPTURA || captura) return;
    const c = await chrome.storage.local.get({ capturaAoVivo: false }).catch(() => ({}));
    if (!c.capturaAoVivo || captura) return;
    captura = N.criarAgendadorCaptura({ executar: capturarAgora });
    // O wa-js pode levar um tempo para ficar pronto depois de a aba abrir.
    for (let i = 0; i < 60 && captura; i++) {
      try {
        await pedir('escutarMensagens', {}, { prazo: 10000 });
        return;
      } catch {
        await new Promise((ok) => setTimeout(ok, 5000));
      }
    }
  }

  function desligarCaptura() {
    captura?.parar();
    captura = null;
  }

  window.addEventListener('message', (ev) => {
    if (ev.source === window && ev.data?.wacervo === 'evento' && ev.data.tipo === 'mensagem') {
      const id = ev.data.chatId;
      if (typeof id === 'string' && id.endsWith('@g.us')) {
        // Grupo que o usuario nao escolheu nunca entra na fila da captura.
        gruposEscolhidos(60000).then((ids) => { if (ids.includes(id)) captura?.marcar(id); }).catch(() => {});
      } else {
        captura?.marcar(id);
      }
    }
  });
  ligarCaptura().catch(() => {});

  // ---------- agente (0.19.0) ----------
  // O fundo.js pega o pedido do `wa mcp` no wa servir e manda para esta aba. Erro
  // devolvido aqui significa que nada foi enviado; o resultado do envio vem em `dados`.
  async function atenderAgente(pedido) {
    if (!pedido?.op) return { ok: false, tipo: 'recusado', erro: 'pedido vazio' };
    if (Date.now() > pedido.executar_ate) {
      return { ok: false, tipo: 'expirado', erro: 'o pedido chegou tarde na aba do WhatsApp; nao rodou' };
    }
    let sit = null;
    try {
      sit = await pedir('situacao', {}, { prazo: 10000 });
    } catch {}
    if (!sit?.pronto) return { ok: false, tipo: 'nao_pronto', erro: 'o WhatsApp Web desta aba ainda nao carregou' };
    if (!sit.logado) return { ok: false, tipo: 'nao_pronto', erro: 'o WhatsApp Web esta sem login (QR code na tela)' };
    try {
      if (pedido.op === 'listar') return { ok: true, dados: { filtro: pedido.args?.filtro, conversas: await pedir('agenteListar', { filtro: pedido.args?.filtro }) } };
      if (pedido.op === 'grupos') return { ok: true, dados: { grupos: await pedir('agenteGrupos', {}) } };
      if (pedido.op === 'ler') {
        const dados = await pedir('agenteLer', { alvo: pedido.args?.alvo, n: pedido.args?.n, grupos: pedido.args?.grupos });
        if (dados?.mensagens) {
          await N.transcreverParaOAgente(dados.mensagens,
            (m) => processarMidia(m, '/transcrever', `agente-${pedido.id}`), { prazoMs: 70000 });
        }
        return { ok: true, dados };
      }
      if (pedido.op === 'enviar') {
        const a = pedido.args || {};
        return {
          ok: true,
          dados: await pedir('agenteEnviar', {
            chatId: a.chatId, texto: a.texto, ultimaT: a.ultimaT, vistos: a.vistos || [], executarAte: pedido.executar_ate,
          }),
        };
      }
      return { ok: false, tipo: 'recusado', erro: `operacao desconhecida: ${pedido.op}` };
    } catch (erro) {
      return { ok: false, tipo: erro.tipo || 'pagina', erro: erro.message };
    }
  }
  // Acorda o laco do agente no fundo.js assim que esta aba existe.
  aoFundo({ tipo: 'agente-oi' }).catch(() => {});

  chrome.runtime.onMessage.addListener((msg, _remetente, responder) => {
    if (msg?.tipo === 'agente') {
      atenderAgente(msg.pedido).then(responder);
      return true;
    }
    if (msg?.tipo === 'rodada-auto') {
      if (operacao) {
        responder({ aceita: false, motivo: 'outra exportacao em andamento na aba do WhatsApp' });
        return false;
      }
      responder({ aceita: true });
      rodadaAutomatica(msg);
      return false;
    }
    if (msg?.tipo !== 'alternar-painel') return;
    host && !host.hidden ? fechar() : abrir();
  });

  // ---------- transcricao na tela ----------
  // Descobre audios so na conversa aberta (#main). A fila e os resultados ficam
  // pelo id da mensagem: trocar de conversa ou fechar o painel nao para nem
  // perde nada, e a caixa volta pintada quando o audio reaparece. Nada e
  // guardado alem do texto no cache do servidor local e desta memoria da aba.
  // `auto` liga a fila; `mostrar` so desenha as caixas. Sao coisas separadas.
  const CAIXA = 'wacervo-transcricao';
  const porDom = new Map(); // data-id do DOM -> {id, duracao} | null (nao e audio)
  const duracoes = new Map(); // id serializado -> segundos (para o prazo do servidor)
  const perguntando = new Set();
  let auto = true;
  let mostrar = true;
  let agendado = false;
  let manterFila = null;

  const fila = N.criarFila({
    processar: (id) => processarMidia({ id, duracao: duracoes.get(id) }, '/transcrever', 'fila'),
    aoMudar: (id) => {
      pintar(id);
      atualizarIndicador();
      atualizarTextoDaFila();
    },
    aoAtividade: (ativa) => {
      clearInterval(manterFila);
      if (ativa) manterFila = setInterval(() => aoFundo({ tipo: 'manter' }).catch(() => {}), 20000);
      atualizarIndicador();
      atualizarTextoDaFila();
    },
  });

  function atualizarTextoDaFila() {
    if (!el.filaTexto) return;
    const n = fila.pendentes;
    el.filaTexto.textContent = n
      ? `${n} audio(s) na fila${fila.pausada ? ' (pausada: so os que voce pedir)' : ''}`
      : fila.pausada ? 'transcricao automatica desligada' : '';
  }

  const escuro = () => document.body.classList.contains('dark') || document.documentElement.classList.contains('dark');

  function bolhaDoAudio(icone, linha) {
    // Sobe do botao de play ate o bloco que tem o rodape da mensagem (hora e
    // tiques): e ali dentro que a caixa encaixa sem quebrar o layout.
    for (let n = icone.parentElement; n && n !== linha.parentElement; n = n.parentElement) {
      if (n.querySelector('[data-testid="msg-meta"]')) return n;
    }
    return icone.closest('._ak49') || linha;
  }

  function iconesDeAudio(main) {
    const titulos = [...main.querySelectorAll('title')].filter((t) =>
      /^ic-(play-arrow|pause)-filled$/.test(t.textContent.trim()),
    );
    return titulos.concat([...main.querySelectorAll('[data-icon="audio-play"], [data-icon="audio-pause"]')]);
  }

  function agendarVarredura() {
    if (agendado) return;
    agendado = true;
    setTimeout(varrer, 300);
  }

  // Botao "exportar" no cabecalho da conversa, a esquerda dos icones de chamada.
  // O WhatsApp recria o cabecalho ao trocar de conversa: a varredura repoe.
  // Sem achar a fileira de icones, entra no fim do cabecalho.
  const BOTAO_CAB = 'wacervo-exportar';
  const ICONE_EXPORTAR =
    '<svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true" fill="currentColor">' +
    '<path d="M5 20h14v-2H5v2zm7-18-5.5 5.5 1.41 1.41L11 5.83V16h2V5.83l3.09 3.08 1.41-1.41L12 2z"/></svg>';

  // Botao redondo com o icone de exportar que abre ou fecha o painel.
  function botaoDoPainel(classe, titulo, rotulo, layout) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = classe;
    b.title = titulo;
    b.setAttribute('aria-label', rotulo);
    b.innerHTML = ICONE_EXPORTAR;
    b.style.cssText =
      'background:none;border:0;padding:8px;border-radius:50%;cursor:pointer;color:inherit;' +
      `align-items:center;justify-content:center;flex:none;opacity:.85;${layout}`;
    b.addEventListener('mouseenter', () => (b.style.background = 'rgba(127,127,127,.2)'));
    b.addEventListener('mouseleave', () => (b.style.background = 'none'));
    b.addEventListener('click', (ev) => {
      ev.stopPropagation(); // senao o WhatsApp abre os dados do contato
      host && !host.hidden ? fechar() : abrir();
    });
    return b;
  }

  function botaoNoCabecalho() {
    const cab = document.querySelector('#main header');
    if (!cab || cab.querySelector(`.${BOTAO_CAB}`)) return;
    const b = botaoDoPainel(
      BOTAO_CAB, 'Exportar conversa (pepita-wa)', 'Exportar conversa com o pepita-wa',
      'margin:0 4px;display:inline-flex',
    );
    // Sobe do ultimo botao (menu) ate a fileira que tem mais de um filho e
    // entra antes do primeiro grupo de icones (sem avatar nem nome).
    const ultimo = [...cab.querySelectorAll('button, [role="button"]')].at(-1);
    let grupo = ultimo;
    while (grupo && grupo.parentElement !== cab && grupo.parentElement.children.length < 2) grupo = grupo.parentElement;
    const fileira = grupo?.parentElement;
    const primeiro =
      fileira &&
      [...fileira.children].find(
        (f) => f.querySelector('button, [role="button"], svg') && !f.querySelector('img, [dir="auto"]'),
      );
    if (primeiro) fileira.insertBefore(b, primeiro);
    else cab.appendChild(b);
  }

  // Botao do painel na barra de icones da esquerda, abaixo da Meta AI: abre o
  // painel sem conversa aberta (o Exportar todas) e na janela do WhatsApp
  // instalado como app, que nao mostra o icone da extensao. Onde entrar sai da
  // posicao dos botoes (ancoraNaBarra); o WhatsApp recria a barra as vezes, e a
  // varredura repoe. Sem barra na tela (QR, carregando, janela estreita), tenta
  // de novo so a cada 2 s: medir todos os botoes forca o layout da pagina.
  const BOTAO_BARRA = 'wacervo-barra';
  let barraTentada = 0;
  let barraAgendada = false;
  function botaoNaBarra() {
    if (document.querySelector(`.${BOTAO_BARRA}`)) return;
    const espera = barraTentada + 2000 - Date.now();
    if (espera > 0) {
      // Garante uma nova tentativa mesmo se o DOM parar de mudar.
      if (!barraAgendada) {
        barraAgendada = true;
        setTimeout(() => {
          barraAgendada = false;
          agendarVarredura();
        }, espera);
      }
      return;
    }
    barraTentada = Date.now();
    const botoes = [...document.querySelectorAll('button, [role="button"]')].filter(
      (e) => !e.closest('#main, #side, [role="dialog"]'),
    );
    const lugar = N.ancoraNaBarra(botoes.map((e) => e.getBoundingClientRect()));
    if (!lugar) return;
    // Sobe da ancora ate o bloco irmao do botao de cima: o novo entra como irmao dele.
    const anterior = botoes[lugar.anterior];
    let item = botoes[lugar.ancora];
    if (anterior) while (item.parentElement && !item.parentElement.contains(anterior)) item = item.parentElement;
    item.after(
      botaoDoPainel(
        BOTAO_BARRA, 'pepita-wa: exportar (esta conversa ou todas)', 'Abrir o painel do pepita-wa',
        'margin:4px auto;display:flex',
      ),
    );
  }

  function varrer() {
    agendado = false;
    botaoNoCabecalho();
    botaoNaBarra();
    if (!auto && !mostrar) return;
    const main = document.getElementById('main');
    if (!main) return;
    for (const icone of iconesDeAudio(main)) {
      const linha = icone.closest('[data-id]');
      if (!linha) continue;
      const domId = linha.dataset.id;
      if (!porDom.has(domId)) {
        perguntarSeEAudio(domId);
        continue;
      }
      const info = porDom.get(domId);
      if (!info) continue;
      if (auto) fila.pedir(info.id); // a fila ignora o que ja esta pronto ou esperando
      if (!mostrar) continue;
      const bolha = bolhaDoAudio(icone, linha);
      let caixa = bolha.querySelector(`:scope > .${CAIXA}`);
      // O WhatsApp reaproveita nos do DOM: caixa de outra mensagem sai.
      if (caixa && caixa.dataset.msg !== info.id) {
        caixa.remove();
        caixa = null;
      }
      if (!caixa) {
        caixa = criarCaixa(info.id);
        bolha.appendChild(caixa);
      }
      const r = fila.estado(info.id);
      if (caixa.dataset.estado !== estadoDe(r)) pintarCaixa(caixa, r);
    }
  }

  async function perguntarSeEAudio(domId) {
    if (perguntando.has(domId)) return;
    perguntando.add(domId);
    try {
      // Sem resposta no prazo, o canal desiste e limpa; a proxima varredura pergunta de novo.
      const info = await pedir('audio', { domId });
      porDom.set(domId, info);
      if (info) duracoes.set(info.id, info.duracao);
      agendarVarredura();
    } catch {
      // WhatsApp ainda carregando: a proxima varredura pergunta de novo.
    } finally {
      perguntando.delete(domId);
    }
  }

  function botao(texto, rotulo, acao) {
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = texto;
    b.setAttribute('aria-label', rotulo);
    b.style.cssText =
      'margin-left:8px;font:inherit;font-size:12px;padding:1px 8px;border-radius:10px;cursor:pointer;' +
      'border:1px solid #00a884;background:transparent;color:inherit;';
    b.addEventListener('click', acao);
    return b;
  }

  function criarCaixa(id) {
    const caixa = document.createElement('div');
    caixa.className = CAIXA;
    caixa.dataset.msg = id;
    caixa.setAttribute('role', 'note');
    caixa.setAttribute('aria-live', 'polite');
    caixa.style.cssText =
      'margin:4px 6px 20px;padding:6px 9px;border-radius:7px;font-size:13.5px;line-height:1.4;' +
      'white-space:pre-wrap;word-break:break-word;user-select:text;cursor:auto;' +
      (escuro() ? 'background:rgba(255,255,255,.07);color:#e9edef;' : 'background:rgba(0,0,0,.05);color:#111b21;');
    // O WhatsApp trata clique e teclado na bolha (abrir menu, selecionar): aqui nao.
    for (const ev of ['click', 'mousedown', 'dblclick', 'keydown']) caixa.addEventListener(ev, (e) => e.stopPropagation());
    return caixa;
  }

  function estadoDe(r) {
    if (!r) return auto ? 'nada' : 'parado';
    if (r.fila) return fila.pausada ? 'pausado' : 'fila';
    if (r.andamento) return 'andamento';
    if (r.erro) return `erro:${r.erro}`;
    return 'texto';
  }

  function pintarCaixa(caixa, r) {
    const id = caixa.dataset.msg;
    const estado = estadoDe(r);
    caixa.dataset.estado = estado;
    caixa.replaceChildren();
    const rotulo = document.createElement('span');
    rotulo.style.cssText = 'font-size:11.5px;font-weight:600;color:#00a884;';
    const corpo = document.createElement('span');
    const transcrever = () => fila.pedir(id, { manual: true });
    let acao = null;
    if (estado === 'parado' || estado === 'pausado') {
      rotulo.textContent = estado === 'pausado' ? '🎙️ na fila (pausada)' : '🎙️ audio';
      acao = botao('Transcrever', 'Transcrever este audio', transcrever);
    } else if (estado === 'nada' || estado === 'fila') {
      rotulo.textContent = '🎙️ na fila para transcrever...';
    } else if (r.andamento) {
      rotulo.textContent = '🎙️ transcrevendo...';
    } else if (r.erro) {
      rotulo.style.color = '#ea0038';
      rotulo.textContent = '🎙️ nao transcrito';
      const dica = r.tipo === 'fora' ? ' Rode `uv run wa servir`.' : '';
      corpo.textContent = ` — ${r.erro}.${dica}`;
      acao = botao('Tentar de novo', 'Tentar transcrever de novo', transcrever);
    } else {
      rotulo.textContent = '🎙️ transcricao';
      corpo.textContent = `\n${r.texto || '(sem fala)'}`;
      if (r.texto) {
        acao = botao('Copiar', 'Copiar a transcricao', (e) => {
          navigator.clipboard.writeText(r.texto).then(() => {
            e.target.textContent = 'copiado';
            setTimeout(() => (e.target.textContent = 'Copiar'), 1200);
          });
        });
      }
    }
    caixa.append(rotulo);
    if (acao) caixa.append(acao);
    caixa.append(corpo);
  }

  function pintar(id) {
    if (!mostrar) return;
    const r = fila.estado(id);
    // Sem CSS.escape: no WhatsApp ele nao existe no content script.
    document.querySelectorAll(`.${CAIXA}`).forEach((c) => c.dataset.msg === id && pintarCaixa(c, r));
  }

  function aplicarOpcoes(novas) {
    if ('auto' in novas) {
      auto = novas.auto;
      auto ? fila.retomar() : fila.pausar();
      if (el.auto) el.auto.checked = auto;
    }
    if ('mostrar' in novas) {
      mostrar = novas.mostrar;
      if (el.mostrar) el.mostrar.checked = mostrar;
      if (!mostrar) document.querySelectorAll(`.${CAIXA}`).forEach((c) => c.remove());
    }
    // Repinta tudo: o estado "pausado"/"parado" depende de `auto`.
    document.querySelectorAll(`.${CAIXA}`).forEach((c) => pintarCaixa(c, fila.estado(c.dataset.msg)));
    atualizarTextoDaFila();
    agendarVarredura();
  }

  // `naTela` era a opcao unica antiga: vale como padrao das duas novas.
  chrome.storage.local.get(['naTela', 'auto', 'mostrar']).then((c) => {
    const antigo = c.naTela ?? true;
    aplicarOpcoes({ auto: c.auto ?? antigo, mostrar: c.mostrar ?? antigo });
  });
  chrome.storage.onChanged.addListener((mudou, area) => {
    if (area !== 'local') return;
    const novas = {};
    if (mudou.auto) novas.auto = mudou.auto.newValue;
    if (mudou.mostrar) novas.mostrar = mudou.mostrar.newValue;
    if (Object.keys(novas).length) aplicarOpcoes(novas);
  });
  new MutationObserver(agendarVarredura).observe(document.documentElement, { childList: true, subtree: true });
})();
