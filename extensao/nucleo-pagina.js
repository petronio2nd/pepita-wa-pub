// Logica da extensao sem DOM nem chrome.*: canal com prazo ate a pagina.js,
// varredura de periodo, operacao cancelavel, exportacao e fila da transcricao
// na tela. Roda igual nos dois mundos do content script e no `node --test`.
(() => {
  'use strict';

  function erroDe(tipo, mensagem, extra) {
    return Object.assign(new Error(mensagem), { tipo }, extra);
  }

  // ---------- canal ponte -> pagina ----------
  // `prazo` e de inatividade: cada mensagem de progresso renova. Sem resposta
  // no prazo, o pedido sai da lista de pendentes e rejeita com tipo `prazo`.
  function criarCanal(postar, { prazoPadrao = 10000 } = {}) {
    let proximo = 0;
    const pendentes = new Map();

    function armar(id) {
      const p = pendentes.get(id);
      clearTimeout(p.relogio);
      p.relogio = setTimeout(() => {
        pendentes.delete(id);
        p.rejeitar(erroDe('prazo', `a pagina nao respondeu (${p.acao}) em ${Math.round(p.prazo / 1000)} s`));
      }, p.prazo);
    }

    function pedir(acao, args = {}, { progresso, prazo = prazoPadrao } = {}) {
      const id = `w${++proximo}`;
      return new Promise((resolver, rejeitar) => {
        pendentes.set(id, { acao, resolver, rejeitar, progresso, prazo });
        armar(id);
        postar({ wacervo: 'pedido', id, acao, ...args });
      });
    }

    function receber(r) {
      if (!r?.wacervo || !pendentes.has(r.id)) return false;
      const p = pendentes.get(r.id);
      if (r.wacervo === 'progresso') {
        armar(r.id);
        p.progresso?.(r.texto);
        return true;
      }
      if (r.wacervo !== 'resposta') return false;
      clearTimeout(p.relogio);
      pendentes.delete(r.id);
      r.ok ? p.resolver(r.dados) : p.rejeitar(erroDe(r.tipo || 'pagina', r.erro));
      return true;
    }

    return { pedir, receber, get pendentes() { return pendentes.size; } };
  }

  // ---------- periodo ----------
  // buscar(null) -> pagina mais recente; buscar(m) -> pagina antes de m.
  // Mensagens so precisam de `t` (segundos); `chaveDe` da o id.
  async function varrerPeriodo(buscar, {
    inicio, fim, limitePaginas = 4000, cancelado = () => false, progresso = () => {}, chaveDe = (m) => m.id,
  }) {
    const maisVelhaDe = (lista) => lista.reduce((a, m) => (m.t < a.t ? m : a), lista[0]);
    let todas = (await buscar(null)) || [];
    let maisVelha = maisVelhaDe(todas);
    let paginas = 0;
    let motivo;
    for (;;) {
      if (!maisVelha) { motivo = 'fim_do_historico'; break; }
      if (maisVelha.t < inicio) { motivo = 'alcancou_inicio'; break; }
      if (paginas >= limitePaginas) { motivo = 'limite_de_paginas'; break; }
      if (cancelado()) throw erroDe('cancelado', 'cancelado');
      progresso(maisVelha.t);
      const antes = (await buscar(maisVelha)) || [];
      paginas++;
      const novas = antes.filter((m) => m.t <= maisVelha.t && chaveDe(m) !== chaveDe(maisVelha));
      if (!novas.length) { motivo = 'fim_do_historico'; break; }
      todas = todas.concat(novas);
      maisVelha = maisVelhaDe(novas);
    }
    return {
      mensagens: todas.filter((m) => m.t >= inicio && m.t <= fim).sort((a, b) => a.t - b.t),
      cobertura: {
        motivoDaParada: motivo,
        paginas,
        maisAntigaEncontrada: maisVelha ? maisVelha.t : null,
        pedido: { inicio, fim },
      },
    };
  }

  // ---------- operacao cancelavel ----------
  // `etapa` corre a promessa contra o cancelamento: cancelar solta na hora,
  // sem esperar o passo em curso (que termina sozinho e e ignorado).
  function criarOperacao() {
    const ouvintes = new Set();
    const op = {
      cancelada: false,
      cancelar() {
        if (op.cancelada) return;
        op.cancelada = true;
        for (const f of [...ouvintes]) f();
      },
      aoCancelar(f) {
        ouvintes.add(f);
        return () => ouvintes.delete(f);
      },
      verificar() {
        if (op.cancelada) throw erroDe('cancelado', 'cancelado');
      },
      etapa(promessa) {
        op.verificar();
        return new Promise((ok, falha) => {
          const soltar = op.aoCancelar(() => falha(erroDe('cancelado', 'cancelado')));
          Promise.resolve(promessa).then(ok, falha).finally(soltar);
        });
      },
    };
    return op;
  }

  // Limita quantas tarefas rodam ao mesmo tempo: `vez(fn)` roda fn quando abre
  // vaga e devolve a promessa dela. A fila de espera anda na ordem de chegada.
  function criarPiscina(limite) {
    let ativos = 0;
    const espera = [];
    const soltar = () => {
      ativos--;
      if (espera.length) espera.shift()();
    };
    return (fn) =>
      new Promise((entrar) => {
        const ir = () => {
          ativos++;
          entrar();
        };
        if (ativos < limite) ir();
        else espera.push(ir);
      }).then(() => fn()).finally(soltar);
  }

  // ---------- exportacao ----------
  const ehAudio = (m) => m.tipo === 'ptt' || m.tipo === 'audio';

  function alvosDe(mensagens, { audio = true, imagem = true } = {}) {
    // Visualizacao unica nao da para baixar pelo WhatsApp Web: nem tenta.
    return mensagens.filter(
      (m) => !m.visualizacaoUnica && ((audio && ehAudio(m)) || (imagem && m.tipo === 'image')),
    );
  }

  // ---------- anexos ----------
  // Fotos, videos e documentos vao para anexos/ ao lado do .md. Audio nao: ja
  // vira texto. O nome sai da data, da hora e do tipo (documento mantem o nome
  // original), e e o mesmo a cada exportacao: exportar de novo pula o que ja
  // esta na pasta.
  const TIPOS_ANEXO = new Set(['image', 'video', 'document']);
  const EXTENSOES = {
    'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp', 'image/gif': 'gif', 'image/heic': 'heic',
    'video/mp4': 'mp4', 'video/3gpp': '3gp', 'video/quicktime': 'mov', 'video/webm': 'webm',
    'application/pdf': 'pdf', 'text/plain': 'txt',
  };
  const PADRAO_EXTENSAO = { image: 'jpg', video: 'mp4', document: 'bin' };

  const anexaveis = (mensagens) => mensagens.filter((m) => !m.visualizacaoUnica && TIPOS_ANEXO.has(m.tipo));

  function extensaoDe(m) {
    const mime = String(m.mimetype || '').split(';')[0].trim().toLowerCase();
    if (EXTENSOES[mime]) return EXTENSOES[mime];
    const sub = mime.split('/')[1] || '';
    return /^[a-z0-9]{2,5}$/.test(sub) ? sub : PADRAO_EXTENSAO[m.tipo];
  }

  // SEG-08: extensoes que o Windows executa ou abre como atalho; ganham `.bin` no fim.
  const EXTENSOES_DE_RISCO = new Set([
    'exe', 'com', 'scr', 'bat', 'cmd', 'ps1', 'vbs', 'js', 'jse', 'wsf', 'hta', 'lnk', 'url', 'msi', 'msp',
    'reg', 'cpl', 'jar', 'vbe', 'wsh', 'pif', 'scf', 'msc', 'chm', 'inf', 'appx', 'msix', 'appxbundle', 'dll', 'sys',
    'ade', 'adp', 'mde', 'mst', 'ps2', 'psm1', 'gadget', 'application', 'jnlp', 'docm', 'xlsm', 'pptm',
  ]);

  // O que o Windows nao aceita em nome de arquivo sai; espacos repetidos viram um.
  // Controles de direcao Unicode (U+202E e cia., que invertem a direcao do nome) saem tambem; ZWJ e ZWNJ ficam.
  function nomeLimpo(nome) {
    return String(nome || '')
      .replace(/[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]/g, '')
      .replace(/[<>:"/\\|?*\u0000-\u001f]/g, ' ')
      .replace(/\s+/g, ' ')
      .trim()
      .replace(/[. ]+$/, '')
      .slice(0, 80)
      .replace(/[. ]+$/, '')
      .trim();
  }

  function nomesDosAnexos(mensagens) {
    const dois = (n) => String(n).padStart(2, '0');
    const usados = new Set();
    const nomes = new Map();
    for (const m of anexaveis(mensagens)) {
      const d = new Date(m.t * 1000);
      const quando =
        `${d.getFullYear()}-${dois(d.getMonth() + 1)}-${dois(d.getDate())}_` +
        `${dois(d.getHours())}${dois(d.getMinutes())}${dois(d.getSeconds())}`;
      let base;
      let ext = extensaoDe(m);
      const original = m.tipo === 'document' ? nomeLimpo(m.arquivo) : '';
      if (original) {
        const ponto = original.lastIndexOf('.');
        if (ponto > 0) {
          base = original.slice(0, ponto).trim();
          ext = original.slice(ponto + 1).trim();
          // `.parcial` e o nome do arquivo pela metade no wa servir: ele recusa.
          if (ext.toLowerCase() === 'parcial') ext = 'parcial.bin';
        } else {
          base = original;
        }
      } else {
        base = m.tipo === 'image' ? 'imagem' : m.tipo === 'video' ? (m.gif ? 'gif' : 'video') : 'documento';
      }
      // SEG-08: extensao executavel do remetente nao vai para a pasta do projeto como esta.
      if (EXTENSOES_DE_RISCO.has(ext.toLowerCase())) ext = `${ext}.bin`;
      let nome = `${quando}_${base}.${ext}`;
      for (let n = 2; usados.has(nome.toLowerCase()); n++) nome = `${quando}_${base}-${n}.${ext}`;
      usados.add(nome.toLowerCase());
      nomes.set(m.id, `anexos/${nome}`);
    }
    return nomes;
  }

  function resumir(mensagens, alvos, textos, reaproveitados, semMidia) {
    const r = {
      mensagens: mensagens.length, audios: 0, semFala: 0, imagensComTexto: 0, textoCurto: 0,
      imagensSemTexto: 0, falhas: 0, reaproveitados, semMidia: semMidia ? alvos.length : 0,
    };
    for (const m of alvos) {
      const t = textos.get(m.id);
      if (!t) continue;
      if (t.erro) r.falhas++;
      else if (ehAudio(m)) t.texto ? r.audios++ : r.semFala++;
      else if (t.texto) r.imagensComTexto++;
      else if (t.curto) r.textoCurto++;
      else r.imagensSemTexto++;
    }
    return r;
  }

  function descreverResumo(r) {
    const partes = [`${r.mensagens} mensagens`];
    if (r.audios) partes.push(`${r.audios} audio(s) transcrito(s)`);
    if (r.semFala) partes.push(`${r.semFala} audio(s) sem fala`);
    if (r.imagensComTexto) partes.push(`${r.imagensComTexto} imagem(ns) com texto`);
    if (r.textoCurto) partes.push(`${r.textoCurto} com texto curto`);
    if (r.reaproveitados) partes.push(`${r.reaproveitados} ja pronta(s) antes`);
    if (r.daTela) partes.push(`${r.daTela} audio(s) com o texto da tela`);
    if (r.semMidia) partes.push(`${r.semMidia} midia(s) sem transcricao/OCR`);
    if (r.anexos) partes.push(`${r.anexos} anexo(s) na pasta`);
    if (r.falhas) partes.push(`${r.falhas} falha(s)`);
    if (r.anexosFalhas) partes.push(`${r.anexosFalhas} anexo(s) nao baixado(s)`);
    return partes.join(', ');
  }

  // deps: coletar(args), processar(m, rota), gerar(chat, msgs, textos, info), salvar(md),
  //       anexar(m, caminho)? -> {arquivo} | {erro}, servidorNoAr()?, fila?,
  //       mostrar(texto)?, passo(feitos, total)?
  // pedido: {args, audio, imagem, anexos?, semMidia?, anterior?: {coleta, textos}, marcaAnterior?}
  // `anterior` reaproveita a coleta e os textos que deram certo: tentar de novo
  // so reprocessa as falhas. Cancelar em qualquer ponto impede o salvar.
  async function exportar(deps, pedido, op = criarOperacao()) {
    const mostrar = deps.mostrar || (() => {});
    let coleta = pedido.anterior?.coleta;
    if (!coleta) {
      mostrar('lendo mensagens...');
      coleta = await op.etapa(deps.coletar(pedido.args));
    }
    const { chat, mensagens } = coleta;
    if (!mensagens.length) throw erroDe('vazio', 'nenhuma mensagem no intervalo');

    const alvos = alvosDe(mensagens, pedido);
    const textos = new Map();
    let reaproveitados = 0;
    const pendentes = [];
    if (!pedido.semMidia) {
      const anteriores = pedido.anterior?.textos || new Map();
      for (const m of alvos) {
        const pronto = anteriores.get(m.id) || (ehAudio(m) ? deps.fila?.resultado(m.id) : null);
        if (pronto && !pronto.erro) {
          textos.set(m.id, pronto);
          reaproveitados++;
        } else {
          pendentes.push(m);
        }
      }
    }

    // Transcricao desligada: o que a fila da tela ja transcreveu entra assim
    // mesmo. Nao custa nada, e o servidor nem e chamado para isso.
    const desligados = { audios: 0, imagens: 0, daTela: 0 };
    if (!pedido.semMidia) {
      const validas = mensagens.filter((m) => !m.visualizacaoUnica);
      if (!(pedido.audio ?? true)) {
        for (const m of validas.filter(ehAudio)) {
          desligados.audios++;
          const pronto = deps.fila?.resultado(m.id);
          if (pronto && !pronto.erro) {
            textos.set(m.id, pronto);
            desligados.daTela++;
          }
        }
      }
      if (!(pedido.imagem ?? true)) desligados.imagens = validas.filter((m) => m.tipo === 'image').length;
    }

    if (pendentes.length) {
      mostrar(
        `${mensagens.length} mensagens; ${pendentes.length} midia(s) a processar` +
          (reaproveitados ? `, ${reaproveitados} ja pronta(s)` : ''),
      );
      if (deps.servidorNoAr && !(await op.etapa(deps.servidorNoAr()))) {
        throw erroDe('fora', 'pepita-wa fora do ar', { coleta, textos });
      }
    }

    // Audios em paralelo, ate `deps.paralelo` (3): no Gemini cada um leva uns
    // 15 s, e um por vez deixava a exportacao lenta. Imagem e anexo continuam
    // um por vez, no laco abaixo, que espera cada audio na ordem da conversa.
    const vez = criarPiscina(Math.max(1, deps.paralelo || 3));
    const audios = new Map(); // id -> Promise do resultado
    for (const m of pendentes) {
      if (!ehAudio(m)) continue;
      // Audio que a fila da tela ja esta transcrevendo: espera o dela.
      const emCurso = deps.fila?.aguardar(m.id);
      const p = emCurso || vez(() => {
        op.verificar();
        return deps.processar(m, '/transcrever');
      }).then((r) => {
        deps.fila?.registrar(m.id, r);
        return r;
      });
      p.catch(() => {}); // quem espera e o laco; cancelado, ninguem espera
      audios.set(m.id, p);
    }

    // Anexos nao dependem do servidor: saem mesmo sem transcricao/OCR.
    const nomes = pedido.anexos && deps.anexar ? nomesDosAnexos(mensagens) : new Map();
    const anexos = new Map(); // id -> {arquivo} | {erro}
    const aProcessar = new Set(pendentes.map((m) => m.id));
    // Uma mensagem por vez, com o OCR e o anexo juntos: a ponte baixa a foto uma vez so.
    const trabalho = mensagens.filter((m) => aProcessar.has(m.id) || nomes.has(m.id));
    let feitos = 0;
    let feitosMidia = 0;
    let feitosAnexo = 0;
    for (const m of trabalho) {
      op.verificar();
      deps.passo?.(++feitos, trabalho.length);
      if (aProcessar.has(m.id)) {
        const audio = ehAudio(m);
        mostrar(`${audio ? 'transcrevendo audio' : 'lendo imagem'} ${++feitosMidia} de ${pendentes.length}...`);
        const r = await op.etapa(audio ? audios.get(m.id) : deps.processar(m, '/ocr'));
        textos.set(m.id, r);
      }
      if (nomes.has(m.id)) {
        op.verificar();
        mostrar(`baixando anexo ${++feitosAnexo} de ${nomes.size}...`);
        anexos.set(m.id, await op.etapa(deps.anexar(m, nomes.get(m.id))));
      }
    }

    op.verificar();
    const info = {
      modo: pedido.args?.modo, cobertura: coleta.cobertura, anterior: pedido.marcaAnterior || null,
      semMidia: !!pedido.semMidia,
      audiosDesligados: desligados.audios, audiosDaTela: desligados.daTela,
      imagensDesligadas: desligados.imagens, anexos: pedido.anexos ? anexos : null,
    };
    const md = deps.gerar(chat, mensagens, textos, info);
    op.verificar();
    const salvoEm = await deps.salvar(md);
    const resumo = resumir(mensagens, alvos, textos, reaproveitados, pedido.semMidia);
    resumo.daTela = desligados.daTela;
    resumo.anexos = [...anexos.values()].filter((a) => !a.erro).length;
    resumo.anexosFalhas = anexos.size - resumo.anexos;
    return { salvoEm, resumo, coleta, textos, anexos };
  }

  // ---------- todas as conversas ----------
  // "Exportar todas": a pagina.js lista os chats do WhatsApp e este trecho decide
  // quais entram, em que ordem, como evitar nome repetido e como somar o resumo.
  // Cada conversa sai pelo `exportar` de cima, uma por vez.
  const SERVIDORES_INDIVIDUAIS = new Set(['c.us', 'lid', 's.whatsapp.net']);

  // Item da lista: {id, nome, t, arquivada, grupo?, newsletter?, broadcast?, psa?, eu?, servidor?}.
  // So conversa com uma pessoa: fora grupo, comunidade, canal, lista de
  // transmissao, status, o aviso oficial do WhatsApp e a conversa "Eu".
  function ehConversaIndividual(c) {
    if (!c?.id) return false;
    if (c.grupo || c.newsletter || c.broadcast || c.psa || c.eu) return false;
    const servidor = c.servidor || String(c.id).split('@')[1] || '';
    return SERVIDORES_INDIVIDUAIS.has(servidor);
  }

  // Filtra, tira id repetido e ordena da atividade mais recente para a mais
  // antiga (cancelar no meio deixa as mais vivas ja gravadas).
  // `grupos`: ids `...@g.us` que o usuario escolheu (0.21.0, [grupos] ids do config.toml).
  // So esses grupos entram; qualquer outro continua fora.
  function ehGrupoEscolhido(c, escolhidos) {
    return !!(c?.id && c.grupo && String(c.id).endsWith('@g.us') && escolhidos.has(c.id) &&
      !c.newsletter && !c.broadcast && !c.psa);
  }

  function selecionarConversas(lista, { grupos = [] } = {}) {
    const escolhidos = new Set(Array.isArray(grupos) ? grupos : []);
    const vistos = new Set();
    const unicas = [];
    for (const c of lista || []) {
      if (!(ehConversaIndividual(c) || ehGrupoEscolhido(c, escolhidos)) || vistos.has(c.id)) continue;
      vistos.add(c.id);
      unicas.push(c);
    }
    const ordenadas = unicas
      .map((c, i) => ({ c, i }))
      .sort((a, b) => (b.c.t || 0) - (a.c.t || 0) || a.i - b.i)
      .map(({ c }) => c);
    // Homonimos: todos levam o numero do id no nome da pasta (nomeUnico), nao so
    // o segundo. A ordem muda com a atividade, e um nome sem sufixo que trocasse
    // de dono entre duas execucoes sobrescreveria a conversa do outro contato.
    const porNome = new Map();
    for (const c of ordenadas) {
      const k = String(c.nome || '').trim().toLowerCase();
      porNome.set(k, (porNome.get(k) || 0) + 1);
    }
    return ordenadas.map((c) => ({ ...c, homonima: porNome.get(String(c.nome || '').trim().toLowerCase()) > 1 }));
  }

  // Nomes que o Windows reserva (CON, NUL...) nao viram pasta: o wa servir recusa.
  const RESERVADO_NO_WINDOWS = /^(con|prn|aux|nul|com[0-9]|lpt[0-9])$/i;

  // Dois contatos com o mesmo nome (e o mesmo periodo) cairiam na mesma pasta:
  // com `forcarId` (homonimo na lista), o nome ja sai com o numero do id
  // (`renata-silva_..-556290000000`), igual em toda execucao; sem ele, so o
  // segundo a aparecer ganha o sufixo; se ainda assim repetir, `-2`, `-3`.
  // `usados` guarda os nomes desta execucao.
  function nomeUnico(usados, base, id, { forcarId = false } = {}) {
    const chave = (n) => n.toLowerCase();
    const usuario = nomeLimpo(String(id || '').split('@')[0]);
    if (RESERVADO_NO_WINDOWS.test(base)) base = `${base}-conversa`;
    let nome = base;
    if ((forcarId || usados.has(chave(nome))) && usuario && !chave(base).endsWith(`-${usuario}`)) nome = `${base}-${usuario}`;
    const raiz = nome;
    for (let n = 2; usados.has(chave(nome)); n++) nome = `${raiz}-${n}`;
    usados.add(chave(nome));
    return nome;
  }

  // deps: listar() -> lista crua de chats; exportarUma(conversa, indice, total)
  //       -> resultado do `exportar` (rejeita com tipo `vazio` quando nao ha
  //       mensagem); aoComecar?(conversa, indice, total); aoTerminar?(item, indice, total)
  // Devolve sempre o resumo, mesmo cancelada ou interrompida: o que ja foi
  // gravado fica e conta. Cada conversa cai em um so lugar: exportada
  // (completa), parcial (parou no limite de paginas), falha ou pulada (sem
  // mensagem). Servidor fora do ar e pagina travada varias vezes seguidas
  // param a execucao, em vez de repetir o mesmo erro em todas as conversas.
  // `soIds` (captura ao vivo): so essas conversas, filtradas depois da selecao para a
  // homonimia valer contra a lista inteira.
  async function exportarTodas(deps, op = criarOperacao(), { maxPrazosSeguidos = 3, soIds = null, grupos = [] } = {}) {
    let conversas = selecionarConversas(await op.etapa(deps.listar()), { grupos });
    if (soIds) {
      const so = new Set(soIds);
      conversas = conversas.filter((c) => so.has(c.id));
    }
    const resumo = {
      total: conversas.length, exportadas: 0, semNovidade: 0, parciais: 0, falhas: 0, puladas: 0, naoTentadas: 0,
      midiasComFalha: 0, anexosFalhos: 0, cancelado: false, parou: null, itens: [],
    };
    let prazosSeguidos = 0;
    let tentadas = 0;
    for (const [i, conversa] of conversas.entries()) {
      if (op.cancelada) { resumo.cancelado = true; break; }
      deps.aoComecar?.(conversa, i + 1, conversas.length);
      tentadas++;
      let item;
      try {
        const r = await deps.exportarUma(conversa, i + 1, conversas.length);
        const parcial = r.coleta?.cobertura?.motivoDaParada === 'limite_de_paginas';
        parcial ? resumo.parciais++ : resumo.exportadas++;
        resumo.midiasComFalha += r.resumo?.falhas || 0;
        resumo.anexosFalhos += r.resumo?.anexosFalhas || 0;
        item = { nome: conversa.nome, estado: parcial ? 'parcial' : 'exportada', salvoEm: r.salvoEm };
        prazosSeguidos = 0;
      } catch (erro) {
        if (erro.tipo === 'cancelado') {
          resumo.cancelado = true;
          tentadas--; // a que estava em curso foi desfeita: nao conta como tentada
          break;
        }
        if (erro.tipo === 'vazio') {
          resumo.puladas++;
          item = { nome: conversa.nome, estado: 'pulada', erro: erro.message };
          prazosSeguidos = 0;
        } else if (erro.tipo === 'sem_novidade') {
          resumo.semNovidade++;
          item = { nome: conversa.nome, estado: 'sem_novidade' };
          prazosSeguidos = 0;
        } else {
          resumo.falhas++;
          item = { nome: conversa.nome, estado: 'falha', erro: erro.message };
          prazosSeguidos = erro.tipo === 'prazo' ? prazosSeguidos + 1 : 0;
          if (erro.tipo === 'fora') resumo.parou = 'fora';
          else if (prazosSeguidos >= maxPrazosSeguidos) resumo.parou = 'prazo';
        }
      }
      resumo.itens.push(item);
      deps.aoTerminar?.(item, i + 1, conversas.length);
      if (resumo.parou) break;
    }
    resumo.naoTentadas = conversas.length - tentadas;
    return resumo;
  }

  // ---------- incremental ----------
  // O "Exportar todas" grava em C:\WHATSAPP (a raiz que o Pepita le) e pula o
  // que ja foi: o servidor guarda, por conversa, o trecho continuo ja exportado
  // (`primeira`..`ultima`, em segundos) e se o historico ja foi lido ate o comeco
  // (`completaAntes`). Cada rodada le o que falta e grava so as mensagens novas.
  const PASTA_TODAS = '70-WHATSAPP';

  // Sem marca ou sem o comeco do historico: le tudo desde o comeco (o recorte tira
  // o que ja foi). Com o comeco ja lido: so o que veio depois da ultima.
  function planoDaConversa(marca, agora) {
    const inicio = marca?.completaAntes ? marca.ultima + 1 : 0;
    return { modo: 'periodo', inicio, fim: agora };
  }

  function chaveDaImportacao(importadas, conversa) {
    if (importadas[conversa.id]) return conversa.id;
    if (conversa.grupo) return null;
    const tel = String(conversa.telefone || '').replace(/\D/g, '');
    if (!tel) return null;
    return [`${tel}@s.whatsapp.net`, `${tel}@c.us`].find((id) => importadas[id]) || null;
  }

  const novasDaConversa = (marca, mensagens) =>
    marca ? mensagens.filter((m) => m.t < marca.primeira || m.t > marca.ultima) : mensagens.slice();

  // coleta: {mensagens, cobertura} do plano acima. Devolve o trecho que fica
  // coberto depois desta rodada, ou null quando nao ha o que marcar (historico
  // vazio, ou a leitura parou antes de encostar no trecho ja marcado, o que
  // abriria um buraco).
  function coberturaNova(marca, coleta) {
    const c = coleta.cobertura || {};
    const lidas = coleta.mensagens || [];
    const primeiraLida = c.motivoDaParada === 'alcancou_inicio' ? c.pedido?.inicio : c.maisAntigaEncontrada;
    if (primeiraLida == null || (!lidas.length && !marca)) return null;
    if (marca && primeiraLida > marca.ultima + 1) return null;
    const novaUltima = lidas.reduce((a, m) => Math.max(a, m.t), -Infinity);
    const primeira = Math.min(primeiraLida, marca?.primeira ?? Infinity, ...(lidas.length ? [lidas[0].t] : []));
    const ultima = Math.max(novaUltima, marca?.ultima ?? -Infinity);
    if (!Number.isFinite(primeira) || !Number.isFinite(ultima)) return null;
    return { primeira, ultima, completaAntes: !!marca?.completaAntes || c.motivoDaParada === 'fim_do_historico' };
  }

  // O que fazer com a conversa depois de ler o plano: `acao` e 'vazio' (nunca teve
  // mensagem), 'sem_novidade' (marca de novo se `cobertura`: o comeco do historico pode
  // ter acabado de ser lido), 'limite' (nada a gravar: abriria um buraco) ou
  // 'exportar' (grava `novas` e so depois marca `cobertura`).
  function decidirConversa(marca, coleta, importadas = []) {
    const cobertura = coberturaNova(marca, coleta);
    const recibos = new Set(importadas);
    const novas = novasDaConversa(marca, coleta.mensagens || []).filter((m) => {
      // MsgKey serializado: fromMe_chat_stanza[_participante]. A pagina tambem
      // manda o stanza bruto; nao compara texto nem minuto para mensagens novas.
      const id = m.stanzaId || /^(?:true|false)_[^_]+_([^_]+)(?:_|$)/.exec(m.id || '')?.[1];
      return !id || !recibos.has(id);
    });
    if (!novas.length) return { acao: marca || (importadas.length && cobertura) ? 'sem_novidade' : 'vazio', cobertura, novas };
    if (!cobertura) return { acao: 'limite', cobertura, novas };
    return { acao: 'exportar', cobertura, novas };
  }

  const dois = (n) => String(n).padStart(2, '0');
  const horaCurta = (quando) => {
    const d = new Date(quando);
    return `${dois(d.getHours())}:${dois(d.getMinutes())}`;
  };
  // "31/10 14:30", de ISO (o servidor) ou milissegundos.
  const diaHora = (quando) => {
    const d = new Date(quando);
    return `${dois(d.getDate())}/${dois(d.getMonth() + 1)} ${horaCurta(d)}`;
  };

  // Marca que o servidor nao guardou depois de o .md estar salvo: a ponte a deixa
  // pendente no navegador e a manda de novo na rodada seguinte, antes de tudo. Se
  // ainda nao der, o plano conta com ela assim mesmo: sem isso, a rodada seguinte
  // gravava as mesmas mensagens de novo num `-2`. A pasta e a do servidor, se houver.
  function marcaComPendente(marca, pendente) {
    if (!pendente) return marca;
    if (!marca) return { ...pendente };
    return {
      ...marca,
      primeira: Math.min(marca.primeira, pendente.primeira),
      ultima: Math.max(marca.ultima, pendente.ultima),
      completaAntes: !!(marca.completaAntes || pendente.completaAntes),
    };
  }

  // Antes da rodada: manda cada pendente (`enviar` devolve {ok, dados} ou {erro});
  // a que foi sai de `pendentes` e a marca do servidor entra em `marcas`; a que
  // nao foi fica e entra em `marcas` junto com a do servidor. Devolve se
  // `pendentes` mudou (para so entao grava-lo).
  async function reenviarPendentes(pendentes, marcas, enviar) {
    let mudou = false;
    for (const [id, p] of Object.entries(pendentes)) {
      const r = await Promise.resolve().then(() => enviar(p)).catch(() => null);
      if (r?.ok) {
        marcas.set(id, r.dados);
        delete pendentes[id];
        mudou = true;
      } else {
        marcas.set(id, marcaComPendente(marcas.get(id), p));
      }
    }
    return mudou;
  }

  // Linha do painel com a ultima rodada (`quando` em ISO, do servidor).
  function descreverRodada(rodada) {
    if (!rodada) return 'nenhuma rodada ainda';
    const quando = diaHora(rodada.quando);
    const com = rodada.exportadas + rodada.parciais;
    if (rodada.interrompida) return `ultima rodada em ${quando}: interrompida; rode de novo para terminar`;
    if (rodada.falhas || rodada.parciais) {
      return `ultima rodada em ${quando}: ${rodada.total} conversa(s), ${rodada.falhas} com falha, ${rodada.parciais} parcial(is)`;
    }
    return `tudo exportado em ${quando} · ${rodada.total} conversa(s), ${com} com novidade`;
  }

  function descreverResumoTodas(r) {
    const partes = [`${r.exportadas} exportada(s)`];
    if (r.semNovidade) partes.push(`${r.semNovidade} sem novidade`);
    if (r.parciais) partes.push(`${r.parciais} parcial(is) (limite de paginas)`);
    if (r.falhas) partes.push(`${r.falhas} com falha`);
    if (r.puladas) partes.push(`${r.puladas} pulada(s) (sem mensagem)`);
    if (r.midiasComFalha) partes.push(`${r.midiasComFalha} midia(s) sem transcricao/OCR`);
    if (r.anexosFalhos) partes.push(`${r.anexosFalhos} anexo(s) nao baixado(s)`);
    let linha = `${partes.join(', ')}, de ${r.total} conversa(s).`;
    if (r.cancelado) linha += `\ncancelado: ${r.naoTentadas} nao exportada(s); o que ja foi gravado ficou.`;
    else if (r.parou === 'fora') {
      linha += `\nparou: pepita-wa fora do ar (${r.naoTentadas} nao exportada(s)). Rode \`uv run wa servir\` e exporte de novo.`;
    } else if (r.parou === 'prazo') {
      linha += `\nparou: o WhatsApp Web parou de responder (${r.naoTentadas} nao exportada(s)). Recarregue a aba.`;
    }
    const problemas = r.itens.filter((x) => x.estado === 'falha' || x.estado === 'parcial');
    for (const x of problemas.slice(0, 5)) {
      linha += `\n${x.estado === 'falha' ? 'falhou' : 'parcial'}: ${x.nome}${x.erro ? ` (${x.erro})` : ''}`;
    }
    if (problemas.length > 5) linha += `\n... e mais ${problemas.length - 5}`;
    return linha;
  }

  // ---------- gravacao pelo wa servir e rodada automatica (0.16.0) ----------
  // O "Exportar todas" grava pelo `wa servir` (POST /gravar), dentro da raiz que
  // ele tem no config ([exportar] raiz, C:\WHATSAPP): nem o clique nem a rodada
  // automatica usam mais o seletor de pasta. A rodada automatica sai do relogio
  // do servidor a cada 30 min; o fundo.js acha ou abre a aba, e a ponte roda.

  // Partes de ate `maximo` bytes para mandar um anexo grande: [[inicio, fim), ...].
  function partesDoArquivo(tamanho, maximo) {
    const partes = [];
    for (let i = 0; i < tamanho; i += maximo) partes.push([i, Math.min(tamanho, i + maximo)]);
    return partes.length ? partes : [[0, 0]];
  }

  // Por que o "Exportar todas" nao pode rodar agora, ou null. `s` e o /status.
  // Automatica tambem para com a transcricao bloqueada: a exportacao e
  // incremental, e uma rodada sem transcricao marcaria os audios como exportados
  // sem o texto.
  function motivoParaNaoExportarTodas(s, { automatica = false } = {}) {
    if (!s?.ok) return `${s?.erro || 'pepita-wa fora do ar'}; rode \`uv run wa servir\``;
    if (!s.exportar) return 'o wa servir roda uma versao anterior a 0.16.0: reinicie-o';
    if (!s.exportar.existe) return `a pasta ${s.exportar.raiz} nao existe: crie-a ou ajuste [exportar] raiz no config.toml`;
    if (automatica && s.transcricao?.bloqueio) return `transcricao parada (${s.transcricao.bloqueio})`;
    return null;
  }

  const QUEM_TRAVA = { manual: 'exportacao de todas (clique)', auto: 'rodada automatica' };

  // Uma trava so entre o clique e a rodada automatica, em qualquer aba. Ela tem
  // inicio e validade; quem roda renova (a cada 20 s). Aba fechada ou travada no
  // meio: a validade vence e a trava cai sozinha.
  // atual: a trava guardada ou null; pedido: {quem, abaId, ...}.
  function decidirTrava(atual, pedido, agora, validadeMs) {
    if (atual && atual.validade > agora) {
      return {
        ok: false, ocupadaPor: atual.quem,
        motivo: `${QUEM_TRAVA[atual.quem] || atual.quem} em andamento desde ${horaCurta(atual.inicio)}`,
      };
    }
    return { ok: true, trava: { ...pedido, inicio: agora, validade: agora + validadeMs } };
  }

  function renovarTrava(atual, abaId, agora, validadeMs) {
    if (!atual || atual.abaId !== abaId || atual.validade <= agora) return null;
    return { ...atual, validade: agora + validadeMs };
  }

  // O que a rotina fecha no fim: so o que ela abriu. `registro`:
  // {abaId, abriuAba, abriuChrome, janelasAntes: {idJanela: [idAba...]}} do comeco
  // da rodada; `janelas`: [{id, abas: [idAba...]}] de agora. Chrome aberto pela
  // rotina: fecha as janelas que ja estavam la no comeco, se nenhuma aba nova
  // entrou nelas (fechar a ultima encerra o Chrome); numa janela que ganhou aba
  // do usuario, fecha so a do WhatsApp. Janela nova do usuario fica.
  function oQueFechar(registro, janelas) {
    const r = { janelas: [], abas: [] };
    if (!registro) return r;
    if (registro.abriuChrome) {
      const antes = registro.janelasAntes || {};
      for (const j of janelas || []) {
        const conhecidas = antes[j.id];
        if (!conhecidas) continue;
        const permitidas = new Set([...conhecidas, registro.abaId]);
        if (j.abas.every((a) => permitidas.has(a))) r.janelas.push(j.id);
        else if (j.abas.includes(registro.abaId)) r.abas.push(registro.abaId);
      }
      return r;
    }
    if (registro.abriuAba && registro.abaId != null) r.abas.push(registro.abaId);
    return r;
  }

  // Resultado da rodada automatica para o servidor: so numeros, nunca nome de contato.
  function resultadoDaRodada(r) {
    const partes = [`${r.exportadas} exportada(s)`, `${r.semNovidade + r.puladas} sem novidade`];
    if (r.parciais) partes.push(`${r.parciais} parcial(is)`);
    if (r.falhas) partes.push(`${r.falhas} com falha`);
    if (r.midiasComFalha) partes.push(`${r.midiasComFalha} midia(s) sem texto`);
    if (r.anexosFalhos) partes.push(`${r.anexosFalhos} anexo(s) nao baixado(s)`);
    let detalhe = `${partes.join(', ')}, de ${r.total}`;
    let resultado = r.falhas ? 'falha' : 'ok';
    if (r.cancelado) {
      resultado = 'interrompida';
      detalhe += '; cancelada';
    } else if (r.parou) {
      resultado = 'interrompida';
      detalhe += r.parou === 'fora' ? '; parou: pepita-wa fora do ar' : '; parou: o WhatsApp Web parou de responder';
    }
    return { resultado, detalhe };
  }

  const NOME_RESULTADO = { ok: 'ok', pulada: 'pulada', falha: 'com falha', interrompida: 'interrompida' };

  // Linha do painel: ligada?, a ultima rodada automatica e a proxima. `r` e o GET /rotina.
  function descreverRotina(r) {
    if (!r) return 'rodada automatica: sem resposta do pepita-wa';
    const quando = diaHora;
    const linhas = [];
    linhas.push(r.ligada ? `rodada automatica a cada ${r.intervalo_min} min` : 'rodada automatica desligada');
    if (r.ultima) {
      const u = r.ultima;
      linhas.push(`ultima: ${quando(u.quando)}, ${NOME_RESULTADO[u.resultado] || u.resultado}${u.detalhe ? ` (${u.detalhe})` : ''}`);
    }
    if (r.ligada && r.pendente) linhas.push('proxima: agora');
    else if (r.ligada && r.proxima) linhas.push(`proxima: ${quando(r.proxima)}`);
    return linhas.join('\n');
  }

  // ---------- fila da transcricao na tela ----------
  // Resultados por id da mensagem (nao pelo DOM): trocar de conversa ou fechar
  // o painel nao perde nada, e a caixa volta pintada quando o audio reaparece.
  // Pausar para a fila; esconder as caixas e outra coisa e fica na ponte.
  // Pedido manual (`manual: true`) anda mesmo com a fila pausada.
  function criarFila({
    processar, aoMudar = () => {}, aoAtividade = () => {}, maxTentativas = 3, esperaErroMs = 30000,
    agora = () => Date.now(),
  }) {
    const estados = new Map(); // id -> {fila}|{andamento}|{texto}|{erro, quando}, sempre com tentativas
    const espera = []; // o fim sai primeiro
    const manuais = new Set();
    const aguardando = new Map();
    let pausada = false;
    let rodando = false;

    function definir(id, r) {
      estados.set(id, r);
      aoMudar(id, r);
    }

    function tirarDaEspera(id) {
      const i = espera.indexOf(id);
      if (i >= 0) espera.splice(i, 1);
    }

    function pedir(id, { primeiro = false, manual = false } = {}) {
      const r = estados.get(id);
      if (r?.texto !== undefined || r?.andamento) return false;
      if (r?.erro && !manual && (r.tentativas >= maxTentativas || agora() - r.quando < esperaErroMs)) return false;
      if (manual) manuais.add(id);
      definir(id, { fila: true, tentativas: manual ? 0 : r?.tentativas || 0 });
      tirarDaEspera(id);
      primeiro || manual ? espera.push(id) : espera.unshift(id);
      rodar();
      return true;
    }

    function proximo() {
      for (let i = espera.length - 1; i >= 0; i--) {
        if (!pausada || manuais.has(espera[i])) return espera.splice(i, 1)[0];
      }
      return null;
    }

    function registrar(id, r) {
      const tentativas = (estados.get(id)?.tentativas || 0) + (r.erro ? 1 : 0);
      const final = r.erro ? { erro: r.erro, tipo: r.tipo, quando: agora(), tentativas } : { texto: r.texto || '', tentativas };
      tirarDaEspera(id);
      manuais.delete(id);
      definir(id, final);
      for (const f of aguardando.get(id) || []) f(final);
      aguardando.delete(id);
    }

    async function rodar() {
      if (rodando) return;
      rodando = true;
      aoAtividade(true);
      try {
        for (let id = proximo(); id !== null; id = proximo()) {
          const tentativas = estados.get(id)?.tentativas || 0;
          definir(id, { andamento: true, tentativas });
          let r;
          try {
            r = await processar(id);
          } catch (erro) {
            r = { erro: erro.message || String(erro) };
          }
          // A exportacao pode ter registrado este id enquanto isso: tanto faz,
          // o resultado e o mesmo arquivo.
          registrar(id, r);
        }
      } finally {
        rodando = false;
        aoAtividade(false);
      }
    }

    return {
      pedir,
      registrar,
      estado: (id) => estados.get(id),
      // So o que ja terminou (texto ou erro); fila e andamento nao contam.
      resultado(id) {
        const r = estados.get(id);
        return r && (r.texto !== undefined || r.erro) ? r : null;
      },
      aguardar(id) {
        if (!estados.get(id)?.andamento) return null;
        return new Promise((ok) => aguardando.set(id, [...(aguardando.get(id) || []), ok]));
      },
      pausar() {
        pausada = true;
      },
      retomar() {
        pausada = false;
        rodar();
      },
      get pausada() { return pausada; },
      get ocupada() { return rodando; },
      get pendentes() { return espera.length + (rodando ? 1 : 0); },
    };
  }

  // ---------- barra da esquerda ----------
  // O botao do painel entra na barra de icones da esquerda do WhatsApp (conversas,
  // status, canais, comunidades, Meta AI), logo abaixo do ultimo icone do grupo de
  // cima. A barra e achada pela posicao, nao por classe do WhatsApp, que muda:
  // botoes estreitos colados na borda esquerda, um embaixo do outro. O grupo de
  // cima acaba no maior vao vertical (o de baixo tem midia e perfil).
  // `caixas` sao os getBoundingClientRect dos botoes, na ordem da lista;
  // devolve o indice do botao depois do qual entrar e o do botao acima dele.
  const BARRA_MAX_ESQUERDA = 80;
  function ancoraNaBarra(caixas) {
    const tamanhoDeIcone = (v) => v >= 20 && v <= 72;
    const naBarra = [];
    caixas.forEach((c, i) => {
      if (c && c.left >= 0 && c.left < BARRA_MAX_ESQUERDA && tamanhoDeIcone(c.width) && tamanhoDeIcone(c.height)) {
        naBarra.push({ i, top: c.top, fim: c.top + c.height });
      }
    });
    naBarra.sort((a, b) => a.top - b.top || a.i - b.i);
    // Botao dentro de botao (role=button em volta de um button) tem a mesma caixa: fica o primeiro.
    const unicos = naBarra.filter((b, k) => k === 0 || Math.abs(b.top - naBarra[k - 1].top) > 4);
    if (!unicos.length) return null;
    const vaos = unicos.slice(1).map((b, k) => b.top - unicos[k].fim);
    let corte = unicos.length - 1;
    if (vaos.length) {
      const maior = Math.max(...vaos);
      const ordenados = [...vaos].sort((a, b) => a - b);
      const mediana = ordenados[Math.floor(ordenados.length / 2)];
      // Sem um vao bem maior que os outros (barra sem grupo de baixo), entra no fim.
      if (maior >= Math.max(48, 3 * mediana)) corte = vaos.indexOf(maior);
    }
    return { ancora: unicos[corte].i, anterior: corte > 0 ? unicos[corte - 1].i : -1 };
  }

  // ---------- versao ----------
  // Extensao e servidor saem sempre com a mesma versao. Diferente quer dizer que
  // um dos dois ficou rodando o codigo antigo: diz qual recarregar.
  function compararVersoes(a, b) {
    const pa = String(a).split('.').map(Number);
    const pb = String(b).split('.').map(Number);
    for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
      const d = (pa[i] || 0) - (pb[i] || 0);
      if (d) return Math.sign(d);
    }
    return 0;
  }

  function avisoDeVersao(extensao, servidor) {
    if (!extensao || !servidor) return null;
    const c = compararVersoes(extensao, servidor);
    if (c > 0) return `servidor na ${servidor}, extensao na ${extensao}: reinicie o \`uv run wa servir\``;
    if (c < 0) return `extensao na ${extensao}, servidor na ${servidor}: recarregue a extensao em chrome://extensions`;
    return null;
  }

  // Nome de uma conversa individual: o salvo nos contatos do usuario; senao o do perfil
  // (pushname, ou verifiedName da conta comercial); senao o numero. O `formattedTitle`
  // do WhatsApp cai no numero formatado quando o contato nao esta salvo, entao
  // "parece numero" nao conta como nome salvo.
  function pareceNumero(t) {
    return /^\+?[\d\s().-]+$/.test(String(t || '').trim());
  }

  function nomeDaConversa({ grupo = false, titulo = '', nomeSalvo = '', pushname = '', verifiedName = '', numero = '' } = {}) {
    const limpo = (v) => String(v || '').trim();
    if (grupo) return { nome: limpo(titulo) || 'grupo', origem: 'salvo' };
    const salvo = limpo(nomeSalvo) || (limpo(titulo) && !pareceNumero(titulo) ? limpo(titulo) : '');
    if (salvo) return { nome: salvo, origem: 'salvo' };
    const perfil = limpo(verifiedName) || limpo(pushname);
    if (perfil && !pareceNumero(perfil)) return { nome: perfil, origem: 'perfil' };
    return { nome: limpo(titulo) || limpo(numero) || '?', origem: 'numero' };
  }

  // ---------- agente (0.19.0) ----------
  // O `wa mcp` pede pelo `wa servir`; o fundo.js pega o pedido e a ponte executa
  // aqui (ler, enviar). As travas que dependem do que a pagina ve ficam aqui; as da
  // sessao (nome aprovado, 30 min) ficam no mcp_whatsapp.py.
  const IDS_DE_CONVERSA = /@(c\.us|lid|g\.us|s\.whatsapp\.net)$/;
  const semAcento = (s) => String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().trim();

  // Formas de um telefone so com digitos, para casar com e sem o 55 e com e sem o
  // 9 do celular brasileiro (o WhatsApp guarda muitos celulares antigos sem o 9).
  function formasDoTelefone(digitos) {
    const formas = new Set([digitos]);
    let n = digitos;
    if (n.startsWith('55') && (n.length === 12 || n.length === 13)) n = n.slice(2);
    formas.add(n);
    if (n.length === 11 && n[2] === '9') formas.add(n.slice(0, 2) + n.slice(3));
    if (n.length === 10) formas.add(n.slice(0, 2) + '9' + n.slice(2));
    return [...formas];
  }

  // `alvo` parece telefone (so +, digitos, espaco, ponto, hifen e parenteses, 8+ digitos)?
  // Devolve os digitos ou ''.
  function digitosDoTelefone(alvo) {
    const a = String(alvo || '').trim();
    if (!/^[+\d\s().-]+$/.test(a)) return '';
    const d = a.replace(/\D/g, '');
    return d.length >= 8 ? d : '';
  }

  // Conversas individuais cujo `telefone` termina nos digitos pedidos (8+ digitos).
  function porTelefone(lista, digitos) {
    const pedidas = formasDoTelefone(digitos).filter((f) => f.length >= 8);
    return (lista || []).filter((c) => {
      const t = String(c.telefone || '').replace(/\D/g, '');
      if (!t || c.grupo) return false;
      return formasDoTelefone(t).some((f) => pedidas.some((p) => f.endsWith(p)));
    });
  }

  // lista: [{id, nome, grupo, eu, telefone}]. alvo: id, "eu", telefone (com ou sem o 55,
  // com ou sem o 9) ou parte do nome (sem acento e sem caixa). Nome igual vence parte do
  // nome. Devolve {conversa} | {ambiguo} | {erro}.
  function resolverConversa(lista, alvo, { limite = 15 } = {}) {
    const a = String(alvo || '').trim();
    if (!a) return { erro: 'diga qual conversa' };
    const resumo = ({ id, nome, grupo }) => ({ id, nome, grupo: !!grupo });
    if (IDS_DE_CONVERSA.test(a)) {
      const achada = (lista || []).find((c) => c.id === a);
      if (achada) return { conversa: achada };
      // Id c.us de quem o WhatsApp so conhece pelo @lid: tenta pelo telefone do id.
      const [usuario, servidor] = a.split('@');
      if (servidor === 'c.us' || servidor === 's.whatsapp.net') {
        const pelo = porTelefone(lista, usuario.replace(/\D/g, ''));
        if (pelo.length === 1) return { conversa: pelo[0] };
        if (pelo.length > 1) return { ambiguo: pelo.slice(0, limite).map(resumo) };
      }
      return { erro: `nenhuma conversa com o id ${a}` };
    }
    const digitos = digitosDoTelefone(a);
    if (digitos) {
      const pelo = porTelefone(lista, digitos);
      if (pelo.length === 1) return { conversa: pelo[0] };
      if (pelo.length > 1) return { ambiguo: pelo.slice(0, limite).map(resumo) };
      // Sem telefone que case, segue para o nome: contato nao salvo tem o numero como nome.
    }
    if (semAcento(a) === 'eu') {
      const eu = (lista || []).filter((c) => c.eu);
      if (eu.length === 1) return { conversa: eu[0] };
      // Nunca cai na busca por nome: "eu" casaria com "Eurico" ou "Deus", e o teste
      // na conversa do usuario iria para um terceiro.
      if (eu.length > 1) return { ambiguo: eu.slice(0, limite).map(({ id, nome, grupo }) => ({ id, nome, grupo: !!grupo })) };
      return { erro: 'a conversa do usuario com ele mesmo nao esta na lista do WhatsApp Web; abra-a uma vez no celular ou passe o id' };
    }
    const procurado = semAcento(a);
    const achados = (lista || []).filter((c) => semAcento(c.nome).includes(procurado));
    const exatos = achados.filter((c) => semAcento(c.nome) === procurado);
    const usar = exatos.length ? exatos : achados;
    if (!usar.length) return { erro: `nenhuma conversa com "${a}" no nome` };
    if (usar.length === 1) return { conversa: usar[0] };
    return { ambiguo: usar.slice(0, limite).map(({ id, nome, grupo }) => ({ id, nome, grupo: !!grupo })) };
  }

  // Chegou mensagem do contato depois da leitura? `msgs`: as ultimas da conversa
  // ({id, t, eu}); `leitura`: {ultimaT, vistos: [ids lidos]}. Olha todas, nao so a
  // ultima: o usuario pode ter respondido pelo celular depois da mensagem nova. Se as
  // buscadas nao alcancam a leitura (nenhuma vista nem anterior a ela) e a conversa
  // tem mais que isso (`completa` false), nao da para provar: conta como nova.
  function novaDepoisDaLeitura(msgs, { ultimaT = 0, vistos = [] } = {}, { completa = false } = {}) {
    const lista = msgs || [];
    const lidos = new Set(vistos);
    if (lista.some((m) => !m.eu && m.t >= ultimaT && !lidos.has(m.id))) return true;
    if (completa) return false;
    // Busca incompleta que nao alcanca a leitura (inclusive vazia depois do filtro): recusa.
    return !lista.some((m) => lidos.has(m.id) || m.t < ultimaT);
  }

  // A mensagem enviada aparece na conversa? Com o id que o envio devolveu, so por ele.
  // Sem id, pelo texto igual de uma mensagem minha que nao existia antes do envio
  // (`antes`: ids da conversa lidos logo antes): uma igual mais antiga nao confirma.
  // Falha do chrome.tabs.sendMessage para a aba do WhatsApp. So "sem receptor" prova
  // que nada rodou nela (aba de antes de recarregar a extensao): `sem_aba`, e pode
  // tentar outra. Qualquer outra (prazo, aba fechada ou recarregada no meio, porta
  // fechada antes da resposta) pode ter vindo depois do envio: `sem_resposta`, e
  // nunca tenta outra aba, para nao enviar duas vezes.
  function classificarFalhaNaAba(erro) {
    const msg = String(erro?.message || erro || '');
    if (!erro?.prazo && /Receiving end does not exist|Could not establish connection/i.test(msg)) {
      return { ok: false, tipo: 'sem_aba', erro: 'a aba do WhatsApp nao tem o pepita-wa: recarregue a aba' };
    }
    return { ok: false, tipo: 'sem_resposta', erro: `a aba do WhatsApp nao respondeu (${erro?.prazo ? 'prazo' : msg.slice(0, 120)})` };
  }

  // Arquivar (0.28.0): o que fazer antes de chamar o WhatsApp. Nome atual diferente do
  // aprovado recusa; para arquivar, mensagem do contato depois da leitura tambem (arquivar
  // esconderia o que ainda nao esta em dia); ja no estado pedido nao chama nada.
  // Devolve {recusa} | {resultado: 'nova' | 'ja_estava'} | {agir: true}.
  function antesDeArquivar({ nomeAtual, nomeEsperado, arquivada, arquivar, msgs, leitura, completa = false } = {}) {
    if (!nomeEsperado || nomeAtual !== nomeEsperado) {
      return { recusa: `o nome atual da conversa e "${nomeAtual}", nao "${nomeEsperado}"` };
    }
    if (!!arquivada === !!arquivar) return { resultado: 'ja_estava' };
    if (arquivar && novaDepoisDaLeitura(msgs, leitura, { completa })) return { resultado: 'nova' };
    return { agir: true };
  }

  function confirmarEnvio(msgs, { idEnviado = '', texto = '', antes = [] } = {}) {
    const minhas = (msgs || []).filter((m) => m.eu);
    if (idEnviado) return minhas.find((m) => m.id === idEnviado) || null;
    const jaHavia = new Set(antes);
    return minhas.find((m) => m.texto === texto && !jaHavia.has(m.id)) || null;
  }

  // Transcreve os audios da leitura do agente ate `prazoMs`, `paralelo` por vez. O que
  // nao terminou no prazo sai como pendente: o servidor continua e guarda no cache,
  // e a proxima leitura ja acha o texto. Nunca inventa: sem texto, fica pendente.
  // processar(m) -> {texto} | {erro, tipo}. Muda `mensagens` (campo `transcricao`).
  async function transcreverParaOAgente(mensagens, processar, { prazoMs = 60000, paralelo = 3 } = {}) {
    const vez = criarPiscina(paralelo);
    let acabou = false;
    const audios = (mensagens || []).filter((m) => ehAudio(m) && !m.visualizacaoUnica);
    for (const m of audios) m.transcricao = { pendente: `nao terminou em ${Math.round(prazoMs / 1000)} s; leia de novo daqui a pouco` };
    const todos = Promise.all(audios.map((m) => vez(async () => {
      if (acabou) return;
      const r = await Promise.resolve().then(() => processar(m)).catch((e) => ({ erro: String(e?.message || e) }));
      if (acabou) return;
      m.transcricao = r && !r.erro && typeof r.texto === 'string' ? { texto: r.texto } : { pendente: r?.erro || 'falhou' };
    })));
    let relogio;
    await Promise.race([todos, new Promise((ok) => { relogio = setTimeout(ok, prazoMs); })]);
    clearTimeout(relogio);
    acabou = true;
    return mensagens;
  }

  // ---------- captura ao vivo (0.20.0) ----------
  // A pagina avisa cada mensagem nova (do contato ou do usuario) e a conversa fica "suja".
  // Depois de `quietoMs` sem aviso novo (ou `maxMs` desde o primeiro aviso pendente),
  // `executar(ids)` exporta as sujas pelo fluxo incremental do Exportar todas. Cada
  // captura grava um .md, por isso a espera larga. `executar` devolve true se rodou;
  // false (outra exportacao, trava ocupada, transcricao parada) ou erro devolvem as
  // conversas, e a proxima tentativa espera `quietoMs`. Uma execucao por vez.
  function criarAgendadorCaptura({ executar, quietoMs = 120000, maxMs = 600000, relogio = {} } = {}) {
    const agora = relogio.agora || (() => Date.now());
    const depois = relogio.depois || ((fn, ms) => setTimeout(fn, ms));
    const cancelar = relogio.cancelar || ((t) => clearTimeout(t));
    const sujas = new Set();
    let desde = null; // primeiro aviso ainda nao capturado
    let naoAntesDe = 0; // depois de uma tentativa que nao rodou
    let timer = null;
    let rodando = false;
    let parado = false;

    function armar() {
      if (parado || rodando || !sujas.size) return;
      if (timer) cancelar(timer);
      const ms = Math.max(Math.min(quietoMs, desde + maxMs - agora()), naoAntesDe - agora(), 0);
      timer = depois(disparar, ms);
    }

    async function disparar() {
      timer = null;
      if (parado || rodando || !sujas.size) return;
      rodando = true;
      const ids = [...sujas];
      const desdeAntes = desde;
      sujas.clear();
      desde = null;
      let rodou = false;
      try {
        rodou = (await executar(ids)) === true;
      } catch {
        rodou = false;
      }
      rodando = false;
      if (parado) return;
      if (!rodou) {
        ids.forEach((id) => sujas.add(id));
        desde = desde === null ? desdeAntes : Math.min(desde, desdeAntes);
        naoAntesDe = agora() + quietoMs;
      }
      armar();
    }

    return {
      marcar(id) {
        if (parado || !id) return;
        sujas.add(id);
        if (desde === null) desde = agora();
        armar();
      },
      parar() {
        parado = true;
        if (timer) cancelar(timer);
        timer = null;
        sujas.clear();
      },
      get pendentes() { return [...sujas]; },
      get rodando() { return rodando; },
    };
  }

  const api = {
    resolverConversa, digitosDoTelefone, formasDoTelefone, novaDepoisDaLeitura, confirmarEnvio, classificarFalhaNaAba, transcreverParaOAgente,
    nomeDaConversa, erroDe, criarCanal, varrerPeriodo, criarOperacao, alvosDe, exportar, descreverResumo, criarFila,
    compararVersoes, avisoDeVersao, nomesDosAnexos,
    ehConversaIndividual, ehGrupoEscolhido, selecionarConversas, nomeUnico, exportarTodas, descreverResumoTodas,
    ancoraNaBarra, PASTA_TODAS, planoDaConversa, chaveDaImportacao, novasDaConversa, coberturaNova, decidirConversa, descreverRodada,
    partesDoArquivo, motivoParaNaoExportarTodas, decidirTrava, renovarTrava, oQueFechar, resultadoDaRodada,
    descreverRotina,
    marcaComPendente, reenviarPendentes, criarAgendadorCaptura, antesDeArquivar,
  };
  if (typeof module !== 'undefined') module.exports = api;
  else globalThis.wacervoNucleo = api;
})();
