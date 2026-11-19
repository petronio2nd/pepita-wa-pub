# Carga historica do iPhone

`python -m wacervo.iphone ChatStorage.sqlite --destino RAIZ --marcas MARCAS`
simula sem alterar a raiz nem a base real das marcas. `--sim` aplica.
O banco de entrada e aberto somente para leitura. Testar primeiro em copia no -lab.

Todos os grupos e conversas arquivadas entram na carga historica (decisao do usuario,
18/11/2026). Isso nao altera a lista dos grupos liberados para MCP/exportacao ao vivo.
Mensagens sem sessao sao contadas como orfas. Status e broadcast ficam fora; canal
com mensagens pode ser preservado no historico, mas nao entra na exportacao ao vivo.

Conversas casam somente por JID ou identificador alternativo do banco, incluindo
o alias de telefone `s.whatsapp.net`/`c.us`. Nome igual nunca une duas conversas.
Se o mesmo ID ja aparece em duas pastas, a marca temporal precisa escolher uma;
a comparacao le as duas e preserva os arquivos de ambas. Ambiguidade sem marca para.

O legado nao tem IDs por mensagem. Nele a comparacao usa dia/minuto, direcao,
autor em grupos e texto/rotulo/legenda, com multiplicidade: dois textos iguais nao
viram um. Por isso essa comparacao do legado nao e prova por ID. Midia sem legenda
so tem tipo, minuto e autor disponiveis; nao se recuperam anexos nem transcricoes.
Tipos desconhecidos do banco sao conservados pelo numero, sem interpretar o evento.
O fuso do cabecalho legado e convertido para Sao Paulo. Nome diferente em grupo
so pode casar se minuto/direcao/corpo tiverem um unico candidato em cada lado.
Eventos de sistema em italico comparam minuto e multiplicidade separadamente.

Cada arquivo novo tem nome por hash e uma referencia curta ao lote. Nao sobrescreve
arquivo existente. Antes de gravar, prepara nas marcas o recibo de IDs e hash,
sem libera-lo para a extensao. Se cair depois de gravar e antes de concluir o recibo,
a proxima carga verifica o hash e conclui o recibo pendente. A base das marcas guarda somente
IDs, caminhos e hashes, nunca o texto. As marcas temporais existentes nao mudam.
`GET /marcas` ganha so as pastas de `importadas`; `GET /importadas?id=...` entrega
os IDs de uma conversa quando ela vai ser exportada. A extensao filtra esses IDs,
mas calcula a cobertura sobre a coleta inteira. Assim o backup nao inventa uma
cobertura continua de datas nem faz pular mensagens que so existem na Web.
Quando o Web lista um LID ausente no banco, o telefone de infoChat pode localizar
o recibo do mesmo telefone. Nenhum nome e usado como identidade.

Antes de aplicar na raiz real, atualizar o servidor, recarregar a extensao e dar
F5 no WhatsApp Web. PARAR o servidor durante a carga e religar depois. O comando
com --sim reserva a porta configurada e recusa enquanto o servidor estiver no ar.
Se o servidor foi iniciado com --porta, informar a mesma --porta ao importador.
Extensao antiga desconhece os recibos e pode
repetir mensagens do backup na proxima exportacao.
Depois de haver recibos, GET /marcas recusa versao ausente/diferente da extensao.
POST /gravar e /marca tambem recusam cliente antigo pelo X-Pepita-WA-Versao,
inclusive se a rodada ja tinha lido as marcas antes de o servidor parar.
A versao vem da aba (ponte.js), nao do service worker: F5 continua obrigatorio.

Datas usam America/Sao_Paulo, incluindo horario de verao historico. Em Windows
sem tzdata, usa o TZif publico incluido em `wacervo/fusos`.
