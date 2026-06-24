"""
src/db_manager.py — Sabueso de Licitaciones
=============================================
Única capa de acceso a la base de datos.

CAMBIOS v3 (Fase 0 Paso 2):
  · Reemplazado datetime.utcnow() (deprecated) por ahora_utc() de models.py.
  · Eliminado el import de datetime (ya no se usa directamente aquí).

CAMBIOS v2 (Step 2):
  · insertar_licitacion guarda url_pdf_ppt y nombre_pdf desde el schema.
  · Si url_pdf_directo está presente, el estado inicial es PDF_PENDIENTE
    en lugar de NUEVA (ya tenemos la URL, solo falta descargar).
  · Nuevo método: obtener_pendientes_pdf → licitaciones listas para descargar.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator, Optional

from sqlalchemy import create_engine, func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

import config
from src.logger import get_logger
from src.models import (
           Base, Licitacion, LicitacionSchema, LogEjecucion, ahora_utc,
           Cliente, PerfilInteres, MatchLicitacion,
       )
logger = get_logger(__name__)


class DatabaseManager:
    """
    Gestor centralizado de la base de datos.

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
        try:
            Base.metadata.create_all(self._engine)
            logger.debug("Schema de BD verificado.")
        except SQLAlchemyError as exc:
            logger.critical("Error inicializando schema: %s", exc)
            raise

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        """
        Context manager con commit/rollback automático.
        Garantiza que la conexión siempre se libera.
        """
        sess = self._SessionFactory()
        try:
            yield sess
            sess.commit()
        except SQLAlchemyError as exc:
            sess.rollback()
            logger.error("Rollback ejecutado: %s", exc)
            raise
        finally:
            sess.close()

    # ── ESCRITURA ─────────────────────────────────────────────────────────────

    def insertar_licitacion(
        self,
        session: Session,
        schema: LicitacionSchema,
        estado_forzado: Optional[str] = None,
    ) -> tuple[bool, str]:
        """
        Inserta una licitación de forma idempotente.

        Estado inicial:
          · PDF_PENDIENTE si url_pdf_directo está presente en el feed
          · NUEVA si no hay URL de PDF en el feed

        Returns:
            (True, "INSERTADA") | (False, "DUPLICADA") | (False, "ERROR")
        """
        if session.get(Licitacion, schema.id):
            logger.debug("Duplicada: %.60s", schema.id)
            return False, "DUPLICADA"

        # Determinamos el estado inicial.
        # Si nos fuerzan un estado (ej: Tarea 0 → licitación ya cerrada),
        # lo respetamos. Si no, decidimos según si el feed incluye la URL del PDF.
        if estado_forzado:
            estado_inicial = estado_forzado
        else:
            estado_inicial = (
                config.EstadoLicitacion.PDF_PENDIENTE
                if schema.url_pdf_directo
                else config.EstadoLicitacion.NUEVA
            )

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
                url_pdf_ppt=schema.url_pdf_directo,   # URL directa del feed
                nombre_pdf=schema.nombre_pdf,
                raw_summary_html=schema.raw_summary_html,
                estado_proceso=estado_inicial,
            )
            session.add(licitacion)
            logger.info(
                "[%s] %.65s | %s | %s€",
                estado_inicial,
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
            logger.error("Error BD insertando: %s", exc)
            return False, "ERROR"

    def actualizar_estado(
        self,
        session: Session,
        licitacion_id: str,
        nuevo_estado: str,
        error_msg: Optional[str] = None,
    ) -> bool:
        """Actualiza el estado del pipeline de una licitación."""
        try:
            result = session.execute(
                update(Licitacion)
                .where(Licitacion.id == licitacion_id)
                .values(
                    estado_proceso=nuevo_estado,
                    updated_at=ahora_utc(),   # Paso 2: reemplaza datetime.utcnow()
                    error_msg=error_msg,
                )
            )
            if result.rowcount == 0:
                logger.warning("ID no encontrado para actualizar: %.60s", licitacion_id)
                return False
            return True
        except SQLAlchemyError as exc:
            logger.error("Error actualizando estado: %s", exc)
            return False

    def marcar_pdf_descargado(
        self,
        session: Session,
        licitacion_id: str,
        ruta_local: str,
    ) -> bool:
        """
        Registra que el PDF se ha descargado correctamente.
        Actualiza ruta_pdf_local y estado → PDF_DESCARGADO.
        """
        try:
            session.execute(
                update(Licitacion)
                .where(Licitacion.id == licitacion_id)
                .values(
                    ruta_pdf_local=ruta_local,
                    estado_proceso=config.EstadoLicitacion.PDF_DESCARGADO,
                    updated_at=ahora_utc(),   # Paso 2: reemplaza datetime.utcnow()
                    error_msg=None,
                )
            )
            logger.debug("PDF descargado registrado: %.50s → %s", licitacion_id, ruta_local)
            return True
        except SQLAlchemyError as exc:
            logger.error("Error marcando PDF descargado: %s", exc)
            return False

    

    # ── MÉTODO 1: obtener PDFs listos para extraer texto ─────────────────────────

    def obtener_pendientes_extraccion(
        self,
        session: Session,
        limite: int = 50,
    ) -> list[Licitacion]:
        """
        Devuelve licitaciones en estado PDF_DESCARGADO que tienen ruta_pdf_local.
        Son las candidatas para el Step 3 (extracción de texto).
        """
        return list(
            session.scalars(
                select(Licitacion)
                .where(
                    Licitacion.estado_proceso == config.EstadoLicitacion.PDF_DESCARGADO,
                    Licitacion.ruta_pdf_local.is_not(None),
                )
                .order_by(Licitacion.fecha_publicacion.desc())
                .limit(limite)
            ).all()
        )
# ── MÉTODO 2: guardar texto extraído y avanzar estado ────────────────────────

    def marcar_texto_extraido(
        self,
        session: Session,
        licitacion_id: str,
        texto: str,
    ) -> bool:
        """
        Guarda el texto extraído del PDF y avanza el estado a ANALISIS_PENDIENTE.

        ANALISIS_PENDIENTE significa: "tenemos el texto, falta que la IA
        lo analice para decidir si es relevante".
        """
        try:
            session.execute(
                update(Licitacion)
                .where(Licitacion.id == licitacion_id)
                .values(
                    texto_extraido=texto,
                    estado_proceso=config.EstadoLicitacion.ANALISIS_PENDIENTE,
                    updated_at=ahora_utc(),
                    error_msg=None,
                )
            )
            logger.debug(
                "Texto extraído guardado: %.50s → %d chars",
                licitacion_id,
                len(texto),
            )
            return True
        except SQLAlchemyError as exc:
            logger.error("Error guardando texto extraído: %s", exc)
            return False

    # ── MÉTODOS NUEVOS FASE 1C ────────────────────────────────────────────────

    def obtener_pendientes_analisis(
        self,
        session: Session,
        limite: int = 50,
    ) -> list[Licitacion]:
        """
        Devuelve licitaciones en estado ANALISIS_PENDIENTE.
        Son las que ya tienen texto extraído y esperan ser analizadas por la IA.
        """
        return list(
            session.scalars(
                select(Licitacion)
                .where(
                    Licitacion.estado_proceso == config.EstadoLicitacion.ANALISIS_PENDIENTE,
                    Licitacion.texto_extraido.is_not(None),
                )
                .order_by(Licitacion.fecha_publicacion.desc())
                .limit(limite)
            ).all()
        )

    def obtener_perfiles_activos(self, session: Session) -> list[PerfilInteres]:
        """
        Devuelve todos los perfiles activos de clientes activos.
        Solo estos perfiles participan en el análisis de relevancia.
        """
        return list(
            session.scalars(
                select(PerfilInteres)
                .join(Cliente, PerfilInteres.cliente_id == Cliente.id)
                .where(
                    PerfilInteres.activo == True,
                    Cliente.activo == True,
                )
            ).all()
        )

    def guardar_match(
        self,
        session: Session,
        licitacion_id: str,
        perfil_id: int,
        paso_filtro_a: bool,
        score_ia: Optional[int],
        razon_ia: Optional[str],
    ) -> bool:
        """
        Guarda el resultado del análisis de una licitación para un perfil.

        Es idempotente: si el match ya existe (de una ejecución anterior),
        no lo duplica (la BD tiene un índice UNIQUE sobre licitacion+perfil).

        Returns:
            True si se guardó correctamente, False si hubo error.
        """
        try:
            # Usamos INSERT OR IGNORE equivalente: intentamos insertar
            # y si ya existe el par (licitacion_id, perfil_id), ignoramos.
            # En SQLAlchemy esto se hace comprobando antes.
            from sqlalchemy import and_
            existente = session.scalar(
                select(MatchLicitacion).where(
                    and_(
                        MatchLicitacion.licitacion_id == licitacion_id,
                        MatchLicitacion.perfil_id == perfil_id,
                    )
                )
            )

            if existente:
                logger.debug(
                    "Match ya existe: licitacion=%.40s perfil=%d",
                    licitacion_id, perfil_id,
                )
                return True

            match = MatchLicitacion(
                licitacion_id=licitacion_id,
                perfil_id=perfil_id,
                paso_filtro_a=paso_filtro_a,
                score_ia=score_ia,
                razon_ia=razon_ia,
                notificado=False,
            )
            session.add(match)
            return True

        except SQLAlchemyError as exc:
            logger.error("Error guardando match: %s", exc)
            return False

    def marcar_licitacion_analizada(
        self,
        session: Session,
        licitacion_id: str,
        es_relevante: bool,
    ) -> bool:
        """
        Actualiza el estado final de la licitación tras el análisis.

        Si algún perfil la encontró relevante → RELEVANTE
        Si ningún perfil la encontró relevante → DESCARTADA
        """
        nuevo_estado = (
            config.EstadoLicitacion.RELEVANTE
            if es_relevante
            else config.EstadoLicitacion.DESCARTADA
        )
        return self.actualizar_estado(session, licitacion_id, nuevo_estado)
    

    def obtener_matches_para_notificar(
        self,
        session: Session,
        limite: int = 50,
    ) -> list[dict]:
        """
        Devuelve los matches relevantes que aún no se han notificado.

        Un match entra en la lista si cumple TODO esto:
          · score_ia >= umbral de relevancia (es interesante)
          · notificado == False (aún no se ha avisado al cliente)
          · el perfil y el cliente están activos

        Devuelve DICCIONARIOS PLANOS (no objetos ORM) con los datos que el
        email necesita. Lo hacemos así para extraer todo dentro de la sesión
        y evitar errores de "lazy loading" al acceder a las relaciones
        (match.perfil.cliente) una vez cerrada la sesión.

        Returns:
            Lista de dicts con: match_id, email_cliente, nombre_cliente,
            nombre_perfil, titulo, organo, presupuesto, link, score, razon.
        """
        # Recorremos matches uniéndolos con perfil y cliente para filtrar
        # por actividad y umbral en una sola consulta.
        matches = session.scalars(
            select(MatchLicitacion)
            .join(PerfilInteres, MatchLicitacion.perfil_id == PerfilInteres.id)
            .join(Cliente, PerfilInteres.cliente_id == Cliente.id)
            .where(
                MatchLicitacion.notificado == False,
                MatchLicitacion.score_ia.is_not(None),
                MatchLicitacion.score_ia >= config.SCORE_RELEVANCIA_MINIMO,
                PerfilInteres.activo == True,
                Cliente.activo == True,
            )
            .order_by(MatchLicitacion.score_ia.desc())
            .limit(limite)
        ).all()

        # Extraemos los datos a diccionarios planos DENTRO de la sesión.
        # Aquí sí podemos acceder a las relaciones (match.perfil, match.licitacion)
        # porque la sesión está abierta.
        resultado = []
        for match in matches:
            licitacion = match.licitacion
            cliente = match.perfil.cliente
            resultado.append({
                "match_id": match.id,
                "email_cliente": cliente.email,
                "nombre_cliente": cliente.nombre,
                "nombre_perfil": match.perfil.nombre,
                "titulo": licitacion.titulo,
                "organo": licitacion.organo_contratacion,
                "presupuesto": licitacion.presupuesto_base,
                "link": licitacion.link_plataforma,
                "score": match.score_ia,
                "razon": match.razon_ia,
            })

        return resultado

    # ── LECTURA ───────────────────────────────────────────────────────────────
    def obtener_pendientes_pdf(
        self,
        session: Session,
        limite: int = 50,
    ) -> list[Licitacion]:
        """
        Devuelve licitaciones en estado PDF_PENDIENTE que tienen URL de PDF.
        Son las candidatas para la descarga en el Step 2.
        """
        return list(
            session.scalars(
                select(Licitacion)
                .where(
                    Licitacion.estado_proceso == config.EstadoLicitacion.PDF_PENDIENTE,
                    Licitacion.url_pdf_ppt.is_not(None),
                )
                .order_by(Licitacion.fecha_publicacion.desc())
                .limit(limite)
            ).all()
        )

    def obtener_por_estado(
        self,
        session: Session,
        estado: str,
        limite: int = 50,
    ) -> list[Licitacion]:
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
        return list(
            session.scalars(
                select(Licitacion)
                .order_by(Licitacion.fecha_publicacion.desc().nulls_last())
                .limit(limite)
            ).all()
        )

    def estadisticas(self, session: Session) -> dict:
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
        log = LogEjecucion(feed_url=feed_url, estado_run="EN_CURSO")
        session.add(log)
        session.flush()
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
        log.timestamp_fin = ahora_utc()   # Paso 2: reemplaza datetime.utcnow()
        log.total_entradas_feed = total_feed
        log.nuevas_insertadas = nuevas
        log.ya_existian = duplicadas
        log.errores = errores
        log.estado_run = estado
        log.mensaje = mensaje
        session.add(log)
        logger.info(
            "Log run #%s cerrado: estado=%s | nuevas=%d | dupl=%d | err=%d",
            log.id, estado, nuevas, duplicadas, errores,
        )
