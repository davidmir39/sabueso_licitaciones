"""
src/db_manager.py — Sabueso de Licitaciones
=============================================
Única capa de acceso a la base de datos.

Responsabilidades (y SOLO estas):
  · Crear/verificar el schema al arrancar.
  · Proveer un context manager de sesión con commit/rollback automático.
  · CRUD de licitaciones y log de ejecuciones.

Lo que NO hace este módulo:
  · Parsear HTML o feeds (eso es utils.py / scraper_atom.py).
  · Formatear output para consola (eso es main.py).
  · Decidir qué licitaciones son relevantes (eso será ia_analyzer.py).
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional

from sqlalchemy import create_engine, func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from src.logger import get_logger
from src.models import Base, Licitacion, LicitacionSchema, LogEjecucion

logger = get_logger(__name__)


class DatabaseManager:
    """
    Gestor centralizado de la base de datos.

    Diseñado para instanciarse una vez en main.py y reutilizarse
    durante toda la ejecución (no es thread-safe por defecto con SQLite,
    pero para este caso de uso single-process es suficiente).

    Uso:
        db = DatabaseManager()
        with db.session() as session:
            db.insertar_licitacion(session, schema)
    """

    def __init__(self, database_url: str = config.DATABASE_URL):
        self.database_url = database_url
        self._engine = create_engine(
            database_url,
            echo=False,
            pool_pre_ping=True,
            connect_args=(
                {"check_same_thread": False} if "sqlite" in database_url else {}
            ),
        )
        self._SessionFactory = sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
        )
        self._inicializar_schema()
        logger.info("DatabaseManager listo: %s", database_url)

    def _inicializar_schema(self) -> None:
        """Crea tablas si no existen. Idempotente y seguro en cada arranque."""
        try:
            Base.metadata.create_all(self._engine)
            logger.debug("Schema de BD verificado correctamente.")
        except SQLAlchemyError as exc:
            logger.critical("Error fatal inicializando schema de BD: %s", exc)
            raise

    # ── Context manager de sesión ─────────────────────────────────────────────

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        """
        Context manager que garantiza:
          · commit() automático si el bloque termina sin excepción.
          · rollback() automático si se lanza cualquier excepción.
          · close() siempre, para liberar la conexión al pool.

        Uso:
            with db.session() as session:
                session.add(objeto)
                # commit ocurre aquí automáticamente
        """
        sess = self._SessionFactory()
        try:
            yield sess
            sess.commit()
        except SQLAlchemyError as exc:
            sess.rollback()
            logger.error("Rollback ejecutado por error de BD: %s", exc)
            raise
        finally:
            sess.close()

    # ── ESCRITURA ─────────────────────────────────────────────────────────────

    def insertar_licitacion(
        self,
        session: Session,
        schema: LicitacionSchema,
    ) -> tuple[bool, str]:
        """
        Inserta una licitación nueva de forma idempotente.

        Verifica existencia antes de insertar (más eficiente que capturar
        IntegrityError) pero también maneja el caso de race condition.

        Returns:
            (True,  "INSERTADA")  → licitación nueva, guardada en BD.
            (False, "DUPLICADA")  → ya existía, ignorada correctamente.
            (False, "ERROR")      → fallo inesperado de BD.
        """
        if session.get(Licitacion, schema.id):
            logger.debug("Duplicada (ya existe): %.60s", schema.id)
            return False, "DUPLICADA"

        try:
            licitacion = Licitacion(
                id=schema.id,
                titulo=schema.titulo,
                link_plataforma=schema.link_plataforma,
                fecha_publicacion=schema.fecha_publicacion,
                fecha_actualizacion=schema.fecha_actualizacion,
                organo_contratacion=schema.organo_contratacion,
                nif_organo=schema.nif_organo,
                tipo_contrato=schema.tipo_contrato,
                presupuesto_base=schema.presupuesto_base,
                valor_estimado=schema.valor_estimado,
                cpv_codigo=schema.cpv_codigo,
                cpv_descripcion=schema.cpv_descripcion,
                lugar_ejecucion=schema.lugar_ejecucion,
                estado_contrato=schema.estado_contrato,
                expediente=schema.expediente,
                raw_summary_html=schema.raw_summary_html,
                estado_proceso=config.EstadoLicitacion.NUEVA,
            )
            session.add(licitacion)
            logger.info(
                "Nueva: %.65s | Órgano: %s | Presupuesto: %s€",
                schema.titulo,
                schema.organo_contratacion or "N/A",
                schema.presupuesto_base or "N/A",
            )
            return True, "INSERTADA"

        except IntegrityError:
            session.rollback()
            logger.warning("Race condition en inserción: %.60s", schema.id)
            return False, "DUPLICADA"
        except SQLAlchemyError as exc:
            session.rollback()
            logger.error("Error de BD insertando %.60s: %s", schema.id, exc)
            return False, "ERROR"

    def actualizar_estado(
        self,
        session: Session,
        licitacion_id: str,
        nuevo_estado: str,
        error_msg: Optional[str] = None,
    ) -> bool:
        """Actualiza el estado del pipeline de una licitación concreta."""
        try:
            result = session.execute(
                update(Licitacion)
                .where(Licitacion.id == licitacion_id)
                .values(
                    estado_proceso=nuevo_estado,
                    updated_at=datetime.utcnow(),
                    error_msg=error_msg,
                )
            )
            if result.rowcount == 0:
                logger.warning("actualizar_estado: ID no encontrado %.60s", licitacion_id)
                return False
            logger.debug("Estado → %s para %.50s", nuevo_estado, licitacion_id)
            return True
        except SQLAlchemyError as exc:
            logger.error("Error actualizando estado: %s", exc)
            return False

    def guardar_urls_pdf(
        self,
        session: Session,
        licitacion_id: str,
        url_ppt: Optional[str] = None,
        url_pca: Optional[str] = None,
    ) -> bool:
        """
        Persiste las URLs de PDFs encontradas por el scraper de detalle (Step 2).
        Actualiza automáticamente el estado a PDF_PENDIENTE si hay al menos una URL.
        """
        try:
            values: dict = {"updated_at": datetime.utcnow()}
            if url_ppt:
                values["url_pdf_ppt"] = url_ppt
            if url_pca:
                values["url_pdf_pca"] = url_pca
            if url_ppt or url_pca:
                values["estado_proceso"] = config.EstadoLicitacion.PDF_PENDIENTE
            session.execute(
                update(Licitacion).where(Licitacion.id == licitacion_id).values(**values)
            )
            return True
        except SQLAlchemyError as exc:
            logger.error("Error guardando URLs PDF: %s", exc)
            return False

    # ── LECTURA ───────────────────────────────────────────────────────────────

    def obtener_por_estado(
        self,
        session: Session,
        estado: str,
        limite: int = 50,
    ) -> list[Licitacion]:
        """Devuelve licitaciones filtradas por estado, ordenadas por fecha desc."""
        return list(
            session.scalars(
                select(Licitacion)
                .where(Licitacion.estado_proceso == estado)
                .order_by(Licitacion.fecha_publicacion.desc())
                .limit(limite)
            ).all()
        )

    def obtener_recientes(
        self,
        session: Session,
        limite: int = 20,
    ) -> list[Licitacion]:
        """Devuelve las N licitaciones más recientes por fecha de publicación."""
        return list(
            session.scalars(
                select(Licitacion)
                .order_by(Licitacion.fecha_publicacion.desc().nulls_last())
                .limit(limite)
            ).all()
        )

    def estadisticas(self, session: Session) -> dict:
        """
        Resumen del estado actual de la BD.
        Devuelve: {'por_estado': {estado: count}, 'total': N, 'ultima_actualizacion': dt}
        """
        estados = session.execute(
            select(Licitacion.estado_proceso, func.count(Licitacion.id))
            .group_by(Licitacion.estado_proceso)
        ).all()
        por_estado = {e: c for e, c in estados}
        return {
            "por_estado": por_estado,
            "total": sum(por_estado.values()),
            "ultima_actualizacion": session.execute(
                select(func.max(Licitacion.updated_at))
            ).scalar(),
        }

    # ── LOG DE EJECUCIONES ────────────────────────────────────────────────────

    def iniciar_log_ejecucion(self, session: Session, feed_url: str) -> LogEjecucion:
        """Registra el inicio de un run. Hace flush para obtener el ID."""
        log = LogEjecucion(feed_url=feed_url, estado_run="EN_CURSO")
        session.add(log)
        session.flush()
        logger.debug("Log de ejecución iniciado: id=%s", log.id)
        return log

    def finalizar_log_ejecucion(
        self,
        session: Session,
        log: LogEjecucion,
        total_feed: int,
        nuevas: int,
        duplicadas: int,
        errores: int,
        estado: str = "OK",
        mensaje: Optional[str] = None,
    ) -> None:
        """Cierra el registro del run con las métricas finales."""
        log.timestamp_fin = datetime.utcnow()
        log.total_entradas_feed = total_feed
        log.nuevas_insertadas = nuevas
        log.ya_existian = duplicadas
        log.errores = errores
        log.estado_run = estado
        log.mensaje = mensaje
        session.add(log)
        logger.info(
            "Log run #%s cerrado: estado=%s | nuevas=%d | duplicadas=%d | errores=%d",
            log.id, estado, nuevas, duplicadas, errores,
        )
