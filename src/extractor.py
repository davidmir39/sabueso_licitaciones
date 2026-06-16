"""
src/extractor.py — Sabueso de Licitaciones
============================================
Step 3 del pipeline: extrae texto de los ficheros descargados.

Estrategia en dos pasos:
  1. Intentar extracción nativa con PyMuPDF (gratis, milisegundos).
     Si el PDF es texto digital (generado por ordenador), esto funciona
     perfectamente y da el texto completo.

  2. Si PyMuPDF devuelve poco texto (PDF escaneado = imagen), llamamos
     a Gemini para hacer OCR. Esto tiene coste pero es necesario para
     los documentos de órganos pequeños que escanean en papel.

Además maneja el caso de ficheros ZIP (que contienen DOCX u otros):
  · Los DOCX son técnicamente ficheros ZIP con XML dentro.
  · Algunos órganos suben el pliego como .docx en vez de .pdf.
  · Los detectamos por sus magic bytes (PK al inicio) y extraemos el texto.

Lo que NO hace este módulo:
  · Analizar si el texto es relevante (eso es ia_client.analizar_relevancia)
  · Guardar en BD (eso lo hace db_manager)
  · Decidir qué ficheros procesar (eso lo decide main.py)
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Optional

import config
from src.logger import get_logger
from src.ia_client import extraer_texto_ocr

logger = get_logger(__name__)

# Intentamos importar PyMuPDF. Si no está instalado, el error sale aquí
# con un mensaje claro en vez de un ImportError críptico más adelante.
try:
    import fitz  # PyMuPDF se importa como 'fitz'
except ImportError:
    raise ImportError(
        "Falta instalar PyMuPDF. Ejecuta: pip install pymupdf"
    )


def _extraer_texto_pymupdf(ruta_pdf: Path) -> str:
    """
    Extrae texto nativo de un PDF usando PyMuPDF.

    Devuelve string vacío si el PDF no tiene texto (es una imagen escaneada).
    """
    try:
        # Abrimos el PDF con PyMuPDF
        documento = fitz.open(str(ruta_pdf))
        paginas_texto = []

        for numero_pagina in range(len(documento)):
            pagina = documento[numero_pagina]
            # get_text() extrae el texto de la página
            texto_pagina = pagina.get_text()
            if texto_pagina.strip():
                paginas_texto.append(texto_pagina)

        documento.close()

        texto_completo = "\n".join(paginas_texto)
        logger.debug(
            "PyMuPDF extrajo %d caracteres de %s",
            len(texto_completo),
            ruta_pdf.name,
        )
        return texto_completo

    except Exception as exc:
        logger.warning("PyMuPDF no pudo leer %s: %s", ruta_pdf.name, exc)
        return ""


def _extraer_texto_docx(ruta_zip: Path) -> Optional[str]:
    """
    Extrae texto de un fichero DOCX (que internamente es un ZIP con XML).

    Un DOCX es un ZIP que contiene un fichero word/document.xml con el
    texto del documento envuelto en etiquetas XML como <w:t>texto</w:t>.

    Esta función abre el ZIP, busca ese XML y extrae el texto limpio.
    """
    try:
        with zipfile.ZipFile(str(ruta_zip), "r") as archivo_zip:
            # El texto principal de un DOCX siempre está en word/document.xml
            if "word/document.xml" not in archivo_zip.namelist():
                logger.warning(
                    "%s es un ZIP pero no parece un DOCX (sin word/document.xml)",
                    ruta_zip.name,
                )
                return None

            xml_bytes = archivo_zip.read("word/document.xml")
            xml_texto = xml_bytes.decode("utf-8", errors="replace")

            # Extraemos el texto de las etiquetas <w:t>...</w:t>
            # que es donde Word guarda el texto del documento
            import re
            fragmentos = re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml_texto)
            texto = " ".join(fragmentos)

            # Limpiamos espacios múltiples
            texto = re.sub(r"\s+", " ", texto).strip()

            logger.info(
                "DOCX extraído: %s → %d caracteres",
                ruta_zip.name,
                len(texto),
            )
            return texto if texto else None

    except zipfile.BadZipFile:
        logger.warning("%s tiene magic bytes ZIP pero está corrupto", ruta_zip.name)
        return None
    except Exception as exc:
        logger.error("Error extrayendo DOCX %s: %s", ruta_zip.name, exc)
        return None


def _es_fichero_zip(ruta: Path) -> bool:
    """
    Comprueba si un fichero es un ZIP leyendo sus magic bytes.
    Los ficheros ZIP empiezan siempre con los bytes 'PK' (0x50 0x4B).
    """
    try:
        with open(ruta, "rb") as f:
            primeros_bytes = f.read(2)
        return primeros_bytes == b"PK"
    except OSError:
        return False


def extraer_texto(ruta_fichero: Path) -> Optional[str]:
    """
    Función principal: extrae texto de un fichero descargado de la PCSP.

    Maneja tres casos:
      1. PDF con texto digital → PyMuPDF (rápido y gratis)
      2. PDF escaneado         → Gemini OCR (un poco más lento, coste mínimo)
      3. DOCX/ZIP              → extracción del XML interno

    Args:
        ruta_fichero: Ruta al fichero en disco (normalmente en data/pdfs/).

    Returns:
        Texto extraído como string, o None si no se pudo extraer nada.
    """
    if not ruta_fichero.exists():
        logger.error("El fichero no existe: %s", ruta_fichero)
        return None

    logger.info("Extrayendo texto de: %s", ruta_fichero.name)

    # ── CASO 1 y 2: Es un PDF ────────────────────────────────────────────────
    if ruta_fichero.suffix.lower() == ".pdf" and not _es_fichero_zip(ruta_fichero):

        # Intentamos extracción nativa primero
        texto = _extraer_texto_pymupdf(ruta_fichero)

        if len(texto.strip()) >= config.TEXTO_MINIMO_CARACTERES:
            # Tenemos suficiente texto → PDF digital, todo bien
            logger.info(
                "Texto nativo OK: %s → %d caracteres",
                ruta_fichero.name,
                len(texto),
            )
            return texto

        # Poco texto → probablemente es un escaneado, usamos Gemini
        logger.info(
            "Poco texto nativo (%d chars), intentando OCR con Gemini: %s",
            len(texto.strip()),
            ruta_fichero.name,
        )
        return extraer_texto_ocr(ruta_fichero)

    # ── CASO 3: Es un ZIP/DOCX ───────────────────────────────────────────────
    if _es_fichero_zip(ruta_fichero):
        logger.info(
            "El fichero es un ZIP/DOCX, intentando extracción de texto: %s",
            ruta_fichero.name,
        )
        return _extraer_texto_docx(ruta_fichero)

    # Si llegamos aquí, no sabemos qué tipo de fichero es
    logger.warning(
        "Tipo de fichero no reconocido, no se puede extraer texto: %s",
        ruta_fichero.name,
    )
    return None
