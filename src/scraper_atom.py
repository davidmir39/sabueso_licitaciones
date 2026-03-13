"""
src/scraper_atom.py — Sabueso de Licitaciones
===============================================
Motor de ingesta del feed ATOM de la PCSP.

CAMBIOS v2 (basados en inspección real del feed 05/03/2026):
  · Órgano → extraído del summary (texto plano), no de cbc_name
  · Presupuesto → extraído de cac_budgetamount (UBL), primera línea
  · URL PDF → directamente de cbc_uri (UBL). NO necesita Playwright.
  · Nombre PDF → de cbc_filename
  · Ciudad/provincia → de cbc_cityname + cbc_countrysubentity
  · NIF → de cac_partyidentification
  · Estado contrato → del summary (ADJ, EV, RES, PUB...)
  · Estrategia de descarga: 3 capas (feedparser nativo → lxml repair → BS4)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Iterator, Optional

import feedparser
import requests
from lxml import etree
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
from src.utils import (
    limpiar_texto,
    parsear_fecha,
    parsear_summary_pcsp,
    parsear_budget_amount,
)

logger = get_logger(__name__)


class PCPSFeedError(Exception):
    """Error específico del feed PCSP."""
    pass


class AtomScraper:
    """
    Scraper stateless del feed ATOM de la PCSP.

    El feed usa el estándar UBL (Universal Business Language), lo que significa
    que los metadatos ricos (presupuesto, localización, URL del PDF) están
    disponibles directamente como campos del feed, sin necesidad de navegar
    páginas web adicionales con Playwright.

    Campos UBL clave que usamos:
      cbc_uri              → URL directa al PDF del expediente
      cbc_filename         → nombre descriptivo del documento
      cac_budgetamount     → presupuesto base (multilínea, tomamos el primero)
      cbc_cityname         → ciudad del órgano
      cbc_countrysubentity → provincia
      cac_partyidentification → NIF/CIF del órgano
      summary (texto plano) → órgano, expediente, estado, importe resumido
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
        session = requests.Session()
        session.headers.update(config.HTTP_HEADERS)
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

    def _descargar_bytes(self) -> bytes:
        resp = self._http.get(self.feed_url, timeout=self.timeout)
        resp.raise_for_status()
        return resp.content

    def _reparar_xml_con_lxml(self, contenido: bytes) -> bytes:
        """Repara XML malformado usando el modo recover de lxml."""
        parser = etree.XMLParser(
            recover=True,
            resolve_entities=False,
            no_network=True,
        )
        arbol = etree.fromstring(contenido, parser)
        return etree.tostring(arbol, encoding="utf-8", xml_declaration=True)

    @retry(
        stop=stop_after_attempt(config.MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((requests.RequestException, PCPSFeedError)),
        before_sleep=before_sleep_log(logger, 30),
        reraise=True,
    )
    def _descargar_feed(self) -> feedparser.FeedParserDict:
        """
        Descarga el feed con 3 estrategias en cascada.

        Estrategia 1 — feedparser nativo (parser tolerante propio).
        Estrategia 2 — lxml recover=True → feedparser (repara XML malformado).
        Estrategia 3 — BeautifulSoup html.parser (último recurso).
        """
        logger.info("Descargando feed PCSP...")

        # — Estrategia 1 —
        feed = feedparser.parse(
            self.feed_url,
            agent=config.HTTP_HEADERS["User-Agent"],
            request_headers={"Accept-Language": "es-ES,es;q=0.9"},
        )
        if feed.entries:
            logger.info("Estrategia 1 OK: %d entradas | bozo=%s", len(feed.entries), feed.bozo)
            return feed

        if feed.bozo:
            logger.warning("Estrategia 1 bozo sin entradas: %s", feed.bozo_exception)

        # — Estrategia 2 —
        try:
            raw = self._descargar_bytes()
            reparado = self._reparar_xml_con_lxml(raw)
            feed2 = feedparser.parse(reparado)
            if feed2.entries:
                logger.info("Estrategia 2 (lxml repair) OK: %d entradas.", len(feed2.entries))
                return feed2
            logger.warning("Estrategia 2: XML reparado pero sin entradas.")
        except Exception as exc:
            logger.warning("Estrategia 2 falló: %s", exc)
            raw = None

        # — Estrategia 3 —
        try:
            if raw is None:
                raw = self._descargar_bytes()
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(raw, "lxml")
            entries_bs = soup.find_all("entry")
            if entries_bs:
                entries_xml = b"".join(str(e).encode("utf-8") for e in entries_bs)
                feed_xml = (
                    b'<?xml version="1.0" encoding="utf-8"?>'
                    b'<feed xmlns="http://www.w3.org/2005/Atom">'
                    + entries_xml + b'</feed>'
                )
                feed3 = feedparser.parse(feed_xml)
                if feed3.entries:
                    logger.warning("Estrategia 3 (BS4) OK: %d entradas.", len(feed3.entries))
                    return feed3
        except Exception as exc:
            logger.error("Estrategia 3 falló: %s", exc)

        raise PCPSFeedError(
            f"Las 3 estrategias fallaron. bozo: {feed.bozo_exception if feed.bozo else 'ninguno'}"
        )

    def _extraer_lugar(self, entry: feedparser.util.FeedParserDict) -> Optional[str]:
        """
        Construye el campo lugar_ejecucion combinando ciudad y provincia
        de los campos UBL directos del entry.
        """
        ciudad = limpiar_texto(entry.get("cbc_cityname"))
        provincia = limpiar_texto(entry.get("cbc_countrysubentity"))

        if ciudad and provincia:
            # Evitar duplicados como "COBEJA (TOLEDO) - Toledo"
            if provincia.upper() not in ciudad.upper():
                return f"{ciudad} ({provincia})"
            return ciudad
        return ciudad or provincia

    def _parsear_entrada(self, entry: feedparser.util.FeedParserDict) -> Optional[LicitacionSchema]:
        """
        Convierte una entrada UBL del feed PCSP en un LicitacionSchema.

        Jerarquía de extracción de campos:
          1. Campos UBL directos (cbc_*, cac_*) → más fiables
          2. Summary (texto plano) → fallback y campos adicionales
          3. Campos ATOM estándar (title, link, updated) → siempre presentes
        """
        try:
            # — ID obligatorio —
            entry_id = entry.get("id") or entry.get("link")
            if not entry_id:
                logger.warning("Entrada sin ID omitida.")
                return None

            # — Título (campo ATOM estándar) —
            titulo = limpiar_texto(entry.get("title")) or "Sin título"

            # — Link a la ficha en la plataforma —
            link = entry.get("link")
            if not link and entry.get("links"):
                link = next(
                    (lk.href for lk in entry.links if lk.get("rel") == "alternate"),
                    entry.links[0].href if entry.links else None,
                )

            # — Fechas —
            fecha_pub = parsear_fecha(entry.get("published") or entry.get("updated"))
            fecha_act = parsear_fecha(entry.get("updated"))

            # — Summary (texto plano): órgano, expediente, estado, importe —
            summary_raw = entry.get("summary", "") or ""
            campos_summary = parsear_summary_pcsp(summary_raw)

            # — Presupuesto: cac_budgetamount tiene prioridad sobre el summary —
            presupuesto = parsear_budget_amount(entry.get("cac_budgetamount"))
            if presupuesto is None:
                presupuesto = campos_summary.get("presupuesto_base")

            # — Órgano de contratación: viene del summary —
            organo = campos_summary.get("organo_contratacion")
            # Fallback: author del entry ATOM (rara vez informado en este feed)
            if not organo:
                organo = limpiar_texto(entry.get("author"))

            # — NIF del órgano —
            nif = limpiar_texto(str(entry.get("cac_partyidentification") or "")) or None

            # — Lugar de ejecución —
            lugar = self._extraer_lugar(entry)

            # — URL directa del PDF (campo UBL cbc_uri) —
            url_pdf = limpiar_texto(entry.get("cbc_uri"))

            # — Nombre descriptivo del PDF (campo UBL cbc_filename) —
            nombre_pdf = limpiar_texto(entry.get("cbc_filename"))

            return LicitacionSchema(
                id=entry_id,
                titulo=titulo,
                link_plataforma=link,
                fecha_publicacion=fecha_pub,
                fecha_actualizacion=fecha_act,
                organo_contratacion=organo,
                nif_organo=nif,
                presupuesto_base=presupuesto,
                lugar_ejecucion=lugar,
                estado_contrato=campos_summary.get("estado_contrato"),
                expediente=campos_summary.get("expediente"),
                url_pdf_directo=url_pdf,
                nombre_pdf=nombre_pdf,
                raw_summary_html=summary_raw[:1000] if summary_raw else None,
            )

        except Exception as exc:
            logger.error(
                "Error parseando entrada '%s': %s: %s",
                entry.get("title", "?")[:50], type(exc).__name__, exc,
            )
            return None

    def iterar_licitaciones(
        self,
        limite: Optional[int] = config.MAX_LICITACIONES_POR_RUN,
    ) -> Iterator[LicitacionSchema]:
        """
        Generador principal. Produce un LicitacionSchema por entrada válida.
        """
        feed = self._descargar_feed()
        entradas = feed.entries[:limite] if limite else feed.entries

        if limite and len(feed.entries) > limite:
            logger.info("Limitando a %d de %d entradas.", limite, len(feed.entries))

        validas = errores = 0
        for i, entry in enumerate(entradas, start=1):
            schema = self._parsear_entrada(entry)
            if schema:
                validas += 1
                yield schema
            else:
                errores += 1
            if i < len(entradas) and self.delay > 0:
                time.sleep(self.delay)

        logger.info("Parseo completado: %d válidas | %d errores.", validas, errores)

    def obtener_metadata_feed(self) -> dict:
        """Metadatos del feed sin procesar entradas. Para --ping."""
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
