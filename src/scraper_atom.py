"""
src/scraper_atom.py — Sabueso de Licitaciones
===============================================
Motor de ingesta del feed ATOM de la PCSP.

Responsabilidades (y SOLO estas):
  · Descargar el feed con reintentos y backoff exponencial.
  · Parsear cada entrada en un LicitacionSchema validado.
  · Producir los schemas uno a uno (generador) para que main.py
    los pase al DatabaseManager.

Lo que NO hace este módulo:
  · Escribir en BD (eso es db_manager.py).
  · Formatear output (eso es main.py).
  · Limpiar HTML o parsear fechas (eso es utils.py).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Iterator, Optional

import feedparser
import requests
from requests.adapters import HTTPAdapter
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from urllib3.util.retry import Retry

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from src.logger import get_logger
from src.models import LicitacionSchema
from src.utils import limpiar_texto, parsear_fecha, parsear_summary_pcsp

logger = get_logger(__name__)


class PCPSFeedError(Exception):
    """Error específico del feed PCSP. Permite captura selectiva en main.py."""
    pass


class AtomScraper:
    """
    Scraper stateless del feed ATOM de la PCSP.

    Stateless significa que no guarda resultados entre llamadas.
    Cada llamada a iterar_licitaciones() hace una descarga fresca del feed.
    Esto garantiza que siempre procesamos las licitaciones más recientes.

    Ejemplo de uso:
        scraper = AtomScraper(feed_url=config.PCSP_FEEDS["completo"])
        for schema in scraper.iterar_licitaciones(limite=20):
            with db.session() as s:
                db.insertar_licitacion(s, schema)
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
        self._http = self._crear_sesion_http()
        logger.info("AtomScraper listo para: %s", feed_url)

    def _crear_sesion_http(self) -> requests.Session:
        """
        Sesión HTTP con:
          · Headers realistas (evita bloqueos 403 del servidor PCSP).
          · Retry a nivel de transporte para errores de red transitoria.
          · Reutilización de conexiones TCP (más eficiente que requests.get()).
        """
        session = requests.Session()
        session.headers.update(config.HTTP_HEADERS)

        # Retry de bajo nivel (urllib3): solo para errores de transporte
        # Los errores HTTP (4xx, 5xx) los gestiona tenacity en _descargar_feed
        transport_retry = Retry(
            total=2,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=transport_retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    @retry(
        stop=stop_after_attempt(config.MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((requests.RequestException, PCPSFeedError)),
        before_sleep=before_sleep_log(logger, 30),  # 30 = logging.WARNING
        reraise=True,
    )
    def _descargar_feed(self) -> feedparser.FeedParserDict:
        """
        Descarga y parsea el feed ATOM con backoff exponencial automático.

        Estrategia de dos capas:
          1. requests descarga el contenido (control de headers/timeout).
          2. feedparser parsea el XML (robusto ante XML mal formado).

        El decorador @retry de tenacity implementa:
          - Intento 1: inmediato
          - Intento 2: espera ~2s
          - Intento 3: espera ~4s
          - Si los 3 fallan: relanza la excepción
        """
        logger.info("Descargando feed PCSP...")

        try:
            resp = self._http.get(self.feed_url, timeout=self.timeout)
            resp.raise_for_status()
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response else "?"
            logger.error("HTTP %s descargando feed: %s", status, exc)
            raise PCPSFeedError(f"HTTP {status}") from exc
        except requests.ConnectionError as exc:
            logger.warning("Error de conexión (reintentando): %s", exc)
            raise
        except requests.Timeout:
            logger.warning("Timeout (%ss) descargando feed (reintentando)", self.timeout)
            raise

        # Pasamos el contenido raw a feedparser para que use nuestros headers
        feed = feedparser.parse(
            resp.content,
            response_headers={"content-type": resp.headers.get("content-type", "")},
        )

        if feed.bozo and not feed.entries:
            raise PCPSFeedError(f"Feed XML inválido: {feed.bozo_exception}")
        elif feed.bozo:
            logger.warning("Feed con advertencias XML: %s — continuando", feed.bozo_exception)

        logger.info(
            "Feed descargado: %d entradas | Título: %s",
            len(feed.entries),
            feed.feed.get("title", "N/A"),
        )
        return feed

    def _parsear_entrada(self, entry: feedparser.util.FeedParserDict) -> Optional[LicitacionSchema]:
        """
        Convierte una entrada cruda de feedparser en un LicitacionSchema.

        Devuelve None si la entrada no tiene los campos mínimos requeridos
        (id y título), en lugar de lanzar excepción, para que el bucle
        en iterar_licitaciones() pueda continuar con la siguiente.
        """
        try:
            # — ID: campo crítico sin el que la entrada es inútil —
            entry_id = entry.get("id") or entry.get("link")
            if not entry_id:
                logger.warning("Entrada sin ID omitida: %s", entry.get("title", "?")[:50])
                return None

            # — Título —
            titulo = limpiar_texto(entry.get("title")) or "Sin título"

            # — Link: puede venir como string o como lista de dicts —
            link = entry.get("link")
            if not link and entry.get("links"):
                link = next(
                    (lk.href for lk in entry.links if lk.get("rel") == "alternate"),
                    entry.links[0].href if entry.links else None,
                )

            # — Fechas —
            fecha_pub = parsear_fecha(entry.get("published") or entry.get("updated"))
            fecha_act = parsear_fecha(entry.get("updated"))

            # — Órgano de contratación (campo ATOM: author) —
            organo_raw = ""
            if hasattr(entry, "author_detail") and entry.author_detail:
                organo_raw = entry.author_detail.get("name", "")
            if not organo_raw:
                organo_raw = entry.get("author", "")

            # — Summary HTML: fuente principal de metadatos enriquecidos —
            summary_html = entry.get("summary", "")
            campos_extra = parsear_summary_pcsp(summary_html)

            # El órgano del summary tiene prioridad sobre el campo 'author' del ATOM
            organo_final = campos_extra.pop("organo_contratacion", None) or limpiar_texto(organo_raw)

            return LicitacionSchema(
                id=entry_id,
                titulo=titulo,
                link_plataforma=link,
                fecha_publicacion=fecha_pub,
                fecha_actualizacion=fecha_act,
                organo_contratacion=organo_final,
                raw_summary_html=summary_html[:5000] if summary_html else None,
                **campos_extra,
            )

        except Exception as exc:
            logger.error(
                "Error parseando entrada '%s': %s: %s",
                entry.get("title", "?")[:50],
                type(exc).__name__,
                exc,
            )
            return None

    def iterar_licitaciones(
        self,
        limite: Optional[int] = config.MAX_LICITACIONES_POR_RUN,
    ) -> Iterator[LicitacionSchema]:
        """
        Generador principal: descarga el feed y produce LicitacionSchema uno a uno.

        Usar yield en lugar de return lista:
          · Permite procesar y persistir cada licitación antes de parsear la siguiente.
          · Controla el uso de memoria en feeds con cientos de entradas.
          · Facilita el rate-limiting entre peticiones.

        Raises:
            PCPSFeedError: si el feed no se puede descargar tras todos los reintentos.
        """
        feed = self._descargar_feed()
        entradas = feed.entries[:limite] if limite else feed.entries

        if limite and len(feed.entries) > limite:
            logger.info("Limitando a %d de %d entradas disponibles.", limite, len(feed.entries))

        validas = errores = 0
        for i, entry in enumerate(entradas, start=1):
            schema = self._parsear_entrada(entry)
            if schema:
                validas += 1
                yield schema
            else:
                errores += 1

            # Rate limiting: no saturar el servidor de la PCSP
            if i < len(entradas) and self.delay > 0:
                time.sleep(self.delay)

        logger.info("Parseo completado: %d válidas | %d errores.", validas, errores)

    def obtener_metadata_feed(self) -> dict:
        """
        Metadatos del feed sin procesar las entradas.
        Útil para healthchecks: python main.py --ping
        """
        try:
            feed = self._descargar_feed()
            return {
                "ok": True,
                "titulo": feed.feed.get("title"),
                "actualizado": feed.feed.get("updated"),
                "total_entradas": len(feed.entries),
                "version_feed": feed.version,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
