"""
main.py — Sabueso de Licitaciones
====================================
Orquestador del Módulo de Ingesta y Persistencia (Paso 1).

Este script es el punto de entrada principal. En producción, se ejecutaría
via cron job, Celery Beat, o APScheduler cada N minutos.

Arquitectura de ejecución:
    main.py
        └── AtomScraper     → Descarga y parsea el feed PCSP
            └── DatabaseManager → Persiste en SQLite
                └── LogEjecucion → Audita cada run

Uso:
    python main.py                    # Ejecuta con configuración por defecto
    python main.py --feed novedades   # Usa el feed de novedades (más liviano)
    python main.py --limite 50        # Procesa hasta 50 licitaciones
    python main.py --dry-run          # Simula sin escribir en BD
    python main.py --stats            # Solo muestra estadísticas de la BD
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from datetime import datetime

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

# Asegurar que el módulo raíz sea encontrable
sys.path.insert(0, str(Path(__file__).parent))

import config
from src.logger import get_logger
from src.db_manager import DatabaseManager
from src.scraper_atom import AtomScraper, PCPSFeedError
from src.models import LicitacionResumen
from src.utils import formatear_presupuesto

logger = get_logger(__name__)
console = Console()


# ===========================================================================
# Funciones de Presentación (Rich)
# ===========================================================================

def mostrar_banner() -> None:
    console.print(Panel.fit(
        "[bold cyan]🐕 SABUESO DE LICITACIONES[/bold cyan]\n"
        "[dim]Módulo de Ingesta y Persistencia — Paso 1[/dim]\n"
        f"[dim]Iniciado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]",
        border_style="cyan",
        box=box.DOUBLE,
    ))


def mostrar_tabla_licitaciones(licitaciones: list, titulo: str = "Últimas Licitaciones") -> None:
    """Renderiza una tabla Rich con las licitaciones procesadas."""
    if not licitaciones:
        console.print("[yellow]No hay licitaciones que mostrar.[/yellow]")
        return

    tabla = Table(
        title=titulo,
        box=box.ROUNDED,
        show_lines=True,
        style="dim",
        title_style="bold cyan",
    )
    tabla.add_column("#", style="dim", width=4, justify="right")
    tabla.add_column("Título", style="white", max_width=45, no_wrap=False)
    tabla.add_column("Órgano", style="yellow", max_width=30, no_wrap=True)
    tabla.add_column("Tipo", style="blue", width=12, no_wrap=True)
    tabla.add_column("Presupuesto", style="green", width=15, justify="right")
    tabla.add_column("Estado", style="magenta", width=12)
    tabla.add_column("Publicación", style="dim", width=12)

    for i, lic in enumerate(licitaciones, 1):
        fecha_str = (
            lic.fecha_publicacion.strftime("%d/%m/%Y")
            if lic.fecha_publicacion
            else "N/A"
        )
        tabla.add_row(
            str(i),
            lic.titulo[:80] + ("…" if len(lic.titulo) > 80 else ""),
            (lic.organo_contratacion or "N/A")[:35],
            (lic.tipo_contrato or "N/A")[:12],
            formatear_presupuesto(lic.presupuesto_base),
            lic.estado_proceso,
            fecha_str,
        )

    console.print(tabla)


def mostrar_estadisticas(stats: dict) -> None:
    """Muestra el resumen del estado de la BD."""
    tabla = Table(
        title="📊 Estado de la Base de Datos",
        box=box.SIMPLE_HEAVY,
        title_style="bold green",
    )
    tabla.add_column("Estado", style="cyan", width=25)
    tabla.add_column("Cantidad", style="white", justify="right", width=15)

    for estado, count in stats.get("por_estado", {}).items():
        icon = {
            "NUEVA": "🆕", "PDF_PENDIENTE": "📄", "PDF_DESCARGADO": "✅",
            "ANALIZADA": "🤖", "RELEVANTE": "⭐", "DESCARTADA": "🗑️",
            "NOTIFICADA": "📧", "ERROR": "❌",
        }.get(estado, "•")
        tabla.add_row(f"{icon} {estado}", str(count))

    tabla.add_section()
    tabla.add_row(
        "[bold]TOTAL[/bold]",
        f"[bold]{stats.get('total', 0)}[/bold]"
    )

    console.print(tabla)
    if stats.get("ultima_actualizacion"):
        console.print(
            f"[dim]Última actualización: {stats['ultima_actualizacion']}[/dim]"
        )


# ===========================================================================
# Lógica Principal de Orquestación
# ===========================================================================

def ejecutar_ingesta(
    feed_key: str = config.ACTIVE_FEED,
    limite: int = config.MAX_LICITACIONES_POR_RUN,
    dry_run: bool = False,
) -> dict:
    """
    Función principal de orquestación. Retorna un dict de resultados
    para facilitar tests y llamadas programáticas.
    
    Args:
        feed_key:   Clave del feed en config.PCSP_FEEDS ("completo" o "novedades")
        limite:     Máximo de licitaciones a procesar
        dry_run:    Si True, procesa pero NO escribe en BD (útil para testing)
    
    Returns:
        dict con claves: total_feed, nuevas, duplicadas, errores, duracion_seg
    """
    inicio = time.monotonic()
    resultados = {"total_feed": 0, "nuevas": 0, "duplicadas": 0, "errores": 0}

    feed_url = config.PCSP_FEEDS.get(feed_key, config.PCSP_FEEDS["completo"])

    if dry_run:
        logger.warning("⚠️  MODO DRY-RUN ACTIVO: No se escribirá en la base de datos")

    # --- Inicializar componentes ---
    db = DatabaseManager()
    scraper = AtomScraper(feed_url=feed_url)

    # --- Abrir sesión de BD y registrar inicio del run ---
    with db.session() as session:
        log_run = db.iniciar_log_ejecucion(session, feed_url=feed_url)
        session.flush()
        log_id = log_run.id
        logger.info(f"Run #{log_id} iniciado | Feed: {feed_key} | Límite: {limite}")

    # --- Bucle principal de ingesta ---
    licitaciones_procesadas = []

    try:
        for schema in scraper.iterar_licitaciones(limite=limite):
            resultados["total_feed"] += 1

            if dry_run:
                # En dry-run, solo contamos sin persistir
                logger.info(
                    f"[DRY-RUN] {schema.titulo[:60]} | "
                    f"Órgano: {schema.organo_contratacion or 'N/A'} | "
                    f"Presupuesto: {schema.presupuesto_base or 'N/A'}€"
                )
                resultados["nuevas"] += 1
                continue

            # Cada licitación se persiste en su propia micro-transacción
            # Esto garantiza que un error en una no afecte a las demás
            with db.session() as session:
                exito, resultado = db.insertar_licitacion(session, schema)
                if resultado == "INSERTADA":
                    resultados["nuevas"] += 1
                elif resultado == "DUPLICADA":
                    resultados["duplicadas"] += 1
                else:
                    resultados["errores"] += 1

    except PCPSFeedError as e:
        logger.critical(f"Error crítico del feed PCSP: {e}")
        resultados["errores"] += 1
        estado_run = "ERROR"
        mensaje_run = str(e)
    except KeyboardInterrupt:
        logger.warning("Ingesta interrumpida por el usuario (Ctrl+C)")
        estado_run = "PARCIAL"
        mensaje_run = "Interrumpido por usuario"
    except Exception as e:
        logger.exception(f"Error inesperado durante la ingesta: {e}")
        resultados["errores"] += 1
        estado_run = "ERROR"
        mensaje_run = str(e)
    else:
        estado_run = "OK" if resultados["errores"] == 0 else "PARCIAL"
        mensaje_run = None
    finally:
        resultados["duracion_seg"] = round(time.monotonic() - inicio, 2)

    # --- Cerrar el log del run ---
    if not dry_run:
        with db.session() as session:
            # Recuperar el log por ID (la sesión anterior ya fue cerrada)
            from sqlalchemy import select
            from src.models import LogEjecucion
            log = session.get(LogEjecucion, log_id)
            if log:
                db.finalizar_log_ejecucion(
                    session, log,
                    total_feed=resultados["total_feed"],
                    nuevas=resultados["nuevas"],
                    duplicadas=resultados["duplicadas"],
                    errores=resultados["errores"],
                    estado=estado_run,
                    mensaje=mensaje_run,
                )

    logger.info(
        f"Run completado en {resultados['duracion_seg']}s | "
        f"Nuevas: {resultados['nuevas']} | "
        f"Duplicadas: {resultados['duplicadas']} | "
        f"Errores: {resultados['errores']}"
    )
    return resultados


# ===========================================================================
# CLI
# ===========================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="🐕 Sabueso de Licitaciones — Módulo de Ingesta",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python main.py                     # Ingestar 20 licitaciones del feed completo
  python main.py --limite 100        # Procesar hasta 100 licitaciones
  python main.py --feed novedades    # Usar el feed de novedades
  python main.py --dry-run           # Simular sin tocar la BD
  python main.py --stats             # Ver estadísticas de la BD
        """,
    )
    parser.add_argument(
        "--feed",
        choices=list(config.PCSP_FEEDS.keys()),
        default=config.ACTIVE_FEED,
        help=f"Feed a procesar (default: {config.ACTIVE_FEED})",
    )
    parser.add_argument(
        "--limite",
        type=int,
        default=config.MAX_LICITACIONES_POR_RUN,
        help=f"Máximo de licitaciones a procesar (default: {config.MAX_LICITACIONES_POR_RUN})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simular ingesta sin escribir en BD",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Mostrar estadísticas de la BD y salir",
    )
    parser.add_argument(
        "--mostrar-tabla",
        action="store_true",
        default=True,
        help="Mostrar tabla de licitaciones ingestadas (default: True)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    mostrar_banner()

    # --- Modo solo estadísticas ---
    if args.stats:
        db = DatabaseManager()
        with db.session() as session:
            stats = db.estadisticas(session)
            licitaciones = db.obtener_recientes(session, limite=10)
        mostrar_estadisticas(stats)
        mostrar_tabla_licitaciones(
            licitaciones,
            titulo="🔍 10 Licitaciones Más Recientes"
        )
        return

    # --- Ejecutar ingesta ---
    console.print(
        f"\n[bold]▶ Iniciando ingesta[/bold] | "
        f"Feed: [cyan]{args.feed}[/cyan] | "
        f"Límite: [yellow]{args.limite}[/yellow] | "
        f"Dry-run: [{'red]SÍ' if args.dry_run else 'green]NO'}[/]\n"
    )

    resultados = ejecutar_ingesta(
        feed_key=args.feed,
        limite=args.limite,
        dry_run=args.dry_run,
    )

    # --- Mostrar resumen del run ---
    console.print(Panel(
        f"[bold green]✅ Ingesta completada en {resultados['duracion_seg']}s[/bold green]\n\n"
        f"  🆕 Nuevas licitaciones:    [green bold]{resultados['nuevas']}[/green bold]\n"
        f"  🔄 Ya existían (ignoradas): [yellow]{resultados['duplicadas']}[/yellow]\n"
        f"  ❌ Errores de parsing:      [red]{resultados['errores']}[/red]\n"
        f"  📋 Total feed procesado:    {resultados['total_feed']}",
        title="📊 Resumen del Run",
        border_style="green" if resultados["errores"] == 0 else "yellow",
        box=box.ROUNDED,
    ))

    # --- Mostrar tabla con las últimas licitaciones ---
    if not args.dry_run and args.mostrar_tabla:
        db = DatabaseManager()
        with db.session() as session:
            recientes = db.obtener_recientes(session, limite=args.limite)
        if recientes:
            mostrar_tabla_licitaciones(
                recientes,
                titulo=f"🐕 {len(recientes)} Últimas Licitaciones Ingestadas"
            )
            console.print(
                f"\n[dim]💡 Próximo paso: ejecutar el scraper de PDFs sobre las "
                f"{resultados['nuevas']} licitaciones nuevas[/dim]"
            )


if __name__ == "__main__":
    main()
