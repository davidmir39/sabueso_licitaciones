"""
src/models.py — Sabueso de Licitaciones
==========================================
Dos capas de modelos:
  1. LicitacionSchema → dataclass de transferencia (scraper → db_manager)
  2. Licitacion       → modelo ORM (tabla SQLite)
  3. LogEjecucion     → auditoría de runs

CAMBIOS v2 (Step 2):
  · LicitacionSchema añade url_pdf_directo y nombre_pdf
    (el feed PCSP incluye la URL del PDF directamente en cbc_uri)
  · Licitacion ORM: url_pdf_ppt almacena esa URL directa
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean, DateTime, Float, Index,
    Integer, String, Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# ──────────────────────────────────────────────────────────────────────────────
# BASE ORM
# ──────────────────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


# ──────────────────────────────────────────────────────────────────────────────
# SCHEMA DE TRANSFERENCIA
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class LicitacionSchema:
    """
    Contenedor de datos que viaja desde AtomScraper hasta DatabaseManager.
    Solo Python estándar, sin dependencias de SQLAlchemy.

    Campos nuevos en v2:
      url_pdf_directo → URL directa del PDF (campo cbc_uri del feed UBL)
      nombre_pdf      → nombre descriptivo del fichero (campo cbc_filename)
    """
    # Obligatorios
    id: str
    titulo: str

    # Feed ATOM
    link_plataforma: Optional[str] = None
    fecha_publicacion: Optional[datetime] = None
    fecha_actualizacion: Optional[datetime] = None

    # Del summary (texto plano) + campos UBL
    organo_contratacion: Optional[str] = None
    nif_organo: Optional[str] = None
    tipo_contrato: Optional[str] = None
    presupuesto_base: Optional[float] = None
    valor_estimado: Optional[float] = None
    cpv_codigo: Optional[str] = None
    cpv_descripcion: Optional[str] = None
    lugar_ejecucion: Optional[str] = None     # ciudad + provincia
    estado_contrato: Optional[str] = None     # ADJ, EV, RES, PUB...
    expediente: Optional[str] = None

    # ── NUEVO Step 2 ──────────────────────────────────────────────────────────
    url_pdf_directo: Optional[str] = None     # cbc_uri del feed
    nombre_pdf: Optional[str] = None          # cbc_filename del feed

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
    # url_pdf_ppt almacena la URL directa del PDF (cbc_uri del feed)
    url_pdf_ppt: Mapped[Optional[str]] = mapped_column(Text)
    url_pdf_pca: Mapped[Optional[str]] = mapped_column(Text)
    nombre_pdf: Mapped[Optional[str]] = mapped_column(Text)
    ruta_pdf_local: Mapped[Optional[str]] = mapped_column(Text)

    # IA (Steps 3-4)
    resumen_ia: Mapped[Optional[str]] = mapped_column(Text)
    es_relevante: Mapped[Optional[bool]] = mapped_column(Boolean)
    error_msg: Mapped[Optional[str]] = mapped_column(Text)

    # Raw
    raw_summary_html: Mapped[Optional[str]] = mapped_column(Text)

    # Auditoría
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
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
# MODELO ORM — log_ejecuciones
# ──────────────────────────────────────────────────────────────────────────────

class LogEjecucion(Base):
    __tablename__ = "log_ejecuciones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    feed_url: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp_inicio: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
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
