"""
src/analizador.py — Sabueso de Licitaciones
=============================================
Motor de relevancia: decide qué licitaciones interesan a cada cliente.

Funciona en dos fases para ser eficiente:

  FASE A — Filtro determinista (gratis, milisegundos):
    Compara la licitación contra los filtros del perfil:
    palabras clave, CPV, presupuesto y provincias.
    Si no pasa este filtro, la licitación se descarta sin llamar a la IA.

  FASE B — Análisis con IA (Gemini, coste mínimo):
    Solo para las licitaciones que pasaron la Fase A.
    Gemini lee el texto del pliego y la descripción del perfil,
    y devuelve un score de 0 a 100 con una razón.

Este diseño evita llamar a la IA para licitaciones que claramente no son
relevantes (por CPV o presupuesto), lo que reduce coste y latencia.
"""

from __future__ import annotations

from typing import Optional

import config
from src.logger import get_logger
from src.ia_client import analizar_relevancia
from src.models import Licitacion, PerfilInteres

logger = get_logger(__name__)


def filtro_determinista(licitacion: Licitacion, perfil: PerfilInteres) -> tuple[bool, str]:
    """
    FASE A: comprueba si una licitación cumple los filtros básicos del perfil.

    Lógica: si un filtro no está configurado en el perfil (None o vacío),
    se ignora (no restringe). Solo filtra si el criterio está definido.

    Args:
        licitacion: Objeto ORM de la licitación.
        perfil:     Objeto ORM del perfil del cliente.

    Returns:
        (True, "OK") si pasa todos los filtros.
        (False, "razón") si falla algún filtro.
    """

    # ── FILTRO POR CPV ────────────────────────────────────────────────────────
    # El CPV (Vocabulario Común de Contratos) es un código europeo que clasifica
    # el tipo de contrato. Ej: 72 = servicios IT, 48 = software.
    # Comprobamos si el CPV de la licitación empieza por alguno de los prefijos
    # configurados en el perfil.
    cpv_prefijos = perfil.get_cpv_prefijos_lista()
    if cpv_prefijos and licitacion.cpv_codigo:
        # Hay prefijos configurados Y la licitación tiene CPV → comprobamos
        coincide_cpv = any(
            licitacion.cpv_codigo.startswith(prefijo)
            for prefijo in cpv_prefijos
        )
        if not coincide_cpv:
            return False, f"CPV {licitacion.cpv_codigo} no coincide con prefijos {cpv_prefijos}"

    # ── FILTRO POR PRESUPUESTO ────────────────────────────────────────────────
    if perfil.presupuesto_min is not None and licitacion.presupuesto_base is not None:
        if licitacion.presupuesto_base < perfil.presupuesto_min:
            return False, (
                f"Presupuesto {licitacion.presupuesto_base:.0f}€ "
                f"< mínimo {perfil.presupuesto_min:.0f}€"
            )

    if perfil.presupuesto_max is not None and licitacion.presupuesto_base is not None:
        if licitacion.presupuesto_base > perfil.presupuesto_max:
            return False, (
                f"Presupuesto {licitacion.presupuesto_base:.0f}€ "
                f"> máximo {perfil.presupuesto_max:.0f}€"
            )

    # ── FILTRO POR PALABRAS CLAVE ─────────────────────────────────────────────
    # Buscamos las palabras clave en el título, descripción CPV y texto del pliego.
    palabras_clave = perfil.get_palabras_clave_lista()
    if palabras_clave:
        # Construimos un texto donde buscar, todo en minúsculas
        partes_texto = [
            licitacion.titulo or "",
            licitacion.cpv_descripcion or "",
            # Solo los primeros 1000 chars del texto para que sea rápido
            (licitacion.texto_extraido or "")[:1000],
        ]
        texto_busqueda = " ".join(partes_texto).lower()

        coincide_keyword = any(kw in texto_busqueda for kw in palabras_clave)
        if not coincide_keyword:
            return False, f"Ninguna palabra clave encontrada: {palabras_clave}"

    # ── FILTRO POR PROVINCIA ──────────────────────────────────────────────────
    provincias = perfil.get_provincias_lista()
    if provincias and licitacion.lugar_ejecucion:
        lugar_lower = licitacion.lugar_ejecucion.lower()
        coincide_provincia = any(prov in lugar_lower for prov in provincias)
        if not coincide_provincia:
            return False, (
                f"Lugar '{licitacion.lugar_ejecucion}' "
                f"no coincide con provincias {provincias}"
            )

    # Si llega aquí, ha pasado todos los filtros
    return True, "OK"


def analizar_licitacion_para_perfil(
    licitacion: Licitacion,
    perfil: PerfilInteres,
) -> dict:
    """
    Analiza si una licitación es relevante para un perfil concreto.

    Ejecuta primero la Fase A (determinista) y, si pasa, la Fase B (IA).

    Args:
        licitacion: Objeto ORM de la licitación (debe tener texto_extraido).
        perfil:     Objeto ORM del perfil del cliente.

    Returns:
        Dict con:
          · paso_filtro_a (bool): si pasó la Fase A
          · score_ia (int|None): puntuación de Gemini (None si no llegó a Fase B)
          · razon_ia (str|None): explicación de Gemini
          · es_relevante (bool): resultado final
    """
    # ── FASE A ────────────────────────────────────────────────────────────────
    paso_a, razon_a = filtro_determinista(licitacion, perfil)

    if not paso_a:
        logger.debug(
            "Fase A: DESCARTADA | perfil='%s' | %s | %.40s",
            perfil.nombre, razon_a, licitacion.titulo,
        )
        return {
            "paso_filtro_a": False,
            "score_ia": None,
            "razon_ia": f"Filtro A: {razon_a}",
            "es_relevante": False,
        }

    logger.debug(
        "Fase A: PASA → Fase B con IA | perfil='%s' | %.40s",
        perfil.nombre, licitacion.titulo,
    )

    # ── FASE B ────────────────────────────────────────────────────────────────
    # Si no hay descripción en el perfil, no podemos llamar a la IA.
    # En ese caso, si pasó la Fase A, lo consideramos relevante con score 70.
    if not perfil.descripcion_ia:
        logger.info(
            "Perfil '%s' sin descripción_ia → score por defecto 70",
            perfil.nombre,
        )
        return {
            "paso_filtro_a": True,
            "score_ia": 70,
            "razon_ia": "Pasó filtros deterministas (sin descripción IA configurada)",
            "es_relevante": True,
        }

    resultado_ia = analizar_relevancia(
        titulo=licitacion.titulo,
        organo=licitacion.organo_contratacion or "Desconocido",
        presupuesto=licitacion.presupuesto_base,
        texto_licitacion=licitacion.texto_extraido or "",
        descripcion_perfil=perfil.descripcion_ia,
    )

    return {
        "paso_filtro_a": True,
        "score_ia": resultado_ia["score"],
        "razon_ia": resultado_ia["razon"],
        "es_relevante": resultado_ia["es_relevante"],
    }
