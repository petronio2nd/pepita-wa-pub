"""Prévia das pastas de número que ganhariam nome (so le; nao renomeia nada).

Depois de uma rodada do Exportar todas na 0.13.0, `marcas.nome` guarda o nome que o
WhatsApp mostra hoje (salvo ou do perfil); `marcas.pasta` segue a da primeira vez,
muitas vezes o numero. Aqui se compara os dois e se propoe a pasta nova.
"""

from __future__ import annotations

import re
import shutil
import unicodedata
from pathlib import Path


def slug(nome: str) -> str:
    """Mesma regra do `slug` do markdown.js."""
    base = "".join(c for c in unicodedata.normalize("NFD", nome) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-") or "conversa"


def _parece_numero(texto: str) -> bool:
    return bool(re.fullmatch(r"\+?[\d\s().-]+", texto.strip()))


def propor(marcas: dict[str, dict]) -> list[dict]:
    """Uma linha por pasta de numero cujo contato ja tem nome. Ordenada pela pasta."""
    usadas = {m["pasta"] for m in marcas.values()}
    linhas = []
    novas: dict[str, int] = {}
    for ident, m in sorted(marcas.items(), key=lambda kv: kv[1]["pasta"]):
        if not _parece_numero(m["pasta"]) or _parece_numero(m["nome"]):
            continue
        nova = slug(m["nome"])
        linhas.append({"id": ident, "pasta": m["pasta"], "nome": m["nome"], "nova": nova})
        novas[nova] = novas.get(nova, 0) + 1
    ocupadas = set(usadas) | {x["nova"] for x in linhas if novas[x["nova"]] == 1 and x["nova"] not in usadas}
    for linha in linhas:
        # Nome repetido (ou pasta que ja existe): leva o numero do id, como o nomeUnico,
        # e segue com -2, -3... se ainda assim estiver ocupada.
        if novas[linha["nova"]] > 1 or (linha["nova"] in usadas and linha["nova"] != linha["pasta"]):
            base = f"{linha['nova']}-{linha['id'].split('@')[0]}"
            nova, n = base, 2
            while nova in ocupadas:
                nova, n = f"{base}-{n}", n + 1
            ocupadas.add(nova)
            linha["nova"] = nova
            linha["colisao"] = True
    return linhas


def aplicar(linhas: list[dict], destino: Path, marcas) -> dict:
    """Renomeia a pasta (e o prefixo dos .md dentro) e troca `marcas.pasta` no mesmo passo.
    Pula o que nao existe no disco ou cuja pasta nova ja existe; nunca sobrescreve."""
    r = {"renomeadas": 0, "sem_pasta": 0, "ocupadas": 0, "arquivos": 0}
    for x in linhas:
        velha, nova = destino / x["pasta"], destino / x["nova"]
        if not velha.is_dir():
            r["sem_pasta"] += 1
            continue
        if nova.exists():
            r["ocupadas"] += 1
            continue
        velha.rename(nova)
        for arq in nova.glob(f"{x['pasta']}_*.md"):
            arq.rename(nova / (x["nova"] + arq.name[len(x["pasta"]):]))
            r["arquivos"] += 1
        marcas.trocar_pasta(x["id"], x["nova"])
        r["renomeadas"] += 1
    return r


def copia_das_marcas(caminho: Path) -> Path:
    alvo = caminho.with_name(caminho.stem + ".antes-dos-nomes" + caminho.suffix)
    shutil.copy2(caminho, alvo)
    return alvo
