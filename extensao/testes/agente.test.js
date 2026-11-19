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

test('resolverConversa: telefone casa com e sem 55, com e sem o 9, e pelo id c.us de um @lid', () => {
  const lista = [
    { id: '211111111111111@lid', nome: 'caio', grupo: false, telefone: '556294670000' },
    { id: '556291234567@c.us', nome: 'Bruna', grupo: false, telefone: '556291234567' },
    { id: '222222222222222@lid', nome: '+55 62 9107-2016', grupo: false, telefone: '' },
    { id: '120363000000000000@g.us', nome: 'Grupo 9467', grupo: true, telefone: '' },
  ];
  for (const alvo of ['+55 62 9467-0000', '+55 62 99467-0000', '62994670000', '9467-0000', '(62) 9 9467-0000']) {
    assert.equal(resolverConversa(lista, alvo).conversa.nome, 'caio', alvo);
  }
  // Id c.us que o WhatsApp so conhece pelo @lid.
  assert.equal(resolverConversa(lista, '5562994670000@c.us').conversa.nome, 'caio');
  assert.match(resolverConversa(lista, '5562900000000@c.us').erro, /nenhuma conversa com o id/);
  // Contato nao salvo sem telefone no contato: cai na busca pelo nome, que e o numero.
  assert.equal(resolverConversa(lista, '+55 62 9107-2016').conversa.id, '222222222222222@lid');
  // Poucos digitos nao vira busca por telefone, e grupo nunca casa por telefone.
  assert.equal(resolverConversa(lista, '9467').conversa.grupo, true);
  assert.match(resolverConversa(lista, '+55 11 98888-7777').erro, /nenhuma conversa com/);
  // Dois contatos com o mesmo fim: ambiguo, nunca escolhe sozinho.
  const dois = [...lista, { id: '333@lid', nome: 'Outro', grupo: false, telefone: '551194670000' }];
  assert.equal(resolverConversa(dois, '9467-0000').ambiguo.length, 2);
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

test('antesDeArquivar (0.28.0): nome, mensagem nova, ja no estado e agir', () => {
  const { antesDeArquivar } = require('../nucleo.js');
  const leitura = { ultimaT: 100, vistos: ['a'] };
  const base = { nomeAtual: 'Renata Silva', nomeEsperado: 'Renata Silva', arquivada: false, arquivar: true, leitura, completa: true };
  const lidas = [{ id: 'a', t: 100, eu: false }];
  assert.deepEqual(antesDeArquivar({ ...base, msgs: lidas }), { agir: true });
  assert.match(antesDeArquivar({ ...base, nomeAtual: 'Carla Mendes', msgs: lidas }).recusa, /Carla Mendes/);
  assert.match(antesDeArquivar({ ...base, nomeEsperado: '', msgs: lidas }).recusa, /nome atual/);
  const nova = lidas.concat({ id: 'b', t: 160, eu: false });
  assert.deepEqual(antesDeArquivar({ ...base, msgs: nova }), { resultado: 'nova' });
  // Mensagem minha depois da leitura nao impede.
  assert.deepEqual(antesDeArquivar({ ...base, msgs: lidas.concat({ id: 'c', t: 170, eu: true }) }), { agir: true });
  assert.deepEqual(antesDeArquivar({ ...base, arquivada: true, msgs: lidas }), { resultado: 'ja_estava' });
  // Ja arquivada com mensagem nova: diz que ja estava, nunca que ficou a vista.
  assert.deepEqual(antesDeArquivar({ ...base, arquivada: true, msgs: nova }), { resultado: 'ja_estava' });
  // Desarquivar nao olha mensagem nova (so devolve a conversa a vista).
  assert.deepEqual(antesDeArquivar({ ...base, arquivada: true, arquivar: false, msgs: nova }), { agir: true });
  assert.deepEqual(antesDeArquivar({ ...base, arquivada: false, arquivar: false, msgs: [] }), { resultado: 'ja_estava' });
});
