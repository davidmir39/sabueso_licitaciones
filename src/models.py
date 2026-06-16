"""
src/models.py — Sabueso de Licitaciones
==========================================
Modelos de datos del proyecto.

Capas:
  1. LicitacionSchema  → dataclass de transferencia (scraper → db_manager)
  2. Licitacion        → ORM, tabla 'licitaciones'
  3. LogEjecucion      → ORM, auditoría de runs del pipeline
  4. Cliente           → ORM, tabla 'clientes' (NUEVO Fase 1A)
  5. PerfilInteres     → ORM, tabla 'perfiles_interes' (NUEVO Fase 1A)
  6. MatchLicitacion   → ORM, tabla 'matches' (NUEVO Fase 1A)

CAMBIOS Fase 1A:
  · Añadidas tablas Cliente, PerfilInteres y MatchLicitacion.
  · Estas tablas representan el concepto de "multi-tenancy ligero":
    varios clientes comparten la misma instancia del sistema, cada uno
    con sus propios perfiles de búsqueda y sus propios resultados.

CAMBIOS v3 (Fase 0 Paso 2):
  · Añadida función ahora_utc() como reemplazo de datetime.utcnow()
    (deprecated desde Python 3.12).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Index,
    Integer, String, Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ──────────────────────────────────────────────────────────────────────────────
# HELPER DE FECHA/HORA
# ──────────────────────────────────────────────────────────────────────────────

def ahora_utc() -> datetime:
    """
    Devuelve la hora UTC actual como datetime naive (sin zona horaria).

    Usamos la API moderna datetime.now(timezone.utc) pero eliminamos el
    tzinfo con .replace(tzinfo=None) para compatibilidad con columnas
    DateTime de SQLite que esperan datetimes naive.

    Si algún día migramos a columnas DateTime(timezone=True), solo hay
    que cambiar esta función en un sitio.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ──────────────────────────────────────────────────────────────────────────────
# BASE ORM
# ──────────────────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


# ──────────────────────────────────────────────────────────────────────────────
# SCHEMA DE TRANSFERENCIA  (scraper → db_manager)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class LicitacionSchema:
    """
    Contenedor de datos que viaja desde AtomScraper hasta DatabaseManager.
    Solo Python estándar, sin dependencias de SQLAlchemy.
    """
    # Obligatorios
    id: str
    titulo: str

    # Feed ATOM
    link_plataforma: Optional[str] = None
    fecha_publicacion: Optional[datetime] = None
    fecha_actualizacion: Optional[datetime] = None

    # Campos UBL y summary
    organo_contratacion: Optional[str] = None
    nif_organo: Optional[str] = None
    tipo_contrato: Optional[str] = None
    presupuesto_base: Optional[float] = None
    valor_estimado: Optional[float] = None
    cpv_codigo: Optional[str] = None
    cpv_descripcion: Optional[str] = None
    lugar_ejecucion: Optional[str] = None
    estado_contrato: Optional[str] = None
    expediente: Optional[str] = None

    # URL y nombre del PDF (del campo cbc_uri del feed)
    url_pdf_directo: Optional[str] = None
    nombre_pdf: Optional[str] = None

    # Raw
    raw_summary_html: Optional[str] = None


# Alias semántico
LicitacionResumen = LicitacionSchema


# ──────────────────────────────────────────────────────────────────────────────
# MODELO ORM — licitaciones
# ──────────────────────────────────────────────────────────────────────────────

