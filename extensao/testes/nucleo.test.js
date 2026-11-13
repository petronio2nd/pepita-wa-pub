// fuso fixo para as horas dos nomes de anexo baterem
process.env.TZ = 'America/Sao_Paulo';

const test = require('node:test');
const assert = require('node:assert/strict');
const {
  criarCanal, varrerPeriodo, criarOperacao, exportar, criarFila, descreverResumo,
  compararVersoes, avisoDeVersao, nomesDosAnexos,
  selecionarConversas, nomeUnico, exportarTodas, descreverResumoTodas, erroDe,
  ancoraNaBarra,
} = require('../nucleo.js');

const esperar = (ms) => new Promise((r) => setTimeout(r, ms));

// O Chrome injeta um arquivo so uma vez por aba, mesmo listado nos dois mundos;
// por isso o mundo MAIN carrega uma copia com outro nome.
test('nucleo-pagina.js e copia exata do nucleo.js', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const ler = (f) => fs.readFileSync(path.join(__dirname, '..', f), 'utf8').replace(/\r\n/g, '\n');
  assert.equal(ler('nucleo-pagina.js'), ler('nucleo.js'),
    'copie de novo: cp extensao/nucleo.js extensao/nucleo-pagina.js');
});

test('versao: manifest, pyproject e __init__ andam juntos', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const raiz = path.join(__dirname, '..', '..');
  const ler = (f) => fs.readFileSync(path.join(raiz, f), 'utf8');
  const manifest = JSON.parse(ler('extensao/manifest.json')).version;
  const pyproject = ler('pyproject.toml').match(/^version\s*=\s*"([^"]+)"/m)[1];
  const init = ler('src/wacervo/__init__.py').match(/__version__\s*=\s*"([^"]+)"/)[1];
  assert.deepEqual({ pyproject, init }, { pyproject: manifest, init: manifest },
    `suba as tres versoes juntas (manifest esta em ${manifest})`);
});

test('versao: compara numero a numero e diz qual lado recarregar', () => {
  assert.equal(compararVersoes('0.3.10', '0.3.9'), 1);
  assert.equal(compararVersoes('0.3', '0.3.0'), 0);
  assert.equal(compararVersoes('0.2.9', '0.3.0'), -1);
  assert.equal(avisoDeVersao('0.3.1', '0.3.1'), null);
  assert.equal(avisoDeVersao('0.3.1', undefined), null);
  assert.match(avisoDeVersao('0.3.1', '0.3.0'), /reinicie o `uv run wa servir`/);
  assert.match(avisoDeVersao('0.3.0', '0.3.1'), /recarregue a extensao/);
});

test('canal: prazo rejeita e limpa o pendente; progresso renova o prazo', async () => {
  const postados = [];
  const canal = criarCanal((m) => postados.push(m), { prazoPadrao: 30 });

  await assert.rejects(canal.pedir('chat'), (e) => e.tipo === 'prazo');
  assert.equal(canal.pendentes, 0);

  const lento = canal.pedir('coletar', {}, { progresso: () => {} });
  const { id } = postados.at(-1);
  for (let i = 0; i < 3; i++) {
    await esperar(20);
    canal.receber({ wacervo: 'progresso', id, texto: 'lendo' });
  }
  canal.receber({ wacervo: 'resposta', id, ok: true, dados: 'pronto' });
  assert.equal(await lento, 'pronto');
  assert.equal(canal.pendentes, 0);

  // Resposta atrasada de pedido que ja expirou e ignorada.
  assert.equal(canal.receber({ wacervo: 'resposta', id: postados[0].id, ok: true }), false);
});

test('canal: erro da pagina chega com o tipo', async () => {
  const postados = [];
  const canal = criarCanal((m) => postados.push(m));
  const p = canal.pedir('coletar');
  canal.receber({ wacervo: 'resposta', id: postados[0].id, ok: false, erro: 'mudou', tipo: 'outra_conversa' });
  await assert.rejects(p, (e) => e.tipo === 'outra_conversa' && e.message === 'mudou');
});

function historico(tempos, tamanho = 3) {
  const msgs = tempos.map((t, i) => ({ id: `m${i}`, t })).sort((a, b) => a.t - b.t);
  return async (antesDe) => {
    const ate = antesDe ? msgs.findIndex((m) => m.id === antesDe.id) : msgs.length;
    return msgs.slice(Math.max(0, ate - tamanho), ate);
  };
}

test('periodo: para ao alcancar o inicio e filtra o intervalo', async () => {
  const r = await varrerPeriodo(historico([10, 20, 30, 40, 50, 60, 70]), { inicio: 35, fim: 65 });
  assert.deepEqual(r.mensagens.map((m) => m.t), [40, 50, 60]);
  assert.equal(r.cobertura.motivoDaParada, 'alcancou_inicio');
  assert.deepEqual(r.cobertura.pedido, { inicio: 35, fim: 65 });
});

test('periodo: historico acaba antes do inicio pedido', async () => {
  const r = await varrerPeriodo(historico([10, 20, 30, 40]), { inicio: 0, fim: 100 });
  assert.equal(r.cobertura.motivoDaParada, 'fim_do_historico');
  assert.equal(r.cobertura.maisAntigaEncontrada, 10);
  assert.equal(r.mensagens.length, 4);
});

test('periodo: limite de paginas vira motivo explicito, nao corte silencioso', async () => {
  const tempos = Array.from({ length: 30 }, (_, i) => i + 1);
  const r = await varrerPeriodo(historico(tempos), { inicio: 0, fim: 100, limitePaginas: 2 });
  assert.equal(r.cobertura.motivoDaParada, 'limite_de_paginas');
  assert.equal(r.cobertura.paginas, 2);
  assert.equal(r.cobertura.maisAntigaEncontrada, 22);
});

test('operacao: cancelar solta a etapa na hora', async () => {
  const op = criarOperacao();
  const lenta = op.etapa(new Promise(() => {}));
  op.cancelar();
  await assert.rejects(lenta, (e) => e.tipo === 'cancelado');
  assert.throws(() => op.verificar(), (e) => e.tipo === 'cancelado');
});

const chat = { id: '556290000000@c.us', nome: 'Renata Silva' };
const mensagens = [
  { id: 'a1', tipo: 'ptt', t: 1 },
  { id: 'a2', tipo: 'ptt', t: 2 },
  { id: 'i1', tipo: 'image', t: 3 },
  { id: 'v1', tipo: 'ptt', t: 4, visualizacaoUnica: true },
  { id: 'c1', tipo: 'chat', t: 5 },
];

