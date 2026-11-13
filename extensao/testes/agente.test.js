// Agente (0.19.0): resolver a conversa, mensagem nova depois da leitura, confirmar o
// envio e transcrever com prazo. So dado ficticio.
const test = require('node:test');
const assert = require('node:assert/strict');
const { resolverConversa, novaDepoisDaLeitura, confirmarEnvio, classificarFalhaNaAba, transcreverParaOAgente } = require('../nucleo.js');

const LISTA = [
  { id: '556290000000@c.us', nome: 'Renata Silva', grupo: false },
  { id: '199999999999999@lid', nome: 'Renata Souza', grupo: false },
  { id: '556290000001@c.us', nome: 'Carla Mendes', grupo: false },
  { id: '120363000000000000@g.us', nome: 'Família Mendes', grupo: true },
  { id: '556290000009@c.us', nome: 'usuario', grupo: false, eu: true },
];

test('resolverConversa: id, eu, nome exato, parte do nome, acento e ambiguidade', () => {
  assert.equal(resolverConversa(LISTA, '199999999999999@lid').conversa.nome, 'Renata Souza');
  assert.match(resolverConversa(LISTA, '556299999999@c.us').erro, /nenhuma conversa com o id/);
  assert.equal(resolverConversa(LISTA, 'eu').conversa.id, '556290000009@c.us');
  assert.equal(resolverConversa(LISTA, 'carla').conversa.id, '556290000001@c.us');
  assert.equal(resolverConversa(LISTA, 'familia').conversa.grupo, true);
  assert.equal(resolverConversa(LISTA, 'renata silva').conversa.id, '556290000000@c.us');
  const amb = resolverConversa(LISTA, 'Renata');
  assert.deepEqual(amb.ambiguo.map((c) => c.id), ['556290000000@c.us', '199999999999999@lid']);
  assert.match(resolverConversa(LISTA, 'Joana').erro, /nenhuma conversa com "Joana"/);
  assert.match(resolverConversa(LISTA, '  ').erro, /diga qual/);
  // "Mendes" casa com a Carla e com o grupo: ambiguo, nunca escolhe sozinho.
  assert.equal(resolverConversa(LISTA, 'Mendes').ambiguo.length, 2);
});

test('resolverConversa: "eu" sem a conversa do usuario nunca cai na busca por nome', () => {
  const semEu = [{ id: '556290000002@c.us', nome: 'Eurico Mendes', grupo: false }];
  assert.match(resolverConversa(semEu, 'eu').erro, /conversa do usuario/);
  assert.match(resolverConversa(semEu, 'Eu').erro, /conversa do usuario/);
});

test('novaDepoisDaLeitura: olha todas as ultimas, nao so a ultima', () => {
  const leitura = { ultimaT: 100, vistos: ['m1', 'm2'] };
  const lidas = [{ id: 'm1', t: 90, eu: false }, { id: 'm2', t: 100, eu: false }];
  assert.equal(novaDepoisDaLeitura(lidas, leitura), false);
  // O usuario respondeu pelo celular depois da mensagem nova: a ultima e dele, mas a nova existe (bug da v4).
  assert.equal(novaDepoisDaLeitura([...lidas, { id: 'm3', t: 110, eu: false }, { id: 'm4', t: 120, eu: true }], leitura), true);
  // No mesmo segundo da ultima lida, mas nao vista.
  assert.equal(novaDepoisDaLeitura([...lidas, { id: 'm5', t: 100, eu: false }], leitura), true);
  // So mensagem minha depois da leitura: pode enviar.
  assert.equal(novaDepoisDaLeitura([...lidas, { id: 'm6', t: 130, eu: true }], leitura), false);
  // A busca nao alcanca a leitura (o usuario mandou muitas depois): nao da para provar, conta como nova.
  const soMinhas = Array.from({ length: 5 }, (_, i) => ({ id: `p${i}`, t: 200 + i, eu: true }));
  assert.equal(novaDepoisDaLeitura(soMinhas, leitura), true);
  // Busca incompleta que ficou vazia depois do filtro (so eventos de sistema): tambem recusa.
  assert.equal(novaDepoisDaLeitura([], leitura), true);
  assert.equal(novaDepoisDaLeitura([], leitura, { completa: true }), false);
  // ...a nao ser que a conversa inteira caiba na busca.
  assert.equal(novaDepoisDaLeitura(soMinhas, leitura, { completa: true }), false);
});

