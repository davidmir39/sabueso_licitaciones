"""
src/utils.py — Sabueso de Licitaciones
=========================================
Funciones auxiliares puras: sin side-effects, sin escritura a BD,
sin llamadas HTTP. Solo transformaciones de datos.

Regla de oro: si una función aquí necesita importar db_manager o scraper,
algo está mal diseñado.
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


# ──────────────────────────────────────────────────────────────────────────────
# LIMPIEZA DE TEXTO
# ──────────────────────────────────────────────────────────────────────────────

def limpiar_texto(texto: Optional[str]) -> Optional[str]:
    """
    Normaliza un string eliminando HTML residual, caracteres de control
    y espacios múltiples. Devuelve None si el resultado está vacío.
    """
    if not texto:
        return None
    texto = re.sub(r"<[^>]+>", " ", texto)                    # tags HTML
    texto = re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]", "", texto)  # control chars
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto or None


# ──────────────────────────────────────────────────────────────────────────────
# PARSEO DE FECHAS
# ──────────────────────────────────────────────────────────────────────────────

_FORMATOS_FECHA = [
    "%Y-%m-%dT%H:%M:%SZ",      # ISO 8601 UTC  (más común en el feed PCSP)
    "%Y-%m-%dT%H:%M:%S%z",     # ISO 8601 con timezone
    "%Y-%m-%dT%H:%M:%S",       # ISO 8601 sin timezone
    "%Y-%m-%d",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
    "%d-%m-%Y",
]


def parsear_fecha(fecha_str: Optional[str]) -> Optional[datetime]:
    """
    Parser robusto de fechas para los múltiples formatos del feed PCSP.
    Intenta primero con formatos conocidos (rápido) y usa dateutil como
    fallback (flexible pero más lento).

    Siempre devuelve datetime sin tzinfo (naive UTC) para uniformidad
    con SQLite, que no tiene tipo TIMESTAMPTZ.
    """
    if not fecha_str:
        return None

    fecha_limpia = fecha_str.strip()

    for fmt in _FORMATOS_FECHA:
        try:
            dt = datetime.strptime(fecha_limpia, fmt)
            return dt.replace(tzinfo=None)  # naive para SQLite
        except ValueError:
            continue

    # Fallback: dateutil
    try:
        return dateutil_parser.parse(fecha_limpia, ignoretz=True)
    except (ValueError, OverflowError):
        logger.debug("No se pudo parsear fecha: '%s'", fecha_str)
        return None


# ──────────────────────────────────────────────────────────────────────────────
# EXTRACCIÓN DE IMPORTES
# ──────────────────────────────────────────────────────────────────────────────

def extraer_importe(texto: Optional[str]) -> Optional[float]:
    """
    Extrae el primer importe monetario de un string en formato español.

    Soporta:
        '1.234.567,89 €'  →  1234567.89
        '250.000,00EUR'   →  250000.0
        '150000'          →  150000.0

    Aplica un filtro de cordura: devuelve None para valores fuera del
    rango razonable de licitaciones públicas (1€ – 10.000M€).
    """
    if not texto:
        return None

    patron = r"\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?"
    matches = re.findall(patron, texto.replace(" ", ""))

    for match in matches:
        try:
            valor = float(match.replace(".", "").replace(",", "."))
            if 1.0 <= valor <= 10_000_000_000:
                return valor
        except ValueError:
            continue

    return None


# ──────────────────────────────────────────────────────────────────────────────
# PARSER DEL SUMMARY HTML DEL FEED PCSP
# ──────────────────────────────────────────────────────────────────────────────

# Mapeo de etiquetas del HTML del feed → campos de LicitacionSchema
_CAMPO_MAP: dict[str, str] = {
    "órgano de contratación":               "organo_contratacion",
    "organo de contratacion":               "organo_contratacion",
    "tipo de contrato":                     "tipo_contrato",
    "presupuesto base de licitación":       "presupuesto_base",
    "presupuesto base de licitacion":       "presupuesto_base",
    "valor estimado del contrato":          "valor_estimado",
    "cpv":                                  "cpv_codigo",
    "lugar de ejecución":                   "lugar_ejecucion",
    "lugar de ejecucion":                   "lugar_ejecucion",
    "estado actual":                        "estado_contrato",
    "estado":                               "estado_contrato",
    "número de expediente":                 "expediente",
    "numero de expediente":                 "expediente",
    "n.i.f. órgano de contratación":        "nif_organo",
    "nif órgano de contratación":           "nif_organo",
}


def parsear_summary_pcsp(html_summary: Optional[str]) -> dict:
    """
    Extrae los campos estructurados del HTML embebido en el <summary>
    del feed ATOM de la PCSP.

    La PCSP usa una tabla HTML con pares clave-valor. Este parser
    soporta dos formatos que han aparecido históricamente:
      · <table><tr><td>Clave</td><td>Valor</td></tr>...</table>
      · <dl><dt>Clave</dt><dd>Valor</dd></dl>

    Los importes monetarios se convierten a float antes de devolverlos.
    Los códigos CPV se normalizan al formato '45000000-7'.

    Returns:
        dict con claves que coinciden con los campos de LicitacionSchema.
    """
    resultado: dict = {}
    if not html_summary:
        return resultado

    try:
        soup = BeautifulSoup(html_summary, "lxml")

        # — Estrategia 1: tabla <tr><td><td> —
        for fila in soup.find_all("tr"):
            celdas = fila.find_all("td")
            if len(celdas) >= 2:
                clave = (limpiar_texto(celdas[0].get_text()) or "").lower().rstrip(":")
                valor = limpiar_texto(celdas[1].get_text()) or ""
                campo = _CAMPO_MAP.get(clave)
                if campo and valor:
                    resultado[campo] = valor

        # — Estrategia 2: listas de definición <dl><dt><dd> —
        for dt in soup.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            if dd:
                clave = (limpiar_texto(dt.get_text()) or "").lower().rstrip(":")
                valor = limpiar_texto(dd.get_text()) or ""
                campo = _CAMPO_MAP.get(clave)
                if campo and valor:
                    resultado[campo] = valor

        # — Post-proceso: importes a float —
        for campo_importe in ("presupuesto_base", "valor_estimado"):
            if campo_importe in resultado:
                resultado[campo_importe] = extraer_importe(resultado[campo_importe])

        # — Post-proceso: normalizar CPV —
        if "cpv_codigo" in resultado:
            cpv_raw = resultado["cpv_codigo"]
            m = re.match(r"(\d{8}-\d)", cpv_raw)
            if m:
                resultado["cpv_codigo"] = m.group(1)
                sep = cpv_raw.find(" - ")
                if sep > 0:
                    resultado["cpv_descripcion"] = cpv_raw[sep + 3:].strip()

    except Exception as exc:
        logger.warning("Error parseando summary HTML: %s", exc)

    return resultado


# ──────────────────────────────────────────────────────────────────────────────
# FORMATEO PARA PRESENTACIÓN
# ──────────────────────────────────────────────────────────────────────────────

def formatear_presupuesto(importe: Optional[float]) -> str:
    """
    Convierte un float de euros a string legible para la tabla Rich.

    Examples:
        1_500_000.0  →  '1.50M €'
        250_000.0    →  '250.000 €'
        None         →  'No especificado'
    """
    if importe is None:
        return "No especificado"
    if importe >= 1_000_000:
        return f"{importe / 1_000_000:.2f}M €"
    if importe >= 1_000:
        return f"{importe:,.0f} €".replace(",", ".")
    return f"{importe:.2f} €"


def generar_nombre_pdf(licitacion_id: str, tipo: str = "ppt") -> str:
    """
    Genera un nombre de fichero seguro para guardar el PDF descargado.
    Usa los últimos 20 caracteres del ID (los más distintivos en la PCSP)
    y un timestamp de fecha para facilitar la organización.
    """
    id_corto = re.sub(r"[^\w]", "_", licitacion_id[-20:])
    timestamp = datetime.now().strftime("%Y%m%d")
    return f"{timestamp}_{tipo}_{id_corto}.pdf"
