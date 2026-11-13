"""Gera o zip do pepita-wa completo para instalar em outro computador, sem a distro.

    python ferramentas/pacote_completo.py [--sufixo NOME] [--saida PASTA]

Parte do `git archive HEAD` (so o que esta commitado: nada de dados/, config.toml
nem .venv) e leva so o que a instalacao usa: extensao, servidor, testes, scripts de
iniciar e parar, o roteiro `docs/instalar-outro-computador.md`. Ficam de fora o
estado e o historico do usuario (STATUS, HANDOFF, auditorias, avaliacoes), o servico da
distro e as skills do projeto. O CLAUDE.md do usuario e trocado por um curto, que manda
o Claude Code do outro computador seguir o roteiro.
"""

import argparse
import io
import re
import subprocess
import sys
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

LEVA = (
    "extensao/", "src/", "tests/", "pyproject.toml", "uv.lock", "config.exemplo.toml",
    "README.md", ".gitignore", "docs/instalar-outro-computador.md",
    "ferramentas/servir-oculto.vbs", "ferramentas/parar-servidor.cmd",
    # So porque os testes os leem (test_transcricao, test_pacote_so_exportar, test_patch_wajs).
    "servico-whisper/servico.py", "ferramentas/pacote_so_exportar.py", "ferramentas/patch_wajs.py",
)
# Dentro do que vai, o que nao serve fora da maquina do usuario.
FORA = ("tests/test_avaliacao_transcricao.py", "extensao/testes/", "extensao/icones/gerar.py")

CLAUDE_MD = """\
# pepita-wa (instalacao em outro computador)

Esta pasta e uma copia do pepita-wa para um Windows sem a distro WSL do autor. Nao e um
repositorio git: nada de commit nem push aqui.

Para instalar ou conferir a instalacao, siga `docs/instalar-outro-computador.md`, na
ordem. Pontos que nao mudam:

- `config.toml` com `[transcricao] vad = "local"` e `[exportar] raiz` numa pasta que
  existe. O `config.toml` e a pasta `dados/` sao desta maquina: nunca apague nem
  sobrescreva ao atualizar.
- Dependencias com `uv sync --extra vad-local`, com o servidor encerrado
  (`ferramentas\\parar-servidor.cmd`).
- O servidor so aceita loopback e a extensao: nao afrouxe isso.
- So o audio com fala vai ao Google, pelo `agy`, na conta Google de quem usa. Nada mais
  sai da maquina.
- Testes: `uv run python -m pytest -q`.
"""


def _git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=RAIZ, capture_output=True, check=True).stdout


def versao() -> str:
    """A do HEAD, a mesma do conteudo do zip."""
    texto = _git("show", "HEAD:pyproject.toml").decode("utf-8")
    return re.search(r'^version = "([^"]+)"', texto, re.M).group(1)


def pendentes() -> list[str]:
    """Mudancas nao commitadas no que vai para o zip: o zip sai do HEAD e as perderia."""
    saida = _git("status", "--porcelain", "--", *LEVA).decode("utf-8")
    return [linha[3:] for linha in saida.splitlines() if not linha[3:].startswith(FORA)]


def entra(nome: str) -> bool:
    return nome.startswith(LEVA) and not nome.startswith(FORA)


def gerar(destino: Path, nome: str) -> Path:
    arquivo = _git("archive", "--format=zip", "HEAD")
    saida = destino / f"{nome}-{versao()}.zip"
    with zipfile.ZipFile(io.BytesIO(arquivo)) as origem, \
            zipfile.ZipFile(saida, "w", zipfile.ZIP_DEFLATED) as z:
        for info in origem.infolist():
            if info.is_dir() or not entra(info.filename):
                continue
            z.writestr(f"{nome}/{info.filename}", origem.read(info))
        z.writestr(f"{nome}/CLAUDE.md", CLAUDE_MD)
    return saida


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--sufixo", default="", help="vai no nome: pepita-wa-completo-<sufixo>")
    p.add_argument("--saida", type=Path, default=RAIZ / "dist")
    a = p.parse_args()
    sufixo = re.sub(r"[^a-z0-9-]+", "-", a.sufixo.lower()).strip("-")
    nome = "pepita-wa-completo" + (f"-{sufixo}" if sufixo else "")
    try:
        faltam = pendentes()
        if faltam:
            sys.exit("commite antes (o zip sai do HEAD):\n  " + "\n  ".join(faltam))
        a.saida.mkdir(parents=True, exist_ok=True)
        saida = gerar(a.saida, nome)
    except subprocess.CalledProcessError as erro:
        sys.exit(f"git archive falhou: {erro.stderr.decode(errors='replace')}")
    print(f"zip: {saida} ({saida.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
