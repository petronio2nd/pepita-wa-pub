"""Servidor HTTP local que a extensao chama durante a exportacao.

    GET  /status      -> {"ok", "versao", "transcricao", "whisper", "whisper_erro",
                          "whisper_carregado", "cache", "cache_legado", "cache_arquivo",
                          "cache_manter_dias", "ocr_minimo"}
                         `transcricao`: {"motor", "modelo", "estado", "erro", "bloqueio",
                         "volta_em", "reserva"}; `whisper*` sao campos legados do detector de fala; sem ASR local.
    POST /transcrever -> corpo = arquivo de audio; {"texto", "cache", "motor"}. Quem
                         transcreve: `transcricao.py` (Gemini pelo agy, com VAD local).
    POST /ocr         -> corpo = arquivo de imagem; {"texto", "cache", "motor"} e, se o texto
                         lido ficou abaixo de `ocr.minimo_caracteres`, {"texto": "", "curto"}
    GET  /marcas      -> {"conversas": {id: marca}, "rodada": ultima rodada ou null}: ate
                         onde o "Exportar todas" ja foi em cada conversa (`marcas.py`)
    POST /marca       -> corpo JSON {"id", "nome", "pasta", "primeira", "ultima",
                         "completaAntes"}; junta ao trecho que ja estava e devolve a marca
    POST /rodada      -> corpo JSON com o resumo da rodada; {"ok": true}
    GET  /arquivo?caminho=C          -> {"existe", "tamanho"} de C dentro de `[exportar] raiz`
    POST /gravar?caminho=C&lote=L[&deslocamento=N&fim=0|1&livre=0|1]
                      -> corpo = bytes; grava em C dentro da raiz (`gravacao.py`), em
                         partes de ate 16 MB; {"caminho" (o nome final), "tamanho", "fim"}
    POST /lote        -> corpo JSON {"lote", "desfazer"}: desfaz ou esquece um lote
    GET  /rotina      -> rodada automatica: {"ligada", "intervalo_min", "proxima",
                         "pendente", "ultima", "extensao_vista"} (`rotina.py`)
    POST /rotina      -> corpo JSON {"ligada"}: liga ou desliga
    POST /rotina/vez  -> a extensao pergunta a cada minuto; {"rodar", "id", "abriuChrome"}
    POST /rotina/fim  -> corpo JSON {"resultado", "detalhe"}: como a rodada terminou
    GET  /rodada      -> {"rodada": ultima rodada ou null}: so a linha do painel, sem as marcas
    POST /agente/pedir    -> so o `wa mcp`: sem `Origin` e com `X-Wacervo-Token`; corpo JSON
                             {"op", "args"}; espera a extensao e devolve {"ok", "dados"} ou
                             {"ok": false, "erro", "tipo"} (`agente.py`)
    POST /agente/proximo  -> so a extensao do `[servidor] extensao_id` (vazio = agente
                             desligado): long-poll de ate 20 s;
                             {"pedido": {"id", "op", "args", "executar_ate"} ou null}
    POST /agente/aceite   -> so a extensao: {"id"} -> {"aceita"}; so executa com aceita true
    POST /agente/resposta -> so a extensao: corpo JSON {"id", "ok", "dados"|"erro", "tipo"}

Erros: 500 e falha desta midia; 503, motor fora agora (cota, agy, distro): a mesma
midia pode dar certo depois. O Pepita conta com essa diferenca.

So escuta em 127.0.0.1. Uma pagina web qualquer nao consegue usa-lo: o `Host`
tem de ser o local (contra DNS rebinding) e, quando ha `Origin`, ele tem de ser
de extensao (`chrome-extension://`). Programa local sem `Origin` (curl, testes)
passa. Nao ha resposta de CORS: a extensao chama pelo service worker, que com
`host_permissions` dispensa CORS; pagina web cai no preflight.
"""

from __future__ import annotations

import hmac
import json
import sys
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs

from . import __version__
from .agente import OPERACOES, ErroDoAgente, FilaDoAgente, criar_token
from .cache import Cache, assinatura, sha256
from .config import Config, carregar
from .gravacao import PARTE_MAXIMA, ErroDeGravacao, Gravador
from .log import obter
from .marcas import Marcas
from .ocr import aplicar_minimo
from .rotina import Rotina
from .transcricao import Indisponivel, Resultado

