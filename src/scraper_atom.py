"""
src/scraper_atom.py — Sabueso de Licitaciones
===============================================
Motor de ingesta del feed ATOM de la PCSP.

Responsabilidades:
  1. Descargar y parsear el feed ATOM con feedparser
  2. Limpiar y estructurar cada entrada en un LicitacionSchema válido
  3. Gestionar reintentos con backoff exponencial (via tenacity)
  4. NO escribir en BD (eso es responsabilidad de db_manager)
  
Principio de responsabilidad única: este módulo solo produce datos,
no los persiste. Esto facilita los tests unitarios y el reemplazo.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Iterator, Optional

import feedparser
import requests
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from src.logger import get_logger
from src.models import LicitacionSchema
from src.utils import parsear_fecha, parsear_summary_pcsp, limpiar_texto

logger = get_logger(__name__)


class PCPSFeedError(Exception):
    """Excepción específica para errores del feed PCSP."""
    pass


class AtomScraper:
    """
    Scraper del feed ATOM de la Plataforma de Contratación del Sector Público.
    
    Diseñado para ser stateless: no almacena resultados, solo los produce.
    La gestión de estado y persistencia es responsabilidad del DatabaseManager.
    
    Ejemplo de uso:
        scraper = AtomScraper(config.PCSP_FEEDS["completo"])
        for licitacion in scraper.iterar_licitaciones(limite=20):
            db.insertar_licitacion(session, licitacion)
    """

    def __init__(
        self,
        feed_url: str,
        timeout: int = config.REQUEST_TIMEOUT,
        delay: float = config.REQUEST_DELAY_SECONDS,
    ):
        self.feed_url = feed_url
        self.timeout = timeout
        self.delay = delay
        self._session = self._crear_sesion_http()
        logger.info(f"AtomScraper inicializado para: {feed_url}")

    def _crear_sesion_http(self) -> requests.Session:
        """
        Crea una sesión HTTP con headers y configuración de producción.
        Usar Session (no requests.get directo) permite reutilizar conexiones TCP.
        """
        session = requests.Session()
        session.headers.update(config.HTTP_HEADERS)
        # Retry a nivel de transporte para errores de red de bajo nivel
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        retry_strategy = Retry(
            total=2,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    @retry(
        stop=stop_after_attempt(config.MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((requests.RequestException, PCPSFeedError)),
        before_sleep=before_sleep_log(logger, 20),  # 20 = logging.WARNING
        reraise=True,
    )
    def _descargar_feed(self) -> feedparser.FeedParserDict:
        """
        Descarga y parsea el feed ATOM con reintentos automáticos.
        
        El decorador @retry de tenacity implementa backoff exponencial:
        - Intento 1: inmediato
        - Intento 2: espera ~2s
        - Intento 3: espera ~4s
        """
        logger.info(f"Descargando feed: {self.feed_url}")

        try:
            # Usamos requests para tener control sobre headers y timeout,
            # luego pasamos el contenido a feedparser para el parsing XML
            response = self._session.get(self.feed_url, timeout=self.timeout)
            response.raise_for_status()
        except requests.HTTPError as e:
            logger.error(f"HTTP {e.response.status_code} al descargar feed: {e}")
            raise PCPSFeedError(f"HTTP error: {e.response.status_code}") from e
        except requests.ConnectionError as e:
            logger.error(f"Error de conexión al feed PCSP: {e}")
            raise  # tenacity reintentará
        except requests.Timeout:
            logger.warning(f"Timeout ({self.timeout}s) descargando feed")
            raise

        # Pasar el contenido raw a feedparser evita que feedparser
        # haga su propia petición HTTP (perdiendo nuestros headers)
        feed = feedparser.parse(
            response.content,
            response_headers={"content-type": response.headers.get("content-type", "")},
        )

        # Verificar que el feed sea válido
        if feed.bozo and feed.bozo_exception:
            # bozo=True significa que feedparser detectó XML mal formado
            # Algunos feeds tienen bozo pero aún son parseables (bozo_exception suave)
            bozo_msg = str(feed.bozo_exception)
            if not feed.entries:
                logger.error(f"Feed inválido (bozo): {bozo_msg}")
                raise PCPSFeedError(f"Feed XML inválido: {bozo_msg}")
            else:
                logger.warning(f"Feed con advertencias XML (bozo): {bozo_msg} — continuando")

        total = len(feed.entries)
        if total == 0:
            logger.warning("El feed retornó 0 entradas. ¿URL correcta? ¿Servicio caído?")
        else:
            logger.info(
                f"Feed descargado: {total} entradas | "
                f"Título: {feed.feed.get('title', 'N/A')}"
            )

        return feed

    def _parsear_entrada(self, entry: feedparser.util.FeedParserDict) -> Optional[LicitacionSchema]:
        """
        Transforma una entrada del feed en un LicitacionSchema validado.
        
        El feed PCSP tiene campos estándar ATOM + campos extendidos propios.
        El campo 'summary' contiene una tabla HTML con los detalles.
        """
        try:
            # --- ID (campo crítico: si no hay ID, la entrada es inútil) ---
            entry_id = getattr(entry, "id", None) or entry.get("id")
            if not entry_id:
                logger.warning(f"Entrada sin ID, omitiendo: {entry.get('title', 'N/A')[:50]}")
                return None

            # --- Título ---
            titulo = limpiar_texto(entry.get("title")) or "Sin título"

            # --- Link principal ---
            link = entry.get("link")
            if not link and entry.get("links"):
                # feedparser puede devolver lista de links
                link = next(
                    (l.href for l in entry.links if l.get("rel") == "alternate"),
                    entry.links[0].href if entry.links else None,
                )

            # --- Fechas ---
            fecha_pub = parsear_fecha(
                entry.get("published") or entry.get("updated")
            )
            fecha_act = parsear_fecha(entry.get("updated"))

            # --- Author → Órgano de contratación (campo ATOM estándar) ---
            organo_raw = entry.get("author") or ""
            if hasattr(entry, "author_detail") and entry.author_detail:
                organo_raw = entry.author_detail.get("name", organo_raw)

            # --- Parsear el HTML del summary para campos adicionales ---
            summary_html = entry.get("summary", "")
            campos_extra = parsear_summary_pcsp(summary_html)

            # El órgano del summary tiene prioridad sobre el campo author del ATOM
            organo_final = campos_extra.pop("organo_contratacion", None) or limpiar_texto(organo_raw)

            # --- Construir y validar el schema ---
            schema = LicitacionSchema(
                id=entry_id,
                titulo=titulo,
                link_plataforma=link,
                fecha_publicacion=fecha_pub,
                fecha_actualizacion=fecha_act,
                organo_contratacion=organo_final,
                raw_summary_html=summary_html[:5000] if summary_html else None,  # Limitar tamaño
                **campos_extra,  # presupuesto_base, tipo_contrato, cpv, etc.
            )
            return schema

        except Exception as e:
            logger.error(
                f"Error parseando entrada '{entry.get('title', 'N/A')[:50]}': "
                f"{type(e).__name__}: {e}"
            )
            return None

    def iterar_licitaciones(
        self,
        limite: Optional[int] = config.MAX_LICITACIONES_POR_RUN,
    ) -> Iterator[LicitacionSchema]:
        """
        Generador principal: descarga el feed y produce LicitacionSchema uno a uno.
        
        Usar un generador (yield) en lugar de retornar una lista permite:
          - Procesar entradas mientras se siguen descargando (pipeline)
          - Controlar la memoria en feeds grandes
          - Aplicar rate limiting entre entradas fácilmente
        
        Args:
            limite: Máximo de entradas a procesar. None = todas.
        
        Yields:
            LicitacionSchema para cada entrada válida del feed
        """
        feed = self._descargar_feed()
        entradas = feed.entries
        
        if limite is not None:
            entradas = entradas[:limite]
            logger.info(f"Procesando las primeras {limite} entradas del feed")

        procesadas = 0
        errores = 0

        for i, entry in enumerate(entradas, 1):
            schema = self._parsear_entrada(entry)
            
            if schema:
                procesadas += 1
                yield schema
            else:
                errores += 1

            # Rate limiting: respetar al servidor de la PCSP
            # (no aplicar en la última entrada)
            if i < len(entradas) and self.delay > 0:
                time.sleep(self.delay)

        logger.info(
            f"Iteración completada: {procesadas} válidas, "
            f"{errores} errores de parsing"
        )

    def obtener_metadata_feed(self) -> dict:
        """
        Retorna metadatos del feed sin procesar las entradas.
        Útil para healthchecks y monitorización.
        """
        try:
            feed = self._descargar_feed()
            return {
                "titulo": feed.feed.get("title"),
                "subtitulo": feed.feed.get("subtitle"),
                "actualizado": feed.feed.get("updated"),
                "total_entradas": len(feed.entries),
                "feed_version": feed.version,
                "bozo": feed.bozo,
            }
        except Exception as e:
            return {"error": str(e)}
