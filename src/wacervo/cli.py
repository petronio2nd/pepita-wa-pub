"""Linha de comando do wacervo: `wa <subcomando>`."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__
from .cache import Cache, assinatura, sha256
from .config import Config, ErroDeConfig, carregar
from .log import configurar, obter

log = obter(__name__)


def _construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wa",
        description="Transcricao e OCR locais para a extensao pepita-wa do WhatsApp Web.",
    )
    parser.add_argument("--versao", action="version", version=f"pepita-wa {__version__}")
    parser.add_argument("-v", "--verboso", action="store_true", help="log detalhado")
    sub = parser.add_subparsers(dest="comando", required=True)

    p_servir = sub.add_parser(
        "servir", help="liga o servidor local que a extensao usa (Ctrl+C encerra)"
    )
    p_servir.add_argument("--porta", type=int, help="padrao: [servidor] porta, ou 8765")
    p_servir.add_argument(
        "--sem-aquecer",
        action="store_true",
        help="compatibilidade: nao ha modelo ASR local para aquecer",
    )
    sub.add_parser("status", help="servidor no ar?, cache e idiomas do OCR")
    sub.add_parser("mcp", help="servidor MCP (stdio) do WhatsApp para o agente; precisa do wa servir no ar")
    sub.add_parser("grupos", help="lista todos os grupos do WhatsApp com o id, para escolher em [grupos] ids "
                                  "do config.toml (precisa do wa servir e do WhatsApp Web abertos)")
    p_transc = sub.add_parser("transcrever", help="transcreve arquivos de audio aqui mesmo")
    p_transc.add_argument("arquivos", nargs="+", type=Path)
    p_ocr = sub.add_parser("ocr", help="le o texto de imagens aqui mesmo")
    p_ocr.add_argument("arquivos", nargs="+", type=Path)
    p_limpar = sub.add_parser(
        "limpar-cache",
        help="apaga textos guardados no cache (sem --sim, so mostra quantos seriam)",
    )
    p_limpar.add_argument(
        "--so-legado", action="store_true",
        help="so os gravados antes da assinatura de configuracao (nao sao mais usados)",
    )
    p_limpar.add_argument(
        "--mais-de-dias", type=int, metavar="N", help="so os gravados ha mais de N dias"
    )
    p_limpar.add_argument("--sim", action="store_true", help="apaga de verdade")
    p_migrar = sub.add_parser(
        "migrar",
        help="leva um lote antigo do Exportar todas para a pasta nova e marca (sem --sim, so mostra)",
    )
    p_migrar.add_argument("origem", type=Path, help="pasta do lote antigo")
    p_migrar.add_argument("--destino", type=Path, default=None,
                          help="padrao: [exportar] raiz (C:/WHATSAPP)")
    p_migrar.add_argument("--sim", action="store_true", help="copia e marca de verdade")
    p_nomes = sub.add_parser("nomes", help="pastas de numero que ja tem nome no WhatsApp (sem --sim, so previa)")
    p_nomes.add_argument("--sim", action="store_true", help="renomeia as pastas e troca a pasta nas marcas")
    p_nomes.add_argument("--destino", type=Path, default=None, help="padrao: [exportar] raiz (C:/WHATSAPP)")
    return parser


def _transcricao(cfg: Config):
    """Gemini como unico ASR e o detector de fala: o da distro ou o local (`vad`)."""
    from .transcricao import TranscricaoGemini

    detector = _detector(cfg)
    return TranscricaoGemini(cfg, detector), detector


def _detector(cfg: Config):
    """O detector de fala que `[transcricao] vad` escolhe."""
    if cfg.transcricao.vad == "local":
        from .vad_local import VadLocal

        return VadLocal(cfg)
    from .transcricao import Transcritor

    return Transcritor(cfg)


def _motores(cfg: Config):
    from .ocr import LeitorDeTexto
    from .servidor import Motores

    motor, distro = _transcricao(cfg)
    motores = Motores(
        transcrever=motor,
        ler_imagem=LeitorDeTexto(cfg),
        nome_transcricao=motor.nome,
        situacao_whisper=distro.situacao,
        situacao_transcricao=motor.situacao,
        assinaturas_audio=motor.assinaturas,
    )
    return motor, motores


def _expirar_cache(cache: Cache, dias: int, intervalo_s: float = 24 * 3600) -> None:
    """`[cache] manter_dias`: apaga o que passou do prazo ao ligar e uma vez por dia."""
    while True:
        try:
            apagados = cache.limpar(dias=dias)
            if apagados:
                log.info("cache: %d resultado(s) com mais de %d dias apagados", apagados, dias)
        except Exception as erro:  # noqa: BLE001 - tenta de novo amanha.
            log.error("nao consegui expirar o cache: %s", erro)
        time.sleep(intervalo_s)


def _limpar_agy(dias: int, intervalo_s: float = 24 * 3600) -> None:
    """`[transcricao.gemini] guardar_agy_dias`: ao ligar e uma vez por dia."""
    from .transcricao import limpar_conversas_agy

    while True:
        try:
            apagadas = limpar_conversas_agy(dias)
            if apagadas:
                log.info("agy: %d conversa(s) do wa com mais de %d dias apagadas", apagadas, dias)
        except Exception as erro:  # noqa: BLE001 - tenta de novo amanha.
            log.error("nao consegui limpar as conversas do agy: %s", erro)
        time.sleep(intervalo_s)


def _como_encerrar() -> str:
    """Como parar o servidor, do jeito que serve a quem esta lendo.

    Rodando escondido (`ferramentas/servir-oculto.vbs`), a saida vai para
    `dados/servidor.log` e nao ha console para receber Ctrl+C."""
    if getattr(sys.stderr, "isatty", lambda: False)():
        return "Ctrl+C"
    return "ferramentas\\parar-servidor.cmd"


def _servir(cfg: Config, args) -> int:
    from .rotina import Rotina
    from .servidor import ServidorLocal

    transcritor, motores = _motores(cfg)
    cache = Cache(cfg.caminho_cache)
    rotina = Rotina(cfg.rotina, cfg.caminho_rotina)
    try:
        servidor = ServidorLocal(cfg, cache, motores, porta=args.porta, rotina=rotina)
    except OSError as erro:
        log.error("nao consegui abrir a porta %s: %s", args.porta or cfg.servidor.porta, erro)
        return 1
    rotina.em_segundo_plano()
    log.info(
        "Exportar todas grava em %s%s; rodada automatica %s (a cada %d min)",
        cfg.exportar.raiz, "" if Path(cfg.exportar.raiz).is_dir() else " (a pasta nao existe!)",
        "ligada" if rotina.ligada else "desligada", cfg.rotina.intervalo_min,
    )

    # So confere o agy; nao ha modelo ASR local para aquecer.
    try:
        transcritor.aquecer()
        log.info("transcricao pelo Gemini (%s) via agy, sem reserva", cfg.transcricao.gemini.modelo)
    except Exception as erro:  # noqa: BLE001
        log.error("transcricao pelo Gemini: %s", erro)

    if cfg.cache.manter_dias:
        threading.Thread(
            target=_expirar_cache, args=(cache, cfg.cache.manter_dias), name="expirar", daemon=True
        ).start()
    if cfg.transcricao.gemini.guardar_agy_dias:
        threading.Thread(
            target=_limpar_agy, args=(cfg.transcricao.gemini.guardar_agy_dias,),
            name="limpar-agy", daemon=True,
        ).start()

    log.info("servindo em http://127.0.0.1:%d  (%s encerra)", servidor.porta, _como_encerrar())
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        log.info("encerrando")
    finally:
        servidor.server_close()
        cache.fechar()
    return 0


def _grupos(cfg: Config) -> int:
    from .agente import ler_token
    from .mcp_whatsapp import cliente

    r = cliente(cfg.servidor.porta, lambda: ler_token(cfg.caminho_token_agente))("grupos", {})
    if not r.get("ok"):
        print(f"erro: {r.get('erro') or r.get('tipo')}", file=sys.stderr)
        return 1
    grupos = sorted((r.get("dados") or {}).get("grupos") or [], key=lambda g: (g.get("nome") or "").lower())
    liberados = set(cfg.grupos.ids)
    print(f"{len(grupos)} grupo(s). [x] = ja liberado em [grupos] ids do config.toml.")
    print()
    for g in grupos:
        print(f"[{'x' if g['id'] in liberados else ' '}] {g.get('nome') or '(sem nome)'}")
        print(f"      {g['id']}")
    sumiu = sorted(liberados - {g["id"] for g in grupos})
    if sumiu:
        print()
        print("Liberados que o WhatsApp nao listou agora: " + ", ".join(sumiu))
    return 0


def _status(cfg: Config) -> int:
    url = f"http://127.0.0.1:{cfg.servidor.porta}/status"
    dados: dict = {}
    try:
        pedido = urllib.request.Request(url, headers={"X-Pepita-WA": "1"})  # SEG-10
        with urllib.request.urlopen(pedido, timeout=3) as resp:
            dados = json.load(resp)
        t = dados.get("transcricao") or {}
        estado = t.get("estado") or dados.get("whisper") or "em_espera"
        print(f"servidor:  no ar em {url} ({t.get('motor', 'whisper')} {estado.replace('_', ' ')})")
    except (urllib.error.URLError, OSError):
        print(f"servidor:  fora do ar ({url}) -- rode ferramentas\\servir-oculto.vbs")

    cache = Cache(cfg.caminho_cache)
    contagem = cache.contagem()
    legado = cache.contagem_legado()
    cache.fechar()
    extra = f", mais {legado} de antes da assinatura (limpe com `wa limpar-cache --so-legado`)" if legado else ""
    print(
        f"cache:     {contagem.get('audio', 0)} audio(s), {contagem.get('imagem', 0)} imagem(ns)"
        f" em {cfg.caminho_cache}{extra}"
    )
    dias = cfg.cache.manter_dias
    print(
        f"retencao:  {f'{dias} dias ([cache] manter_dias)' if dias else 'ate `wa limpar-cache`'}"
    )

    try:
        from .ocr import idiomas_disponiveis

        idiomas = idiomas_disponiveis()
        marca = "ok" if cfg.ocr.idioma in idiomas else f"FALTA {cfg.ocr.idioma}"
        print(f"ocr:       {', '.join(idiomas) or 'nenhum idioma'} ({marca})")
    except Exception as erro:  # noqa: BLE001
        print(f"ocr:       indisponivel ({erro})")

    t = cfg.transcricao
    from .transcricao import caminho_agy

    if t.motor == "gemini":
        agy = caminho_agy(cfg)
        print(f"gemini:    {t.gemini.modelo} pelo agy ({agy or 'agy NAO ENCONTRADO'}),"
              f" ate {t.gemini.paralelo} ao mesmo tempo, {len(t.gemini.termos)} termo(s)")
        guarda = t.gemini.guardar_agy_dias
        print(f"agy:       conversas do wa {f'apagadas depois de {guarda} dias' if guarda else 'guardadas'}"
              f" ([transcricao.gemini] guardar_agy_dias)")
    situacao = _detector(cfg).situacao()
    if t.vad == "local":
        estado = situacao["erro"] if situacao["estado"] == "fora" else "pronto"
        print(f"vad:       Silero na CPU dentro do wa servir (vad = \"local\"): {estado}")
    else:
        if situacao["estado"] == "fora":
            print(f"distro:    {situacao['erro']}")
        else:
            erro = f": {situacao['erro']}" if situacao["erro"] else ""
            print(f"distro:    servico no ar em {t.servico} ({situacao['estado'].replace('_', ' ')}{erro})")
        print("vad:       Silero na CPU da distro")
    print("            obrigatorio antes do Gemini; sem ASR de reserva")
    agente = dados.get("agente")
    if agente is not None:
        vista = agente.get("extensao_vista_ha_s")
        print(f"agente:    extensao {'conectada' if agente.get('conectada') else 'desconectada'}"
              f"{f' (vista ha {vista:.0f} s)' if vista is not None else ' (nunca vista)'}; wa mcp usa"
              f" {cfg.caminho_token_agente}")
    return 0


def _local(cfg: Config, args, tipo: str) -> int:
    if tipo == "audio":
        motor, _ = _transcricao(cfg)
    else:
        from .ocr import LeitorDeTexto

        motor = LeitorDeTexto(cfg)
    cache = Cache(cfg.caminho_cache)
    codigo = 0
    for caminho in args.arquivos:
        try:
            dados = caminho.read_bytes()
        except OSError as erro:
            log.error("%s: %s", caminho, erro)
            codigo = 1
            continue
        # Mesma chave do servidor: o que um processa, o outro reaproveita.
        sha = sha256(dados)
        assinaturas = motor.assinaturas() if hasattr(motor, "assinaturas") else [assinatura(cfg, tipo)]
        texto = next((t for t in (cache.obter(sha, tipo, a) for a in assinaturas) if t is not None), None)
        if texto is None:
            from .transcricao import Resultado

            try:
                saida = motor(dados)
            except Exception as erro:  # noqa: BLE001
                log.error("%s: %s", caminho.name, erro)
                codigo = 1
                continue
            if isinstance(saida, Resultado):
                texto = saida.texto
                cache.guardar(sha, tipo, saida.assinatura or assinatura(cfg, tipo), texto, saida.motor)
            else:
                texto = saida
                cache.guardar(sha, tipo, assinatura(cfg, tipo), texto, "cli")
        if len(args.arquivos) > 1:
            print(f"== {caminho.name}")
        if tipo == "imagem":
            from .ocr import aplicar_minimo

            r = aplicar_minimo(texto, cfg.ocr.minimo_caracteres)
            if "curto" in r:
                print(f"(texto curto, abaixo de {cfg.ocr.minimo_caracteres} caracteres: {r['curto']})")
                continue
            texto = r["texto"]
        print(texto or "(sem texto)")
    cache.fechar()
    return codigo


def _limpar_cache(cfg: Config, args) -> int:
    if args.mais_de_dias is not None and args.mais_de_dias < 0:
        log.error("--mais-de-dias precisa ser 0 ou mais")
        return 2
    if not cfg.caminho_cache.exists():
        print("cache vazio: nada a apagar")
        return 0
    cache = Cache(cfg.caminho_cache)
    try:
        if not args.sim:
            n = cache.contar(args.so_legado, args.mais_de_dias)
            print(f"{n} resultado(s) seriam apagados de {cfg.caminho_cache}. Repita com --sim.")
            return 0
        n = cache.limpar(args.so_legado, args.mais_de_dias)
        print(f"{n} resultado(s) apagados")
    finally:
        cache.fechar()
    return 0


def _migrar(cfg: Config, args) -> int:
    from .marcas import Marcas
    from .migrar import aplicar, planejar

    args.destino = args.destino or Path(cfg.exportar.raiz)
    if not args.origem.is_dir():
        log.error("%s nao e uma pasta", args.origem)
        return 2
    if args.destino.resolve() == args.origem.resolve():
        log.error("origem e destino sao a mesma pasta")
        return 2
    marcas = Marcas(cfg.caminho_marcas)
    try:
        conversas, problemas = planejar(args.origem, marcas.todas())
        for p in problemas:
            print(f"fica de fora: {p}")
        if not args.sim:
            print(f"{len(conversas)} conversa(s) iriam para {args.destino}, cada uma na pasta do contato. "
                  "Repita com --sim.")
            return 0
        r = aplicar(conversas, args.destino, marcas)
        print(f"{r['conversas']} conversa(s) marcadas, {r['copiados']} arquivo(s) copiados para {args.destino}")
    finally:
        marcas.fechar()
    return 0


def _nomes(cfg: Config, args) -> int:
    from .marcas import Marcas
    from .nomes import aplicar, copia_das_marcas, propor

    args.destino = args.destino or Path(cfg.exportar.raiz)
    marcas = Marcas(cfg.caminho_marcas)
    try:
        linhas = propor(marcas.todas())
        for x in linhas:
            print(f"{x['pasta']}  ->  {x['nova']}  ({x['nome']})" + ("  [homonimo]" if x.get("colisao") else ""))
        if not args.sim:
            print(f"{len(linhas)} pasta(s) de numero com nome. Nada foi renomeado (use --sim).")
            return 0
        print("copia das marcas:", copia_das_marcas(cfg.caminho_marcas))
        r = aplicar(linhas, args.destino, marcas)
        print(f"{r['renomeadas']} pasta(s) renomeadas, {r['arquivos']} .md renomeados, "
              f"{r['sem_pasta']} sem pasta no disco, {r['ocupadas']} puladas (nome novo ja existe)")
    finally:
        marcas.fechar()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _construir_parser().parse_args(argv)
    # No Windows, saida por pipe sai em cp1252 e estraga os acentos.
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(encoding="utf-8")
    configurar(args.verboso)
    try:
        cfg = carregar()
    except ErroDeConfig as erro:
        log.error("%s", erro)
        return 2
    if args.comando == "mcp":
        from .mcp_whatsapp import main as mcp

        return mcp()
    if cfg.transcricao.vad == "local":
        from .vad_local import precarregar

        precarregar()  # antes do OCR: o winrt traz um msvcp140.dll que derruba o onnxruntime

    if args.comando == "servir":
        return _servir(cfg, args)
    if args.comando == "status":
        return _status(cfg)
    if args.comando == "grupos":
        return _grupos(cfg)
    if args.comando == "transcrever":
        return _local(cfg, args, "audio")
    if args.comando == "ocr":
        return _local(cfg, args, "imagem")
    if args.comando == "limpar-cache":
        return _limpar_cache(cfg, args)
    if args.comando == "migrar":
        return _migrar(cfg, args)
    if args.comando == "nomes":
        return _nomes(cfg, args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
