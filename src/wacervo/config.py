"""Carrega config.toml em dataclasses. O arquivo e opcional: sem ele, valem os padroes."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


class ErroDeConfig(Exception):
    """config.toml invalido."""


@dataclass(frozen=True)
class ConfigServidor:
    porta: int = 8765
    # Id da extensao em chrome://extensions. Vazio = aceita qualquer extensao;
    # preenchido = so ela fala com o servidor.
    extensao_id: str = ""


# Termos que o Gemini deve grafar certo quando forem ditos. O usuario troca no
# config.toml; nome de pessoa fica la, nunca aqui.
TERMOS_PADRAO = (
    "SISBAJUD", "RENAJUD", "SERASAJUD", "Bacenjud", "teimosinha",
    "exceção de pré-executividade", "prescrição intercorrente", "Projudi", "PJe",
    "alvará", "conta judicial", "IA", "LGPD", "Pix", "CPF", "CNPJ",
)


@dataclass(frozen=True)
class ConfigGemini:
    # Gemini pelo CLI `agy` (Google Antigravity), na assinatura do usuario (2026-10-20).
    modelo: str = "gemini-3.8-flash-low"
    # Caminho do agy.exe. Vazio = o do PATH ou %LOCALAPPDATA%\agy\bin\agy.exe.
    agy: str = ""
    # Chamadas ao agy ao mesmo tempo. Cada uma leva uns 15 a 20 s.
    paralelo: int = 3
    # Prazo de uma chamada; passou, conta como falha do agy.
    prazo_s: int = 240
    # Cota estourada ou agy sem login: o Gemini descansa este tanto antes de tentar de novo.
    pausa_min: int = 30
    contexto: str = "Mensagem de voz de WhatsApp."
    termos: tuple[str, ...] = TERMOS_PADRAO
    # O agy guarda cada transcricao em ~/.gemini/antigravity-cli; o `wa servir` apaga as
    # do wa com mais destes dias (usuario, 2026-10-20). 0 = nao apaga.
    guardar_agy_dias: int = 90


@dataclass(frozen=True)
class ConfigTranscricao:
    # Gemini pelo agy e o unico motor de transcricao.
    motor: str = "gemini"
    # Compatibilidade com configs antigas; nunca habilita ASR local.
    reserva: bool = False
    # Servico da distro WSL2 (servico-whisper/): apenas detector de voz. So
    # loopback.
    servico: str = "http://127.0.0.1:18765"
    # Onde mede a fala antes do Gemini: "distro" (o servico acima, o do usuario) ou
    # "local" (dentro do `wa servir`, para computador sem a distro; pede o extra
    # vad-local). Desde a 0.18.0.
    vad: str = "distro"
    # Campos legados: lidos para compatibilidade e assinaturas antigas do cache.
    # Nenhum deles carrega modelo ASR ou seleciona dispositivo em execucao.
    modelo: str = "deepdml/faster-whisper-large-v3-turbo-ct2"
    dispositivo: str = "cuda"
    tipo_computacao: str = "float16"
    idioma: str = "pt"
    descarregar_apos_min: int = 10
    gemini: ConfigGemini = field(default_factory=ConfigGemini)

    def __post_init__(self) -> None:
        if self.motor != "gemini":
            raise ErroDeConfig("transcricao: somente o motor gemini esta disponivel")
        object.__setattr__(self, "reserva", False)


@dataclass(frozen=True)
class ConfigOcr:
    idioma: str = "pt-BR"
    # Abaixo disto (letras e digitos) a imagem e tratada como "sem texto":
    # foto comum costuma render meia duzia de caracteres soltos.
    minimo_caracteres: int = 15


@dataclass(frozen=True)
class ConfigCache:
    # Dias que um texto fica no cache; o `wa servir` apaga os mais velhos ao
    # ligar e uma vez por dia. 0 = guarda ate `wa limpar-cache`.
    manter_dias: int = 0


@dataclass(frozen=True)
class ConfigExportar:
    # Raiz do "Exportar todas" (a que o Pepita le). Desde a 0.16.0 quem grava e o
    # `wa servir`, sempre dentro dela; a pasta tem que existir.
    raiz: str = "D:/70-WHATSAPP"


@dataclass(frozen=True)
class ConfigRotina:
    # Rodada automatica do "Exportar todas" (usuario, 2026-10-31). O liga/desliga do
    # painel fica em dados/rotina.json; este e so o padrao de quando ele nao existe.
    ligada: bool = True
    intervalo_min: int = 30
    # Primeira rodada depois de o `wa servir` ligar (no logon, o Chrome ainda abre).
    primeira_apos_min: int = 5
    # Sem o Chrome aberto, o `wa servir` abre o chrome.exe no WhatsApp Web.
    abrir_chrome: bool = True
    # Caminho do chrome.exe. Vazio = os lugares de sempre do Windows.
    chrome: str = ""
    # Pasta do perfil do Chrome onde a extensao esta (--profile-directory, por
    # exemplo "Default" ou "Profile 1"). Vazio = o perfil que o Chrome escolher.
    perfil: str = ""


@dataclass(frozen=True)
class ConfigGrupos:
    # Grupos que o usuario escolheu (0.21.0): so estes aparecem no MCP e entram no Exportar
    # todas. Ids `...@g.us`; o nome fica so como comentario no config.toml.
    ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Config:
    raiz: Path
    servidor: ConfigServidor = field(default_factory=ConfigServidor)
    transcricao: ConfigTranscricao = field(default_factory=ConfigTranscricao)
    ocr: ConfigOcr = field(default_factory=ConfigOcr)
    cache: ConfigCache = field(default_factory=ConfigCache)
    exportar: ConfigExportar = field(default_factory=ConfigExportar)
    rotina: ConfigRotina = field(default_factory=ConfigRotina)
    grupos: ConfigGrupos = field(default_factory=ConfigGrupos)

    @property
    def pasta_dados(self) -> Path:
        return self.raiz / "dados"

    @property
    def caminho_cache(self) -> Path:
        return self.pasta_dados / "cache.sqlite3"

    @property
    def caminho_marcas(self) -> Path:
        return self.pasta_dados / "marcas.sqlite3"

    @property
    def caminho_rotina(self) -> Path:
        return self.pasta_dados / "rotina.json"

    @property
    def caminho_token_agente(self) -> Path:
        # 0.19.0: o `wa mcp` manda este token ao `wa servir` (agente.py).
        return self.pasta_dados / "agente.token"


def raiz_do_projeto() -> Path:
    """A raiz e a pasta que contem o pyproject.toml deste pacote."""
    return Path(__file__).resolve().parents[2]


def _secao(dados: dict, nome: str) -> dict:
    valor = dados.get(nome, {})
    if not isinstance(valor, dict):
        raise ErroDeConfig(f"config.toml: [{nome}] precisa ser uma secao")
    return valor


def _inteiro(secao: dict, nome: str, campo: str, padrao: int, minimo: int, maximo: int) -> int:
    valor = secao.get(campo, padrao)
    # bool e subclasse de int no Python; `porta = true` nao e porta.
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise ErroDeConfig(f"config.toml: [{nome}] {campo} precisa ser um numero inteiro")
    if not minimo <= valor <= maximo:
        raise ErroDeConfig(f"config.toml: [{nome}] {campo} precisa estar entre {minimo} e {maximo}")
    return valor


def _texto(secao: dict, nome: str, campo: str, padrao: str, vazio: bool = False,
           opcoes: tuple[str, ...] = ()) -> str:
    valor = secao.get(campo, padrao)
    if not isinstance(valor, str):
        raise ErroDeConfig(f'config.toml: [{nome}] {campo} precisa ser texto entre aspas')
    if not vazio and not valor.strip():
        raise ErroDeConfig(f"config.toml: [{nome}] {campo} nao pode ficar vazio")
    if opcoes and valor not in opcoes:
        raise ErroDeConfig(f"config.toml: [{nome}] {campo} precisa ser um de: {', '.join(opcoes)}")
    return valor


def _logico(secao: dict, nome: str, campo: str, padrao: bool) -> bool:
    valor = secao.get(campo, padrao)
    if not isinstance(valor, bool):
        raise ErroDeConfig(f"config.toml: [{nome}] {campo} precisa ser true ou false")
    return valor


def _termos(secao: dict, nome: str, campo: str, padrao: tuple[str, ...]) -> tuple[str, ...]:
    valor = secao.get(campo, list(padrao))
    if not isinstance(valor, list) or not all(isinstance(v, str) and v.strip() for v in valor):
        raise ErroDeConfig(f"config.toml: [{nome}] {campo} precisa ser uma lista de textos")
    return tuple(v.strip() for v in valor)


def _ids_de_grupos(secao: dict) -> tuple[str, ...]:
    valor = secao.get("ids", [])
    if not isinstance(valor, list) or not all(isinstance(v, str) for v in valor):
        raise ErroDeConfig("config.toml: [grupos] ids precisa ser uma lista de textos")
    ids: list[str] = []
    for v in valor:
        # Aceita o arroba largo (U+FF20) que aparece ao copiar de alguns textos.
        v = v.strip().replace("＠", "@")
        if not v.endswith("@g.us") or len(v) <= len("@g.us") or " " in v:
            raise ErroDeConfig(f"config.toml: [grupos] ids: \"{v}\" nao e um id de grupo (...@g.us)")
        if v not in ids:
            ids.append(v)
    return tuple(ids)


def _gemini(t: dict) -> ConfigGemini:
    g = _secao(t, "gemini")
    nome, padrao = "transcricao.gemini", ConfigGemini()
    return ConfigGemini(
        modelo=_texto(g, nome, "modelo", padrao.modelo),
        agy=_texto(g, nome, "agy", padrao.agy, vazio=True),
        paralelo=_inteiro(g, nome, "paralelo", padrao.paralelo, 1, 8),
        prazo_s=_inteiro(g, nome, "prazo_s", padrao.prazo_s, 30, 1800),
        pausa_min=_inteiro(g, nome, "pausa_min", padrao.pausa_min, 1, 24 * 60),
        contexto=_texto(g, nome, "contexto", padrao.contexto, vazio=True),
        termos=_termos(g, nome, "termos", padrao.termos),
        guardar_agy_dias=_inteiro(g, nome, "guardar_agy_dias", padrao.guardar_agy_dias, 0, 36_500),
    )


def _servico_local(secao: dict, nome: str, campo: str, padrao: str) -> str:
    valor = _texto(secao, nome, campo, padrao).strip()
    partes = urlsplit(valor)
    try:
        porta = partes.port
    except ValueError:
        porta = None
    if (
        partes.scheme != "http"
        or partes.hostname not in ("127.0.0.1", "localhost", "::1")
        or porta is None
        or partes.path.strip("/")
        or partes.query
    ):
        raise ErroDeConfig(
            f"config.toml: [{nome}] {campo} precisa ser http://127.0.0.1:<porta>"
            " (a midia nao sai da maquina)"
        )
    return valor


def carregar(caminho: Path | None = None, raiz: Path | None = None) -> Config:
    raiz = raiz or raiz_do_projeto()
    caminho = caminho or raiz / "config.toml"
    if not caminho.exists():
        return Config(raiz=raiz)
    try:
        dados = tomllib.loads(caminho.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as erro:
        raise ErroDeConfig(f"{caminho.name} invalido: {erro}") from erro

    s = _secao(dados, "servidor")
    t = _secao(dados, "transcricao")
    o = _secao(dados, "ocr")
    c = _secao(dados, "cache")
    e = _secao(dados, "exportar")
    r = _secao(dados, "rotina")
    g = _secao(dados, "grupos")
    padrao_s, padrao_t, padrao_o = ConfigServidor(), ConfigTranscricao(), ConfigOcr()
    padrao_r = ConfigRotina()
    return Config(
        raiz=raiz,
        servidor=ConfigServidor(
            porta=_inteiro(s, "servidor", "porta", padrao_s.porta, 1, 65535),
            extensao_id=_texto(s, "servidor", "extensao_id", padrao_s.extensao_id, vazio=True),
        ),
        transcricao=ConfigTranscricao(
            motor=_texto(t, "transcricao", "motor", padrao_t.motor, opcoes=("gemini",)),
            reserva=_logico(t, "transcricao", "reserva", padrao_t.reserva),
            servico=_servico_local(t, "transcricao", "servico", padrao_t.servico),
            vad=_texto(t, "transcricao", "vad", padrao_t.vad, opcoes=("distro", "local")),
            modelo=_texto(t, "transcricao", "modelo", padrao_t.modelo),
            dispositivo=_texto(
                t, "transcricao", "dispositivo", padrao_t.dispositivo, opcoes=("cuda", "cpu", "auto")
            ),
            tipo_computacao=_texto(t, "transcricao", "tipo_computacao", padrao_t.tipo_computacao),
            # Vazio = o whisper detecta o idioma.
            idioma=_texto(t, "transcricao", "idioma", padrao_t.idioma, vazio=True),
            descarregar_apos_min=_inteiro(
                t, "transcricao", "descarregar_apos_min", padrao_t.descarregar_apos_min, 0, 24 * 60
            ),
            gemini=_gemini(t),
        ),
        ocr=ConfigOcr(
            idioma=_texto(o, "ocr", "idioma", padrao_o.idioma),
            minimo_caracteres=_inteiro(
                o, "ocr", "minimo_caracteres", padrao_o.minimo_caracteres, 0, 10_000
            ),
        ),
        cache=ConfigCache(
            manter_dias=_inteiro(c, "cache", "manter_dias", ConfigCache().manter_dias, 0, 36_500),
        ),
        exportar=ConfigExportar(raiz=_texto(e, "exportar", "raiz", ConfigExportar().raiz).strip()),
        rotina=ConfigRotina(
            ligada=_logico(r, "rotina", "ligada", padrao_r.ligada),
            intervalo_min=_inteiro(r, "rotina", "intervalo_min", padrao_r.intervalo_min, 5, 24 * 60),
            primeira_apos_min=_inteiro(r, "rotina", "primeira_apos_min", padrao_r.primeira_apos_min, 1, 24 * 60),
            abrir_chrome=_logico(r, "rotina", "abrir_chrome", padrao_r.abrir_chrome),
            chrome=_texto(r, "rotina", "chrome", padrao_r.chrome, vazio=True).strip(),
            perfil=_texto(r, "rotina", "perfil", padrao_r.perfil, vazio=True).strip(),
        ),
        grupos=ConfigGrupos(ids=_ids_de_grupos(g)),
    )
