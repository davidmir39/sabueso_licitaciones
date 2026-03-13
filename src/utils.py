"""
src/utils.py — Sabueso de Licitaciones
=========================================
Funciones auxiliares puras: sin side-effects, sin BD, sin HTTP.

IMPORTANTE — estructura real del feed PCSP (verificada 05/03/2026):
  · summary → texto plano separado por ';'
              "Id licitación: X; Órgano de Contratación: Y; Importe: Z EUR; Estado: W"
  · cbc_name → título del contrato (igual que entry.title), NO el órgano
  · cac_budgetamount → string multilínea, primera línea = importe base sin IVA
  · cbc_uri → URL directa al PDF adjunto
  · cbc_filename → nombre descriptivo del documento
  · cbc_cityname / cbc_countrysubentity → ciudad / provincia
  · cac_partyidentification → NIF/CIF del órgano
"""

from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.logger import get_logger

logger = get_logger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# LIMPIEZA DE TEXTO
# ──────────────────────────────────────────────────────────────────────────────

def limpiar_texto(texto: Optional[str]) -> Optional[str]:
    """
    Normaliza un string: elimina HTML residual, caracteres de control
    y espacios múltiples. Devuelve None si el resultado está vacío.
    """
    if not texto:
        return None
    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]", "", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto or None


# ──────────────────────────────────────────────────────────────────────────────
# PARSEO DE FECHAS
# ──────────────────────────────────────────────────────────────────────────────

_FORMATOS_FECHA = [
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
    "%d-%m-%Y",
]


def parsear_fecha(fecha_str: Optional[str]) -> Optional[datetime]:
    """
    Parser robusto de fechas para los múltiples formatos del feed PCSP.
    Devuelve datetime naive (sin tzinfo) para uniformidad con SQLite.
    """
    if not fecha_str:
        return None

    fecha_limpia = fecha_str.strip()

    for fmt in _FORMATOS_FECHA:
        try:
            return datetime.strptime(fecha_limpia, fmt).replace(tzinfo=None)
        except ValueError:
            continue

    # Fallback: dateutil
    try:
        from dateutil import parser as dateutil_parser
        return dateutil_parser.parse(fecha_limpia, ignoretz=True)
    except Exception:
        logger.debug("No se pudo parsear fecha: '%s'", fecha_str)
        return None


# ──────────────────────────────────────────────────────────────────────────────
# PARSER DEL SUMMARY (TEXTO PLANO, SEPARADO POR ';')
# ──────────────────────────────────────────────────────────────────────────────

def parsear_summary_pcsp(summary: Optional[str]) -> dict:
    """
    Extrae campos del summary de texto plano del feed PCSP.

    Formato real verificado:
        "Id licitación: 4/2025; Órgano de Contratación: Ayuntamiento X;
         Importe: 154163.5 EUR; Estado: ADJ"

    Returns dict con claves: expediente, organo_contratacion, importe_summary,
                             estado_contrato_summary
    """
    resultado = {}
    if not summary:
        return resultado

    # Separamos por ';' y procesamos cada par 'Clave: Valor'
    partes = [p.strip() for p in summary.split(";") if p.strip()]

    for parte in partes:
        if ":" not in parte:
            continue
        # Solo dividimos en el primer ':' para no romper valores con ':'
        clave, _, valor = parte.partition(":")
        clave = clave.strip().lower()
        valor = valor.strip()

        if not valor:
            continue

        if "id licitaci" in clave or clave == "id":
            resultado["expediente"] = valor
        elif "rgano" in clave or "contrataci" in clave:
            resultado["organo_contratacion"] = valor
        elif "importe" in clave:
            resultado["importe_summary"] = valor
            # Extraer el float del importe: "154163.5 EUR" → 154163.5
            importe_float = extraer_importe(valor)
            if importe_float:
                resultado["presupuesto_base"] = importe_float
        elif "estado" in clave:
            resultado["estado_contrato"] = valor

    return resultado


# ──────────────────────────────────────────────────────────────────────────────
# PARSER DEL PRESUPUESTO UBL (cac_budgetamount)
# ──────────────────────────────────────────────────────────────────────────────

def parsear_budget_amount(raw: Optional[str]) -> Optional[float]:
    """
    Extrae el importe base del campo UBL cac_budgetamount.

    El campo devuelve un string multilínea con 3 valores:
        154163.5        ← importe base sin IVA  (el que queremos)
        186537.83       ← importe con IVA
        154163.5        ← repetición del base

    Tomamos siempre el primero (primera línea no vacía).
    """
    if not raw:
        return None

    for linea in str(raw).splitlines():
        linea = linea.strip()
        if not linea:
            continue
        try:
            valor = float(linea.replace(",", "."))
            if 1.0 <= valor <= 10_000_000_000:
                return valor
        except ValueError:
            continue

    return None


# ──────────────────────────────────────────────────────────────────────────────
# EXTRACCIÓN DE IMPORTES DE TEXTO LIBRE
# ──────────────────────────────────────────────────────────────────────────────

def extraer_importe(texto: Optional[str]) -> Optional[float]:
    """
    Extrae el primer importe monetario de un string en formato español o
    decimal estándar.

    Ejemplos:
        "154163.5 EUR"   → 154163.5
        "1.234.567,89 €" → 1234567.89
        "250.000,00EUR"  → 250000.0
    """
    if not texto:
        return None

    # Formato español: 1.234.567,89
    patron_es = r"\d{1,3}(?:\.\d{3})*,\d{1,2}"
    m = re.search(patron_es, texto.replace(" ", ""))
    if m:
        try:
            valor = float(m.group().replace(".", "").replace(",", "."))
            if 1.0 <= valor <= 10_000_000_000:
                return valor
        except ValueError:
            pass

    # Formato decimal estándar: 154163.5
    patron_std = r"\d+(?:\.\d+)?"
    matches = re.findall(patron_std, texto)
    for match in matches:
        try:
            valor = float(match)
            if 1.0 <= valor <= 10_000_000_000:
                return valor
        except ValueError:
            continue

    return None


# ──────────────────────────────────────────────────────────────────────────────
# FORMATEO PARA PRESENTACIÓN (Rich)
# ──────────────────────────────────────────────────────────────────────────────

def formatear_presupuesto(importe: Optional[float]) -> str:
    """Convierte un float de euros a string legible para la tabla Rich."""
    if importe is None:
        return "Sin dato"
    if importe >= 1_000_000:
        return f"{importe / 1_000_000:.2f}M €"
    if importe >= 1_000:
        return f"{importe:,.0f} €".replace(",", ".")
    return f"{importe:.2f} €"


def generar_nombre_pdf(licitacion_id: str, nombre_original: Optional[str] = None) -> str:
    """
    Genera un nombre de fichero seguro para guardar el PDF.
    Usa el nombre original del documento si está disponible.
    """
    timestamp = datetime.now().strftime("%Y%m%d")
    if nombre_original:
        nombre_limpio = re.sub(r"[^\w\s-]", "", nombre_original).strip()
        nombre_limpio = re.sub(r"\s+", "_", nombre_limpio)[:60]
        return f"{timestamp}_{nombre_limpio}.pdf"
    id_corto = re.sub(r"[^\w]", "_", licitacion_id[-20:])
    return f"{timestamp}_{id_corto}.pdf"