function deps(extra = {}) {
  const salvos = [];
  const processados = [];
  return {
    salvos,
    processados,
    coletar: async () => ({ chat, mensagens, cobertura: { motivoDaParada: 'alcancou_inicio' } }),
    processar: async (m) => {
      processados.push(m.id);
      return m.id === 'a2' ? { erro: 'falhou' } : m.tipo === 'image' ? { texto: '', curto: 'R$ 40' } : { texto: 'Vi sim' };
    },
    gerar: (c, ms, textos, info) => ({ c, n: ms.length, textos, info }),
    salvar: async (md) => {
      salvos.push(md);
      return 'renata.md';
    },
    ...extra,
  };
}

test('exportar: resumo conta cada resultado e nao tenta visualizacao unica', async () => {
  const d = deps();
  const r = await exportar(d, { args: { modo: 'carregadas' }, audio: true, imagem: true });
  assert.deepEqual(d.processados, ['a1', 'a2', 'i1']);
  assert.equal(r.salvoEm, 'renata.md');
  assert.equal(r.resumo.audios, 1);
  assert.equal(r.resumo.falhas, 1);
  assert.equal(r.resumo.textoCurto, 1);
  assert.equal(d.salvos[0].info.modo, 'carregadas');
  assert.match(descreverResumo(r.resumo), /5 mensagens, 1 audio\(s\) transcrito\(s\), 1 com texto curto, 1 falha/);
});

test('exportar: cancelar durante a ultima midia impede o salvar', async () => {
  const op = criarOperacao();
  let soltar;
  const d = deps({
    processar: (m) => {
      if (m.id !== 'i1') return Promise.resolve({ texto: 'ok' });
      return new Promise((r) => { soltar = r; });
    },
  });
  const p = exportar(d, { args: {}, audio: true, imagem: true }, op);
  while (!soltar) await esperar(1);
  op.cancelar();
  soltar({ texto: 'tarde demais' });
  await assert.rejects(p, (e) => e.tipo === 'cancelado');
  assert.equal(d.salvos.length, 0);
});

test('exportar: tentar de novo so reprocessa as falhas', async () => {
  const d = deps();
  const primeira = await exportar(d, { args: {}, audio: true, imagem: true });
  d.processados.length = 0;
  let coletas = 0;
  const d2 = { ...d, coletar: async () => { coletas++; }, processar: async (m) => { d.processados.push(m.id); return { texto: 'agora foi' }; } };
  const segunda = await exportar(d2, { args: {}, audio: true, imagem: true, anterior: primeira });
  assert.equal(coletas, 0);
  assert.deepEqual(d.processados, ['a2']);
  assert.equal(segunda.resumo.falhas, 0);
  assert.equal(segunda.resumo.reaproveitados, 2);
});

test('exportar: servidor fora do ar avisa sem perder a coleta; sem midia salva assim mesmo', async () => {
  const d = deps({ servidorNoAr: async () => false });
  await assert.rejects(
    exportar(d, { args: {}, audio: true, imagem: true }),
    (e) => e.tipo === 'fora' && e.coleta.mensagens.length === 5,
  );
  assert.equal(d.salvos.length, 0);

  const r = await exportar(d, { args: {}, audio: true, imagem: true, semMidia: true });
  assert.equal(d.processados.length, 0);
  assert.equal(r.resumo.semMidia, 3);
  assert.equal(d.salvos[0].info.semMidia, true);
});

test('exportar: reaproveita o que a fila da tela ja transcreveu', async () => {
  const fila = criarFila({ processar: async () => ({ texto: 'da tela' }) });
  fila.pedir('a1');
  while (fila.ocupada) await esperar(1);
  const d = deps({ fila });
  const r = await exportar(d, { args: {}, audio: true, imagem: false });
  assert.deepEqual(d.processados, ['a2']);
  assert.equal(r.textos.get('a1').texto, 'da tela');
  // O resultado da exportacao volta para a fila (a caixa aparece depois).
  assert.equal(fila.estado('a2').erro, 'falhou');
});

test('exportar: transcricao desligada usa o texto da tela e nao chama o servidor', async () => {
  const fila = criarFila({ processar: async () => ({ texto: 'da tela' }) });
  fila.pedir('a1');
  while (fila.ocupada) await esperar(1);
  let consultas = 0;
  const d = deps({ fila, servidorNoAr: async () => { consultas++; return true; } });
  const r = await exportar(d, { args: {}, audio: false, imagem: false });
  assert.deepEqual(d.processados, []);
  assert.equal(consultas, 0);
  assert.equal(r.textos.get('a1').texto, 'da tela');
  assert.equal(r.textos.has('a2'), false);
  // a1 e a2 contam; o de visualizacao unica nao.
  const { audiosDesligados, audiosDaTela, imagensDesligadas } = d.salvos[0].info;
  assert.deepEqual({ audiosDesligados, audiosDaTela, imagensDesligadas }, {
    audiosDesligados: 2, audiosDaTela: 1, imagensDesligadas: 1,
  });
  assert.match(descreverResumo(r.resumo), /1 audio\(s\) com o texto da tela/);
});

test('exportar: com as opcoes ligadas nada conta como desligado', async () => {
  const d = deps();
  await exportar(d, { args: {}, audio: true, imagem: true });
  const { audiosDesligados, imagensDesligadas } = d.salvos[0].info;
  assert.equal(audiosDesligados + imagensDesligadas, 0);
});

test('fila: resultado fica pelo id, pausa segura a fila, manual passa na frente', async () => {
  const feitos = [];
  const fila = criarFila({ processar: async (id) => { feitos.push(id); return { texto: `t-${id}` }; } });
  fila.pausar();
  fila.pedir('x1');
  fila.pedir('x2');
  await esperar(5);
  assert.deepEqual(feitos, []);
  assert.ok(fila.estado('x1').fila);

  fila.pedir('x2', { manual: true });
  while (fila.ocupada) await esperar(1);
  assert.deepEqual(feitos, ['x2']);

  fila.retomar();
  while (fila.ocupada) await esperar(1);
  assert.deepEqual(feitos, ['x2', 'x1']);
  assert.equal(fila.resultado('x1').texto, 't-x1');
  assert.equal(fila.pedir('x1'), false); // pronto nao volta para a fila
});