test('confirmarEnvio: pelo id ou pelo texto igual depois do envio', () => {
  const msgs = [{ id: 'x1', t: 200, eu: true, texto: 'Combinado.' }, { id: 'x2', t: 210, eu: false, texto: 'Combinado.' }];
  assert.equal(confirmarEnvio(msgs, { idEnviado: 'x1' }).id, 'x1');
  assert.equal(confirmarEnvio(msgs, { texto: 'Combinado.', antes: [] }).id, 'x1');
  // A minha ja existia antes do envio; a do contato nao conta.
  assert.equal(confirmarEnvio(msgs, { texto: 'Combinado.', antes: ['x1'] }), null);
  // Com id, so o id confirma: um texto igual mais antigo nao vale.
  assert.equal(confirmarEnvio(msgs, { idEnviado: 'x9', texto: 'Combinado.' }), null);
});

test('classificarFalhaNaAba: so sem receptor tenta outra aba', () => {
  assert.equal(classificarFalhaNaAba(new Error('Could not establish connection. Receiving end does not exist.')).tipo, 'sem_aba');
  // Porta fechada antes da resposta (aba recarregada no meio): pode ter enviado.
  assert.equal(classificarFalhaNaAba(new Error('The message port closed before a response was received.')).tipo, 'sem_resposta');
  assert.equal(classificarFalhaNaAba(new Error('No tab with id: 12.')).tipo, 'sem_resposta');
  // Prazo nunca vira sem_aba, mesmo com texto parecido.
  const prazo = Object.assign(new Error('Receiving end does not exist'), { prazo: true });
  assert.match(classificarFalhaNaAba(prazo).erro, /\(prazo\)/);
  assert.equal(classificarFalhaNaAba(prazo).tipo, 'sem_resposta');
  assert.equal(classificarFalhaNaAba(undefined).tipo, 'sem_resposta');
});

test('transcreverParaOAgente: o que nao termina no prazo fica pendente, nunca inventado', async () => {
  const msgs = [
    { id: 'a1', tipo: 'ptt' },
    { id: 'a2', tipo: 'ptt' },
    { id: 'a3', tipo: 'audio', visualizacaoUnica: true },
    { id: 'a4', tipo: 'ptt' },
    { id: 't1', tipo: 'chat', texto: 'Oi' },
  ];
  const chamados = [];
  const processar = (m) => {
    chamados.push(m.id);
    if (m.id === 'a1') return Promise.resolve({ texto: 'Vi sim, ta tudo certo.' });
    if (m.id === 'a2') return new Promise(() => {}); // nunca termina
    return Promise.resolve({ erro: 'o WhatsApp nao entregou a midia', tipo: 'indisponivel' });
  };
  await transcreverParaOAgente(msgs, processar, { prazoMs: 50, paralelo: 3 });
  assert.deepEqual(msgs[0].transcricao, { texto: 'Vi sim, ta tudo certo.' });
  assert.match(msgs[1].transcricao.pendente, /nao terminou/);
  assert.equal(msgs[2].transcricao, undefined); // visualizacao unica nem vai
  assert.deepEqual(msgs[3].transcricao, { pendente: 'o WhatsApp nao entregou a midia' });
  assert.equal(msgs[4].transcricao, undefined);
  assert.deepEqual(chamados.sort(), ['a1', 'a2', 'a4']);
});

test('transcreverParaOAgente: texto vazio (sem fala) e resultado, nao pendencia', async () => {
  const msgs = [{ id: 'a1', tipo: 'ptt' }];
  await transcreverParaOAgente(msgs, async () => ({ texto: '' }), { prazoMs: 1000 });
  assert.deepEqual(msgs[0].transcricao, { texto: '' });
});
