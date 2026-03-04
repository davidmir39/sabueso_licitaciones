"""
src/db_manager.py — Sabueso de Licitaciones
=============================================
Gestión de base de datos con SQLAlchemy 2.0.

Principios de diseño:
  - Context Manager para sesiones (garantiza commit/rollback automático)
  - Idempotencia: INSERT OR IGNORE via merge() de SQLAlchemy
  - Separación de responsabilidades: este módulo SOLO habla con la BD
  - Preparado para migración a PostgreSQL cambiando solo DATABASE_URL
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional

from sqlalchemy import create_engine, select, func, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

# Asegurar imports relativos correctos
sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from src.logger import get_logger
from src.models import Base, Licitacion, LicitacionSchema, LogEjecucion

logger = get_logger(__name__)


class DatabaseManager:
    """
    Gestor centralizado de la base de datos.
    
    Uso básico:
        db = DatabaseManager()
        with db.session() as session:
            licitaciones = db.obtener_pendientes(session)
    
    Diseñado para ser instanciado una vez (singleton en main.py)
    y reutilizado durante toda la ejecución.
    """

    def __init__(self, database_url: str = config.DATABASE_URL):
        self.database_url = database_url
        self._engine = create_engine(
            database_url,
            echo=False,          # True para ver SQL en desarrollo
            pool_pre_ping=True,  # Verifica conexión antes de usarla
            connect_args=(
                {"check_same_thread": False}
                if "sqlite" in database_url
                else {}
            ),
        )
        self._SessionFactory = sessionmaker(
            bind=self._engine,
            expire_on_commit=False,  # Permite acceder a atributos post-commit
        )
        self._inicializar_schema()
        logger.info(f"DatabaseManager inicializado: {database_url}")

    def _inicializar_schema(self) -> None:
        """
        Crea las tablas si no existen. Idempotente.
        En producción, usa Alembic para migraciones controladas.
        """
        try:
            Base.metadata.create_all(self._engine)
            logger.debug("Schema verificado/creado correctamente")
        except SQLAlchemyError as e:
            logger.critical(f"Error fatal inicializando schema: {e}")
            raise

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        """
        Context manager para sesiones de BD.
        Garantiza commit en éxito y rollback automático en excepción.
        
        Uso:
            with db.session() as session:
                session.add(objeto)
                # commit automático al salir del with
        """
        sess = self._SessionFactory()
        try:
            yield sess
            sess.commit()
        except SQLAlchemyError as e:
            sess.rollback()
            logger.error(f"Error en sesión BD, rollback ejecutado: {e}")
            raise
        finally:
            sess.close()

    # -----------------------------------------------------------------------
    # ESCRITURA
    # -----------------------------------------------------------------------

    def insertar_licitacion(
        self,
        session: Session,
        schema: LicitacionSchema,
    ) -> tuple[bool, str]:
        """
        Inserta una licitación si no existe (idempotente).
        
        Returns:
            (True, "INSERTADA") si es nueva
            (False, "DUPLICADA") si ya existe en BD
            (False, "ERROR") si falló la inserción
        """
        # Verificar existencia ANTES de intentar insertar (más eficiente que catch)
        existe = session.get(Licitacion, schema.id)
        if existe:
            logger.debug(f"[DUPLICADA] {schema.id[:50]}")
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
                f"[NUEVA] {schema.titulo[:70]} | "
                f"Órgano: {schema.organo_contratacion or 'N/A'} | "
                f"Presupuesto: {schema.presupuesto_base or 'N/A'}€"
            )
            return True, "INSERTADA"

        except IntegrityError:
            session.rollback()
            logger.warning(f"[RACE CONDITION] ID duplicado en inserción: {schema.id[:50]}")
            return False, "DUPLICADA"
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"[ERROR BD] Fallo insertando {schema.id[:50]}: {e}")
            return False, "ERROR"

    def actualizar_estado(
        self,
        session: Session,
        licitacion_id: str,
        nuevo_estado: str,
        error_msg: Optional[str] = None,
    ) -> bool:
        """Actualiza el estado del ciclo de vida de una licitación."""
        try:
            stmt = (
                update(Licitacion)
                .where(Licitacion.id == licitacion_id)
                .values(
                    estado_proceso=nuevo_estado,
                    updated_at=datetime.utcnow(),
                    error_msg=error_msg,
                )
            )
            result = session.execute(stmt)
            if result.rowcount == 0:
                logger.warning(f"actualizar_estado: ID no encontrado {licitacion_id[:50]}")
                return False
            logger.debug(f"Estado actualizado → {nuevo_estado} para {licitacion_id[:50]}")
            return True
        except SQLAlchemyError as e:
            logger.error(f"Error actualizando estado: {e}")
            return False

    def guardar_urls_pdf(
        self,
        session: Session,
        licitacion_id: str,
        url_ppt: Optional[str] = None,
        url_pca: Optional[str] = None,
    ) -> bool:
        """Persiste las URLs de los PDFs encontrados en el scraping de detalle."""
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
        except SQLAlchemyError as e:
            logger.error(f"Error guardando URLs PDF: {e}")
            return False

    # -----------------------------------------------------------------------
    # LECTURA
    # -----------------------------------------------------------------------

    def obtener_por_estado(
        self,
        session: Session,
        estado: str,
        limite: int = 50,
    ) -> list[Licitacion]:
        """Obtiene licitaciones filtradas por estado del pipeline."""
        stmt = (
            select(Licitacion)
            .where(Licitacion.estado_proceso == estado)
            .order_by(Licitacion.fecha_publicacion.desc())
            .limit(limite)
        )
        return list(session.scalars(stmt).all())

    def obtener_recientes(
        self,
        session: Session,
        limite: int = 20,
    ) -> list[Licitacion]:
        """Obtiene las N licitaciones más recientes por fecha de publicación."""
        stmt = (
            select(Licitacion)
            .order_by(Licitacion.fecha_publicacion.desc().nulls_last())
            .limit(limite)
        )
        return list(session.scalars(stmt).all())

    def estadisticas(self, session: Session) -> dict:
        """
        Retorna un resumen del estado actual de la BD.
        Útil para dashboards y logs de ejecución.
        """
        stats = {}
        estados = session.execute(
            select(Licitacion.estado_proceso, func.count(Licitacion.id))
            .group_by(Licitacion.estado_proceso)
        ).all()
        stats["por_estado"] = {estado: count for estado, count in estados}
        stats["total"] = sum(stats["por_estado"].values())
        stats["ultima_actualizacion"] = session.execute(
            select(func.max(Licitacion.updated_at))
        ).scalar()
        return stats

    # -----------------------------------------------------------------------
    # LOG DE EJECUCIONES
    # -----------------------------------------------------------------------

    def iniciar_log_ejecucion(self, session: Session, feed_url: str) -> LogEjecucion:
        """Registra el inicio de un run. Retorna el objeto para actualización posterior."""
        log = LogEjecucion(feed_url=feed_url, estado_run="EN_CURSO")
        session.add(log)
        session.flush()  # Para obtener el ID antes del commit
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
        """Cierra el registro del run con estadísticas finales."""
        log.timestamp_fin = datetime.utcnow()
        log.total_entradas_feed = total_feed
        log.nuevas_insertadas = nuevas
        log.ya_existian = duplicadas
        log.errores = errores
        log.estado_run = estado
        log.mensaje = mensaje
        session.add(log)