test('fila: erro espera antes de tentar de novo e desiste depois do maximo', async () => {
  let relogio = 0;
  let chamadas = 0;
  const fila = criarFila({
    processar: async () => { chamadas++; throw new Error('servidor ocupado'); },
    maxTentativas: 2, esperaErroMs: 100, agora: () => relogio,
  });
  fila.pedir('e1');
  while (fila.ocupada) await esperar(1);
  assert.equal(fila.estado('e1').erro, 'servidor ocupado');
  assert.equal(fila.pedir('e1'), false); // cedo demais
  relogio = 200;
  assert.equal(fila.pedir('e1'), true);
  while (fila.ocupada) await esperar(1);
  relogio = 400;
  assert.equal(fila.pedir('e1'), false); // chegou no maximo
  assert.equal(fila.pedir('e1', { manual: true }), true); // o clique sempre vale
  while (fila.ocupada) await esperar(1);
  assert.equal(chamadas, 3);
});

test('fila: aguardar devolve o resultado do que esta em andamento', async () => {
  let soltar;
  const fila = criarFila({ processar: () => new Promise((r) => { soltar = r; }) });
  fila.pedir('a1');
  while (!soltar) await esperar(1);
  const espera = fila.aguardar('a1');
  assert.ok(espera);
  soltar({ texto: 'junto' });
  assert.equal((await espera).texto, 'junto');
  assert.equal(fila.aguardar('a1'), null);
});