log = obter(__name__)

TAMANHO_MAXIMO = 64 * 1024 * 1024  # 64 MB: audio longo cabe, video nao vem para ca.
# Pedido recusado com corpo ate este tamanho: le e descarta antes de responder.
DESCARTE_MAXIMO = 1024 * 1024
# Marca e resumo de rodada sao poucos campos.
JSON_MAXIMO = 64 * 1024
# Resposta do `ler` com dezenas de mensagens e transcricoes.
RESPOSTA_AGENTE_MAXIMA = 4 * 1024 * 1024

# bytes do arquivo -> texto, ou Resultado (texto, motor e a assinatura com que o
# texto entra no cache). Trocaveis nos testes.
Motor = Callable[[bytes], "str | Resultado"]


@dataclass
class Motores:
    transcrever: Motor
    ler_imagem: Motor
    nome_transcricao: str = "whisper"
    nome_ocr: str = "windows-ocr"
    # {"estado", "erro", "carregado"}. estado: em_espera | carregando | pronto |
    # processando | erro, como o servico da distro diz, ou fora quando ele nao
    # responde (ver Transcritor.situacao).
    situacao_whisper: Callable[[], dict] = lambda: {
        "estado": "em_espera", "erro": None, "carregado": False,
    }
    # O formato de TranscricaoGemini.situacao. None = so o whisper.
    situacao_transcricao: Callable[[], dict] | None = None
    # Assinaturas do cache onde procurar o audio, em ordem. None = a da config.
    assinaturas_audio: Callable[[], list[str]] | None = None


class _Aplicacao:
    def __init__(self, cfg: Config, cache: Cache, motores: Motores, marcas: Marcas,
                 gravador: Gravador, rotina: Rotina | None) -> None:
        self.cfg = cfg
        self.cache = cache
        self.motores = motores
        self.marcas = marcas
        self.gravador = gravador
        # None: sem rodada automatica (testes que nao precisam dela).
        self.rotina = rotina
        self.hosts_validos: set[str] = set()
        self.agente = FilaDoAgente()
        self.token_agente = criar_token(cfg.caminho_token_agente)
        # Chave do cache -> trabalho em andamento. A exportacao e a transcricao
        # na tela podem pedir o mesmo audio ao mesmo tempo: o segundo pedido
        # espera o resultado do primeiro em vez de rodar o motor de novo.
        self._em_andamento: dict[tuple[str, str, str], Future] = {}
        self._trava = threading.Lock()

    def origem_permitida(self, origem: str | None) -> bool:
        if origem is None:
            return True
        prefixo = "chrome-extension://"
        if not origem.startswith(prefixo):
            return False
        esperado = self.cfg.servidor.extensao_id
        return not esperado or origem[len(prefixo):].rstrip("/") == esperado

    def processar(self, tipo: str, dados: bytes) -> dict:
        resposta = self._processar(tipo, dados)
        if tipo == "imagem":
            resposta.update(aplicar_minimo(resposta["texto"], self.cfg.ocr.minimo_caracteres))
        return resposta

    def _assinaturas(self, tipo: str) -> list[str]:
        if tipo == "audio" and self.motores.assinaturas_audio is not None:
            return self.motores.assinaturas_audio()
        return [assinatura(self.cfg, tipo)]

    def _do_cache(self, sha: str, tipo: str) -> dict | None:
        for sig in self._assinaturas(tipo):
            achado = self.cache.obter_com_motor(sha, tipo, sig)
            if achado is not None:
                return {"texto": achado[0], "cache": True, "motor": achado[1]}
        return None

    def _processar(self, tipo: str, dados: bytes) -> dict:
        sha = sha256(dados)
        resposta = self._do_cache(sha, tipo)
        if resposta is not None:
            return resposta
        chave = (sha, tipo, assinatura(self.cfg, tipo))

        with self._trava:
            trabalho = self._em_andamento.get(chave)
            dono = trabalho is None
            if dono:
                trabalho = self._em_andamento[chave] = Future()
        if not dono:
            # Mesma midia ja no motor: espera o resultado dela (ou a falha dela).
            return {**trabalho.result(), "cache": True}

        try:
            resposta = self._rodar(chave, tipo, dados)
        except BaseException as erro:
            trabalho.set_exception(erro)
            raise
        else:
            trabalho.set_result(resposta)
        finally:
            # Sai do mapa tambem na falha: o proximo pedido tenta de novo.
            with self._trava:
                self._em_andamento.pop(chave, None)
        return resposta

    def _rodar(self, chave: tuple[str, str, str], tipo: str, dados: bytes) -> dict:
        # Outro pedido pode ter terminado entre a consulta e a reserva da chave.
        resposta = self._do_cache(chave[0], tipo)
        if resposta is not None:
            return resposta
        if tipo == "audio":
            motor, nome = self.motores.transcrever, self.motores.nome_transcricao
        else:
            motor, nome = self.motores.ler_imagem, self.motores.nome_ocr
        inicio = time.monotonic()
        saida = motor(dados)
        if isinstance(saida, Resultado):
            texto, nome, sig = saida.texto, saida.motor, saida.assinatura or chave[2]
        else:
            texto, sig = saida, chave[2]
        log.info(
            "%s de %d KB processado em %.1f s por %s (%d caracteres)",
            tipo, len(dados) // 1024, time.monotonic() - inicio, nome, len(texto),
        )
        self.cache.guardar(chave[0], tipo, sig, texto, nome)
        return {"texto": texto, "cache": False, "motor": nome}


