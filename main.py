"""
main.py — Sabueso de Licitaciones
====================================
Orquestador del pipeline Steps 1 + 2.

Step 1 — Ingesta: descarga el feed ATOM y persiste licitaciones en SQLite.
Step 2 — Descarga PDFs: descarga los PDFs cuya URL ya está en el feed.

Flujo completo de una ejecución normal:
    1. AtomScraper descarga el feed → LicitacionSchema[]
    2. DatabaseManager persiste cada schema:
         · Con URL de PDF → estado PDF_PENDIENTE
         · Sin URL de PDF → estado NUEVA
    3. PDFDownloader descarga los PDFs en PDF_PENDIENTE
         · Éxito → estado PDF_DESCARGADO + ruta_pdf_local
         · Error → estado ERROR

Uso:
    python main.py                        # Steps 1 + 2 completos
    python main.py --solo-ingesta         # Solo Step 1 (sin descargar PDFs)
    python main.py --solo-pdfs            # Solo Step 2 (descarga PDFs pendientes)
    python main.py --limite 50            # Máximo 50 licitaciones
    python main.py --dry-run              # Simula sin escribir nada
    python main.py --stats                # Estadísticas de la BD
    python main.py --ping                 # Verifica conectividad
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
from src.downloader import PDFDownloader
from src.utils import formatear_presupuesto

logger = get_logger(__name__)
console = Console()


# ──────────────────────────────────────────────────────────────────────────────
# PRESENTACIÓN
# ──────────────────────────────────────────────────────────────────────────────

def mostrar_banner() -> None:
    console.print(Panel.fit(
        "[bold cyan]🐕 SABUESO DE LICITACIONES[/bold cyan]\n"
        "[dim]Pipeline Steps 1 + 2 — Ingesta y Descarga de PDFs[/dim]\n"
        f"[dim]{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]",
        border_style="cyan",
        box=box.DOUBLE,
    ))


def mostrar_tabla_licitaciones(licitaciones: list, titulo: str) -> None:
    if not licitaciones:
        console.print("[yellow]No hay licitaciones que mostrar.[/yellow]")
        return

    tabla = Table(title=titulo, box=box.ROUNDED, show_lines=True, title_style="bold cyan")
    tabla.add_column("#",            width=4,  justify="right", style="dim")
    tabla.add_column("Título",       max_width=40, style="white")
    tabla.add_column("Órgano",       max_width=28, style="yellow")
    tabla.add_column("Presupuesto",  width=14, justify="right", style="green")
    tabla.add_column("Estado",       width=16, style="magenta")
    tabla.add_column("Publicación",  width=12, style="dim")
    tabla.add_column("PDF",          width=5,  justify="center")

    for i, lic in enumerate(licitaciones, 1):
        fecha_str = lic.fecha_publicacion.strftime("%d/%m/%Y") if lic.fecha_publicacion else "N/A"
        pdf_icon = "✅" if lic.ruta_pdf_local else ("📄" if lic.url_pdf_ppt else "—")
        tabla.add_row(
            str(i),
            lic.titulo[:65] + ("…" if len(lic.titulo) > 65 else ""),
            (lic.organo_contratacion or "N/A")[:28],
            formatear_presupuesto(lic.presupuesto_base),
            lic.estado_proceso,
            fecha_str,
            pdf_icon,
        )
    console.print(tabla)


def mostrar_estadisticas(stats: dict) -> None:
    tabla = Table(
        title="📊 Estado de la Base de Datos",
        box=box.SIMPLE_HEAVY,
        title_style="bold green",
    )
    tabla.add_column("Estado",   style="cyan",  width=22)
    tabla.add_column("Cantidad", style="white", width=10, justify="right")

    iconos = {
        "NUEVA": "🆕", "PDF_PENDIENTE": "📄", "PDF_DESCARGADO": "✅",
        "ANALISIS_PENDIENTE": "⏳", "ANALIZADA": "🤖",
        "RELEVANTE": "⭐", "DESCARTADA": "🗑️",
        "NOTIFICADA": "📧", "ERROR": "❌",
    }
    for estado, count in sorted(stats.get("por_estado", {}).items(), key=lambda x: -x[1]):
        tabla.add_row(f"{iconos.get(estado, '•')} {estado}", str(count))
    tabla.add_section()
    tabla.add_row("[bold]TOTAL[/bold]", f"[bold]{stats.get('total', 0)}[/bold]")
    console.print(tabla)


# ──────────────────────────────────────────────────────────────────────────────
# STEP 1 — INGESTA
# ──────────────────────────────────────────────────────────────────────────────

def ejecutar_ingesta(
    feed_key: str,
    limite: int,
    dry_run: bool,
    db: DatabaseManager,
) -> dict:
    """Descarga el feed y persiste licitaciones en BD."""
    inicio = time.monotonic()
    resultados = {"total_feed": 0, "nuevas": 0, "duplicadas": 0, "errores": 0}
    estado_run, mensaje_run = "OK", None

    feed_url = config.PCSP_FEEDS[feed_key]
    scraper = AtomScraper(feed_url=feed_url)

    log_id = None
    if not dry_run:
        with db.session() as session:
            log = db.iniciar_log_ejecucion(session, feed_url=feed_url)
            log_id = log.id
        logger.info("Run #%s iniciado | feed=%s | limite=%d", log_id, feed_key, limite)

    try:
        for schema in scraper.iterar_licitaciones(limite=limite):
            resultados["total_feed"] += 1
            if dry_run:
                logger.info(
                    "[DRY-RUN] %s | %s | %s€ | PDF: %s",
                    schema.titulo[:60],
                    schema.organo_contratacion or "N/A",
                    schema.presupuesto_base or "N/A",
                    "✅" if schema.url_pdf_directo else "—",
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
        logger.critical("Error crítico del feed: %s", exc)
        resultados["errores"] += 1
        estado_run, mensaje_run = "ERROR", str(exc)
    except KeyboardInterrupt:
        logger.warning("Ingesta interrumpida por el usuario.")
        estado_run, mensaje_run = "PARCIAL", "Interrumpido"
    except Exception as exc:
        logger.exception("Error inesperado: %s", exc)
        resultados["errores"] += 1
        estado_run, mensaje_run = "ERROR", str(exc)
    finally:
        resultados["duracion_seg"] = round(time.monotonic() - inicio, 2)

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
    return resultados


# ──────────────────────────────────────────────────────────────────────────────
# STEP 2 — DESCARGA DE PDFs
# ──────────────────────────────────────────────────────────────────────────────

def ejecutar_descarga_pdfs(
    limite: int,
    dry_run: bool,
    db: DatabaseManager,
) -> dict:
    """Descarga los PDFs de las licitaciones en estado PDF_PENDIENTE."""
    inicio = time.monotonic()

    with db.session() as session:
        pendientes = db.obtener_pendientes_pdf(session, limite=limite)

    if not pendientes:
        logger.info("No hay PDFs pendientes de descarga.")
        return {"descargados": 0, "omitidos": 0, "errores": 0, "duracion_seg": 0.0}

    logger.info("PDFs pendientes de descarga: %d", len(pendientes))

    if dry_run:
        for licit in pendientes:
            logger.info(
                "[DRY-RUN PDF] %s → %s",
                licit.nombre_pdf or "sin nombre",
                licit.url_pdf_ppt[:60] if licit.url_pdf_ppt else "sin URL",
            )
        return {
            "descargados": len(pendientes),
            "omitidos": 0,
            "errores": 0,
            "duracion_seg": round(time.monotonic() - inicio, 2),
        }

    downloader = PDFDownloader()
    stats = downloader.descargar_lote(pendientes, db)
    stats["duracion_seg"] = round(time.monotonic() - inicio, 2)
    return stats


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="🐕 Sabueso de Licitaciones",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python main.py                    # Pipeline completo (ingesta + PDFs)
  python main.py --solo-ingesta     # Solo Step 1
  python main.py --solo-pdfs        # Solo Step 2 (PDFs pendientes)
  python main.py --limite 50        # Máximo 50 licitaciones
  python main.py --dry-run          # Simula sin escribir nada
  python main.py --stats            # Estadísticas de la BD
  python main.py --ping             # Verifica conectividad
        """,
    )
    parser.add_argument(
        "--feed", choices=list(config.PCSP_FEEDS.keys()),
        default=config.ACTIVE_FEED,
    )
    parser.add_argument(
        "--limite", type=int,
        default=config.MAX_LICITACIONES_POR_RUN,
        metavar="N",
    )
    parser.add_argument("--dry-run",     action="store_true")
    parser.add_argument("--stats",       action="store_true")
    parser.add_argument("--ping",        action="store_true")
    parser.add_argument("--solo-ingesta", action="store_true", help="Solo Step 1")
    parser.add_argument("--solo-pdfs",    action="store_true", help="Solo Step 2")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    mostrar_banner()

    # — Ping —
    if args.ping:
        scraper = AtomScraper(feed_url=config.PCSP_FEEDS[args.feed])
        meta = scraper.obtener_metadata_feed()
        if meta.get("ok"):
            console.print(Panel(
                f"✅ Feed accesible\n"
                f"📰 {meta['titulo']}\n"
                f"📦 {meta['total_entradas']} entradas\n"
                f"🕒 {meta['actualizado']}",
                title="PING OK", border_style="green",
            ))
            return 0
        console.print(Panel(f"❌ {meta['error']}", title="PING FAIL", border_style="red"))
        return 1

    db = DatabaseManager()

    # — Solo estadísticas —
    if args.stats:
        with db.session() as session:
            stats = db.estadisticas(session)
            recientes = db.obtener_recientes(session, limite=10)
        mostrar_estadisticas(stats)
        mostrar_tabla_licitaciones(recientes, "🔍 10 Más Recientes")
        return 0

    # — Pipeline —
    ejecutar_step1 = not args.solo_pdfs
    ejecutar_step2 = not args.solo_ingesta

    res_ingesta = {"nuevas": 0, "duplicadas": 0, "errores": 0, "duracion_seg": 0.0}
    res_pdfs    = {"descargados": 0, "omitidos": 0, "errores": 0, "duracion_seg": 0.0}

    # ── Step 1 ────────────────────────────────────────────────────────────────
    if ejecutar_step1:
        console.print(
            f"\n[bold]▶ Step 1 — Ingesta[/bold] | "
            f"Feed: [cyan]{args.feed}[/cyan] | "
            f"Límite: [yellow]{args.limite}[/yellow] | "
            f"Dry-run: [{'red]SÍ' if args.dry_run else 'green]NO'}[/]\n"
        )
        res_ingesta = ejecutar_ingesta(args.feed, args.limite, args.dry_run, db)

        color = "green" if res_ingesta["errores"] == 0 else "yellow"
        console.print(Panel(
            f"[bold]Step 1 completado en {res_ingesta['duracion_seg']}s[/bold]\n\n"
            f"  🆕 Nuevas:     [green bold]{res_ingesta['nuevas']}[/green bold]\n"
            f"  🔄 Duplicadas: [yellow]{res_ingesta['duplicadas']}[/yellow]\n"
            f"  ❌ Errores:    [red]{res_ingesta['errores']}[/red]",
            title="📥 Ingesta", border_style=color, box=box.ROUNDED,
        ))

    # ── Step 2 ────────────────────────────────────────────────────────────────
    if ejecutar_step2 and not args.dry_run or (ejecutar_step2 and args.dry_run):
        console.print(f"\n[bold]▶ Step 2 — Descarga de PDFs[/bold]\n")
        res_pdfs = ejecutar_descarga_pdfs(args.limite, args.dry_run, db)

        color = "green" if res_pdfs["errores"] == 0 else "yellow"
        console.print(Panel(
            f"[bold]Step 2 completado en {res_pdfs['duracion_seg']}s[/bold]\n\n"
            f"  ✅ Descargados: [green bold]{res_pdfs['descargados']}[/green bold]\n"
            f"  ⏭️  Omitidos:    [yellow]{res_pdfs['omitidos']}[/yellow]\n"
            f"  ❌ Errores:     [red]{res_pdfs['errores']}[/red]",
            title="📄 Descarga PDFs", border_style=color, box=box.ROUNDED,
        ))

    # ── Tabla final y estadísticas ────────────────────────────────────────────
    with db.session() as session:
        recientes = db.obtener_recientes(session, limite=args.limite)
        stats = db.estadisticas(session)

    if recientes:
        mostrar_tabla_licitaciones(
            recientes,
            titulo=f"🐕 {len(recientes)} Últimas Licitaciones",
        )

    mostrar_estadisticas(stats)

    total_errores = res_ingesta["errores"] + res_pdfs["errores"]
    return 0 if total_errores == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
