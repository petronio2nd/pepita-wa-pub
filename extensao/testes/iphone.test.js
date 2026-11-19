const { test } = require('node:test');
const assert = require('node:assert/strict');
const N = require('../nucleo.js');

test('backup exclui so os IDs recebidos e preserva cobertura da coleta', () => {
  const coleta = { mensagens: [
    { id: 'false_199999999999999@lid_FICTICIO1', t: 100 },
    { id: 'false_199999999999999@lid_FICTICIO2', t: 101 },
    { id: 'false_199999999999999@lid_NOVA', t: 102 },
  ], cobertura: { motivoDaParada: 'fim_do_historico', maisAntigaEncontrada: 100 } };
  const r = N.decidirConversa(null, coleta, ['FICTICIO1','FICTICIO2']);
  assert.deepEqual(r.novas.map(m => m.t), [102]);
  assert.equal(r.cobertura.primeira, 100);
  assert.equal(r.cobertura.ultima, 102);
  assert.equal(N.decidirConversa(null, coleta).novas.length, 3);
  assert.equal(N.decidirConversa(null, coleta, ['FICTICIO1','FICTICIO2','NOVA']).acao, 'sem_novidade');
});

test('telefone da Web encontra recibo do iPhone mesmo quando o id mudou para lid', () => {
  const recibos = {'556290000000@s.whatsapp.net': {pasta: 'renata'}};
  assert.equal(N.chaveDaImportacao(recibos, {id:'199999999999999@lid',telefone:'556290000000'}), '556290000000@s.whatsapp.net');
  assert.equal(N.chaveDaImportacao(recibos, {id:'199999999999999@g.us',grupo:true,telefone:'556290000000'}), null);
});
