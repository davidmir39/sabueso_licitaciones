"""
config.py — Sabueso de Licitaciones
=====================================
Fuente única de verdad para toda la configuración.
Usa variables de entorno (.env) con valores por defecto seguros.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths base del proyecto (siempre absolutos para evitar errores de CWD)
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data" / "pdfs"
DATABASE_DIR = BASE_DIR / "database"
LOGS_DIR = BASE_DIR / "logs"

# Crear directorios si no existen (idempotente)
for _dir in [DATA_DIR, DATABASE_DIR, LOGS_DIR]:
    _dir.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Cargar variables de entorno
# ---------------------------------------------------------------------------
load_dotenv(BASE_DIR / ".env")

# ---------------------------------------------------------------------------
# Base de Datos
# ---------------------------------------------------------------------------
DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    f"sqlite:///{DATABASE_DIR / 'sabueso.db'}"
)

# ---------------------------------------------------------------------------
# PCSP — Plataforma de Contratación del Sector Público
# Feeds ATOM oficiales (verificados 2024)
# ---------------------------------------------------------------------------
PCSP_FEEDS: dict[str, str] = {
    # Feed COMPLETO: todas las licitaciones con XML enriquecido
    "completo": os.getenv(
        "PCSP_FEED_URL_COMPLETO",
        "https://contrataciondelestado.es/sindicacion/sindicacion_1044/licitacionesPerfilesContratanteCompleto3.atom"
    ),
    # Feed de NOVEDADES: solo las más recientes (más liviano, ideal para polling)
    "novedades": os.getenv(
        "PCSP_FEED_URL_NOVEDADES",
        "https://contrataciondelestado.es/sindicacion/sindicacion_1045/licitacionesPerfilesContratante3.atom"
    ),
}

# Feed activo por defecto
ACTIVE_FEED: str = os.getenv("ACTIVE_FEED", "completo")

# ---------------------------------------------------------------------------
# HTTP / Scraping
# ---------------------------------------------------------------------------
REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", "30"))
REQUEST_DELAY_SECONDS: float = float(os.getenv("REQUEST_DELAY_SECONDS", "1.5"))
MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "3"))
MAX_LICITACIONES_POR_RUN: int = int(os.getenv("MAX_LICITACIONES_POR_RUN", "20"))

# User-Agent realista para evitar bloqueos del servidor PCSP
HTTP_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; SabuesoLicitaciones/1.0; "
        "+https://sabueso-licitaciones.es/bot)"
    ),
    "Accept": "application/atom+xml, application/xml;q=0.9, */*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9",
    "Cache-Control": "no-cache",
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_DIR: Path = LOGS_DIR
LOG_MAX_BYTES: int = 10 * 1024 * 1024  # 10 MB por archivo de log
LOG_BACKUP_COUNT: int = 5               # Mantener 5 archivos rotados

# ---------------------------------------------------------------------------
# Estados del ciclo de vida de una licitación
# Centralizado aquí para que sea la única fuente de verdad
# ---------------------------------------------------------------------------
class EstadoLicitacion:
    NUEVA = "NUEVA"                         # Recién ingestada del feed
    PDF_PENDIENTE = "PDF_PENDIENTE"         # Link detectado, PDF sin descargar
    PDF_DESCARGADO = "PDF_DESCARGADO"       # PDF en disco
    ANALISIS_PENDIENTE = "ANALISIS_PENDIENTE"   # Listo para IA
    ANALIZADA = "ANALIZADA"                 # IA local (Llama 4) procesó
    RELEVANTE = "RELEVANTE"                 # IA determinó que es relevante
    DESCARTADA = "DESCARTADA"               # IA determinó que no es relevante
    NOTIFICADA = "NOTIFICADA"               # Enviada al cliente
    ERROR = "ERROR"                         # Falló algún paso

