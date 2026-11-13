"""Logging do projeto: humano no terminal, sem ruido."""

from __future__ import annotations

import logging
import sys

_FORMATO = "%(asctime)s  %(levelname)-7s  %(message)s"
_HORA = "%H:%M:%S"


def configurar(verboso: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verboso else logging.INFO,
        format=_FORMATO,
        datefmt=_HORA,
        stream=sys.stderr,
    )
    for barulhento in ("faster_whisper", "httpx", "httpcore", "huggingface_hub"):
        logging.getLogger(barulhento).setLevel(logging.WARNING)


def obter(nome: str) -> logging.Logger:
    return logging.getLogger(nome)