class Licitacion(Base):
    __tablename__ = "licitaciones"

    # Identificación
    id: Mapped[str] = mapped_column(String(512), primary_key=True)
    titulo: Mapped[str] = mapped_column(Text, nullable=False)
    link_plataforma: Mapped[Optional[str]] = mapped_column(Text)

    # Metadatos del feed
    fecha_publicacion: Mapped[Optional[datetime]] = mapped_column(DateTime)
    fecha_actualizacion: Mapped[Optional[datetime]] = mapped_column(DateTime)
    organo_contratacion: Mapped[Optional[str]] = mapped_column(Text)
    nif_organo: Mapped[Optional[str]] = mapped_column(String(30))
    tipo_contrato: Mapped[Optional[str]] = mapped_column(String(100))
    presupuesto_base: Mapped[Optional[float]] = mapped_column(Float)
    valor_estimado: Mapped[Optional[float]] = mapped_column(Float)
    cpv_codigo: Mapped[Optional[str]] = mapped_column(String(20))
    cpv_descripcion: Mapped[Optional[str]] = mapped_column(Text)
    lugar_ejecucion: Mapped[Optional[str]] = mapped_column(Text)
    estado_contrato: Mapped[Optional[str]] = mapped_column(String(20))
    expediente: Mapped[Optional[str]] = mapped_column(String(120))

    # Pipeline
    estado_proceso: Mapped[str] = mapped_column(
        String(30), nullable=False, default="NUEVA"
    )
    url_pdf_ppt: Mapped[Optional[str]] = mapped_column(Text)
    url_pdf_pca: Mapped[Optional[str]] = mapped_column(Text)
    nombre_pdf: Mapped[Optional[str]] = mapped_column(Text)
    ruta_pdf_local: Mapped[Optional[str]] = mapped_column(Text)

    # Texto extraído del PDF (Step 3)
    texto_extraido: Mapped[Optional[str]] = mapped_column(Text)

    # IA (Steps 3-4)
    resumen_ia: Mapped[Optional[str]] = mapped_column(Text)
    es_relevante: Mapped[Optional[bool]] = mapped_column(Boolean)
    error_msg: Mapped[Optional[str]] = mapped_column(Text)

    # Raw
    raw_summary_html: Mapped[Optional[str]] = mapped_column(Text)

    # Auditoría
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=ahora_utc, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=ahora_utc, onupdate=ahora_utc, nullable=False
    )

    # Relación inversa: matches de esta licitación con perfiles de clientes
    # 'lazy="select"' significa que solo se carga cuando se accede explícitamente
    matches: Mapped[list["MatchLicitacion"]] = relationship(
        back_populates="licitacion", lazy="select"
    )

    __table_args__ = (
        Index("ix_licitaciones_estado_proceso", "estado_proceso"),
        Index("ix_licitaciones_fecha_pub", "fecha_publicacion"),
        Index("ix_licitaciones_organo", "organo_contratacion"),
        Index("ix_licitaciones_cpv", "cpv_codigo"),
    )

    def __repr__(self) -> str:
        return (
            f"<Licitacion estado={self.estado_proceso} "
            f"titulo={self.titulo[:40]}>"
        )


# ──────────────────────────────────────────────────────────────────────────────
# MODELO ORM — clientes  (NUEVO Fase 1A)
# ──────────────────────────────────────────────────────────────────────────────

