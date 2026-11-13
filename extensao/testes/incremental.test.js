// Achados da revisao da 0.12.0 (incremental do "Exportar todas"), agora que a
// rodada vai rodar sozinha a cada 30 minutos. Cada teste reproduz um achado.
// O que ainda nao foi corrigido fica em `aindaNao`, o xfail estrito do
// tests/test_seguranca.py: o teste diz o comportamento certo, e o `aindaNao`
// falha quando ele passar a valer (tire o `aindaNao` quando corrigir).
process.env.TZ = 'America/Sao_Paulo';

const test = require('node:test');
const assert = require('node:assert/strict');
const {
  planoDaConversa, decidirConversa, varrerPeriodo, exportar, marcaComPendente, reenviarPendentes,
} = require('../nucleo.js');

function aindaNao(nome, motivo, fn) {
  test(`${nome} (ainda nao: ${motivo})`, async () => {
    let passou = false;
    try {
      await fn();
      passou = true;
    } catch (erro) {
      if (!(erro instanceof assert.AssertionError)) throw erro;
    }
    assert.equal(passou, false, 'o comportamento certo passou a valer: tire o aindaNao');
  });
}

const ID = '556290000000@c.us';

// Historico do WhatsApp Web em paginas de `tamanho`, da mais nova para tras,
// como o `buscar` que a pagina.js entrega ao varrerPeriodo.
function buscador(historico, tamanho = 2) {
  const ordem = () => historico.slice().sort((a, b) => a.t - b.t);
  return async (antesDe) => {
    const lista = ordem();
    const fim = antesDe ? lista.findIndex((m) => m.id === antesDe.id) : lista.length;
    return lista.slice(Math.max(0, fim - tamanho), fim);
  };
}

// O POST /marca do marcas.py: junta por minimo e maximo e recusa trecho solto.
function servidorFalso() {
  const marcas = new Map();
  let falhas = 0;
  return {
    marcas,
    falharProximas(n) { falhas = n; },
    marcar(c) {
      if (falhas > 0) { falhas--; return { erro: 'pepita-wa fora do ar', tipo: 'fora' }; }
      const a = marcas.get(c.id);
      let m = { ...c };
      if (a) {
        if (c.primeira > a.ultima + 1 || c.ultima < a.primeira - 1) return { erro: 'nao encosta', tipo: 'servidor' };
        m = { ...c, pasta: a.pasta, primeira: Math.min(c.primeira, a.primeira), ultima: Math.max(c.ultima, a.ultima),
          completaAntes: c.completaAntes || a.completaAntes };
      }
      marcas.set(c.id, m);
      return { ok: true, dados: m };
    },
  };
}

// Uma conversa numa rodada, na ordem da ponte.js (rodarTodas/exportarUma):
// marcas do servidor, pendentes do navegador, plano, leitura, decisao, .md salvo, marca.
async function rodada({ historico, servidor, agora, pendentes = null, limitePaginas = 4000 }) {
  const marcas = new Map(servidor.marcas);
  if (pendentes) await reenviarPendentes(pendentes, marcas, async (p) => servidor.marcar(p));
  const marca = marcas.get(ID);
  const coleta = await varrerPeriodo(buscador(historico), { ...planoDaConversa(marca, agora), limitePaginas });
  const decisao = decidirConversa(marca, coleta);
  const corpo = { id: ID, nome: 'Renata Silva', pasta: marca?.pasta || 'renata-silva', ...decisao.cobertura };
  const marcar = (salvou) => {
    const r = servidor.marcar(corpo);
    if (pendentes && !r.ok && salvou) pendentes[ID] = corpo;
    if (pendentes && r.ok) delete pendentes[ID];
  };
  if (decisao.acao !== 'exportar') {
    if (decisao.acao === 'sem_novidade' && decisao.cobertura) marcar(false);
    return { acao: decisao.acao, gravadas: [] };
  }
  const gravadas = decisao.novas.map((m) => m.id); // o .md com estas mensagens foi salvo
  marcar(true);
  return { acao: 'exportar', gravadas };
}

const msg = (id, t) => ({ id, t, tipo: 'chat' });