class _Manipulador(BaseHTTPRequestHandler):
    server: "ServidorLocal"
    protocol_version = "HTTP/1.1"

    def log_message(self, formato, *args):  # o http.server fala demais.
        log.debug("%s " + formato, self.address_string(), *args)

    def _responder(self, codigo: int, corpo: dict, fechar: bool = False) -> None:
        """`fechar`: a resposta sai sem ler o corpo do pedido. Com HTTP/1.1 a
        conexao seria reaproveitada e o corpo nao lido viraria o proximo pedido;
        por isso ela fecha."""
        dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(dados)))
        if fechar:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(dados)

    def _recusar(self, codigo: int, corpo: dict) -> None:
        """Responde sem usar o corpo do pedido. Fechar a conexao com o corpo ainda no
        buffer faz o Windows mandar RST, e o cliente perde a resposta (WinError 10053):
        corpo de ate 1 MB e lido e jogado fora antes, e a conexao segue aberta. Maior
        que isso, chunked ou corpo que nao chega inteiro, fecha."""
        try:
            tamanho = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            tamanho = -1
        if self.headers.get("Transfer-Encoding") or not 0 <= tamanho <= DESCARTE_MAXIMO:
            self._responder(codigo, corpo, fechar=True)
            return
        if tamanho and len(self.rfile.read(tamanho)) != tamanho:
            self._responder(codigo, corpo, fechar=True)
            return
        self._responder(codigo, corpo)

    def _autorizado(self) -> bool:
        app = self.server.app
        if self.headers.get("Host", "") not in app.hosts_validos:
            self._recusar(403, {"erro": "host nao permitido"})
            return False
        if not app.origem_permitida(self.headers.get("Origin")):
            self._recusar(403, {"erro": "origem nao permitida"})
            return False
        return True

    def do_OPTIONS(self):  # noqa: N802 - nome exigido pelo http.server.
        self._recusar(403, {"erro": "sem CORS"})

    def _consulta(self, rota: str) -> dict[str, str] | None:
        """Parametros de `rota?a=1&b=2`, ou None se o caminho e outro. So /arquivo e
        /gravar levam consulta; as outras rotas so casam com o caminho exato."""
        caminho, _, consulta = self.path.partition("?")
        if caminho != rota:
            return None
        return {k: v[-1] for k, v in parse_qs(consulta, keep_blank_values=True).items()}

    def do_GET(self):  # noqa: N802
        if not self._autorizado():
            return
        app = self.server.app
        if self.path == "/marcas":
            self._responder(200, {"conversas": app.marcas.todas(), "rodada": app.marcas.ultima_rodada()})
            return
        if self.path == "/rotina":
            if app.rotina is None:
                self._recusar(404, {"erro": "rota desconhecida"})
                return
            self._responder(200, app.rotina.situacao())
            return
        consulta = self._consulta("/arquivo")
        if consulta is not None:
            try:
                self._responder(200, app.gravador.existe(consulta.get("caminho", "")))
            except ErroDeGravacao as erro:
                self._responder(erro.codigo, {"erro": str(erro)})
            return
        if self.path == "/rodada":
            self._responder(200, {"rodada": app.marcas.ultima_rodada()})
            return
        if self.path != "/status":
            self._recusar(404, {"erro": "rota desconhecida"})
            return
        whisper = app.motores.situacao_whisper()
        transcricao = app.motores.situacao_transcricao
        self._responder(200, {
            "ok": True,
            "versao": __version__,
            "transcricao": transcricao() if transcricao else {
                "motor": "whisper", "modelo": app.motores.nome_transcricao,
                "estado": whisper["estado"], "erro": whisper["erro"],
                "bloqueio": whisper["erro"], "volta_em": None, "reserva": False,
            },
            "whisper": whisper["estado"],
            "whisper_erro": whisper["erro"],
            "whisper_carregado": whisper["carregado"],
            "cache": app.cache.contagem(),
            "cache_legado": app.cache.contagem_legado(),
            "cache_arquivo": str(app.cfg.caminho_cache),
            "cache_manter_dias": app.cfg.cache.manter_dias,
            "ocr_minimo": app.cfg.ocr.minimo_caracteres,
            # 0.16.0: quem grava o "Exportar todas" e o servidor, nesta raiz.
            "exportar": app.gravador.situacao(),
            "rotina": app.rotina is not None,
            # 0.19.0: a extensao esta atendendo o `wa mcp`?
            "agente": app.agente.situacao(),
            # 0.21.0: grupos escolhidos pelo usuario; a extensao exporta so estes (ids, sem nome).
            # Relido do config.toml a cada /status: editar a lista vale na proxima rodada, sem reiniciar.
            # Config ilegivel = nenhum grupo (na duvida, fora).
            "grupos": _grupos_do_config(app.cfg),
        })

    def do_POST(self):  # noqa: N802
        if not self._autorizado():
            return
        if self.path.startswith("/agente/"):
            self._agente()
            return
        if self.path in ("/marca", "/rodada", "/lote", "/rotina", "/rotina/vez", "/rotina/fim"):
            self._post_json()
            return
        consulta = self._consulta("/gravar")
        if consulta is not None:
            self._gravar(consulta)
            return
        tipo = {"/transcrever": "audio", "/ocr": "imagem"}.get(self.path)
        if tipo is None:
            self._recusar(404, {"erro": "rota desconhecida"})
            return
        try:
            tamanho = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            tamanho = -1
        if tamanho == 0:
            self._responder(400, {"erro": "corpo vazio"})
            return
        if tamanho < 0:
            self._responder(400, {"erro": "Content-Length invalido"}, fechar=True)
            return
        if tamanho > TAMANHO_MAXIMO:
            self._responder(413, {"erro": "arquivo grande demais"}, fechar=True)
            return
        dados = self.rfile.read(tamanho)
        if len(dados) != tamanho:
            # Envio interrompido no meio: o fundo.js aborta os fetches quando o
            # usuario cancela uma exportacao. Sem isto o pedaco que chegou iria para
            # o motor, que carrega o modelo na GPU so para falhar no arquivo
            # quebrado.
            log.debug("corpo incompleto: %d de %d bytes", len(dados), tamanho)
            self._responder(400, {"erro": "corpo incompleto"}, fechar=True)
            return
        try:
            resposta = self.server.app.processar(tipo, dados)
        except Indisponivel as erro:
            log.error("motor fora processando %s: %s", tipo, erro)
            self._responder(503, {"erro": str(erro)})
            return
        except Exception as erro:  # noqa: BLE001 - vira "[falha ...]" na extensao.
            log.error("falha processando %s: %s", tipo, erro)
            self._responder(500, {"erro": str(erro) or erro.__class__.__name__})
            return
        self._responder(200, resposta)


    def _post_json(self) -> None:
        try:
            tamanho = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            tamanho = -1
        if not 0 < tamanho <= JSON_MAXIMO:
            self._recusar(400 if tamanho <= 0 else 413, {"erro": "corpo JSON vazio ou grande demais"})
            return
        dados = self.rfile.read(tamanho)
        if len(dados) != tamanho:
            self._responder(400, {"erro": "corpo incompleto"}, fechar=True)
            return
        try:
            corpo = json.loads(dados)
            if not isinstance(corpo, dict):
                raise ValueError("esperava um objeto")
            app = self.server.app
            marcas = app.marcas
            if self.path == "/marca":
                resposta = marcas.registrar(corpo)
            elif self.path == "/rodada":
                marcas.registrar_rodada(corpo)
                resposta = {"ok": True}
            elif self.path == "/lote":
                resposta = app.gravador.encerrar_lote(corpo.get("lote"), bool(corpo.get("desfazer")))
            elif app.rotina is None:
                self._responder(404, {"erro": "rota desconhecida"})
                return
            elif self.path == "/rotina":
                if not isinstance(corpo.get("ligada"), bool):
                    raise ValueError("ligada precisa ser true ou false")
                resposta = app.rotina.ligar(corpo["ligada"])
            elif self.path == "/rotina/vez":
                resposta = app.rotina.vez()
            else:
                resposta = app.rotina.fim(corpo)
        except ErroDeGravacao as erro:
            self._responder(erro.codigo, {"erro": str(erro)})
            return
        except ValueError as erro:  # JSONDecodeError e ValueError
            self._responder(400, {"erro": str(erro)})
            return
        self._responder(200, resposta)

    def _ler_corpo_json(self, maximo: int) -> dict | None:
        """Le o corpo JSON (objeto) ou responde o erro e devolve None."""
        try:
            tamanho = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            tamanho = -1
        if self.headers.get("Transfer-Encoding") or not 0 < tamanho <= maximo:
            self._recusar(400 if tamanho <= maximo else 413, {"erro": "corpo JSON vazio ou grande demais"})
            return None
        dados = self.rfile.read(tamanho)
        if len(dados) != tamanho:
            self._responder(400, {"erro": "corpo incompleto"}, fechar=True)
            return None
        try:
            corpo = json.loads(dados)
        except ValueError as erro:
            self._responder(400, {"erro": str(erro)})
            return None
        if not isinstance(corpo, dict):
            self._responder(400, {"erro": "esperava um objeto"})
            return None
        return corpo

    def _agente(self) -> None:
        """Ponte do agente (agente.py). A rota do MCP e mais estrita que o resto: nenhuma
        `Origin` (so programa local) e o token de dados/agente.token, porque ela manda
        mensagem. As da extensao exigem a `Origin` de extensao (o `_autorizado` ja a confere)."""
        app = self.server.app
        origem = self.headers.get("Origin")
        if self.path == "/agente/pedir":
            token = self.headers.get("X-Wacervo-Token", "")
            if origem is not None or not hmac.compare_digest(token.encode(), app.token_agente.encode()):
                self._recusar(403, {"erro": "so o wa mcp, com o token"})
                return
            corpo = self._ler_corpo_json(JSON_MAXIMO)
            if corpo is None:
                return
            op, args = corpo.get("op"), corpo.get("args") or {}
            if op not in OPERACOES or not isinstance(args, dict):
                self._responder(400, {"erro": "op ou args invalidos"})
                return
            try:
                resposta = app.agente.pedir(op, args)
            except ErroDoAgente as erro:
                self._responder(200, {"ok": False, "erro": str(erro), "tipo": erro.tipo})
                return
            self._responder(200, resposta)
            return
        if origem is None:
            self._recusar(403, {"erro": "so a extensao"})
            return
        # Quem pega o pedido le conversa e envia mensagem: so a extensao com o id do
        # config.toml. Com `[servidor] extensao_id` vazio, o agente fica desligado
        # (qualquer extensao com acesso a 127.0.0.1 poderia se passar pelo pepita-wa).
        if not app.cfg.servidor.extensao_id:
            self._recusar(403, {"erro": "agente desligado: preencha [servidor] extensao_id no config.toml"})
            return
        if self.path == "/agente/proximo":
            corpo = self._ler_corpo_json(JSON_MAXIMO)
            if corpo is None:
                return
            try:
                espera = float(corpo.get("espera_s", 20))
            except (TypeError, ValueError):
                espera = 20.0
            self._responder(200, {"pedido": app.agente.proximo(espera)})
            return
        if self.path == "/agente/aceite":
            corpo = self._ler_corpo_json(JSON_MAXIMO)
            if corpo is None:
                return
            id_ = corpo.get("id")
            self._responder(200, {"aceita": isinstance(id_, str) and app.agente.aceitar(id_)})
            return
        if self.path == "/agente/resposta":
            corpo = self._ler_corpo_json(RESPOSTA_AGENTE_MAXIMA)
            if corpo is None:
                return
            id_ = corpo.pop("id", None)
            aceita = isinstance(id_, str) and app.agente.responder(id_, corpo)
            self._responder(200, {"aceita": aceita})
            return
        self._recusar(404, {"erro": "rota desconhecida"})

    def _gravar(self, consulta: dict[str, str]) -> None:
        if self.headers.get("Transfer-Encoding"):
            self._responder(411, {"erro": "mande com Content-Length"}, fechar=True)
            return
        try:
            tamanho = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            tamanho = -1
        if tamanho < 0:
            self._responder(400, {"erro": "Content-Length invalido"}, fechar=True)
            return
        if tamanho > PARTE_MAXIMA:
            self._responder(413, {"erro": "parte grande demais"}, fechar=True)
            return
        dados = self.rfile.read(tamanho) if tamanho else b""
        if len(dados) != tamanho:
            self._responder(400, {"erro": "corpo incompleto"}, fechar=True)
            return
        try:
            deslocamento = int(consulta.get("deslocamento", "0") or "0")
            if deslocamento < 0:
                raise ValueError
        except ValueError:
            self._responder(400, {"erro": "deslocamento invalido"})
            return
        try:
            resposta = self.server.app.gravador.gravar(
                consulta.get("caminho", ""), dados, lote=consulta.get("lote", ""),
                deslocamento=deslocamento, fim=consulta.get("fim", "1") != "0",
                livre=consulta.get("livre", "0") == "1",
            )
        except ErroDeGravacao as erro:
            self._responder(erro.codigo, {"erro": str(erro)})
            return
        except OSError as erro:
            log.error("nao gravou na pasta do Exportar todas: %s", erro)
            self._responder(500, {"erro": f"nao gravou na pasta ({erro.strerror or erro})"})
            return
        self._responder(200, resposta)


