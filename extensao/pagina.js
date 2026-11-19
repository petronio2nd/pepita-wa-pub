// Mundo MAIN do web.whatsapp.com: unico lugar com acesso ao WPP (wa-js).
// Responde pedidos da ponte.js. Desde a 0.19.0 tambem atende o agente (`wa mcp`):
// listar, ler, enviar e arquivar (0.28.0), com as travas da pagina no nucleo.js.
(() => {
  'use strict';

  const IGNORAR = new Set(['e2e_notification', 'protocol', 'notification_template', 'reaction', 'debug']);
  let cancelado = false;
  let escutando = false; // captura ao vivo (0.20.0)

  const serial = (wid) => (typeof wid === 'string' ? wid : wid?._serialized || wid?.toString?.() || '');

  function nome(wid, avulso) {
    const id = serial(wid);
    if (!id) return avulso || '?';
    let c;
    try {
      c = WPP.whatsapp.ContactStore.get(id);
    } catch {}
    if (c?.isMe) return 'Eu';
    const achado = c?.name || c?.pushname || c?.verifiedName || avulso;
    if (achado) return achado;
    const [usuario, servidor] = id.split('@');
    if (servidor === 'c.us' || servidor === 's.whatsapp.net') return '+' + usuario;
    return c?.formattedName || usuario;
  }

  function autorDe(msg) {
    if (msg.id?.fromMe) return 'Eu';
    return nome(msg.author || msg.from, msg.notifyName);
  }

  function resumoCitado(q) {
    if (!q) return '';
    if (q.type === 'chat') return q.body || '';
    if (q.caption) return q.caption;
    const rotulos = {
      image: '[imagem]', video: '[video]', ptt: '[audio]', audio: '[audio]', document: '[documento]',
      sticker: '[figurinha]', location: '[localizacao]', vcard: '[contato]', poll_creation: '[enquete]',
    };
    return rotulos[q.type] || `[${q.type || 'mensagem'}]`;
  }

  function telefones(vcard) {
    return (vcard || '')
      .split(/\r?\n/)
      .filter((l) => /^(item\d+\.)?TEL/i.test(l))
      .map((l) => l.slice(l.lastIndexOf(':') + 1).trim())
      .filter(Boolean);
  }

  function serializar(msg) {
    const tipo = msg.type;
    const o = {
      id: serial(msg.id),
      stanzaId: typeof msg.id?.id === 'string' ? msg.id.id : '',
      t: msg.t,
      eu: !!msg.id?.fromMe,
      autor: autorDe(msg),
      tipo,
      subtipo: msg.subtype || '',
      texto: '',
      encaminhada: !!msg.isForwarded,
      editada: !!(msg.latestEditMsgKey || msg.latestEditSenderTimestampMs),
      visualizacaoUnica: !!msg.isViewOnce,
    };

    if (tipo === 'chat') o.texto = msg.body || '';
    else if (msg.caption && msg.caption !== msg.filename) o.texto = msg.caption;

    if (msg.quotedMsg || msg.quotedStanzaID) {
      o.citado = {
        autor: msg.quotedParticipant ? nome(msg.quotedParticipant) : '',
        texto: resumoCitado(msg.quotedMsg),
        // INS-12: id e hora da citada, para o servidor dizer de quando ela e (qual imagem foi citada).
        id: typeof msg.quotedStanzaID === 'string' ? msg.quotedStanzaID : '',
        t: Number(msg.quotedMsg?.t) || 0,
      };
    }

    switch (tipo) {
      case 'ptt':
      case 'audio':
      case 'video':
        o.duracao = Number(msg.duration) || 0;
        o.gif = !!msg.isGif;
        break;
      case 'document':
        o.arquivo = msg.filename || '';
        o.paginas = msg.pageCount || 0;
        break;
      case 'location':
        o.lat = msg.lat;
        o.lng = msg.lng;
        o.local = msg.loc || '';
        break;
      case 'vcard':
        o.contatos = [{ nome: msg.vcardFormattedName || '', tel: telefones(msg.body) }];
        break;
      case 'multi_vcard':
        o.contatos = (msg.vcardList || []).map((v) => ({ nome: v.displayName || '', tel: telefones(v.vcard) }));
        break;
      case 'poll_creation':
        o.enquete = { nome: msg.pollName || '', opcoes: (msg.pollOptions || []).map((p) => p.name) };
        break;
      case 'gp2':
      case 'notification':
      case 'call_log':
        o.alvos = (msg.recipients || []).map((r) => nome(r));
        o.corpo = msg.body || '';
        break;
    }
    o.mimetype = msg.mimetype || '';
    return o;
  }

  function chatAtivo() {
    const chat = WPP.chat.getActiveChat();
    if (!chat) throw new Error('abra uma conversa primeiro');
    return chat;
  }

  function infoChat(chat) {
    const id = serial(chat.id);
    const grupo = !!(chat.isGroup || id.endsWith('@g.us'));
    let c;
    try {
      c = WPP.whatsapp.ContactStore.get(id);
    } catch {}
    // @lid nao carrega o telefone no id; o contato pode trazer o wid c.us em phoneNumber.
    const [usuario, servidor] = id.split('@');
    let telefone = '';
    if (!grupo) {
      if (servidor === 'c.us' || servidor === 's.whatsapp.net') telefone = usuario;
      else {
        const pn = c?.phoneNumber;
        telefone = pn ? (pn.user || serial(pn).split('@')[0] || '') : '';
      }
    }
    const { nome: nomeFinal, origem } = wacervoNucleo.nomeDaConversa({
      grupo,
      titulo: chat.formattedTitle || chat.name || '',
      nomeSalvo: c?.name,
      pushname: c?.pushname,
      verifiedName: c?.verifiedName,
      numero: telefone ? '+' + telefone : '',
    });
    return { id, nome: nomeFinal, origemNome: origem, grupo, telefone };
  }

  function limpar(msgs) {
    const vistos = new Map();
    for (const m of msgs) {
      if (!m || IGNORAR.has(m.type)) continue;
      vistos.set(serial(m.id), m);
    }
    return [...vistos.values()].sort((a, b) => a.t - b.t);
  }

  // Varredura em nucleo.js (testada em node); aqui so a busca no WPP.
  function periodo(chat, inicio, fim, progresso) {
    const id = serial(chat.id);
    return wacervoNucleo.varrerPeriodo(
      (antesDe) =>
        antesDe
          ? WPP.chat.getMessages(id, { count: 50, direction: 'before', id: serial(antesDe.id) })
          : WPP.chat.getMessages(id, { count: 50 }),
      {
        inicio,
        fim,
        cancelado: () => cancelado,
        chaveDe: (m) => serial(m.id),
        progresso: (t) => progresso(`buscando mensagens... ${new Date(t * 1000).toLocaleDateString()}`),
      },
    );
  }

  const erro = (tipo, mensagem) => wacervoNucleo.erroDe(tipo, mensagem);
  const pausa = (ms) => new Promise((ok) => setTimeout(ok, ms));

  // Usuario (sem o @) do proprio usuario, no c.us e no lid.
  function meusUsuarios() {
    const meus = new Set();
    for (const f of ['getMyUserId', 'getMyUserLid']) {
      try {
        const wid = WPP.conn[f]();
        if (wid) meus.add(wid.user || serial(wid).split('@')[0]);
      } catch {}
    }
    return meus;
  }

  function ehEu(id, meus) {
    let contato;
    try {
      contato = WPP.whatsapp.ContactStore.get(id);
    } catch {}
    return !!(contato?.isMe || meus.has(id.split('@')[0]));
  }

  const acoes = {
    async chat() {
      const chat = WPP.chat.getActiveChat();
      if (!chat) return null;
      const msgs = limpar(chat.msgs.getModelsArray());
      return {
        ...infoChat(chat),
        carregadas: msgs.length,
        primeira: msgs[0]?.t || 0,
        ultima: msgs.at(-1)?.t || 0,
      };
    },

    // `esperado` e a conversa congelada no clique: se o usuario trocou de conversa
    // enquanto escolhia o arquivo, recusa em vez de exportar a errada.
    async coletar({ modo, inicio, fim, esperado }, progresso) {
      cancelado = false;
      const chat = chatAtivo();
      const info = infoChat(chat);
      if (esperado && info.id !== esperado.id) {
        throw erro(
          'outra_conversa',
          `a conversa aberta mudou (esperava ${esperado.nome}, esta ${info.nome}). ` +
            'Volte para ela e exporte de novo.',
        );
      }
      let brutas;
      let cobertura = null;
      if (modo === 'periodo') {
        const r = await periodo(chat, inicio, fim, progresso);
        brutas = r.mensagens;
        cobertura = r.cobertura;
      } else {
        brutas = chat.msgs.getModelsArray();
      }
      // A conversa tambem nao pode ter mudado durante a busca.
      if (serial(WPP.chat.getActiveChat()?.id) !== info.id) {
        throw erro('outra_conversa', `a conversa aberta mudou durante a leitura de ${info.nome}. Exporte de novo.`);
      }
      return { chat: info, mensagens: limpar(brutas).map(serializar), cobertura };
    },

    // "Exportar todas": os chats de pessoas, arquivados incluidos. O WPP.chat.list
    // com onlyUsers ja deixa de fora grupo, canal e lista de transmissao; os
    // campos abaixo deixam o nucleo.js decidir de novo (selecionarConversas) e
    // tirar o aviso do WhatsApp e a conversa "Eu". So le; nao abre a conversa.
    // Captura ao vivo (0.20.0): avisa a ponte de cada mensagem nova, so com o id da
    // conversa (nada do texto). Registra uma vez por aba.
    escutarMensagens() {
      if (!escutando) {
        WPP.on('chat.new_message', (msg) => {
          try {
            const chatId = serial(msg?.id?.remote);
            // Pessoa ou grupo (a ponte descarta o grupo que o usuario nao escolheu); status e canal ficam fora.
            if (/@(c\.us|lid|g\.us)$/.test(chatId) && !IGNORAR.has(msg?.type)) {
              window.postMessage({ wacervo: 'evento', tipo: 'mensagem', chatId }, location.origin);
            }
          } catch {}
        });
        escutando = true;
      }
      return { escutando };
    },

    // `grupos`: ids `...@g.us` que o usuario escolheu (0.21.0); so estes entram alem das pessoas.
    async listarConversas({ grupos } = {}) {
      let chats = await WPP.chat.list({ onlyUsers: true, ignoreGroupMetadata: true });
      const escolhidos = new Set(Array.isArray(grupos) ? grupos.filter((g) => typeof g === 'string' && g.endsWith('@g.us')) : []);
      if (escolhidos.size) {
        const todos = await WPP.chat.list({ onlyGroups: true, ignoreGroupMetadata: true });
        chats = chats.concat(todos.filter((c) => escolhidos.has(serial(c.id))));
      }
      const meus = meusUsuarios();
      return chats.map((c) => {
        const id = serial(c.id);
        const [usuario, servidor] = id.split('@');
        let contato;
        try {
          contato = WPP.whatsapp.ContactStore.get(id);
        } catch {}
        const info = infoChat(c);
        return {
          id,
          nome: info.nome,
          origemNome: info.origemNome,
          telefone: info.telefone,
          t: Number(c.t) || 0,
          arquivada: !!c.archive,
          servidor,
          grupo: !!(c.isGroup || c.id?.isGroup?.()),
          newsletter: !!(c.isNewsletter || c.id?.isNewsletter?.()),
          broadcast: !!(c.id?.isBroadcast?.() || c.isBroadcast),
          psa: !!(c.isPSA || c.id?.isPSA?.()),
          eu: !!(contato?.isMe || meus.has(usuario)),
        };
      });
    },

    // Uma conversa pelo id, sem abri-la na tela (a de hoje exporta so a aberta).
    // Periodo: varre pelo WPP.chat.getMessages, como a exportacao de uma conversa.
    // Carregado: a pagina mais recente (50) mais o que o WhatsApp ja tem na memoria
    // para esse chat; conversa nao aberta quase nunca tem mais que isso carregado.
    async coletarConversa({ chatId, modo, inicio, fim }, progresso) {
      cancelado = false;
      const chat = WPP.chat.get(chatId);
      if (!chat) throw erro('indisponivel', 'o WhatsApp nao tem essa conversa');
      const info = infoChat(chat);
      let brutas;
      let cobertura = null;
      if (modo === 'periodo') {
        const r = await periodo(chat, inicio, fim, progresso);
        brutas = r.mensagens;
        cobertura = r.cobertura;
      } else {
        const recentes = (await WPP.chat.getMessages(serial(chat.id), { count: 50 })) || [];
        brutas = recentes.concat(chat.msgs?.getModelsArray?.() || []);
      }
      if (cancelado) throw erro('cancelado', 'cancelado');
      return { chat: info, mensagens: limpar(brutas).map(serializar), cobertura };
    },

    // Transcricao na tela: a ponte acha a bolha de audio no DOM e pergunta aqui
    // se o data-id dela e mesmo audio. O data-id pode vir serializado
    // (false_<jid>_<id>) ou so com o id curto, conforme a versao do WhatsApp Web.
    async audio({ domId }) {
      let msg = null;
      try {
        msg = WPP.whatsapp.MsgStore.get(domId);
      } catch {}
      if (!msg) {
        const lista = WPP.chat.getActiveChat()?.msgs?.getModelsArray?.() || [];
        msg = lista.find((m) => m.id?.id === domId || serial(m.id) === domId) || null;
      }
      if (!msg || (msg.type !== 'ptt' && msg.type !== 'audio') || msg.isViewOnce) return null;
      return { id: serial(msg.id), duracao: Number(msg.duration) || 0 };
    },

    async midia({ msgId }) {
      let blob;
      try {
        blob = await WPP.chat.downloadMedia(msgId);
      } catch (e) {
        // Midia expirada no aparelho, apagada ou ainda nao sincronizada.
        throw erro('indisponivel', `o WhatsApp nao entregou a midia (${e?.message || e})`);
      }
      if (!blob?.size) throw erro('indisponivel', 'o WhatsApp entregou a midia vazia');
      return { bytes: await blob.arrayBuffer(), mimetype: blob.type };
    },

    async cancelar() {
      cancelado = true;
    },

    // ---------- agente (0.19.0) ----------
    // Nada aqui abre a conversa na tela: abrir marcaria como lida e mandaria o
    // aviso de leitura ao contato. Nao mexe no `cancelado` das exportacoes.
    // filtro: 'nao_lidas' (padrao), 'ativas' (nao arquivadas, com ou sem nao lidas) ou 'arquivadas'.
    // Le so a lista em memoria do WhatsApp; nao abre conversa nem marca como lida.
    async agenteListar({ filtro } = {}) {
      const todas = filtro === 'ativas' || filtro === 'arquivadas';
      let chats = await WPP.chat.list(todas ? { ignoreGroupMetadata: true } : { onlyWithUnreadMessage: true });
      if (todas) {
        chats = chats.filter((c) => !(c.isNewsletter || c.id?.isNewsletter?.() || c.id?.isBroadcast?.() || c.isBroadcast
          || c.isPSA || c.id?.isPSA?.()));
        chats = chats.filter((c) => (filtro === 'arquivadas' ? !!c.archive : !c.archive));
        chats.sort((a, b) => (Number(b.t) || 0) - (Number(a.t) || 0));
      }
      return chats.map((c) => {
        const info = infoChat(c);
        return {
          id: info.id, nome: info.nome, grupo: info.grupo, naoLidas: Math.max(0, Number(c.unreadCount) || 0),
          t: Number(c.t) || 0, arquivada: !!c.archive, telefone: info.telefone,
        };
      });
    },

    // Todos os grupos, para o `wa grupos` (o usuario escolhe quais liberar em [grupos] ids).
    async agenteGrupos() {
      const chats = await WPP.chat.list({ onlyGroups: true, ignoreGroupMetadata: true });
      return chats.map((c) => {
        const info = infoChat(c);
        return { id: info.id, nome: info.nome, grupo: true, t: Number(c.t) || 0 };
      });
    },

    // `grupos`: ids liberados pelo usuario. Grupo fora dela nem entra na busca (antes da prioridade
    // por nome exato e do limite de candidatos); sem `grupos`, nenhum grupo.
    async agenteLer({ alvo, n, grupos }) {
      const meus = meusUsuarios();
      const liberados = new Set(Array.isArray(grupos) ? grupos : []);
      const lista = (await WPP.chat.list()).map((c) => {
        const info = infoChat(c);
        return {
          id: info.id, nome: info.nome, grupo: info.grupo, eu: !info.grupo && ehEu(info.id, meus), telefone: info.telefone,
        };
      }).filter((c) => !c.grupo || liberados.has(c.id));
      const r = wacervoNucleo.resolverConversa(lista, alvo);
      if (r.ambiguo) return { ambiguo: r.ambiguo };
      if (r.erro) throw erro('nao_achou', r.erro);
      const chat = WPP.chat.get(r.conversa.id);
      if (!chat) throw erro('nao_achou', 'o WhatsApp nao tem essa conversa');
      const quantas = Math.max(1, Math.min(Number(n) || 30, 100));
      const brutas = (await WPP.chat.getMessages(r.conversa.id, { count: quantas })) || [];
      return { chat: { ...infoChat(chat), eu: r.conversa.eu }, mensagens: limpar(brutas).map(serializar) };
    },

    // So devolve erro (rejeita) antes de chamar o envio; dali em diante devolve o
    // resultado, para o agente saber se a mensagem pode ter saido.
    async agenteEnviar({ chatId, texto, ultimaT, vistos, executarAte }) {
      if (typeof texto !== 'string' || !texto.trim()) throw erro('recusado', 'texto vazio');
      if (!WPP.chat.get(chatId)) throw erro('recusado', 'o WhatsApp nao tem essa conversa');
      const resumo = (m) => ({ id: serial(m.id), t: m.t, eu: !!m.id?.fromMe, texto: m.body });
      // Ate 100 para alcancar a leitura mesmo com o usuario mandando muitas pelo celular.
      const BUSCA = 100;
      const brutas = (await WPP.chat.getMessages(chatId, { count: BUSCA })) || [];
      const ultimas = limpar(brutas).map(resumo);
      const completa = brutas.length < BUSCA;
      if (wacervoNucleo.novaDepoisDaLeitura(ultimas, { ultimaT, vistos }, { completa })) return { resultado: 'nova' };
      // O prazo e conferido aqui, logo antes do envio, depois de todas as esperas.
      if (!(Number(executarAte) > Date.now())) throw erro('expirado', 'o pedido passou do prazo antes do envio; nao enviou');
      const antes = ultimas.map((m) => m.id);
      let r;
      try {
        r = await WPP.chat.sendTextMessage(chatId, texto, { createChat: false });
      } catch (e) {
        return { resultado: 'nao_confirmado', erro: String(e?.message || e) };
      }
      const idEnviado = serial(r?.id);
      for (let i = 0; i < 4; i++) {
        await pausa(1500);
        let fim = [];
        try {
          fim = limpar((await WPP.chat.getMessages(chatId, { count: 5 })) || []).map(resumo);
        } catch {}
        const achada = wacervoNucleo.confirmarEnvio(fim, { idEnviado, texto, antes });
        if (achada) {
          let ack = r?.ack;
          try {
            ack = WPP.whatsapp.MsgStore.get(achada.id)?.ack ?? ack;
          } catch {}
          return { resultado: 'enviado', ack: ack ?? null };
        }
      }
      return { resultado: 'nao_confirmado' };
    },

    // Arquivar ou desarquivar (0.28.0). Como o envio: so rejeita antes de chamar o WhatsApp;
    // dali em diante devolve o resultado. `chatId` e sempre o da leitura (o MCP nunca manda nome).
    async agenteArquivar({ chatId, nomeEsperado, arquivar, ultimaT, vistos, executarAte }) {
      const chat = WPP.chat.get(chatId);
      if (!chat) throw erro('recusado', 'o WhatsApp nao tem essa conversa');
      const quer = arquivar === true;
      let msgs = [];
      let completa = true;
      if (quer) {
        const BUSCA = 100;
        const brutas = (await WPP.chat.getMessages(chatId, { count: BUSCA })) || [];
        msgs = limpar(brutas).map((m) => ({ id: serial(m.id), t: m.t, eu: !!m.id?.fromMe }));
        completa = brutas.length < BUSCA;
      }
      const atual = WPP.chat.get(chatId) || chat;
      const d = wacervoNucleo.antesDeArquivar({
        nomeAtual: infoChat(atual).nome, nomeEsperado, arquivada: !!atual.archive, arquivar: quer,
        msgs, leitura: { ultimaT, vistos: vistos || [] }, completa,
      });
      if (d.recusa) throw erro('recusado', d.recusa);
      if (d.resultado) return { resultado: d.resultado };
      // O prazo e conferido aqui, logo antes de agir, depois das esperas.
      if (!(Number(executarAte) > Date.now())) throw erro('expirado', 'o pedido passou do prazo; nada mudou');
      let falha = '';
      try {
        await WPP.chat.archive(chatId, quer);
      } catch (e) {
        falha = String(e?.message || e);
      }
      // Erro do WhatsApp nunca vira "feito", mesmo com a marca local trocada (o wa-js troca a
      // marca antes de o servidor confirmar). Sem erro, a marca local e a melhor prova que ha.
      if (falha) return { resultado: 'nao_confirmado', erro: falha.slice(0, 200) };
      for (let i = 0; i < 4; i++) {
        await pausa(1000);
        if (!!WPP.chat.get(chatId)?.archive === quer) return { resultado: 'feito' };
      }
      return { resultado: 'nao_confirmado' };
    },

    // Rodada automatica (0.16.0): o WhatsApp Web terminou de carregar e tem login?
    // Na tela do QR code, isAuthenticated fica falso.
    async situacao() {
      return { pronto: !!WPP.conn.isMainReady?.(), logado: !!WPP.conn.isAuthenticated?.() };
    },
  };

  window.addEventListener('message', async (ev) => {
    const p = ev.data;
    if (ev.source !== window || p?.wacervo !== 'pedido' || !acoes[p.acao]) return;
    const responder = (corpo, transferir) =>
      window.postMessage({ wacervo: 'resposta', id: p.id, ...corpo }, location.origin, transferir || []);
    try {
      // Logo depois de recarregar a aba o wa-js leva uns segundos para ficar
      // pronto: espera um pouco antes de recusar (o menor prazo da ponte e 5 s).
      for (let t = 0; !window.WPP?.isReady && t < 3000; t += 200) await new Promise((r) => setTimeout(r, 200));
      if (!window.WPP?.isReady) throw new Error('WhatsApp Web ainda carregando: espere uns segundos e tente de novo');
      const progresso = (texto) => window.postMessage({ wacervo: 'progresso', id: p.id, texto }, location.origin);
      const dados = await acoes[p.acao](p, progresso);
      responder({ ok: true, dados }, dados?.bytes ? [dados.bytes] : []);
    } catch (e) {
      responder({ ok: false, erro: String(e?.message || e), tipo: e?.tipo || (e?.message === 'cancelado' ? 'cancelado' : 'pagina') });
    }
  });
})();
