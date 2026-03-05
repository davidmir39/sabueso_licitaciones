"""
config.py — Sabueso de Licitaciones
======================================
Fuente única de verdad para toda la configuración.

Variables de entorno: copia .env.example a .env y ajusta los valores.
En producción B2B, usa un gestor de secretos (AWS Secrets Manager,
HashiCorp Vault) en lugar de ficheros .env.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# ──────────────────────────────────────────────────────────────────────────────
# RUTAS BASE
# ──────────────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data" / "pdfs"
DATABASE_DIR = BASE_DIR / "database"
LOGS_DIR = BASE_DIR / "logs"

for _dir in (DATA_DIR, DATABASE_DIR, LOGS_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

# ──────────────────────────────────────────────────────────────────────────────
# VARIABLES DE ENTORNO
# ──────────────────────────────────────────────────────────────────────────────
load_dotenv(BASE_DIR / ".env")

# ──────────────────────────────────────────────────────────────────────────────
# BASE DE DATOS
# ──────────────────────────────────────────────────────────────────────────────
DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    f"sqlite:///{DATABASE_DIR / 'sabueso.db'}",
)

# ──────────────────────────────────────────────────────────────────────────────
# FEEDS ATOM — PCSP (Plataforma de Contratación del Sector Público)
# ──────────────────────────────────────────────────────────────────────────────
PCSP_FEEDS: dict[str, str] = {
    # Todas las licitaciones con XML enriquecido
    "completo": os.getenv(
        "PCSP_FEED_COMPLETO",
        "https://contrataciondelestado.es/sindicacion/sindicacion_1044/"
        "licitacionesPerfilesContratanteCompleto3.atom",
    ),
    # Solo novedades: más ligero, ideal para polling frecuente
    "novedades": os.getenv(
        "PCSP_FEED_NOVEDADES",
        "https://contrataciondelestado.es/sindicacion/sindicacion_1045/"
        "licitacionesPerfilesContratante3.atom",
    ),
}

ACTIVE_FEED: str = os.getenv("ACTIVE_FEED", "completo")

# ──────────────────────────────────────────────────────────────────────────────
# HTTP / SCRAPING
# ──────────────────────────────────────────────────────────────────────────────
REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", "30"))
REQUEST_DELAY_SECONDS: float = float(os.getenv("REQUEST_DELAY_SECONDS", "1.0"))
MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "3"))
MAX_LICITACIONES_POR_RUN: int = int(os.getenv("MAX_LICITACIONES_POR_RUN", "20"))

HTTP_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; SabuesoLicitaciones/1.0; "
        "+https://sabueso-licitaciones.es/bot)"
    ),
    "Accept": "application/atom+xml, application/xml;q=0.9, */*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9",
    "Cache-Control": "no-cache",
}

# ──────────────────────────────────────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_DIR: Path = LOGS_DIR
LOG_MAX_BYTES: int = 10 * 1024 * 1024   # 10 MB
LOG_BACKUP_COUNT: int = 5               # 5 ficheros rotados → 50 MB máx

# ──────────────────────────────────────────────────────────────────────────────
# MÁQUINA DE ESTADOS DEL PIPELINE
# ──────────────────────────────────────────────────────────────────────────────
class EstadoLicitacion:
    NUEVA               = "NUEVA"               # Recién ingestada del feed
    PDF_PENDIENTE       = "PDF_PENDIENTE"        # Link detectado, PDF sin descargar
    PDF_DESCARGADO      = "PDF_DESCARGADO"       # PDF guardado en disco
    ANALISIS_PENDIENTE  = "ANALISIS_PENDIENTE"   # En cola para la IA
    ANALIZADA           = "ANALIZADA"            # Llama 4 procesó el texto
    RELEVANTE           = "RELEVANTE"            # IA: interesante para el cliente
    DESCARTADA          = "DESCARTADA"           # IA: no relevante
    NOTIFICADA          = "NOTIFICADA"           # Enviada al cliente (GPT-5)
    ERROR               = "ERROR"               # Falló algún paso