test('achado 1: marca que falha depois de salvar duplicava as mensagens na rodada seguinte', async () => {
  const historico = [msg('a', 100), msg('b', 200)];

  // Como era (sem pendente): a rodada seguinte grava as mesmas mensagens num -2.
  const antes = servidorFalso();
  antes.falharProximas(1);
  assert.deepEqual((await rodada({ historico, servidor: antes, agora: 1000 })).gravadas, ['a', 'b']);
  assert.equal(antes.marcas.size, 0, 'o .md foi salvo e a marca nao');
  assert.deepEqual((await rodada({ historico, servidor: antes, agora: 2000 })).gravadas, ['a', 'b'], 'duplicou');

  // Com a marca pendente no navegador: a rodada seguinte manda a marca antes e grava so o novo.
  const servidor = servidorFalso();
  const pendentes = {};
  servidor.falharProximas(1);
  assert.deepEqual((await rodada({ historico, servidor, agora: 1000, pendentes })).gravadas, ['a', 'b']);
  assert.ok(pendentes[ID], 'a marca ficou pendente');
  historico.push(msg('c', 1500));
  assert.deepEqual((await rodada({ historico, servidor, agora: 2000, pendentes })).gravadas, ['c']);
  assert.deepEqual(pendentes, {});
  const m = servidor.marcas.get(ID);
  assert.deepEqual([m.primeira, m.ultima, m.completaAntes], [100, 1500, true]);
});

test('achado 1: servidor ainda fora na rodada seguinte, a pendente vale no plano e nao duplica', async () => {
  const historico = [msg('a', 100), msg('b', 200)];
  const servidor = servidorFalso();
  const pendentes = {};
  servidor.falharProximas(1);
  await rodada({ historico, servidor, agora: 1000, pendentes });
  // A pendente nao vai (fora), mas o plano ja conta com ela; a marca nova cobre as duas.
  servidor.falharProximas(1);
  historico.push(msg('c', 1500));
  assert.deepEqual((await rodada({ historico, servidor, agora: 2000, pendentes })).gravadas, ['c']);
  assert.deepEqual(pendentes, {});
  assert.deepEqual([servidor.marcas.get(ID).primeira, servidor.marcas.get(ID).ultima], [100, 1500]);
});

test('marcaComPendente: junta o trecho e fica com a pasta da marca do servidor', () => {
  const p = { id: ID, nome: 'Renata Silva', pasta: 'outra', primeira: 50, ultima: 400, completaAntes: true };
  assert.equal(marcaComPendente(undefined, null), undefined);
  assert.deepEqual(marcaComPendente(undefined, p), p);
  const m = { nome: 'Renata Silva', pasta: 'renata-silva', primeira: 100, ultima: 300, completaAntes: false };
  assert.deepEqual(marcaComPendente(m, null), m);
  assert.deepEqual(marcaComPendente(m, p),
    { nome: 'Renata Silva', pasta: 'renata-silva', primeira: 50, ultima: 400, completaAntes: true });
});

aindaNao('achado 2: mensagem que chega depois, no mesmo segundo da ultima marcada, sai na rodada seguinte',
  'escolha de produto: guardar os ids do ultimo segundo na marca ou repetir esse segundo', async () => {
    const servidor = servidorFalso();
    const historico = [msg('a', 100), msg('b', 200)];
    await rodada({ historico, servidor, agora: 1000 });
    // 'c' tem o mesmo t de 'b', mas so chegou ao WhatsApp Web depois da rodada.
    historico.push(msg('c', 200));
    assert.deepEqual((await rodada({ historico, servidor, agora: 2000 })).gravadas, ['c']);
  });

