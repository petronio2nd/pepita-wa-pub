// Captura ao vivo (0.20.0): agendador do nucleo e o filtro soIds do exportarTodas.
// So dado ficticio.
const test = require('node:test');
const assert = require('node:assert/strict');
const { criarAgendadorCaptura, exportarTodas } = require('../nucleo.js');

function relogioFalso() {
  let t = 0;
  let seq = 0;
  const timers = new Map();
  return {
    agora: () => t,
    depois: (fn, ms) => { const id = ++seq; timers.set(id, { fn, quando: t + ms }); return id; },
    cancelar: (id) => timers.delete(id),
    // Avanca o tempo rodando os timers vencidos, um por vez, na ordem.
    async andar(ms) {
      const fim = t + ms;
      for (;;) {
        const prox = [...timers.entries()].filter(([, v]) => v.quando <= fim).sort((a, b) => a[1].quando - b[1].quando)[0];
        if (!prox) break;
        timers.delete(prox[0]);
        t = prox[1].quando;
        prox[1].fn(); // sem await: um executar pode ficar pendente de proposito
        await new Promise((ok) => setImmediate(ok));
      }
      t = fim;
    },
  };
}

const A = '556290000001@c.us';
const B = '199999999999999@lid';

test('captura: espera o silencio e junta as conversas numa execucao', async () => {
  const r = relogioFalso();
  const chamadas = [];
  const ag = criarAgendadorCaptura({ executar: async (ids) => { chamadas.push(ids); return true; }, quietoMs: 100, maxMs: 1000, relogio: r });
  ag.marcar(A);
  await r.andar(60);
  ag.marcar(B);
  ag.marcar(A);
  await r.andar(60);
  assert.equal(chamadas.length, 0); // ainda nao ficou 100 ms quieto
  await r.andar(60);
  assert.deepEqual(chamadas, [[A, B]]);
  assert.deepEqual(ag.pendentes, []);
});

test('captura: mensagens sem parar saem no maxMs', async () => {
  const r = relogioFalso();
  const chamadas = [];
  const ag = criarAgendadorCaptura({ executar: async (ids) => { chamadas.push(ids); return true; }, quietoMs: 100, maxMs: 300, relogio: r });
  for (let i = 0; i < 6; i++) {
    ag.marcar(A);
    await r.andar(60);
  }
  assert.equal(chamadas.length, 1);
});

test('captura: ocupado devolve as conversas e espera antes de tentar de novo', async () => {
  const r = relogioFalso();
  const respostas = [false, true];
  const chamadas = [];
  const ag = criarAgendadorCaptura({ executar: async (ids) => { chamadas.push([r.agora(), ids]); return respostas.shift(); }, quietoMs: 100, maxMs: 150, relogio: r });
  ag.marcar(A);
  await r.andar(100);
  assert.equal(chamadas.length, 1);
  assert.deepEqual(ag.pendentes, [A]);
  // Aviso novo logo depois da recusa nao dispara na hora (desde + maxMs ja venceu).
  ag.marcar(B);
  await r.andar(50);
  assert.equal(chamadas.length, 1);
  await r.andar(60);
  assert.equal(chamadas.length, 2);
  assert.deepEqual(chamadas[1], [200, [A, B]]);
  assert.deepEqual(ag.pendentes, []);
});

test('captura: erro no executar conta como nao rodou; uma execucao por vez', async () => {
  const r = relogioFalso();
  let soltar;
  let ativas = 0;
  let pico = 0;
  const chamadas = [];
  const ag = criarAgendadorCaptura({
    executar: async (ids) => {
      chamadas.push(ids);
      if (chamadas.length === 1) throw new Error('caiu');
      ativas++;
      pico = Math.max(pico, ativas);
      await new Promise((ok) => (soltar = ok));
      ativas--;
      return true;
    },
    quietoMs: 10, maxMs: 100, relogio: r,
  });
  ag.marcar(A);
  await r.andar(10);
  assert.deepEqual(ag.pendentes, [A]);
  await r.andar(10); // segunda tentativa: fica rodando
  assert.equal(ag.rodando, true);
  ag.marcar(B); // chega durante a execucao: vai para a proxima
  await r.andar(50);
  assert.equal(chamadas.length, 2);
  soltar();
  await new Promise((ok) => setImmediate(ok));
  await r.andar(20);
  assert.deepEqual(chamadas, [[A], [A], [B]]);
  assert.equal(pico, 1);
});

test('captura: parar descarta o pendente e ignora avisos', async () => {
  const r = relogioFalso();
  const chamadas = [];
  const ag = criarAgendadorCaptura({ executar: async (ids) => { chamadas.push(ids); return true; }, quietoMs: 10, relogio: r });
  ag.marcar(A);
  ag.parar();
  ag.marcar(B);
  await r.andar(1000);
  assert.deepEqual(chamadas, []);
});

test('exportarTodas com soIds: so as conversas pedidas, homonimia contra a lista inteira', async () => {
  const lista = [
    { id: A, nome: 'Renata Silva', grupo: false, t: 5 },
    { id: B, nome: 'Renata Silva', grupo: false, t: 4 },
    { id: '556290000002@c.us', nome: 'Carla Mendes', grupo: false, t: 3 },
  ];
  const vistas = [];
  const deps = {
    listar: async () => lista,
    exportarUma: async (c) => {
      vistas.push(c);
      return { salvoEm: 'x/x.md', resumo: { falhas: 0, anexosFalhas: 0 }, coleta: { cobertura: null } };
    },
  };
  const r = await exportarTodas(deps, undefined, { soIds: [B] });
  assert.deepEqual(vistas.map((c) => c.id), [B]);
  assert.equal(vistas[0].homonima, true);
  assert.equal(r.total, 1);
  assert.equal(r.exportadas, 1);
});
