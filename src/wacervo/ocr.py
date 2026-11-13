"""Texto de imagem com o OCR nativo do Windows (Windows.Media.Ocr).

Nao baixa modelo nem usa GPU: o Windows ja traz o reconhecedor do idioma
instalado (confira com `wa status`). Roda em milissegundos por imagem.
"""

from __future__ import annotations

import asyncio
import re
import threading

from .config import Config
from .log import obter

log = obter(__name__)


class ErroDeOcr(Exception):
    """O OCR nao esta disponivel ou a imagem nao pode ser lida."""


def idiomas_disponiveis() -> list[str]:
    from winrt.windows.media.ocr import OcrEngine

    return [lang.language_tag for lang in OcrEngine.available_recognizer_languages]


def caracteres_uteis(texto: str) -> int:
    return len(re.findall(r"\w", texto))


def aplicar_minimo(texto: str, minimo: int) -> dict:
    """Separa texto de verdade de texto curto (foto comum rende uns caracteres
    soltos). O curto nao some: volta em `curto` para a exportacao registrar,
    porque pode ser um horario, valor ou codigo."""
    if caracteres_uteis(texto) >= minimo:
        return {"texto": texto}
    return {"texto": "", "curto": texto} if texto.strip() else {"texto": ""}


async def _ler(dados: bytes, idioma: str) -> str:
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import (
        BitmapAlphaMode,
        BitmapDecoder,
        BitmapInterpolationMode,
        BitmapPixelFormat,
        BitmapTransform,
        ColorManagementMode,
        ExifOrientationMode,
    )
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    motor = OcrEngine.try_create_from_language(Language(idioma))
    if motor is None:
        raise ErroDeOcr(f"o Windows nao tem o OCR de {idioma!r} instalado")

    fluxo = InMemoryRandomAccessStream()
    escritor = DataWriter(fluxo)
    escritor.write_bytes(dados)
    await escritor.store_async()
    escritor.detach_stream()
    fluxo.seek(0)

    try:
        decodificador = await BitmapDecoder.create_async(fluxo)
    except OSError as erro:
        raise ErroDeOcr(f"formato de imagem nao reconhecido: {erro}") from erro

    # O OCR recusa imagem maior que `max_image_dimension` (4096 hoje).
    maximo = OcrEngine.max_image_dimension
    largura, altura = decodificador.oriented_pixel_width, decodificador.oriented_pixel_height
    transformacao = BitmapTransform()
    escala = min(1.0, maximo / max(largura, altura, 1))
    if escala < 1.0:
        transformacao.scaled_width = int(largura * escala)
        transformacao.scaled_height = int(altura * escala)
        transformacao.interpolation_mode = BitmapInterpolationMode.FANT
    bitmap = await decodificador.get_software_bitmap_transformed_async(
        BitmapPixelFormat.BGRA8,
        BitmapAlphaMode.PREMULTIPLIED,
        transformacao,
        ExifOrientationMode.RESPECT_EXIF_ORIENTATION,
        ColorManagementMode.DO_NOT_COLOR_MANAGE,
    )
    resultado = await motor.recognize_async(bitmap)
    return "\n".join(linha.text for linha in resultado.lines).strip()


class LeitorDeTexto:
    """Chamavel: bytes da imagem -> todo o texto lido, sem limiar.

    O limiar (`ocr.minimo_caracteres`) entra so na resposta, com `aplicar_minimo`:
    o cache guarda a leitura inteira e mudar o limiar nao exige ler de novo."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._trava = threading.Lock()

    def __call__(self, dados: bytes) -> str:
        with self._trava:
            return asyncio.run(_ler(dados, self.cfg.ocr.idioma))