test('anexos: nome pela data e hora, documento mantem o nome, repetido ganha numero', () => {
  const t = Date.UTC(2026, 8, 17, 17, 25, 9) / 1000; // 14:25:09 em Sao Paulo
  const nomes = nomesDosAnexos([
    { id: 'i1', tipo: 'image', t, mimetype: 'image/jpeg' },
    { id: 'i2', tipo: 'image', t, mimetype: 'image/png' },
    { id: 'i3', tipo: 'image', t, mimetype: 'image/jpeg' },
    { id: 'v1', tipo: 'video', t, mimetype: 'video/mp4; codecs=avc1' },
    { id: 'g1', tipo: 'video', t, gif: true, mimetype: 'video/mp4' },
    { id: 'd1', tipo: 'document', t, arquivo: 'Contrato: versao "final"?.PDF', mimetype: 'application/pdf' },
    { id: 'd2', tipo: 'document', t, arquivo: '', mimetype: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' },
    { id: 'a1', tipo: 'ptt', t },
    { id: 'u1', tipo: 'image', t, visualizacaoUnica: true },
    { id: 's1', tipo: 'sticker', t },
  ]);
  assert.deepEqual(Object.fromEntries(nomes), {
    i1: 'anexos/2026-09-17_142509_imagem.jpg',
    i2: 'anexos/2026-09-17_142509_imagem.png',
    i3: 'anexos/2026-09-17_142509_imagem-2.jpg',
    v1: 'anexos/2026-09-17_142509_video.mp4',
    g1: 'anexos/2026-09-17_142509_gif.mp4',
    d1: 'anexos/2026-09-17_142509_Contrato versao final.PDF',
    d2: 'anexos/2026-09-17_142509_documento.bin',
  });
});

const comAnexos = [
  { id: 'i1', tipo: 'image', t: 1, mimetype: 'image/jpeg' },
  { id: 'a1', tipo: 'ptt', t: 2 },
  { id: 'd1', tipo: 'document', t: 3, arquivo: 'contrato.pdf' },
  { id: 'v1', tipo: 'video', t: 4 },
];

test('exportar: anexos saem junto com o OCR da mesma foto e nao dependem do servidor', async () => {
  const ordem = [];
  const d = deps({
    coletar: async () => ({ chat, mensagens: comAnexos }),
    processar: async (m) => { ordem.push(`ocr:${m.id}`); return { texto: 'ok' }; },
    anexar: async (m, caminho) => {
      ordem.push(`anexo:${m.id}`);
      return m.id === 'v1' ? { erro: 'nao disponivel no WhatsApp' } : { arquivo: caminho };
    },
  });
  const r = await exportar(d, { args: {}, audio: true, imagem: true, anexos: true });
  // O audio sai na frente, em paralelo; o resto segue a ordem da conversa.
  assert.ok(ordem.includes('ocr:a1'));
  assert.deepEqual(ordem.filter((x) => x !== 'ocr:a1'), ['ocr:i1', 'anexo:i1', 'anexo:d1', 'anexo:v1']);
  assert.equal(r.resumo.anexos, 2);
  assert.equal(r.resumo.anexosFalhas, 1);
  assert.match(descreverResumo(r.resumo), /2 anexo\(s\) na pasta, .*1 anexo\(s\) nao baixado\(s\)/);
  assert.equal(d.salvos[0].info.anexos.get('d1').arquivo.startsWith('anexos/'), true);

  // Sem transcricao/OCR (servidor fora), os anexos saem assim mesmo.
  ordem.length = 0;
  await exportar({ ...d, servidorNoAr: async () => false }, { args: {}, anexos: true, semMidia: true });
  assert.deepEqual(ordem, ['anexo:i1', 'anexo:d1', 'anexo:v1']);

  // Opcao desligada: nada de anexo, e o .md nao fala deles.
  ordem.length = 0;
  await exportar(d, { args: {}, audio: false, imagem: false, anexos: false });
  assert.deepEqual(ordem, []);
  assert.equal(d.salvos.at(-1).info.anexos, null);
});

test('exportar: cancelar durante um anexo impede o salvar', async () => {
  const op = criarOperacao();
  let soltar;
  const d = deps({
    coletar: async () => ({ chat, mensagens: comAnexos }),
    anexar: (m, caminho) => (m.id === 'd1' ? new Promise((r) => { soltar = r; }) : Promise.resolve({ arquivo: caminho })),
  });
  const p = exportar(d, { args: {}, audio: false, imagem: false, anexos: true }, op);
  while (!soltar) await esperar(1);
  op.cancelar();
  soltar({ arquivo: 'tarde demais' });
  await assert.rejects(p, (e) => e.tipo === 'cancelado');
  assert.equal(d.salvos.length, 0);
});

test('exportar: audios vao em paralelo ate o limite e o texto fica com o audio certo', async () => {
  const muitos = Array.from({ length: 7 }, (_, i) => ({ id: `a${i}`, tipo: 'ptt', t: i }));
  let ativos = 0;
  let pico = 0;
  const soltar = new Map();
  const d = deps({
    coletar: async () => ({ chat, mensagens: muitos, cobertura: {} }),
    paralelo: 3,
    processar: (m) => {
      ativos++;
      pico = Math.max(pico, ativos);
      return new Promise((r) => soltar.set(m.id, () => { ativos--; r({ texto: `texto ${m.id}` }); }));
    },
  });
  let fim = false;
  const p = exportar(d, { args: {}, audio: true, imagem: true }).finally(() => { fim = true; });
  // Solta sempre o mais novo entre os ativos: a ordem de chegada nao pode trocar os textos.
  while (!fim) {
    await esperar(1);
    const ids = [...soltar.keys()].sort((a, b) => Number(b.slice(1)) - Number(a.slice(1)));
    if (ids.length) {
      const f = soltar.get(ids[0]);
      soltar.delete(ids[0]);
      f();
    }
  }
  const r = await p;
  assert.equal(pico, 3);
  for (const m of muitos) assert.equal(r.textos.get(m.id).texto, `texto ${m.id}`);
  assert.equal(r.resumo.audios, 7);
});

test('exportar: cancelar com audios na espera nao chama o servidor para eles', async () => {
  const op = criarOperacao();
  const muitos = Array.from({ length: 5 }, (_, i) => ({ id: `a${i}`, tipo: 'ptt', t: i }));
  const chamados = [];
  const d = deps({
    coletar: async () => ({ chat, mensagens: muitos, cobertura: {} }),
    paralelo: 2,
    processar: (m) => {
      chamados.push(m.id);
      return new Promise(() => {});
    },
  });
  const p = exportar(d, { args: {}, audio: true, imagem: true }, op);
  while (chamados.length < 2) await esperar(1);
  op.cancelar();
  await assert.rejects(p, (e) => e.tipo === 'cancelado');
  await esperar(5);
  assert.deepEqual(chamados, ['a0', 'a1']);
  assert.equal(d.salvos.length, 0);
});

// ---------- todas as conversas ----------
const bruto = (id, nome, t, extra = {}) => ({ id, nome, t, arquivada: false, ...extra });

test('todas: so conversa individual, arquivadas dentro, mais recente primeiro', () => {
  const lista = [
    bruto('556290000001@c.us', 'Renata Silva', 100),
    bruto('556290000002@c.us', 'Carla Mendes', 300, { arquivada: true }),
    bruto('199999999999999@lid', 'Contato lid', 200),
    bruto('556290000003@s.whatsapp.net', 'Contato antigo', 50),
    bruto('120363000000000000@g.us', 'Grupo da familia', 400, { grupo: true }),
    bruto('120363000000000001@g.us', 'Grupo sem a flag', 410),
    bruto('120363000000000002@newsletter', 'Canal', 420, { newsletter: true }),
    bruto('120363000000000003@newsletter', 'Canal sem a flag', 421),
    bruto('556290000004@broadcast', 'Lista de transmissao', 430),
    bruto('status@broadcast', 'Status', 440),
    bruto('0@c.us', 'WhatsApp', 450, { psa: true }),
    bruto('556290000009@c.us', 'Eu', 460, { eu: true }),
    bruto('556290000001@c.us', 'Renata Silva (repetida)', 10),
    bruto('120363000000000004@g.us', 'Comunidade', 470, { grupo: true }),
    { nome: 'sem id', t: 1 },
  ];
  assert.deepEqual(
    selecionarConversas(lista).map((c) => c.nome),
    ['Carla Mendes', 'Contato lid', 'Renata Silva', 'Contato antigo'],
  );
  assert.deepEqual(selecionarConversas(null), []);
});

test('todas: nome repetido ganha o id e depois um numero, sem diferenciar maiuscula', () => {
  const usados = new Set();
  assert.equal(nomeUnico(usados, 'renata-silva_2026-09-17', '556290000001@c.us'), 'renata-silva_2026-09-17');
  assert.equal(nomeUnico(usados, 'renata-silva_2026-09-17', '556290000002@c.us'), 'renata-silva_2026-09-17-556290000002');
  assert.equal(nomeUnico(usados, 'Renata-Silva_2026-09-17-556290000002', '556290000002@c.us'),
    'Renata-Silva_2026-09-17-556290000002-2');
  assert.equal(nomeUnico(usados, 'carla-mendes', ''), 'carla-mendes');
  assert.equal(nomeUnico(usados, 'carla-mendes', ''), 'carla-mendes-2');
});

test('todas: homonimos na lista levam o id no nome, em qualquer ordem e em toda execucao', () => {
  const lista = [
    bruto('556290000001@c.us', 'Renata Silva', 300),
    bruto('556290000005@c.us', 'renata silva ', 100),
    bruto('556290000002@c.us', 'Carla Mendes', 200),
  ];
  assert.deepEqual(
    selecionarConversas(lista).map((c) => [c.nome, c.homonima]),
    [['Renata Silva', true], ['Carla Mendes', false], ['renata silva ', true]],
  );
  const usados = new Set();
  assert.equal(nomeUnico(usados, 'renata-silva_2026-09-17', '556290000001@c.us', { forcarId: true }),
    'renata-silva_2026-09-17-556290000001');
  assert.equal(nomeUnico(usados, 'renata-silva_2026-09-17', '556290000005@c.us', { forcarId: true }),
    'renata-silva_2026-09-17-556290000005');
  // Noutra execucao, com a ordem invertida, cada um continua com o seu nome.
  const outra = new Set();
  assert.equal(nomeUnico(outra, 'renata-silva_2026-09-17', '556290000005@c.us', { forcarId: true }),
    'renata-silva_2026-09-17-556290000005');
  assert.equal(nomeUnico(outra, 'renata-silva_2026-09-17', '556290000001@c.us', { forcarId: true }),
    'renata-silva_2026-09-17-556290000001');
  assert.equal(nomeUnico(new Set(), 'carla-mendes', '556290000002@c.us', { forcarId: false }), 'carla-mendes');
});

function depsTodas(lista, porConversa, extra = {}) {
  const feitas = [];
  let ativas = 0;
  let pico = 0;
  return {
    feitas,
    get pico() { return pico; },
    listar: async () => lista,
    exportarUma: async (c) => {
      ativas++;
      pico = Math.max(pico, ativas);
      try {
        await esperar(1);
        feitas.push(c.nome);
        return await porConversa(c);
      } finally {
        ativas--;
      }
    },
    ...extra,
  };
}

const feita = (extra = {}) => ({ salvoEm: 'pasta/x.md', resumo: { falhas: 0, anexosFalhas: 0 }, coleta: { cobertura: null }, ...extra });

test('todas: uma por vez, na ordem, e o resumo separa exportada, parcial, falha e pulada', async () => {
  const lista = [
    bruto('556290000001@c.us', 'Renata Silva', 5),
    bruto('556290000002@c.us', 'Carla Mendes', 4),
    bruto('556290000003@c.us', 'Contato C', 3),
    bruto('556290000004@c.us', 'Contato D', 2),
    bruto('556290000005@c.us', 'Contato E', 1),
    bruto('120363000000000000@g.us', 'Grupo', 9, { grupo: true }),
  ];
  const marcas = [];
  const d = depsTodas(lista, async (c) => {
    if (c.nome === 'Carla Mendes') return feita({ coleta: { cobertura: { motivoDaParada: 'limite_de_paginas' } } });
    if (c.nome === 'Contato C') throw erroDe('pagina', 'a conversa sumiu');
    if (c.nome === 'Contato D') throw erroDe('vazio', 'nenhuma mensagem no intervalo');
    if (c.nome === 'Contato E') return feita({ resumo: { falhas: 2, anexosFalhas: 1 } });
    return feita();
  }, {
    aoComecar: (c, i, n) => marcas.push(`${i}/${n} ${c.nome}`),
  });
  const r = await exportarTodas(d);
  assert.deepEqual(d.feitas, ['Renata Silva', 'Carla Mendes', 'Contato C', 'Contato D', 'Contato E']);
  assert.equal(d.pico, 1);
  assert.deepEqual(marcas, ['1/5 Renata Silva', '2/5 Carla Mendes', '3/5 Contato C', '4/5 Contato D', '5/5 Contato E']);
  assert.deepEqual(
    { e: r.exportadas, p: r.parciais, f: r.falhas, s: r.puladas, t: r.total, n: r.naoTentadas, m: r.midiasComFalha, a: r.anexosFalhos },
    { e: 2, p: 1, f: 1, s: 1, t: 5, n: 0, m: 2, a: 1 },
  );
  assert.equal(r.exportadas + r.parciais + r.falhas + r.puladas, r.total);
  const texto = descreverResumoTodas(r);
  assert.match(texto, /2 exportada\(s\), 1 parcial\(is\).*, 1 com falha, 1 pulada\(s\).*, de 5 conversa\(s\)/);
  assert.match(texto, /falhou: Contato C \(a conversa sumiu\)/);
  assert.match(texto, /parcial: Carla Mendes/);
});

test('todas: cancelar no meio guarda o que ja saiu e conta o resto como nao exportado', async () => {
  const op = criarOperacao();
  const lista = ['A', 'B', 'C', 'D'].map((n, i) => bruto(`55629000000${i}@c.us`, n, 10 - i));
  const d = depsTodas(lista, async (c) => {
    if (c.nome === 'B') {
      op.cancelar();
      throw erroDe('cancelado', 'cancelado');
    }
    return feita();
  });
  const r = await exportarTodas(d, op);
  assert.deepEqual(d.feitas, ['A', 'B']);
  assert.equal(r.cancelado, true);
  assert.deepEqual({ e: r.exportadas, n: r.naoTentadas, t: r.total }, { e: 1, n: 3, t: 4 });
  assert.match(descreverResumoTodas(r), /cancelado: 3 nao exportada/);

  // Cancelada entre duas conversas: nem comeca a proxima.
  const op2 = criarOperacao();
  const d2 = depsTodas(lista, async () => { op2.cancelar(); return feita(); });
  const r2 = await exportarTodas(d2, op2);
  assert.deepEqual(d2.feitas, ['A']);
  assert.deepEqual({ c: r2.cancelado, e: r2.exportadas, n: r2.naoTentadas }, { c: true, e: 1, n: 3 });
});

test('todas: servidor fora do ar e pagina travada em serie param a execucao', async () => {
  const lista = ['A', 'B', 'C', 'D', 'E'].map((n, i) => bruto(`55629000000${i}@c.us`, n, 10 - i));
  const fora = depsTodas(lista, async (c) => {
    if (c.nome === 'B') throw erroDe('fora', 'pepita-wa fora do ar');
    return feita();
  });
  const r = await exportarTodas(fora);
  assert.deepEqual(fora.feitas, ['A', 'B']);
  assert.deepEqual({ p: r.parou, e: r.exportadas, f: r.falhas, n: r.naoTentadas }, { p: 'fora', e: 1, f: 1, n: 3 });
  assert.match(descreverResumoTodas(r), /parou: pepita-wa fora do ar \(3 nao exportada/);

  // Dois prazos seguidos com um sucesso no meio nao param; tres seguidos param.
  const travada = depsTodas(lista, async (c) => {
    if (c.nome === 'C') return feita();
    throw erroDe('prazo', 'a pagina nao respondeu');
  });
  const r2 = await exportarTodas(travada);
  assert.deepEqual(travada.feitas, ['A', 'B', 'C', 'D', 'E']);
  assert.equal(r2.parou, null);
  const sempre = depsTodas(lista, async () => { throw erroDe('prazo', 'a pagina nao respondeu'); });
  const r3 = await exportarTodas(sempre);
  assert.deepEqual(sempre.feitas, ['A', 'B', 'C']);
  assert.deepEqual({ p: r3.parou, f: r3.falhas, n: r3.naoTentadas }, { p: 'prazo', f: 3, n: 2 });
});

test('todas: junto com o exportar, conversa sem mensagem e pulada e as outras saem', async () => {
  const lista = [
    bruto('556290000001@c.us', 'Renata Silva', 2),
    bruto('556290000002@c.us', 'Carla Mendes', 1),
  ];
  const salvos = [];
  const d = {
    listar: async () => lista,
    exportarUma: (c) => exportar(
      {
        coletar: async () => ({
          chat: { id: c.id, nome: c.nome },
          mensagens: c.nome === 'Carla Mendes' ? [] : [{ id: 'c1', tipo: 'chat', t: 1 }],
          cobertura: { motivoDaParada: 'fim_do_historico' },
        }),
        processar: async () => ({ texto: '' }),
        gerar: () => 'md',
        salvar: async () => { salvos.push(c.nome); return `pasta/${c.nome}.md`; },
      },
      { args: { modo: 'periodo' }, audio: true, imagem: true },
    ),
  };
  const r = await exportarTodas(d);
  assert.deepEqual(salvos, ['Renata Silva']);
  assert.deepEqual({ e: r.exportadas, s: r.puladas, f: r.falhas }, { e: 1, s: 1, f: 0 });
});

// Barra da esquerda como no WhatsApp Web de 2026-10: conversas, chamadas,
// status, canais, comunidades, separador, Meta AI; embaixo, midia e perfil.
const icone = (top, left = 12) => ({ top, left, width: 40, height: 40 });

test('ancoraNaBarra: entra depois do ultimo icone do grupo de cima', () => {
  const caixas = [
    icone(40), icone(84), icone(128), icone(172), icone(216), icone(280), // Meta AI depois do separador
    icone(935), icone(980), // midia e perfil
    { top: 40, left: 100, width: 300, height: 40 }, // botao da lista de conversas: fora
    { top: 10, left: 600, width: 40, height: 40 }, // cabecalho da conversa: fora
  ];
  assert.deepEqual(ancoraNaBarra(caixas), { ancora: 5, anterior: 4 });
});

test('ancoraNaBarra: botao dentro de botao conta uma vez, e a ordem da lista nao importa', () => {
  const caixas = [icone(980), icone(280), icone(40), icone(40, 14), icone(84), icone(935)];
  assert.deepEqual(ancoraNaBarra(caixas), { ancora: 1, anterior: 4 });
});

test('ancoraNaBarra: sem grupo de baixo entra no fim; sem barra, nada', () => {
  assert.deepEqual(ancoraNaBarra([icone(40), icone(84), icone(150)]), { ancora: 2, anterior: 1 });
  assert.deepEqual(ancoraNaBarra([icone(40)]), { ancora: 0, anterior: -1 });
  assert.equal(ancoraNaBarra([{ top: 0, left: 300, width: 40, height: 40 }, { top: 0, left: 0, width: 0, height: 0 }]), null);
});

test('incremental: o plano le desde o comeco ate o historico estar completo', () => {
  const {
    planoDaConversa, PASTA_TODAS,
  } = require('../nucleo.js');
  assert.equal(PASTA_TODAS, '70-WHATSAPP');
  assert.deepEqual(planoDaConversa(undefined, 5000), { modo: 'periodo', inicio: 0, fim: 5000 });
  assert.deepEqual(planoDaConversa({ primeira: 100, ultima: 200, completaAntes: false }, 5000),
    { modo: 'periodo', inicio: 0, fim: 5000 });
  assert.deepEqual(planoDaConversa({ primeira: 100, ultima: 200, completaAntes: true }, 5000),
    { modo: 'periodo', inicio: 201, fim: 5000 });
});

test('incremental: o recorte tira o trecho ja exportado', () => {
  const { novasDaConversa } = require('../nucleo.js');
  const msgs = [50, 100, 150, 200, 250].map((t) => ({ id: `m${t}`, t }));
  assert.deepEqual(novasDaConversa(null, msgs).map((m) => m.t), [50, 100, 150, 200, 250]);
  assert.deepEqual(novasDaConversa({ primeira: 100, ultima: 200 }, msgs).map((m) => m.t), [50, 250]);
});

test('incremental: a cobertura nova junta o lido ao marcado sem abrir buraco', () => {
  const { coberturaNova } = require('../nucleo.js');
  const msgs = (...ts) => ts.map((t) => ({ id: `m${t}`, t }));
  const cob = (motivoDaParada, maisAntigaEncontrada, inicio = 0, fim = 900) =>
    ({ motivoDaParada, maisAntigaEncontrada, pedido: { inicio, fim } });

  // Primeira vez, historico inteiro lido.
  assert.deepEqual(coberturaNova(null, { mensagens: msgs(10, 50, 80), cobertura: cob('fim_do_historico', 10) }),
    { primeira: 10, ultima: 80, completaAntes: true });
  // Primeira vez, parou no limite: o comeco fica para a proxima rodada.
  assert.deepEqual(coberturaNova(null, { mensagens: msgs(40, 80), cobertura: cob('limite_de_paginas', 40) }),
    { primeira: 40, ultima: 80, completaAntes: false });
  // Marca da migracao (sem o comeco): le tudo, junta e fica completa.
  const migrada = { primeira: 100, ultima: 200, completaAntes: false };
  assert.deepEqual(coberturaNova(migrada, { mensagens: msgs(10, 100, 200, 300), cobertura: cob('fim_do_historico', 10) }),
    { primeira: 10, ultima: 300, completaAntes: true });
  // Completa: so o que veio depois; sem mensagem nova, a marca fica igual.
  const completa = { primeira: 10, ultima: 300, completaAntes: true };
  assert.deepEqual(coberturaNova(completa, { mensagens: msgs(400), cobertura: cob('alcancou_inicio', 290, 301) }),
    { primeira: 10, ultima: 400, completaAntes: true });
  assert.deepEqual(coberturaNova(completa, { mensagens: [], cobertura: cob('alcancou_inicio', 290, 301) }),
    { primeira: 10, ultima: 300, completaAntes: true });
  // Parou no limite antes de encostar no trecho marcado: nada a marcar.
  assert.equal(coberturaNova(migrada, { mensagens: msgs(500, 600), cobertura: cob('limite_de_paginas', 500) }), null);
  // Historico vazio.
  assert.equal(coberturaNova(null, { mensagens: [], cobertura: cob('fim_do_historico', null) }), null);
});

test('incremental: resumo conta sem novidade e o painel diz a ultima rodada', async () => {
  const { descreverRodada } = require('../nucleo.js');
  const lista = [bruto('556290000001@c.us', 'Renata Silva', 5), bruto('556290000002@c.us', 'Carla Mendes', 4)];
  const d = depsTodas(lista, async (c) => {
    if (c.nome === 'Carla Mendes') throw erroDe('sem_novidade', 'sem novidade');
    return feita();
  });
  const r = await exportarTodas(d);
  assert.deepEqual({ e: r.exportadas, s: r.semNovidade, f: r.falhas }, { e: 1, s: 1, f: 0 });
  assert.match(descreverResumoTodas(r), /1 exportada\(s\), 1 sem novidade, de 2 conversa\(s\)/);

  assert.equal(descreverRodada(null), 'nenhuma rodada ainda');
  const base = { quando: '2026-10-24T13:05:00+00:00', total: 300, exportadas: 10, sem_novidade: 288, parciais: 0, falhas: 0, interrompida: false };
  assert.equal(descreverRodada(base), 'tudo exportado em 24/10 10:05 · 300 conversa(s), 10 com novidade');
  assert.match(descreverRodada({ ...base, falhas: 2 }), /ultima rodada em 24\/10 10:05: 300 conversa\(s\), 2 com falha/);
  assert.match(descreverRodada({ ...base, interrompida: true }), /interrompida/);
});

test('incremental: decidir separa vazio, sem novidade, limite e exportar', () => {
  const { decidirConversa } = require('../nucleo.js');
  const msgs = (...ts) => ts.map((t) => ({ id: `m${t}`, t }));
  const cob = (motivoDaParada, maisAntigaEncontrada, inicio = 0) =>
    ({ motivoDaParada, maisAntigaEncontrada, pedido: { inicio, fim: 900 } });
  assert.equal(decidirConversa(null, { mensagens: [], cobertura: cob('fim_do_historico', null) }).acao, 'vazio');
  const migrada = { primeira: 100, ultima: 200, completaAntes: false };
  const igual = decidirConversa(migrada, { mensagens: msgs(100, 200), cobertura: cob('fim_do_historico', 100) });
  assert.equal(igual.acao, 'sem_novidade');
  assert.deepEqual(igual.cobertura, { primeira: 100, ultima: 200, completaAntes: true });
  assert.equal(decidirConversa(migrada, { mensagens: msgs(500), cobertura: cob('limite_de_paginas', 500) }).acao, 'limite');
  const nova = decidirConversa(migrada, { mensagens: msgs(10, 150, 300), cobertura: cob('fim_do_historico', 10) });
  assert.equal(nova.acao, 'exportar');
  assert.deepEqual(nova.novas.map((m) => m.t), [10, 300]);
});

test('nomeDaConversa: salvo, so o perfil, sem nome', () => {
  const { nomeDaConversa } = require('../nucleo.js');
  // salvo nos contatos do usuario: vence o perfil
  assert.deepEqual(
    nomeDaConversa({ titulo: 'Renata Silva', nomeSalvo: 'Renata Silva', pushname: 'Rê', numero: '+55 62 9000-0000' }),
    { nome: 'Renata Silva', origem: 'salvo' },
  );
  // titulo do WhatsApp que nao e numero tambem conta como salvo
  assert.equal(nomeDaConversa({ titulo: 'Carla Mendes', numero: '+55 62 9000-0000' }).origem, 'salvo');
  // nao salvo: o titulo e o numero formatado, entao vale o nome do perfil
  assert.deepEqual(
    nomeDaConversa({ titulo: '+55 62 9000-0000', pushname: 'Dra Renata Silva', numero: '+55 62 9000-0000' }),
    { nome: 'Dra Renata Silva', origem: 'perfil' },
  );
  // conta comercial: verifiedName antes do pushname
  assert.deepEqual(
    nomeDaConversa({ titulo: '+55 62 9000-0000', verifiedName: 'Clinica Exemplo', pushname: 'x', numero: '' }),
    { nome: 'Clinica Exemplo', origem: 'perfil' },
  );
  // sem nome nenhum: o numero
  assert.deepEqual(
    nomeDaConversa({ titulo: '+55 62 9000-0000', pushname: '~', numero: '+55 62 9000-0000' }),
    { nome: '~', origem: 'perfil' },
  );
  assert.deepEqual(
    nomeDaConversa({ titulo: '+55 62 9000-0000', numero: '+55 62 9000-0000' }),
    { nome: '+55 62 9000-0000', origem: 'numero' },
  );
  assert.equal(nomeDaConversa({ grupo: true, titulo: 'Grupo X', pushname: 'a' }).nome, 'Grupo X');
});

// ---------- 0.16.0: gravacao pelo wa servir e rodada automatica ----------

test('partesDoArquivo corta em pedacos e manda ao menos uma parte', () => {
  const { partesDoArquivo } = require('../nucleo.js');
  assert.deepEqual(partesDoArquivo(0, 8), [[0, 0]]);
  assert.deepEqual(partesDoArquivo(8, 8), [[0, 8]]);
  assert.deepEqual(partesDoArquivo(20, 8), [[0, 8], [8, 16], [16, 20]]);
});

test('motivoParaNaoExportarTodas: fora do ar, servidor velho, pasta sem existir e transcricao parada', () => {
  const { motivoParaNaoExportarTodas: motivo } = require('../nucleo.js');
  const bom = { ok: true, exportar: { raiz: 'D:/70-WHATSAPP', existe: true }, transcricao: { bloqueio: null } };
  assert.equal(motivo(bom), null);
  assert.equal(motivo(bom, { automatica: true }), null);
  assert.match(motivo({ ok: false, erro: 'pepita-wa fora do ar em http://127.0.0.1:8765/status' }), /fora do ar/);
  assert.match(motivo(null), /fora do ar/);
  assert.match(motivo({ ok: true }), /anterior a 0\.16\.0/);
  assert.match(motivo({ ...bom, exportar: { raiz: 'D:/70-WHATSAPP', existe: false } }), /D:\/70-WHATSAPP nao existe/);
  const parada = { ...bom, transcricao: { bloqueio: 'agy sem login e whisper fora do ar' } };
  // O clique pode exportar sem transcricao (o usuario ve o aviso); a automatica pula.
  assert.equal(motivo(parada), null);
  assert.match(motivo(parada, { automatica: true }), /transcricao parada \(agy sem login/);
});

test('decidirTrava: uma so entre o clique e a automatica, com validade', () => {
  const { decidirTrava, renovarTrava } = require('../nucleo.js');
  const agora = new Date(2026, 9, 31, 14, 30).getTime();
  const d = decidirTrava(null, { quem: 'auto', abaId: 7 }, agora, 300000);
  assert.equal(d.ok, true);
  assert.deepEqual(d.trava, { quem: 'auto', abaId: 7, inicio: agora, validade: agora + 300000 });
  const outra = decidirTrava(d.trava, { quem: 'manual', abaId: 9 }, agora + 1000, 300000);
  assert.deepEqual(outra, { ok: false, ocupadaPor: 'auto', motivo: 'rodada automatica em andamento desde 14:30' });
  // A propria aba tambem nao entra duas vezes.
  assert.equal(decidirTrava(d.trava, { quem: 'auto', abaId: 7 }, agora + 1000, 300000).ok, false);
  // Vencida (aba fechada no meio): a trava cai.
  assert.equal(decidirTrava(d.trava, { quem: 'manual', abaId: 9 }, agora + 300000, 300000).ok, true);
  // So a aba dona renova, e so antes de vencer.
  assert.equal(renovarTrava(d.trava, 9, agora + 1000, 300000), null);
  assert.equal(renovarTrava(d.trava, 7, agora + 1000, 300000).validade, agora + 301000);
  assert.equal(renovarTrava(d.trava, 7, agora + 300000, 300000), null);
  assert.equal(renovarTrava(null, 7, agora, 300000), null);
});

test('oQueFechar: so o que a rotina abriu', () => {
  const { oQueFechar } = require('../nucleo.js');
  const janelas = [{ id: 1, abas: [10, 11] }, { id: 2, abas: [20] }];
  // Aba ja aberta pelo usuario: nada fecha.
  assert.deepEqual(oQueFechar({ abaId: 10, abriuAba: false, abriuChrome: false }, janelas), { janelas: [], abas: [] });
  // A rotina abriu so a aba: fecha so ela.
  assert.deepEqual(oQueFechar({ abaId: 11, abriuAba: true, abriuChrome: false }, janelas), { janelas: [], abas: [11] });
  // A rotina abriu o Chrome: fecha as janelas do comeco (a ultima encerra o Chrome)...
  const registro = { abaId: 10, abriuAba: false, abriuChrome: true, janelasAntes: { 1: [10] } };
  assert.deepEqual(oQueFechar(registro, [{ id: 1, abas: [10] }]), { janelas: [1], abas: [] });
  // ... mas a janela que ganhou aba do usuario fica, sem a do WhatsApp, e a janela nova do usuario fica.
  assert.deepEqual(oQueFechar(registro, [{ id: 1, abas: [10, 12] }, { id: 3, abas: [30] }]), { janelas: [], abas: [10] });
  assert.deepEqual(oQueFechar(null, janelas), { janelas: [], abas: [] });
});

test('resultadoDaRodada leva so numeros, nunca nome de contato', () => {
  const { resultadoDaRodada } = require('../nucleo.js');
  const base = {
    total: 300, exportadas: 4, semNovidade: 290, puladas: 3, parciais: 0, falhas: 0, midiasComFalha: 0, anexosFalhos: 0,
    cancelado: false, parou: null, itens: [{ nome: 'Renata Silva', estado: 'falha', erro: 'x' }],
  };
  assert.deepEqual(resultadoDaRodada(base), { resultado: 'ok', detalhe: '4 exportada(s), 293 sem novidade, de 300' });
  const falha = resultadoDaRodada({ ...base, falhas: 1, midiasComFalha: 2 });
  assert.equal(falha.resultado, 'falha');
  assert.doesNotMatch(falha.detalhe, /Renata/);
  assert.match(falha.detalhe, /1 com falha, 2 midia\(s\) sem texto/);
  assert.equal(resultadoDaRodada({ ...base, parou: 'fora' }).resultado, 'interrompida');
  assert.match(resultadoDaRodada({ ...base, cancelado: true }).detalhe, /cancelada$/);
});

test('descreverRotina: ligada, a ultima e a proxima', () => {
  const { descreverRotina } = require('../nucleo.js');
  const ultima = { quando: new Date(2026, 9, 31, 14, 30).toISOString(), resultado: 'pulada', detalhe: 'transcricao parada (cota)' };
  const proxima = new Date(2026, 9, 31, 15, 0).toISOString();
  assert.equal(
    descreverRotina({ ligada: true, intervalo_min: 30, ultima, proxima, pendente: false }),
    'rodada automatica a cada 30 min\nultima: 31/10 14:30, pulada (transcricao parada (cota))\nproxima: 31/10 15:00',
  );
  assert.equal(descreverRotina({ ligada: true, intervalo_min: 30, ultima: null, proxima, pendente: true }),
    'rodada automatica a cada 30 min\nproxima: agora');
  assert.equal(descreverRotina({ ligada: false, intervalo_min: 30, ultima: null, proxima: null }), 'rodada automatica desligada');
  assert.match(descreverRotina(null), /sem resposta/);
});

test('nomes que o wa servir recusaria nao saem do nucleo', () => {
  const { nomeUnico, nomesDosAnexos } = require('../nucleo.js');
  assert.equal(nomeUnico(new Set(), 'con', '556290000000@c.us'), 'con-conversa');
  assert.equal(nomeUnico(new Set(), 'renata-silva', '556290000000@c.us'), 'renata-silva');
  const nomes = nomesDosAnexos([
    { id: 'd1', tipo: 'document', t: Date.UTC(2026, 8, 17, 17, 25, 9) / 1000, arquivo: 'backup.parcial', mimetype: 'application/octet-stream' },
  ]);
  assert.equal(nomes.get('d1'), 'anexos/2026-09-17_142509_backup.parcial.bin');
});

test('todas: grupo so entra se o usuario o escolheu (ids em grupos); o resto continua fora', () => {
  const g = (id, nome, t, extra = {}) => ({ id, nome, t, grupo: true, servidor: 'g.us', ...extra });
  const lista = [
    { id: '556290000000@c.us', nome: 'Renata Silva', t: 10, servidor: 'c.us' },
    g('120363000000000001@g.us', 'Familia', 30),
    g('120363000000000002@g.us', 'Condominio', 20),
    g('120363000000000003@g.us', 'Comunidade', 40, { newsletter: true }),
  ];
  assert.deepEqual(selecionarConversas(lista).map((c) => c.nome), ['Renata Silva']);
  assert.deepEqual(selecionarConversas(lista, { grupos: [] }).map((c) => c.nome), ['Renata Silva']);
  assert.deepEqual(
    selecionarConversas(lista, { grupos: ['120363000000000001@g.us', '120363000000000003@g.us'] }).map((c) => c.nome),
    ['Familia', 'Renata Silva'],
  );
  // O id escolhido nao vale para quem nao e grupo: pessoa listada em `grupos` nada muda.
  assert.deepEqual(selecionarConversas(lista, { grupos: ['556290000000@c.us'] }).map((c) => c.nome), ['Renata Silva']);
});
