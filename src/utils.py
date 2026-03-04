"""
src/utils.py — Sabueso de Licitaciones
=========================================
Funciones auxiliares puras (sin side effects, sin dependencias externas).
Todos los parsers y limpiadores de datos viven aquí.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup
from dateutil import parser as dateutil_parser

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.logger import get_logger

logger = get_logger(__name__)


# ===========================================================================
# Limpieza de Texto
# ===========================================================================

def limpiar_texto(texto: Optional[str]) -> Optional[str]:
    """
    Normaliza espacios, elimina caracteres de control y limpia
    artefactos de encoding habituales en los feeds de la PCSP.
    """
    if not texto:
        return None
    # Eliminar tags HTML residuales
    texto = re.sub(r"<[^>]+>", " ", texto)
    # Normalizar espacios (tabs, newlines, múltiples espacios)
    texto = re.sub(r"\s+", " ", texto)
    # Eliminar caracteres de control no-ASCII excepto caracteres españoles
    texto = re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]", "", texto)
    return texto.strip() or None


def extraer_importe(texto: Optional[str]) -> Optional[float]:
    """
    Extrae el primer importe monetario encontrado en un string.
    Maneja formatos españoles: '1.234.567,89 €', '250.000,00EUR', '150000'
    
    Returns:
        float o None si no se puede parsear
    """
    if not texto:
        return None
    # Buscar patrón numérico con formato español o internacional
    patron = r"[\d]{1,3}(?:[.\d]{4})*(?:,\d{2})?"
    matches = re.findall(patron, texto.replace(" ", ""))
    for match in matches:
        try:
            limpio = match.replace(".", "").replace(",", ".")
            valor = float(limpio)
            # Filtro de cordura: importes de licitaciones entre 1€ y 10.000M€
            if 1.0 <= valor <= 10_000_000_000:
                return valor
        except ValueError:
            continue
    return None


def parsear_fecha(fecha_str: Optional[str]) -> Optional[datetime]:
    """
    Parser robusto de fechas que maneja múltiples formatos del feed PCSP.
    Usa dateutil como fallback tras intentos con formatos conocidos.
    """
    if not fecha_str:
        return None

    # Formatos conocidos del feed ATOM de la PCSP (más rápido que dateutil)
    formatos_conocidos = [
        "%Y-%m-%dT%H:%M:%SZ",       # ISO 8601 UTC
        "%Y-%m-%dT%H:%M:%S%z",      # ISO 8601 con timezone
        "%Y-%m-%dT%H:%M:%S",        # ISO 8601 sin timezone
        "%Y-%m-%d",                  # Solo fecha
        "%d/%m/%Y",                  # Formato español
        "%d-%m-%Y",
    ]
    fecha_limpia = fecha_str.strip()
    for fmt in formatos_conocidos:
        try:
            return datetime.strptime(fecha_limpia, fmt).replace(tzinfo=None)
        except ValueError:
            continue

    # Fallback: dateutil (más lento pero muy flexible)
    try:
        return dateutil_parser.parse(fecha_limpia, ignoretz=True)
    except (ValueError, OverflowError):
        logger.debug(f"No se pudo parsear fecha: '{fecha_str}'")
        return None


# ===========================================================================
# Parser del Summary HTML del Feed PCSP
# ===========================================================================

# Mapeo de etiquetas conocidas del HTML del feed PCSP a campos del schema
_CAMPO_MAP: dict[str, str] = {
    "órgano de contratación": "organo_contratacion",
    "organo de contratacion": "organo_contratacion",
    "tipo de contrato": "tipo_contrato",
    "objeto del contrato": "titulo_alternativo",
    "presupuesto base de licitación": "presupuesto_base",
    "presupuesto base de licitacion": "presupuesto_base",
    "valor estimado del contrato": "valor_estimado",
    "cpv": "cpv_codigo",
    "lugar de ejecución": "lugar_ejecucion",
    "lugar de ejecucion": "lugar_ejecucion",
    "estado actual": "estado_contrato",
    "estado": "estado_contrato",
    "número de expediente": "expediente",
    "numero de expediente": "expediente",
    "n.i.f. órgano de contratación": "nif_organo",
}


def parsear_summary_pcsp(html_summary: Optional[str]) -> dict:
    """
    Extrae campos estructurados del HTML embebido en el <summary> del
    feed ATOM de la PCSP.
    
    El feed PCSP usa una tabla HTML con pares clave-valor. Ejemplo:
    
        <table>
          <tr>
            <td class="tdTitulo">Tipo de Contrato</td>
            <td class="tdContenido">Servicios</td>
          </tr>
          ...
        </table>
    
    Returns:
        dict con los campos extraídos (keys = campos del LicitacionSchema)
    """
    resultado: dict = {}

    if not html_summary:
        return resultado

    try:
        soup = BeautifulSoup(html_summary, "lxml")

        # --- Estrategia 1: Tabla de pares clave-valor (formato más común) ---
        filas = soup.find_all("tr")
        for fila in filas:
            celdas = fila.find_all("td")
            if len(celdas) >= 2:
                clave_raw = limpiar_texto(celdas[0].get_text()) or ""
                valor_raw = limpiar_texto(celdas[1].get_text()) or ""
                clave_norm = clave_raw.lower().strip(":")
                
                campo = _CAMPO_MAP.get(clave_norm)
                if campo and valor_raw:
                    resultado[campo] = valor_raw

        # --- Estrategia 2: Definición lists <dl><dt><dd> (formato alternativo) ---
        dts = soup.find_all("dt")
        for dt in dts:
            dd = dt.find_next_sibling("dd")
            if dd:
                clave_norm = (limpiar_texto(dt.get_text()) or "").lower().strip(":")
                valor_raw = limpiar_texto(dd.get_text()) or ""
                campo = _CAMPO_MAP.get(clave_norm)
                if campo and valor_raw:
                    resultado[campo] = valor_raw

        # --- Post-procesado: convertir importes a float ---
        for campo_importe in ("presupuesto_base", "valor_estimado"):
            if campo_importe in resultado:
                resultado[campo_importe] = extraer_importe(resultado[campo_importe])

        # --- Extraer código CPV limpio (ej: "45000000-7 - Trabajos de construcción") ---
        if "cpv_codigo" in resultado:
            cpv_raw = resultado["cpv_codigo"]
            cpv_match = re.match(r"(\d{8}-\d)", cpv_raw)
            if cpv_match:
                resultado["cpv_codigo"] = cpv_match.group(1)
                # El resto es la descripción
                desc_start = cpv_raw.find(" - ")
                if desc_start > 0:
                    resultado["cpv_descripcion"] = cpv_raw[desc_start + 3:].strip()

    except Exception as e:
        logger.warning(f"Error parseando summary HTML: {e}")

    return resultado


# ===========================================================================
# Utilidades de I/O
# ===========================================================================

def generar_nombre_pdf(licitacion_id: str, tipo: str = "ppt") -> str:
    """
    Genera un nombre de archivo seguro para el PDF descargado.
    Evita caracteres problemáticos en nombres de archivo.
    """
    # Usar los últimos 20 chars del ID (suelen ser los más distintivos en PCSP)
    id_corto = re.sub(r"[^\w]", "_", licitacion_id[-20:])
    timestamp = datetime.now().strftime("%Y%m%d")
    return f"{timestamp}_{tipo}_{id_corto}.pdf"


def formatear_presupuesto(importe: Optional[float]) -> str:
    """Formatea un importe float a string legible en formato español."""
    if importe is None:
        return "No especificado"
    if importe >= 1_000_000:
        return f"{importe / 1_000_000:.2f}M €"
    if importe >= 1_000:
        return f"{importe:,.0f} €".replace(",", ".")
    return f"{importe:.2f} €"