def _grupos_do_config(cfg) -> list[str]:
    try:
        return list(carregar(raiz=cfg.raiz).grupos.ids)
    except Exception:  # noqa: BLE001
        return []


class ServidorLocal(ThreadingHTTPServer):
    daemon_threads = True

    # Quem desiste no meio derruba a conexao: o fundo.js aborta os fetches ao
    # cancelar uma exportacao, e o navegador fecha conexao ociosa quando quer.
    # Sem isto o http.server despeja um traceback no console a cada vez.
    _DESISTIU = (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)

    def handle_error(self, request, client_address) -> None:
        erro = sys.exception()
        if isinstance(erro, self._DESISTIU):
            log.debug("conexao encerrada pelo outro lado: %s", erro.__class__.__name__)
            return
        super().handle_error(request, client_address)

    def __init__(self, cfg: Config, cache: Cache, motores: Motores, porta: int | None = None,
                 marcas: Marcas | None = None, gravador: Gravador | None = None,
                 rotina: Rotina | None = None):
        self.app = _Aplicacao(cfg, cache, motores, marcas or Marcas(cfg.caminho_marcas),
                              gravador or Gravador(Path(cfg.exportar.raiz)), rotina)
        super().__init__(("127.0.0.1", cfg.servidor.porta if porta is None else porta), _Manipulador)
        # Porta 0 (testes) so vira numero depois do bind.
        self.app.hosts_validos = {f"127.0.0.1:{self.porta}", f"localhost:{self.porta}"}

    def server_close(self) -> None:
        super().server_close()
        if self.app.rotina:
            self.app.rotina.parar()
        self.app.marcas.fechar()

    @property
    def porta(self) -> int:
        return self.server_address[1]

    def em_segundo_plano(self) -> threading.Thread:
        t = threading.Thread(target=self.serve_forever, name="servidor", daemon=True)
        t.start()
        return t
