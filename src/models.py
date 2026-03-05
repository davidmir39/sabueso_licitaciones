"""
src/models.py — Sabueso de Licitaciones
==========================================
Dos capas de modelos en un único fichero:

  1. LicitacionSchema  → dataclass de transferencia (scraper → db_manager).
                         Vive solo en memoria; no tiene nada de SQLAlchemy.

  2. Licitacion        → modelo ORM (tabla 'licitaciones' en SQLite).

  3. LogEjecucion      → modelo ORM (tabla 'log_ejecuciones').
                         Audita cada run del pipeline para diagnóstico.

Separar schema de ORM nos permite:
  · Testear el scraper sin BD (solo dataclasses).
  · Cambiar el ORM en el futuro sin tocar el scraper.
  · Serializar/deserializar limpiamente hacia APIs REST (Step 5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean, Column, DateTime, Float, Index,
    Integer, String, Text, event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# ──────────────────────────────────────────────────────────────────────────────
# BASE ORM
# ──────────────────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


# ──────────────────────────────────────────────────────────────────────────────
# SCHEMA DE TRANSFERENCIA (Scraper → DB)
# No tiene dependencias de SQLAlchemy. Solo Python estándar.
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class LicitacionSchema:
    """
    Contenedor de datos que viaja desde AtomScraper hasta DatabaseManager.

    Todas las transformaciones y limpiezas de datos deben ocurrir ANTES
    de crear este objeto (en utils.py). Aquí solo hay datos ya normalizados.

    Los campos opcionales reflejan la realidad del feed PCSP: no todas
    las licitaciones tienen todos los campos informados.
    """
    # — Campos obligatorios —
    id: str
    titulo: str

    # — Campos del feed ATOM —
    link_plataforma: Optional[str] = None
    fecha_publicacion: Optional[datetime] = None
    fecha_actualizacion: Optional[datetime] = None

    # — Extraídos del <summary> HTML —
    organo_contratacion: Optional[str] = None
    nif_organo: Optional[str] = None
    tipo_contrato: Optional[str] = None
    presupuesto_base: Optional[float] = None        # en euros (float tras parseo)
    valor_estimado: Optional[float] = None          # en euros
    cpv_codigo: Optional[str] = None               # ej: "45000000-7"
    cpv_descripcion: Optional[str] = None          # ej: "Trabajos de construcción"
    lugar_ejecucion: Optional[str] = None
    estado_contrato: Optional[str] = None          # estado en la plataforma PCSP
    expediente: Optional[str] = None

    # — Datos crudos (para reprocesado sin volver al feed) —
    raw_summary_html: Optional[str] = None


# TypeAlias semántico para listas de licitaciones (usado en main.py y tests)
LicitacionResumen = LicitacionSchema


# ──────────────────────────────────────────────────────────────────────────────
# MODELO ORM — TABLA licitaciones
# ──────────────────────────────────────────────────────────────────────────────

class Licitacion(Base):
    """
    Persistencia de una licitación pública.

    Columnas del pipeline (estado_proceso, url_pdf_*) están pensadas para
    que los Steps 2, 3 y 4 puedan actualizar la misma fila sin crear
    tablas adicionales hasta que el volumen lo justifique.
    """
    __tablename__ = "licitaciones"

    # — Identificación —
    id: Mapped[str] = mapped_column(String(512), primary_key=True)
    titulo: Mapped[str] = mapped_column(Text, nullable=False)
    link_plataforma: Mapped[Optional[str]] = mapped_column(Text)

    # — Metadatos del feed —
    fecha_publicacion: Mapped[Optional[datetime]] = mapped_column(DateTime)
    fecha_actualizacion: Mapped[Optional[datetime]] = mapped_column(DateTime)
    organo_contratacion: Mapped[Optional[str]] = mapped_column(Text)
    nif_organo: Mapped[Optional[str]] = mapped_column(String(20))
    tipo_contrato: Mapped[Optional[str]] = mapped_column(String(100))
    presupuesto_base: Mapped[Optional[float]] = mapped_column(Float)
    valor_estimado: Mapped[Optional[float]] = mapped_column(Float)
    cpv_codigo: Mapped[Optional[str]] = mapped_column(String(20))
    cpv_descripcion: Mapped[Optional[str]] = mapped_column(Text)
    lugar_ejecucion: Mapped[Optional[str]] = mapped_column(Text)
    estado_contrato: Mapped[Optional[str]] = mapped_column(String(80))
    expediente: Mapped[Optional[str]] = mapped_column(String(120))

    # — Pipeline de procesado (Steps 2-5) —
    estado_proceso: Mapped[str] = mapped_column(String(30), nullable=False, default="NUEVA")
    url_pdf_ppt: Mapped[Optional[str]] = mapped_column(Text)   # Pliego de Prescripciones Técnicas
    url_pdf_pca: Mapped[Optional[str]] = mapped_column(Text)   # Pliego de Cláusulas Administrativas
    ruta_pdf_local: Mapped[Optional[str]] = mapped_column(Text)
    resumen_ia: Mapped[Optional[str]] = mapped_column(Text)    # Output de Llama 4 (Step 4)
    es_relevante: Mapped[Optional[bool]] = mapped_column(Boolean)
    error_msg: Mapped[Optional[str]] = mapped_column(Text)

    # — Datos crudos —
    raw_summary_html: Mapped[Optional[str]] = mapped_column(Text)

    # — Auditoría —
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # — Índices para queries del pipeline —
    __table_args__ = (
        Index("ix_licitaciones_estado_proceso", "estado_proceso"),
        Index("ix_licitaciones_fecha_pub", "fecha_publicacion"),
        Index("ix_licitaciones_organo", "organo_contratacion"),
        Index("ix_licitaciones_cpv", "cpv_codigo"),
    )

    def __repr__(self) -> str:
        return (
            f"<Licitacion id=...{self.id[-20:]} "
            f"estado={self.estado_proceso} "
            f"titulo={self.titulo[:40]}>"
        )


# ──────────────────────────────────────────────────────────────────────────────
# MODELO ORM — TABLA log_ejecuciones
# ──────────────────────────────────────────────────────────────────────────────

class LogEjecucion(Base):
    """
    Auditoría de cada run del pipeline.

    Permite responder a preguntas como:
      · ¿A qué hora corrió el sistema ayer?
      · ¿Cuántas licitaciones nuevas encontró cada día?
      · ¿Hubo errores la semana pasada?

    Indispensable para el soporte B2B y para optimizar el cron schedule.
    """
    __tablename__ = "log_ejecuciones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    feed_url: Mapped[str] = mapped_column(Text, nullable=False)

    # — Tiempos —
    timestamp_inicio: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    timestamp_fin: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # — Métricas del run —
    total_entradas_feed: Mapped[Optional[int]] = mapped_column(Integer)
    nuevas_insertadas: Mapped[Optional[int]] = mapped_column(Integer)
    ya_existian: Mapped[Optional[int]] = mapped_column(Integer)
    errores: Mapped[Optional[int]] = mapped_column(Integer)

    # — Resultado —
    estado_run: Mapped[str] = mapped_column(String(20), default="EN_CURSO")  # OK | ERROR | PARCIAL
    mensaje: Mapped[Optional[str]] = mapped_column(Text)

    def duracion_segundos(self) -> Optional[float]:
        """Calcula la duración del run si ya finalizó."""
        if self.timestamp_fin and self.timestamp_inicio:
            return (self.timestamp_fin - self.timestamp_inicio).total_seconds()
        return None

    def __repr__(self) -> str:
        return (
            f"<LogEjecucion id={self.id} "
            f"estado={self.estado_run} "
            f"nuevas={self.nuevas_insertadas}>"
        )
