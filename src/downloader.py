"""
src/downloader.py — Sabueso de Licitaciones
=============================================
Módulo de descarga de PDFs (Step 2).

Responsabilidades:
  · Descargar el PDF desde la URL directa (cbc_uri del feed UBL).
  · Guardar el fichero en data/pdfs/ con un nombre único y legible.
  · Verificar que el fichero descargado es un PDF real (magic bytes).
  · Actualizar la BD: ruta_pdf_local + estado → PDF_DESCARGADO.

Lo que NO hace este módulo:
  · Leer ni analizar el contenido del PDF (eso es el Step 3 con Gemini).
  · Decidir qué PDFs descargar (eso lo decide main.py consultando la BD).
  · Navegar páginas web (no necesario: las URLs ya están en el feed).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from src.logger import get_logger
from src.utils import generar_nombre_pdf

logger = get_logger(__name__)

# Cabecera mágica de un fichero PDF: los primeros 4 bytes deben ser '%PDF'
_PDF_MAGIC = b"%PDF"


class PDFDownloader:
    """
    Descargador de PDFs de la PCSP.

    Uso:
        downloader = PDFDownloader()
        ruta = downloader.descargar(
            url="https://contrataciondelestado.es/FileSystem/...",
            nombre_sugerido="Acuerdo_iniciacion",
            licitacion_id="https://...idEvolucion=123"
        )
        if ruta:
            db.marcar_pdf_descargado(session, licitacion_id, str(ruta))
    """

    def __init__(
        self,
        directorio_destino: Path = config.DATA_DIR,
        timeout: int = config.REQUEST_TIMEOUT,
        delay_entre_descargas: float = 1.5,
    ):
        self.directorio_destino = directorio_destino
        self.directorio_destino.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.delay = delay_entre_descargas
        self._http = self._crear_sesion_http()

    def _crear_sesion_http(self) -> requests.Session:
        """
        Sesión HTTP con headers que imitan un navegador descargando un fichero.
        El servidor de la PCSP verifica el Referer en algunas URLs de documentos.
        """
        session = requests.Session()
        session.headers.update({
            **config.HTTP_HEADERS,
            "Accept": "application/pdf,application/octet-stream,*/*;q=0.8",
            "Referer": "https://contrataciondelsectorpublico.gob.es/",
        })
        transport_retry = Retry(
            total=3,
            backoff_factor=1.0,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=transport_retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def _es_pdf_valido(self, contenido: bytes) -> bool:
        """
        Verifica que el contenido descargado es un PDF real comprobando
        los magic bytes (%PDF al inicio del fichero).

        Sin esta verificación, podríamos guardar páginas de error HTML
        como si fueran PDFs, lo que rompería el Step 3 silenciosamente.
        """
        return contenido[:4] == _PDF_MAGIC

    def descargar(
        self,
        url: str,
        nombre_sugerido: Optional[str],
        licitacion_id: str,
    ) -> Optional[Path]:
        """
        Descarga un PDF y lo guarda en el directorio de destino.

        Args:
            url:             URL directa del PDF (cbc_uri del feed).
            nombre_sugerido: Nombre descriptivo (cbc_filename del feed).
            licitacion_id:   ID de la licitación (para el nombre del fichero).

        Returns:
            Path al fichero guardado, o None si la descarga falló.
        """
        nombre_fichero = generar_nombre_pdf(licitacion_id, nombre_sugerido)
        ruta_destino = self.directorio_destino / nombre_fichero

        # Si ya existe (ejecución anterior), no re-descargamos
        if ruta_destino.exists() and ruta_destino.stat().st_size > 0:
            logger.debug("PDF ya existe, omitiendo descarga: %s", nombre_fichero)
            return ruta_destino

        logger.info("Descargando PDF: %s → %s", nombre_sugerido or "sin nombre", nombre_fichero)

        try:
            resp = self._http.get(url, timeout=self.timeout, stream=True)
            resp.raise_for_status()

            # Descargamos en memoria primero para verificar magic bytes
            # antes de escribir en disco (evita ficheros corruptos a medias)
            contenido = b""
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    contenido += chunk

            if not contenido:
                logger.warning("Respuesta vacía descargando PDF: %s", url[:80])
                return None

            if not self._es_pdf_valido(contenido):
                # El servidor devolvió algo que no es un PDF
                # (puede ser una página de error HTML o un redirect)
                inicio = contenido[:200].decode("utf-8", errors="replace")
                logger.warning(
                    "El contenido descargado no es un PDF válido. "
                    "Inicio del contenido: %s", inicio
                )
                return None

            ruta_destino.write_bytes(contenido)
            logger.info(
                "PDF guardado: %s (%.1f KB)",
                nombre_fichero,
                len(contenido) / 1024,
            )
            return ruta_destino

        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response else "?"
            logger.error("HTTP %s descargando PDF %s: %s", status, nombre_fichero, exc)
            return None
        except requests.RequestException as exc:
            logger.error("Error de red descargando PDF %s: %s", nombre_fichero, exc)
            return None
        except OSError as exc:
            logger.error("Error guardando PDF en disco %s: %s", ruta_destino, exc)
            return None

    def descargar_lote(
        self,
        licitaciones: list,
        db,
    ) -> dict:
        """
        Descarga los PDFs de una lista de licitaciones y actualiza la BD.

        Args:
            licitaciones: Lista de objetos Licitacion (ORM) con url_pdf_ppt.
            db:           Instancia de DatabaseManager para actualizar estados.

        Returns:
            Dict con métricas: descargados, omitidos, errores.
        """
        stats = {"descargados": 0, "omitidos": 0, "errores": 0}

        for i, licit in enumerate(licitaciones, start=1):
            if not licit.url_pdf_ppt:
                logger.debug("Sin URL de PDF: %.50s — omitida.", licit.titulo)
                stats["omitidos"] += 1
                continue

            ruta = self.descargar(
                url=licit.url_pdf_ppt,
                nombre_sugerido=licit.nombre_pdf,
                licitacion_id=licit.id,
            )

            if ruta:
                with db.session() as session:
                    db.marcar_pdf_descargado(session, licit.id, str(ruta))
                stats["descargados"] += 1
            else:
                with db.session() as session:
                    db.actualizar_estado(
                        session, licit.id,
                        config.EstadoLicitacion.ERROR,
                        error_msg="Fallo en descarga de PDF",
                    )
                stats["errores"] += 1

            # Rate limiting: respetamos al servidor de la PCSP
            if i < len(licitaciones):
                time.sleep(self.delay)

        logger.info(
            "Descarga de lote completada: %d descargados | %d omitidos | %d errores.",
            stats["descargados"], stats["omitidos"], stats["errores"],
        )
        return stats
