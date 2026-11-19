"""Aplica o patch minimo no wa-js (extensao/vendor/wppconnect-wa.js).

O WhatsApp Web deixou de expor `Lid1X1MigrationUtils.isLidMigrated`. O wa-js 4.7.2
declara o modulo, avisa por console.error que nao achou e ainda tenta embrulhar a funcao
ausente (outro console.error). Os dois erros aparecem no painel de erros da extensao a
cada carga do WhatsApp Web e nao tem efeito. O patch:

1. poe `isLidMigrated` e `Lid1X1MigrationUtils` em IGNORE_FAIL_MODULES (o que o upstream
   faz com outros modulos que o WhatsApp tirou);
2. so embrulha `isLidMigrated` quando a funcao existe.

Depois de baixar uma release nova do npm (`@wppconnect/wa-js`) e copiar o
`dist/wppconnect-wa.js` por cima, rode de novo: `python ferramentas/patch_wajs.py`.
Se a release nova ja tratar isso, o script avisa que os trechos nao batem; nesse caso
nao aplique nada. Idempotente: rodar duas vezes nao muda o arquivo.
"""

from __future__ import annotations

import sys
from pathlib import Path

VENDOR = Path(__file__).resolve().parent.parent / "extensao" / "vendor" / "wppconnect-wa.js"

TROCAS = [
    (
        '"ConnGetters","Constants"',
        '"ConnGetters","Constants","isLidMigrated","Lid1X1MigrationUtils"',
    ),
    (
        # wa-js 4.8.2 (18/11/2026); na 4.7.2 as variaveis eram c e l.
        "(0,l.wrapModuleFunction)(r.isLidMigrated,",
        "r.isLidMigrated&&(0,l.wrapModuleFunction)(r.isLidMigrated,",
    ),
]


def aplicar(texto: str) -> str:
    """Devolve o texto com o patch. Cada trecho tem que aparecer exatamente uma vez
    (no original) ou o novo ja estar la (arquivo ja com patch)."""
    for antigo, novo in TROCAS:
        if novo in texto:
            continue
        if texto.count(antigo) != 1:
            raise ValueError(f"trecho nao encontrado uma vez so ({texto.count(antigo)}x): {antigo[:60]}")
        texto = texto.replace(antigo, novo)
    return texto


def main() -> int:
    original = VENDOR.read_text(encoding="utf-8")
    try:
        novo = aplicar(original)
    except ValueError as erro:
        print(f"nao apliquei: {erro}", file=sys.stderr)
        return 1
    if novo == original:
        print("ja estava com o patch")
        return 0
    VENDOR.write_text(novo, encoding="utf-8", newline="")
    print("patch aplicado em", VENDOR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