aindaNao('achado 3: midia que falhou na rodada e refeita numa rodada seguinte',
  'escolha de produto: repetir a mensagem num arquivo novo ou reescrever o .md ja salvo', async () => {
    const servidor = servidorFalso();
    const historico = [msg('a', 100), { id: 'au', t: 200, tipo: 'ptt' }];
    const marca = undefined;
    const coleta = await varrerPeriodo(buscador(historico), planoDaConversa(marca, 1000));
    const decisao = decidirConversa(marca, coleta);
    const r = await exportar({
      coletar: async () => ({ chat: { id: ID, nome: 'Renata Silva' }, ...coleta, mensagens: decisao.novas }),
      processar: async () => ({ erro: 'cota do agy' }),
      gerar: () => 'md',
      salvar: async () => { servidor.marcar({ id: ID, nome: 'Renata Silva', pasta: 'renata-silva', ...decisao.cobertura }); return 'x.md'; },
    }, { args: { modo: 'incremental' }, audio: true, imagem: true });
    assert.equal(r.resumo.falhas, 1, 'o .md saiu com o audio sem transcricao');
    assert.ok(servidor.marcas.get(ID).ultima >= 200, 'e a marca cobre o audio');
    // A rodada seguinte deveria voltar ao audio que falhou.
    assert.notEqual((await rodada({ historico, servidor, agora: 2000 })).acao, 'sem_novidade');
  });

aindaNao('achado 4: historico maior que o limite de paginas avanca para tras rodada a rodada',
  'deixado: nao perde nem duplica o que chega; pede ler a partir da marca na pagina.js', async () => {
    // limitePaginas 3 com paginas de 2 faz o papel das 4000 paginas.
    const servidor = servidorFalso();
    const historico = Array.from({ length: 20 }, (_, i) => msg(`m${i}`, (i + 1) * 10));
    await rodada({ historico, servidor, agora: 1000, limitePaginas: 3 });
    const primeira1 = servidor.marcas.get(ID).primeira;
    assert.ok(primeira1 > 10, 'a primeira rodada parou antes do comeco');
    historico.push(msg('n1', 500));
    await rodada({ historico, servidor, agora: 2000, limitePaginas: 3 });
    assert.ok(servidor.marcas.get(ID).primeira < primeira1, 'a segunda rodada deveria ir mais para tras');
  });

test('achado 4: com o limite, nada duplica e o que chega continua saindo', async () => {
  const servidor = servidorFalso();
  const historico = Array.from({ length: 20 }, (_, i) => msg(`m${i}`, (i + 1) * 10));
  const r1 = await rodada({ historico, servidor, agora: 1000, limitePaginas: 3 });
  historico.push(msg('n1', 500));
  const r2 = await rodada({ historico, servidor, agora: 2000, limitePaginas: 3 });
  assert.deepEqual(r2.gravadas, ['n1']);
  assert.equal(r1.gravadas.filter((id) => r2.gravadas.includes(id)).length, 0);
});

test('reenviarPendentes: a que foi sai, a que falhou ou lancou fica e vale no plano', async () => {
  const p1 = { id: '556290000001@c.us', nome: 'Renata Silva', pasta: 'renata-silva', primeira: 10, ultima: 20, completaAntes: true };
  const p2 = { id: '556290000002@c.us', nome: 'Carla Mendes', pasta: 'carla-mendes', primeira: 30, ultima: 40, completaAntes: false };
  const p3 = { ...p2, id: '556290000003@c.us', pasta: 'carla-mendes-3' };
  const pendentes = { [p1.id]: p1, [p2.id]: p2, [p3.id]: p3 };
  const marcas = new Map([[p2.id, { nome: 'Carla Mendes', pasta: 'carla-mendes', primeira: 35, ultima: 50, completaAntes: true }]]);
  const mudou = await reenviarPendentes(pendentes, marcas, async (c) => {
    if (c.id === p1.id) return { ok: true, dados: { ...c, nome: 'servidor' } };
    if (c.id === p2.id) return { erro: 'pepita-wa fora do ar', tipo: 'fora' };
    throw new Error('extensao recarregada');
  });
  assert.equal(mudou, true);
  assert.deepEqual(Object.keys(pendentes), [p2.id, p3.id]);
  assert.equal(marcas.get(p1.id).nome, 'servidor');
  assert.deepEqual([marcas.get(p2.id).primeira, marcas.get(p2.id).ultima], [30, 50]);
  assert.deepEqual(marcas.get(p3.id), p3);
  assert.equal(await reenviarPendentes({}, new Map(), async () => ({ ok: true })), false);
});
