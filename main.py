"""
main.py — Sabueso de Licitaciones
====================================
Orquestador del Módulo de Ingesta y Persistencia (Step 1).

Este fichero SOLO orquesta: lee argumentos, inicializa componentes,
llama a los módulos en orden y presenta el resultado. No contiene
lógica de negocio ni acceso directo a BD o HTTP.

En producción se ejecutaría via cron, Celery Beat o APScheduler.

Uso:
    python main.py                    # 20 licitaciones del feed completo
    python main.py --limite 100       # hasta 100 licitaciones
    python main.py --feed novedades   # feed más ligero
    python main.py --dry-run          # simula sin escribir en BD
    python main.py --stats            # estadísticas de la BD
    python main.py --ping             # verifica conectividad con la PCSP
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

sys.path.insert(0, str(Path(__file__).parent))

import config
from src.logger import get_logger
from src.db_manager import DatabaseManager
from src.scraper_atom import AtomScraper, PCPSFeedError
from src.utils import formatear_presupuesto

logger = get_logger(__name__)
console = Console()


# ──────────────────────────────────────────────────────────────────────────────
# PRESENTACIÓN (Rich)
# ──────────────────────────────────────────────────────────────────────────────

def mostrar_banner() -> None:
    console.print(Panel.fit(
        "[bold cyan]🐕 SABUESO DE LICITACIONES[/bold cyan]\n"
        "[dim]Módulo de Ingesta y Persistencia — Step 1[/dim]\n"
        f"[dim]{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]",
        border_style="cyan",
        box=box.DOUBLE,
    ))


def mostrar_tabla_licitaciones(licitaciones: list, titulo: str = "Licitaciones") -> None:
    if not licitaciones:
        console.print("[yellow]No hay licitaciones que mostrar.[/yellow]")
        return

    tabla = Table(
        title=titulo,
        box=box.ROUNDED,
        show_lines=True,
        title_style="bold cyan",
    )
    tabla.add_column("#",            width=4,  justify="right", style="dim")
    tabla.add_column("Título",       max_width=42, no_wrap=False, style="white")
    tabla.add_column("Órgano",       max_width=28, no_wrap=True,  style="yellow")
    tabla.add_column("Tipo",         width=14, no_wrap=True,      style="blue")
    tabla.add_column("Presupuesto",  width=16, justify="right",   style="green")
    tabla.add_column("Estado",       width=12,                    style="magenta")
    tabla.add_column("Publicación",  width=12, style="dim")

    for i, lic in enumerate(licitaciones, 1):
        fecha_str = (
            lic.fecha_publicacion.strftime("%d/%m/%Y")
            if lic.fecha_publicacion else "N/A"
        )
        tabla.add_row(
            str(i),
            lic.titulo[:80] + ("…" if len(lic.titulo) > 80 else ""),
            (lic.organo_contratacion or "N/A")[:28],
            (lic.tipo_contrato or "N/A")[:14],
            formatear_presupuesto(lic.presupuesto_base),
            lic.estado_proceso,
            fecha_str,
        )
    console.print(tabla)


def mostrar_estadisticas(stats: dict) -> None:
    tabla = Table(
        title="📊 Estado de la Base de Datos",
        box=box.SIMPLE_HEAVY,
        title_style="bold green",
    )
    tabla.add_column("Estado",   style="cyan",  width=25)
    tabla.add_column("Cantidad", style="white", width=12, justify="right")

    iconos = {
        "NUEVA": "🆕", "PDF_PENDIENTE": "📄", "PDF_DESCARGADO": "✅",
        "ANALISIS_PENDIENTE": "⏳", "ANALIZADA": "🤖",
        "RELEVANTE": "⭐", "DESCARTADA": "🗑️",
        "NOTIFICADA": "📧", "ERROR": "❌",
    }
    for estado, count in sorted(stats.get("por_estado", {}).items(), key=lambda x: -x[1]):
        icono = iconos.get(estado, "•")
        tabla.add_row(f"{icono} {estado}", str(count))

    tabla.add_section()
    tabla.add_row("[bold]TOTAL[/bold]", f"[bold]{stats.get('total', 0)}[/bold]")
    console.print(tabla)

    if stats.get("ultima_actualizacion"):
        console.print(f"[dim]Última actualización: {stats['ultima_actualizacion']}[/dim]")


# ──────────────────────────────────────────────────────────────────────────────
# LÓGICA DE ORQUESTACIÓN
# ──────────────────────────────────────────────────────────────────────────────

def ejecutar_ingesta(
    feed_key: str = config.ACTIVE_FEED,
    limite: int = config.MAX_LICITACIONES_POR_RUN,
    dry_run: bool = False,
) -> dict:
    """
    Orquesta la descarga y persistencia de licitaciones.

    Diseño intencional: cada licitación se persiste en su propia
    micro-transacción. Si una falla, las demás no se ven afectadas.

    Returns:
        dict con métricas: total_feed, nuevas, duplicadas, errores, duracion_seg
    """
    inicio = time.monotonic()
    resultados = {"total_feed": 0, "nuevas": 0, "duplicadas": 0, "errores": 0}
    estado_run = "OK"
    mensaje_run = None

    feed_url = config.PCSP_FEEDS.get(feed_key, config.PCSP_FEEDS["completo"])
    db = DatabaseManager()
    scraper = AtomScraper(feed_url=feed_url)

    if dry_run:
        logger.warning("MODO DRY-RUN: no se escribirá en la base de datos.")

    # — Registrar inicio del run en BD —
    log_id = None
    if not dry_run:
        with db.session() as session:
            log = db.iniciar_log_ejecucion(session, feed_url=feed_url)
            log_id = log.id
        logger.info("Run #%s iniciado | feed=%s | limite=%d", log_id, feed_key, limite)

    # — Bucle principal —
    try:
        for schema in scraper.iterar_licitaciones(limite=limite):
            resultados["total_feed"] += 1

            if dry_run:
                logger.info(
                    "[DRY-RUN] %s | %s | %s€",
                    schema.titulo[:60],
                    schema.organo_contratacion or "N/A",
                    schema.presupuesto_base or "N/A",
                )
                resultados["nuevas"] += 1
                continue

            with db.session() as session:
                _, resultado = db.insertar_licitacion(session, schema)

            if resultado == "INSERTADA":
                resultados["nuevas"] += 1
            elif resultado == "DUPLICADA":
                resultados["duplicadas"] += 1
            else:
                resultados["errores"] += 1

    except PCPSFeedError as exc:
        logger.critical("Error crítico del feed PCSP: %s", exc)
        resultados["errores"] += 1
        estado_run, mensaje_run = "ERROR", str(exc)
    except KeyboardInterrupt:
        logger.warning("Ingesta interrumpida por el usuario (Ctrl+C).")
        estado_run, mensaje_run = "PARCIAL", "Interrumpido por usuario"
    except Exception as exc:
        logger.exception("Error inesperado durante la ingesta: %s", exc)
        resultados["errores"] += 1
        estado_run, mensaje_run = "ERROR", str(exc)
    finally:
        resultados["duracion_seg"] = round(time.monotonic() - inicio, 2)

    # — Cerrar log del run —
    if not dry_run and log_id is not None:
        with db.session() as session:
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
        "Run completado en %.2fs | nuevas=%d | duplicadas=%d | errores=%d",
        resultados["duracion_seg"],
        resultados["nuevas"],
        resultados["duplicadas"],
        resultados["errores"],
    )
    return resultados


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="🐕 Sabueso de Licitaciones — Módulo de Ingesta",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python main.py                     # 20 licitaciones del feed completo
  python main.py --limite 100        # hasta 100 licitaciones
  python main.py --feed novedades    # feed de novedades (más ligero)
  python main.py --dry-run           # simula sin tocar la BD
  python main.py --stats             # estadísticas de la BD
  python main.py --ping              # verifica conectividad con la PCSP
        """,
    )
    parser.add_argument(
        "--feed", choices=list(config.PCSP_FEEDS.keys()),
        default=config.ACTIVE_FEED,
        help=f"Feed a procesar (default: {config.ACTIVE_FEED})",
    )
    parser.add_argument(
        "--limite", type=int, default=config.MAX_LICITACIONES_POR_RUN,
        metavar="N",
        help=f"Máximo de licitaciones a procesar (default: {config.MAX_LICITACIONES_POR_RUN})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Simular ingesta sin escribir en BD",
    )
    parser.add_argument(
        "--stats", action="store_true",
        help="Mostrar estadísticas de la BD y salir",
    )
    parser.add_argument(
        "--ping", action="store_true",
        help="Verificar conectividad con el feed PCSP y salir",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    mostrar_banner()

    # — Solo ping —
    if args.ping:
        scraper = AtomScraper(feed_url=config.PCSP_FEEDS[args.feed])
        meta = scraper.obtener_metadata_feed()
        if meta.get("ok"):
            console.print(Panel(
                f"✅ Feed accesible\n"
                f"📰 Título: {meta['titulo']}\n"
                f"📦 Entradas: {meta['total_entradas']}\n"
                f"🕒 Actualizado: {meta['actualizado']}",
                title="PING OK", border_style="green",
            ))
            return 0
        else:
            console.print(Panel(f"❌ {meta['error']}", title="PING FAIL", border_style="red"))
            return 1

    # — Solo estadísticas —
    if args.stats:
        db = DatabaseManager()
        with db.session() as session:
            stats = db.estadisticas(session)
            recientes = db.obtener_recientes(session, limite=10)
        mostrar_estadisticas(stats)
        mostrar_tabla_licitaciones(recientes, titulo="🔍 10 Más Recientes")
        return 0

    # — Ingesta principal —
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

    color = "green" if resultados["errores"] == 0 else "yellow"
    console.print(Panel(
        f"[bold green]✅ Completado en {resultados['duracion_seg']}s[/bold green]\n\n"
        f"  🆕 Nuevas:      [green bold]{resultados['nuevas']}[/green bold]\n"
        f"  🔄 Duplicadas:  [yellow]{resultados['duplicadas']}[/yellow]\n"
        f"  ❌ Errores:     [red]{resultados['errores']}[/red]\n"
        f"  📋 Total feed:  {resultados['total_feed']}",
        title="📊 Resumen del Run",
        border_style=color,
        box=box.ROUNDED,
    ))

    if not args.dry_run and resultados["nuevas"] > 0:
        db = DatabaseManager()
        with db.session() as session:
            recientes = db.obtener_recientes(session, limite=args.limite)
        mostrar_tabla_licitaciones(
            recientes,
            titulo=f"🐕 {len(recientes)} Últimas Licitaciones Ingestadas",
        )
        console.print(
            f"\n[dim]💡 Próximo paso (Step 2): navegar a las {resultados['nuevas']} "
            f"licitaciones nuevas y extraer URLs de PDFs.[/dim]\n"
        )

    return 0 if resultados["errores"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