class Cliente(Base):
    """
    Representa a un cliente del servicio Sabueso.

    Un cliente tiene uno o más PerfilInteres que definen qué tipo de
    licitaciones le interesan. El sistema analiza las licitaciones nuevas
    contra cada perfil activo y notifica al cliente si hay matches.
    """
    __tablename__ = "clientes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Datos de contacto
    nombre: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)

    # activo=False → no se le envían notificaciones ni se analizan sus perfiles.
    # Útil para pausar clientes sin borrar sus datos.
    activo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Auditoría
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=ahora_utc, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=ahora_utc, onupdate=ahora_utc, nullable=False
    )

    # Relación: un cliente tiene N perfiles
    perfiles: Mapped[list["PerfilInteres"]] = relationship(
        back_populates="cliente",
        lazy="select",
        # Si borras un cliente, se borran también sus perfiles.
        # Evita huérfanos en la BD.
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Cliente id={self.id} email={self.email} activo={self.activo}>"


# ──────────────────────────────────────────────────────────────────────────────
# MODELO ORM — perfiles_interes  (NUEVO Fase 1A)
# ──────────────────────────────────────────────────────────────────────────────

class PerfilInteres(Base):
    """
    Define qué tipo de licitaciones le interesan a un cliente.

    Tiene dos capas de filtrado:

    CAPA A — Filtros deterministas (baratos, sin IA):
      · palabras_clave   → texto separado por comas: "software,desarrollo,web"
      · cpv_prefijos     → prefijos CPV separados por comas: "72,48"
                           El CPV 72 es todo el sector IT.
                           El CPV 48 es software de aplicación.
      · presupuesto_min  → presupuesto mínimo en euros (None = sin límite)
      · presupuesto_max  → presupuesto máximo en euros (None = sin límite)
      · provincias       → provincias separadas por comas: "Madrid,Barcelona"
                           None = toda España

    CAPA B — Descripción para la IA (solo si pasa la Capa A):
      · descripcion_ia   → texto libre que se envía a Gemini como contexto
                           Ejemplo: "Somos una consultora de IT de 10 personas
                           especializada en desarrollo web con Python y Django.
                           Buscamos contratos de desarrollo de aplicaciones web,
                           portales ciudadanos y mantenimiento de software para
                           administraciones públicas."
    """
    __tablename__ = "perfiles_interes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Clave foránea: a qué cliente pertenece este perfil
    # ForeignKey("clientes.id") le dice a SQLAlchemy que este campo
    # referencia la columna 'id' de la tabla 'clientes'.
    cliente_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("clientes.id"), nullable=False
    )

    # Nombre descriptivo del perfil (para que el cliente lo identifique)
    nombre: Mapped[str] = mapped_column(String(200), nullable=False)

    # ── CAPA A: Filtros deterministas ─────────────────────────────────────────
    # Guardamos como texto separado por comas para simplicidad.
    # En producción avanzada se usaría una tabla de relación N:M,
    # pero para empezar esto es más que suficiente y mucho más simple.

    # "python,django,desarrollo web,portal ciudadano"
    palabras_clave: Mapped[Optional[str]] = mapped_column(Text)

    # "72,48" (solo los primeros 2 dígitos del CPV para cubrir subcategorías)
    cpv_prefijos: Mapped[Optional[str]] = mapped_column(String(200))

    # Rango de presupuesto de interés (None = sin límite)
    presupuesto_min: Mapped[Optional[float]] = mapped_column(Float)
    presupuesto_max: Mapped[Optional[float]] = mapped_column(Float)

    # "Madrid,Barcelona,Valencia" (None = toda España)
    provincias: Mapped[Optional[str]] = mapped_column(Text)

    # ── CAPA B: Descripción para la IA ───────────────────────────────────────
    descripcion_ia: Mapped[Optional[str]] = mapped_column(Text)

    # activo=False → este perfil no se procesa aunque el cliente esté activo.
    # Útil para perfiles de prueba o pausados temporalmente.
    activo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Auditoría
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=ahora_utc, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=ahora_utc, onupdate=ahora_utc, nullable=False
    )

    # Relaciones
    cliente: Mapped["Cliente"] = relationship(back_populates="perfiles")
    matches: Mapped[list["MatchLicitacion"]] = relationship(
        back_populates="perfil",
        lazy="select",
        cascade="all, delete-orphan",
    )

    # ── MÉTODOS DE CONVENIENCIA ───────────────────────────────────────────────

    def get_palabras_clave_lista(self) -> list[str]:
        """
        Devuelve las palabras clave como lista limpia.
        "python, django , web" → ["python", "django", "web"]
        """
        if not self.palabras_clave:
            return []
        return [p.strip().lower() for p in self.palabras_clave.split(",") if p.strip()]

    def get_cpv_prefijos_lista(self) -> list[str]:
        """
        Devuelve los prefijos CPV como lista.
        "72,48" → ["72", "48"]
        """
        if not self.cpv_prefijos:
            return []
        return [c.strip() for c in self.cpv_prefijos.split(",") if c.strip()]

    def get_provincias_lista(self) -> list[str]:
        """
        Devuelve las provincias como lista en minúsculas para comparación.
        "Madrid,Barcelona" → ["madrid", "barcelona"]
        """
        if not self.provincias:
            return []
        return [p.strip().lower() for p in self.provincias.split(",") if p.strip()]

    def __repr__(self) -> str:
        return (
            f"<PerfilInteres id={self.id} "
            f"cliente_id={self.cliente_id} "
            f"nombre={self.nombre!r}>"
        )


# ──────────────────────────────────────────────────────────────────────────────
# MODELO ORM — matches  (NUEVO Fase 1A)
# ──────────────────────────────────────────────────────────────────────────────

