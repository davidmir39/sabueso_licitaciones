"""
src/ia_client.py — Sabueso de Licitaciones
============================================
Capa de abstracción para llamadas a la IA (Gemini).

Todo el código que necesite hablar con una IA lo hace SIEMPRE a través
de este módulo. Si mañana cambiamos de Gemini a otro proveedor, solo
hay que modificar este fichero.

Funciones:
  · extraer_texto_ocr(ruta_pdf)        → extrae texto de un PDF escaneado
  · analizar_relevancia(...)           → decide si una licitación es relevante
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import config
from src.logger import get_logger

logger = get_logger(__name__)


def _get_cliente_gemini():
    """
    Crea y devuelve el cliente de la API de Gemini.

    Lo creamos aquí dentro (no al importar el módulo) para que el error
    de "API key no configurada" solo salte cuando realmente se usa la IA.
    """
    try:
        from google import genai
    except ImportError:
        raise ImportError(
            "Falta instalar google-genai. Ejecuta: pip install google-genai"
        )

    if not config.GEMINI_API_KEY:
        raise ValueError(
            "GEMINI_API_KEY no está configurada. "
            "Añádela al fichero .env: GEMINI_API_KEY=tu_key_aqui"
        )

    return genai.Client(api_key=config.GEMINI_API_KEY)


def extraer_texto_ocr(ruta_pdf: Path) -> Optional[str]:
    """
    Usa Gemini para extraer el texto de un PDF escaneado (imagen).

    Solo se llama cuando PyMuPDF no puede extraer texto suficiente
    (porque el PDF es una imagen, no texto digital).

    Args:
        ruta_pdf: Ruta al fichero PDF en disco.

    Returns:
        Texto extraído, o None si hubo un error.
    """
    logger.info("OCR con Gemini: %s", ruta_pdf.name)

    try:
        cliente = _get_cliente_gemini()
        contenido_pdf = ruta_pdf.read_bytes()

        respuesta = cliente.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=[
                {
                    "inline_data": {
                        "mime_type": "application/pdf",
                        "data": contenido_pdf,
                    }
                },
                (
                    "Extrae y devuelve todo el texto de este documento PDF. "
                    "Devuelve SOLO el texto extraído, sin comentarios ni explicaciones. "
                    "Mantén la estructura del documento lo mejor posible."
                ),
            ],
        )

        texto = respuesta.text
        if not texto or not texto.strip():
            logger.warning("Gemini OCR devolvió texto vacío para: %s", ruta_pdf.name)
            return None

        logger.info("OCR completado: %s → %d caracteres", ruta_pdf.name, len(texto))
        return texto.strip()

    except Exception as exc:
        logger.error("Error en OCR con Gemini para %s: %s", ruta_pdf.name, exc)
        return None


def analizar_relevancia(
    titulo: str,
    organo: str,
    presupuesto: Optional[float],
    texto_licitacion: str,
    descripcion_perfil: str,
) -> dict:
    """
    Usa Gemini para puntuar si una licitación es relevante para un perfil.

    Le pasamos el texto del pliego y la descripción del perfil del cliente.
    Gemini devuelve un score (0-100) y una razón breve.

    Args:
        titulo:             Título de la licitación.
        organo:             Órgano de contratación.
        presupuesto:        Presupuesto en euros (puede ser None).
        texto_licitacion:   Texto extraído del pliego.
        descripcion_perfil: Descripción en texto libre del perfil del cliente.

    Returns:
        Dict con:
          · score (int):        puntuación 0-100
          · razon (str):        explicación breve
          · es_relevante (bool): True si score >= SCORE_RELEVANCIA_MINIMO
    """
    logger.debug("Analizando relevancia con Gemini: %.50s", titulo)

    # Limitamos el texto del pliego a 4000 caracteres para no gastar
    # demasiados tokens. Con 4000 chars tenemos suficiente contexto.
    texto_truncado = texto_licitacion[:4000] if texto_licitacion else "(sin texto)"

    presupuesto_str = f"{presupuesto:,.0f} €" if presupuesto else "No especificado"

    # Este es el prompt que le mandamos a Gemini.
    # Le pedimos que responda SOLO con JSON para poder parsearlo fácilmente.
    prompt = f"""Eres un asistente especializado en licitaciones públicas españolas.

Analiza si la siguiente licitación es relevante para una empresa con este perfil:

PERFIL DE LA EMPRESA:
{descripcion_perfil}

LICITACIÓN A ANALIZAR:
- Título: {titulo}
- Órgano contratante: {organo}
- Presupuesto: {presupuesto_str}
- Texto del pliego:
{texto_truncado}

Responde ÚNICAMENTE con un objeto JSON con este formato exacto (sin texto adicional):
{{
  "score": <número entero del 0 al 100>,
  "razon": "<explicación breve de 1-2 frases en español>"
}}

Donde:
- score = 0 → completamente irrelevante para el perfil
- score = 100 → perfectamente adecuado para el perfil
- razon → por qué asignas ese score"""

    try:
        cliente = _get_cliente_gemini()

        respuesta = cliente.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=prompt,
        )

        texto_respuesta = respuesta.text.strip()

        # Limpiamos posibles marcas de código (```json ... ```) que a veces
        # añade Gemini aunque le pidamos JSON puro
        texto_respuesta = texto_respuesta.replace("```json", "").replace("```", "").strip()

        datos = json.loads(texto_respuesta)

        score = int(datos.get("score", 0))
        razon = str(datos.get("razon", "Sin explicación"))

        # Nos aseguramos de que el score está en el rango válido
        score = max(0, min(100, score))

        es_relevante = score >= config.SCORE_RELEVANCIA_MINIMO

        logger.info(
            "Relevancia: score=%d (%s) | %.50s",
            score,
            "✓ RELEVANTE" if es_relevante else "✗ DESCARTADA",
            titulo,
        )

        return {
            "score": score,
            "razon": razon,
            "es_relevante": es_relevante,
        }

    except json.JSONDecodeError as exc:
        logger.error(
            "Gemini no devolvió JSON válido para '%.40s': %s | Respuesta: %s",
            titulo, exc, texto_respuesta[:200],
        )
        # Devolvemos score 0 para no bloquear el pipeline
        return {"score": 0, "razon": "Error al parsear respuesta de IA", "es_relevante": False}

    except Exception as exc:
        logger.error("Error analizando relevancia para '%.40s': %s", titulo, exc)
        return {"score": 0, "razon": f"Error: {exc}", "es_relevante": False}