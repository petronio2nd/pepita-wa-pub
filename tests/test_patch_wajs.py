"""O patch do wa-js (ferramentas/patch_wajs.py) e idempotente e a copia do vendor o tem."""

import importlib.util
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("patch_wajs", RAIZ / "ferramentas" / "patch_wajs.py")
patch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(patch)

ORIGINAL = (
    'x=new Set(["ConnGetters","Constants"]);'
    "a,(0,l.wrapModuleFunction)(r.isLidMigrated,(e,...t)=>{try{return e(...t)}catch(e){return!1}}),b"
)


def test_aplica_uma_vez_e_e_idempotente():
    novo = patch.aplicar(ORIGINAL)
    assert '"isLidMigrated","Lid1X1MigrationUtils"' in novo
    assert "r.isLidMigrated&&(0,l.wrapModuleFunction)(r.isLidMigrated," in novo
    assert patch.aplicar(novo) == novo


def test_trecho_ausente_ou_repetido_recusa():
    with pytest.raises(ValueError):
        patch.aplicar("nada a ver")
    with pytest.raises(ValueError):
        patch.aplicar(ORIGINAL + ORIGINAL)


def test_vendor_do_repositorio_tem_o_patch():
    texto = patch.VENDOR.read_text(encoding="utf-8")
    assert patch.aplicar(texto) == texto
