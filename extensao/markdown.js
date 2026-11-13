// Monta o .md da exportacao a partir das mensagens serializadas pela pagina.js
// e dos textos que o pepita-wa devolveu (transcricao de audio, texto de imagem).
// Funcao pura: roda igual no content script e no `node --test`.
(() => {
  'use strict';

  const dois = (n) => String(n).padStart(2, '0');
  const dia = (d) => `${d.getFullYear()}-${dois(d.getMonth() + 1)}-${dois(d.getDate())}`;
  const hora = (d) => `${dois(d.getHours())}:${dois(d.getMinutes())}`;
  const duracao = (s) => `${Math.floor(s / 60)}:${dois(Math.round(s % 60))}`;
  const umaLinha = (s, max = 200) => {
    const t = String(s || '').replace(/\s+/g, ' ').trim();
    return t.length > max ? t.slice(0, max - 1) + '…' : t;
  };
  const citar = (s) => String(s).split(/\r?\n/).map((l) => `> ${l}`).join('\n');

  function slug(nome) {
    const s = String(nome || '')
      .normalize('NFD')
      .replace(/[̀-ͯ]/g, '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '');
    return s || 'conversa';
  }

  function nomeDoArquivo(chat, inicio, fim) {
    const a = dia(inicio);
    const b = dia(fim);
    return `${slug(chat.nome)}_${a}${a === b ? '' : `_a_${b}`}.md`;
  }

  // anexos: Map id -> {arquivo: 'anexos/...'} | {erro}; ausente = nao foi pedido.
  // O texto do link e o caminho, para quem le o .md cru achar o arquivo.
  function anexo(m, anexos) {
    const a = anexos?.get(m.id);
    if (!a) return '';
    if (a.erro) return ` · *[anexo nao baixado: ${a.erro}]*`;
    const destino = a.arquivo
      .split('/')
      .map((p) => encodeURIComponent(p).replace(/\(/g, '%28').replace(/\)/g, '%29'))
      .join('/');
    return ` · [${a.arquivo.replace(/([[\]\\])/g, '\\$1')}](${destino})`;
  }

  // textos: Map id -> {texto} | {erro}; ausente = nao foi pedido.
  function midiaComTexto(m, rotulo, textos) {
    const r = textos.get(m.id);
    let linha = rotulo;
    if (m.tipo === 'ptt' || m.tipo === 'audio') {
      if (r?.erro) linha += ` — *[falha ao transcrever: ${r.erro}]*`;
      else if (r) linha += ` — ${r.texto || '*[sem fala]*'}`;
      if (m.texto) linha += `\n*legenda:* ${m.texto}`;
      return linha;
    }
    if (m.texto) linha += ` — *legenda:* ${m.texto}`;
    if (r?.erro) linha += `\n*[texto da imagem nao lido: ${r.erro}]*`;
    else if (r?.texto) linha += `\n*texto na imagem:*\n${citar(r.texto)}`;
    else if (r?.curto) linha += `\n*texto curto na imagem (pode ser ruido):* ${umaLinha(r.curto)}`;
    return linha;
  }

  const FRASES_GRUPO = {
    add: (a, alvos) => `${a} adicionou ${alvos}`,
    invite: (a, alvos) => `${alvos || a} entrou pelo link`,
    linked_group_join: (a, alvos) => `${alvos || a} entrou`,
    remove: (a, alvos) => `${a} removeu ${alvos}`,
    leave: (a, alvos) => `${alvos || a} saiu`,
    create: (a) => `${a} criou o grupo`,
    subject: (a, _alvos, corpo) => `${a} mudou o assunto do grupo${corpo ? ` para "${corpo}"` : ''}`,
    description: (a) => `${a} mudou a descricao do grupo`,
    picture: (a) => `${a} mudou a foto do grupo`,
    promote: (a, alvos) => `${a} tornou ${alvos} admin`,
    demote: (a, alvos) => `${a} tirou ${alvos} de admin`,
    announce: (a) => `${a} mudou quem pode mandar mensagens`,
    restrict: (a) => `${a} mudou quem pode editar o grupo`,
  };

  function sistema(m) {
    const alvos = (m.alvos || []).join(', ');
    if (m.tipo === 'call_log') {
      const video = m.subtipo.includes('video') ? 'de video' : 'de voz';
      return m.subtipo.startsWith('miss') ? `chamada ${video} perdida` : `chamada ${video}`;
    }
    const frase = FRASES_GRUPO[m.subtipo];
    if (frase) return frase(m.autor, alvos, umaLinha(m.corpo, 120));
    return `evento do WhatsApp (${m.tipo}${m.subtipo ? `/${m.subtipo}` : ''})`;
  }

  function corpo(m, textos, anexos) {
    switch (m.tipo) {
      case 'chat':
        return m.texto;
      case 'ptt':
      case 'audio':
        return midiaComTexto(
          m,
          `🎙️ *audio ${duracao(m.duracao || 0)}${m.visualizacaoUnica ? ' (visualizacao unica)' : ''}*`,
          textos,
        );
      case 'image':
        return midiaComTexto(
          m,
          (m.visualizacaoUnica ? '🖼️ *imagem (visualizacao unica)*' : '🖼️ *imagem*') + anexo(m, anexos),
          textos,
        );
      case 'video': {
        const r = (m.gif ? '🎞️ *gif*' : `🎬 *video ${duracao(m.duracao || 0)}*`) + anexo(m, anexos);
        return m.texto ? `${r} — *legenda:* ${m.texto}` : r;
      }
      case 'document': {
        const r = `📄 *documento* \`${m.arquivo || 'sem nome'}\`` + anexo(m, anexos);
        return m.texto ? `${r} — ${m.texto}` : r;
      }
      case 'sticker':
        return '*(figurinha)*';
      case 'location': {
        const onde = m.local ? `${umaLinha(m.local)} ` : '';
        return `📍 ${onde}(${m.lat}, ${m.lng})`;
      }
      case 'vcard':
      case 'multi_vcard':
        return (m.contatos || [])
          .map((c) => `👤 ${c.nome || 'contato'}${c.tel.length ? ` — ${c.tel.join(', ')}` : ''}`)
          .join('\n');
      case 'poll_creation':
        return [`📊 *enquete:* ${m.enquete?.nome || ''}`, ...(m.enquete?.opcoes || []).map((o) => `- ${o}`)].join('\n');
      case 'revoked':
        return '*(mensagem apagada)*';
      case 'ciphertext':
        return '*(mensagem ainda nao chegou neste aparelho)*';
      default:
        return m.texto || `*(${m.tipo})*`;
    }
  }

  // info (opcional): {modo, cobertura, fuso, semMidia, audiosDesligados, audiosDaTela,
  // imagensDesligadas, anexos}. Diz como a conversa foi coletada e avisa quando o arquivo
  // nao cobre tudo o que foi pedido, ou quando uma opcao do painel estava desligada.
  function cabecalhoDaColeta(info) {
    const linhas = [];
    const pedido = info.cobertura?.pedido;
    const fuso = info.fuso ? ` · fuso ${info.fuso}` : '';
    if (info.modo === 'incremental') {
      // Exportar todas: so o que faltava; `anterior` e o trecho que outros arquivos ja cobrem.
      const ant = info.anterior;
      linhas.push(
        ant
          ? `> coleta: so o que faltava; as exportacoes anteriores cobrem ${dia(new Date(ant.primeira * 1000))} a ${dia(new Date(ant.ultima * 1000))}${fuso}`
          : `> coleta: historico disponivel no WhatsApp Web${fuso}`,
      );
    } else if (info.modo === 'periodo' && pedido) {
      const a = dia(new Date(pedido.inicio * 1000));
      const b = dia(new Date(pedido.fim * 1000));
      linhas.push(`> periodo pedido: ${a === b ? a : `${a} a ${b}`}${fuso}`);
    } else if (info.modo) {
      linhas.push(`> coleta: mensagens ja carregadas na tela${fuso}`);
    } else if (fuso) {
      linhas.push(`> fuso ${info.fuso}`);
    }
    const c = info.cobertura;
    const antiga = c?.maisAntigaEncontrada ? dia(new Date(c.maisAntigaEncontrada * 1000)) : '';
    if (c?.motivoDaParada === 'limite_de_paginas') {
      linhas.push(
        `> ⚠️ exportacao parcial: a busca parou no limite de ${c.paginas} paginas; ` +
          `a mensagem mais antiga lida e de ${antiga}, depois do inicio pedido.`,
      );
    } else if (info.modo !== 'incremental' && c?.motivoDaParada === 'fim_do_historico' && pedido && c.maisAntigaEncontrada > pedido.inicio) {
      linhas.push(`> o historico disponivel no WhatsApp Web comeca em ${antiga}, depois do inicio pedido.`);
    }
    if (info.semMidia) {
      linhas.push('> ⚠️ exportado sem transcricao dos audios nem texto das imagens (pepita-wa fora do ar).');
    }
    // Opcoes desmarcadas no painel: sem isto, um audio sem texto nao diz por que.
    const audios = info.audiosDesligados || 0;
    const daTela = info.audiosDaTela || 0;
    if (audios && daTela >= audios) {
      linhas.push(
        `> transcricao dos audios desligada nesta exportacao; o texto dos ${audios} audio(s) veio da transcricao feita na tela.`,
      );
    } else if (audios) {
      linhas.push(
        `> ⚠️ transcricao dos audios desligada nesta exportacao: ${audios - daTela} de ${audios} audio(s) sem texto` +
          (daTela ? ` (${daTela} com o texto da transcricao feita na tela).` : '.'),
      );
    }
    if (info.imagensDesligadas) {
      linhas.push(
        `> leitura do texto das imagens desligada nesta exportacao: ${info.imagensDesligadas} imagem(ns) sem o texto.`,
      );
    }
    if (info.anexos?.size) {
      const falhas = [...info.anexos.values()].filter((a) => a.erro).length;
      const ok = info.anexos.size - falhas;
      linhas.push(
        `> anexos (fotos, videos e documentos) na pasta anexos/: ${ok} arquivo(s)` +
          (falhas ? `; ⚠️ ${falhas} nao baixado(s), marcados na mensagem.` : '.'),
      );
    }
    return linhas;
  }

  function gerar(chat, mensagens, textos = new Map(), agora = new Date(), info = null) {
    const linhas = [];
    const primeira = mensagens.length ? new Date(mensagens[0].t * 1000) : agora;
    const ultima = mensagens.length ? new Date(mensagens.at(-1).t * 1000) : agora;
    const intervalo = dia(primeira) === dia(ultima) ? dia(primeira) : `${dia(primeira)} a ${dia(ultima)}`;

    linhas.push(`# ${chat.nome}`, '');
    linhas.push(
      `> ${chat.grupo ? 'grupo' : 'contato'}${chat.origemNome === 'perfil' ? ' (nome do perfil)' : ''} · \`${chat.id}\`${chat.telefone && !chat.id.startsWith(chat.telefone + '@') ? ` · +${chat.telefone}` : ''} · ${intervalo} · ${mensagens.length} mensagens · ` +
        `exportado em ${dia(agora)} ${hora(agora)}`,
    );
    if (info) linhas.push(...cabecalhoDaColeta(info));

    let diaAtual = '';
    for (const m of mensagens) {
      const d = new Date(m.t * 1000);
      if (dia(d) !== diaAtual) {
        diaAtual = dia(d);
        linhas.push('', `## ${diaAtual}`);
      }
      linhas.push('');
      if (['gp2', 'notification', 'call_log'].includes(m.tipo)) {
        linhas.push(`*${hora(d)} · ${sistema(m)}*`);
        continue;
      }
      const marcas = [m.editada && '*(editada)*', m.encaminhada && '*(encaminhada)*'].filter(Boolean);
      linhas.push(`**${hora(d)} · ${m.autor}**${marcas.length ? ' ' + marcas.join(' ') : ''}`);
      if (m.citado) {
        const quem = m.citado.autor ? `${m.citado.autor}: ` : '';
        linhas.push(`> ↩ *${quem}${umaLinha(m.citado.texto) || '...'}*`);
      }
      linhas.push(corpo(m, textos, info?.anexos));
    }
    linhas.push('');
    return linhas.join('\n');
  }

  const api = { gerar, nomeDoArquivo, slug, dia };
  if (typeof module !== 'undefined') module.exports = api;
  else globalThis.wacervoMarkdown = api;
})();