class MatchLicitacion(Base):
    """
    Resultado del análisis de una licitación para un perfil concreto.

    Esta tabla es el corazón del sistema: registra qué licitaciones han sido
    analizadas para qué perfiles, cuál fue el resultado (score) y si ya se
    ha notificado al cliente.

    Sin esta tabla:
      · Analizaríamos las mismas licitaciones múltiples veces (costoso).
      · Enviaríamos notificaciones duplicadas al cliente.
      · No tendríamos histórico de qué se analizó y cuándo.

    La clave única (licitacion_id, perfil_id) garantiza que una licitación
    solo se analiza una vez por perfil, aunque el pipeline corra N veces.
    """
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Qué licitación y para qué perfil
    licitacion_id: Mapped[str] = mapped_column(
        String(512), ForeignKey("licitaciones.id"), nullable=False
    )
    perfil_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("perfiles_interes.id"), nullable=False
    )

    # ── RESULTADO DE LA CAPA A (filtro determinista) ──────────────────────────
    # True si pasó el filtro de palabras clave / CPV / presupuesto / provincia.
    # False si fue descartada en la Capa A sin llegar a la IA.
    paso_filtro_a: Mapped[bool] = mapped_column(Boolean, nullable=False)

    # ── RESULTADO DE LA CAPA B (IA) ───────────────────────────────────────────
    # Puntuación de 0 a 100 asignada por Gemini.
    # None si no llegó a la Capa B (fue descartada en la Capa A).
    score_ia: Mapped[Optional[int]] = mapped_column(Integer)

    # Explicación breve de Gemini: por qué es (o no es) relevante.
    razon_ia: Mapped[Optional[str]] = mapped_column(Text)

    # ── NOTIFICACIÓN ─────────────────────────────────────────────────────────
    # True cuando ya se ha enviado el email al cliente.
    # Impide enviar la misma licitación dos veces.
    notificado: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    notificado_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Auditoría
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=ahora_utc, nullable=False
    )

    # Relaciones inversas (para navegar desde un match a su licitación o perfil)
    licitacion: Mapped["Licitacion"] = relationship(back_populates="matches")
    perfil: Mapped["PerfilInteres"] = relationship(back_populates="matches")

    __table_args__ = (
        # Garantiza que una licitación solo se analiza una vez por perfil
        # UniqueConstraint importado implícitamente por SQLAlchemy al usar
        # el parámetro sqlite_on_conflict en versiones antiguas.
        # Usamos Index con unique=True que es más portable.
        Index(
            "ix_matches_licitacion_perfil",
            "licitacion_id", "perfil_id",
            unique=True,
        ),
        Index("ix_matches_perfil_id", "perfil_id"),
        Index("ix_matches_notificado", "notificado"),
    )

    def __repr__(self) -> str:
        return (
            f"<MatchLicitacion licitacion={self.licitacion_id[:30]} "
            f"perfil={self.perfil_id} score={self.score_ia}>"
        )


# ──────────────────────────────────────────────────────────────────────────────
# MODELO ORM — log_ejecuciones
# ──────────────────────────────────────────────────────────────────────────────

class LogEjecucion(Base):
    __tablename__ = "log_ejecuciones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    feed_url: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp_inicio: Mapped[datetime] = mapped_column(DateTime, default=ahora_utc)
    timestamp_fin: Mapped[Optional[datetime]] = mapped_column(DateTime)
    total_entradas_feed: Mapped[Optional[int]] = mapped_column(Integer)
    nuevas_insertadas: Mapped[Optional[int]] = mapped_column(Integer)
    ya_existian: Mapped[Optional[int]] = mapped_column(Integer)
    errores: Mapped[Optional[int]] = mapped_column(Integer)
    estado_run: Mapped[str] = mapped_column(String(20), default="EN_CURSO")
    mensaje: Mapped[Optional[str]] = mapped_column(Text)

    def duracion_segundos(self) -> Optional[float]:
        if self.timestamp_fin and self.timestamp_inicio:
            return (self.timestamp_fin - self.timestamp_inicio).total_seconds()
        return None

    def __repr__(self) -> str:
        return f"<LogEjecucion id={self.id} estado={self.estado_run}>"