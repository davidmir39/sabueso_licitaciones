"""
main.py — Sabueso de Licitaciones
====================================
Orquestador del pipeline Steps 1 + 2 + 3 + 4.

Step 1 — Ingesta:     descarga el feed ATOM y persiste licitaciones.
Step 2 — PDFs:        descarga los PDFs cuya URL está en el feed.
Step 3 — Extracción:  extrae el texto de los PDFs descargados.
Step 4 — Análisis:    decide si cada licitación es relevante para cada cliente.

Uso:
    python main.py                          # Pipeline completo (Steps 1-4)
    python main.py --solo-ingesta           # Solo Step 1
    python main.py --solo-pdfs              # Solo Step 2
    python main.py --solo-extraccion        # Solo Step 3
    python main.py --solo-analisis          # Solo Step 4
    python main.py --limite 10              # Máximo 10 licitaciones
    python main.py --dry-run                # Simula sin escribir nada
    python main.py --stats                  # Estadísticas de la BD
    python main.py --ping                   # Verifica conectividad
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

import config
from src.logger import get_logger
from src.db_manager import DatabaseManager
from src.scraper_atom import AtomScraper, PCPSFeedError, _esta_cerrada
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
        "[dim]Pipeline Steps 1-4 — Ingesta, PDFs, Extracción y Análisis[/dim]\n"
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
    tabla.add_column("Estado",       width=18, style="magenta")
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

def ejecutar_ingesta(feed_key: str, limite: int, dry_run: bool, db: DatabaseManager) -> dict:
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

    try:
        for schema in scraper.iterar_licitaciones(limite=limite):
            resultados["total_feed"] += 1
            if dry_run:
                logger.info("[DRY-RUN] %s | %s€", schema.titulo[:60], schema.presupuesto_base or "N/A")
                resultados["nuevas"] += 1
                continue
            # si la licitación ya está cerrada, la insertamos
            # directamente como DESCARTADA, sin gastar Steps 2-4 en ella.
            estado_forzado = None
            if _esta_cerrada(schema.estado_contrato):
                estado_forzado = config.EstadoLicitacion.DESCARTADA

            with db.session() as session:
                _, resultado = db.insertar_licitacion(session, schema, estado_forzado)
            if resultado == "INSERTADA":     resultados["nuevas"] += 1
            elif resultado == "DUPLICADA":   resultados["duplicadas"] += 1
            else:                            resultados["errores"] += 1
    except PCPSFeedError as exc:
        logger.critical("Error crítico del feed: %s", exc)
        resultados["errores"] += 1
        estado_run, mensaje_run = "ERROR", str(exc)
    except KeyboardInterrupt:
        logger.warning("Ingesta interrumpida.")
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

def ejecutar_descarga_pdfs(limite: int, dry_run: bool, db: DatabaseManager) -> dict:
    inicio = time.monotonic()

    with db.session() as session:
        pendientes = db.obtener_pendientes_pdf(session, limite=limite)

    if not pendientes:
        logger.info("No hay PDFs pendientes de descarga.")
        return {"descargados": 0, "omitidos": 0, "errores": 0, "duracion_seg": 0.0}

    if dry_run:
        for licit in pendientes:
            logger.info("[DRY-RUN PDF] %s", licit.nombre_pdf or "sin nombre")
        return {"descargados": len(pendientes), "omitidos": 0, "errores": 0,
                "duracion_seg": round(time.monotonic() - inicio, 2)}

    downloader = PDFDownloader()
    stats = downloader.descargar_lote(pendientes, db)
    stats["duracion_seg"] = round(time.monotonic() - inicio, 2)
    return stats


# ──────────────────────────────────────────────────────────────────────────────
# STEP 3 — EXTRACCIÓN DE TEXTO
# ──────────────────────────────────────────────────────────────────────────────

def ejecutar_extraccion(limite: int, dry_run: bool, db: DatabaseManager) -> dict:
    inicio = time.monotonic()
    stats = {"extraidos": 0, "errores": 0, "duracion_seg": 0.0}

    with db.session() as session:
        pendientes = db.obtener_pendientes_extraccion(session, limite=limite)

    if not pendientes:
        logger.info("No hay PDFs pendientes de extracción.")
        return stats

    if dry_run:
        for licit in pendientes:
            logger.info("[DRY-RUN EXTRACCIÓN] %.60s", licit.titulo)
        stats["extraidos"] = len(pendientes)
        stats["duracion_seg"] = round(time.monotonic() - inicio, 2)
        return stats

    from src.extractor import extraer_texto

    for licit in pendientes:
        ruta = Path(licit.ruta_pdf_local)
        texto = extraer_texto(ruta)

        if texto and len(texto.strip()) >= config.TEXTO_MINIMO_CARACTERES:
            with db.session() as session:
                db.marcar_texto_extraido(session, licit.id, texto)
            stats["extraidos"] += 1
        else:
            with db.session() as session:
                db.actualizar_estado(session, licit.id, config.EstadoLicitacion.ERROR,
                                     error_msg="No se pudo extraer texto")
            stats["errores"] += 1

    stats["duracion_seg"] = round(time.monotonic() - inicio, 2)
    return stats


# ──────────────────────────────────────────────────────────────────────────────
# STEP 4 — ANÁLISIS DE RELEVANCIA
# ──────────────────────────────────────────────────────────────────────────────

def ejecutar_analisis(limite: int, dry_run: bool, db: DatabaseManager) -> dict:
    """
    Para cada licitación en ANALISIS_PENDIENTE, la analiza contra todos
    los perfiles activos y guarda el resultado en la tabla 'matches'.
    """
    inicio = time.monotonic()
    stats = {"relevantes": 0, "descartadas": 0, "errores": 0, "duracion_seg": 0.0}

    # Obtenemos las licitaciones pendientes y los perfiles activos
    with db.session() as session:
        pendientes = db.obtener_pendientes_analisis(session, limite=limite)
        perfiles = db.obtener_perfiles_activos(session)

    if not pendientes:
        logger.info("No hay licitaciones pendientes de análisis.")
        return stats

    if not perfiles:
        logger.warning(
            "No hay perfiles activos. Crea uno con: "
            "python scripts/crear_cliente_prueba.py"
        )
        return stats

    logger.info(
        "Analizando %d licitaciones contra %d perfiles...",
        len(pendientes), len(perfiles),
    )

    if dry_run:
        for licit in pendientes:
            logger.info("[DRY-RUN ANÁLISIS] %.60s", licit.titulo)
        stats["relevantes"] = len(pendientes)
        stats["duracion_seg"] = round(time.monotonic() - inicio, 2)
        return stats

    from src.analizador import analizar_licitacion_para_perfil

    for licit in pendientes:
        # Para esta licitación, la analizamos contra cada perfil
        alguno_relevante = False
        hubo_fallo_tecnico = False   # ¿algún perfil no se pudo evaluar por fallo de IA?

        for perfil in perfiles:
            try:
                resultado = analizar_licitacion_para_perfil(licit, perfil)

                # Guardamos el match en la BD
                with db.session() as session:
                    db.guardar_match(
                        session,
                        licitacion_id=licit.id,
                        perfil_id=perfil.id,
                        paso_filtro_a=resultado["paso_filtro_a"],
                        score_ia=resultado["score_ia"],
                        razon_ia=resultado["razon_ia"],
                    )

                if resultado["es_relevante"]:
                    alguno_relevante = True
                    logger.info(
                        "RELEVANTE score=%d para '%s': %.50s",
                        resultado["score_ia"] or 0,
                        perfil.nombre,
                        licit.titulo,
                    )

                # Si este perfil no se pudo evaluar por un fallo de IA, lo anotamos.
                if resultado.get("fallo_tecnico", False):
                    hubo_fallo_tecnico = True
                    logger.warning(
                        "Fallo técnico de IA para '%s': %.50s → se reintentará",
                        perfil.nombre, licit.titulo,
                    )

            except Exception as exc:
                logger.error(
                    "Error analizando licitación '%.40s' para perfil '%s': %s",
                    licit.titulo, perfil.nombre, exc,
                )

        # Actualizamos el estado final de la licitación.
        # Tres casos posibles (la relevancia tiene prioridad sobre el fallo):
        #   1. Algún perfil la marcó relevante → RELEVANTE
        #   2. No relevante, pero hubo fallo técnico → vuelve a ANALISIS_PENDIENTE
        #      (no la descartamos por un fallo de infraestructura, se reintentará)
        #   3. No relevante y sin fallos → DESCARTADA (descarte legítimo)
        with db.session() as session:
            if alguno_relevante:
                db.marcar_licitacion_analizada(session, licit.id, True)
                stats["relevantes"] += 1
            elif hubo_fallo_tecnico:
                db.actualizar_estado(
                    session, licit.id,
                    config.EstadoLicitacion.ANALISIS_PENDIENTE,
                )
                stats["errores"] += 1
            else:
                db.marcar_licitacion_analizada(session, licit.id, False)
                stats["descartadas"] += 1
    stats["duracion_seg"] = round(time.monotonic() - inicio, 2)
    logger.info(
        "Análisis completado: %d relevantes | %d descartadas",
        stats["relevantes"], stats["descartadas"],
    )
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
  python main.py                        # Pipeline completo (Steps 1-4)
  python main.py --solo-ingesta         # Solo Step 1
  python main.py --solo-pdfs            # Solo Step 2
  python main.py --solo-extraccion      # Solo Step 3
  python main.py --solo-analisis        # Solo Step 4
  python main.py --limite 10            # Máximo 10 licitaciones
  python main.py --dry-run              # Simula sin escribir nada
  python main.py --stats                # Estadísticas de la BD
  python main.py --ping                 # Verifica conectividad
        """,
    )
    parser.add_argument("--feed", choices=list(config.PCSP_FEEDS.keys()), default=config.ACTIVE_FEED)
    parser.add_argument("--limite", type=int, default=config.MAX_LICITACIONES_POR_RUN, metavar="N")
    parser.add_argument("--dry-run",         action="store_true")
    parser.add_argument("--stats",           action="store_true")
    parser.add_argument("--ping",            action="store_true")
    parser.add_argument("--solo-ingesta",    action="store_true")
    parser.add_argument("--solo-pdfs",       action="store_true")
    parser.add_argument("--solo-extraccion", action="store_true")
    parser.add_argument("--solo-analisis",   action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    mostrar_banner()

    if args.ping:
        scraper = AtomScraper(feed_url=config.PCSP_FEEDS[args.feed])
        meta = scraper.obtener_metadata_feed()
        if meta.get("ok"):
            console.print(Panel(
                f"✅ Feed accesible\n📰 {meta['titulo']}\n"
                f"📦 {meta['total_entradas']} entradas\n🕒 {meta['actualizado']}",
                title="PING OK", border_style="green",
            ))
            return 0
        console.print(Panel(f"❌ {meta['error']}", title="PING FAIL", border_style="red"))
        return 1

    db = DatabaseManager()

    if args.stats:
        with db.session() as session:
            stats = db.estadisticas(session)
            recientes = db.obtener_recientes(session, limite=10)
        mostrar_estadisticas(stats)
        mostrar_tabla_licitaciones(recientes, "🔍 10 Más Recientes")
        return 0

    # Decidimos qué steps ejecutar
    algun_solo = args.solo_ingesta or args.solo_pdfs or args.solo_extraccion or args.solo_analisis
    ejecutar_step1 = args.solo_ingesta    or not algun_solo
    ejecutar_step2 = args.solo_pdfs       or not algun_solo
    ejecutar_step3 = args.solo_extraccion or not algun_solo
    ejecutar_step4 = args.solo_analisis   or not algun_solo

    res = {
        "ingesta":    {"nuevas": 0, "duplicadas": 0, "errores": 0, "duracion_seg": 0.0},
        "pdfs":       {"descargados": 0, "omitidos": 0, "errores": 0, "duracion_seg": 0.0},
        "extraccion": {"extraidos": 0, "errores": 0, "duracion_seg": 0.0},
        "analisis":   {"relevantes": 0, "descartadas": 0, "errores": 0, "duracion_seg": 0.0},
    }

    if ejecutar_step1:
        console.print(f"\n[bold]▶ Step 1 — Ingesta[/bold] | Feed: [cyan]{args.feed}[/cyan] | Límite: [yellow]{args.limite}[/yellow]\n")
        res["ingesta"] = ejecutar_ingesta(args.feed, args.limite, args.dry_run, db)
        color = "green" if res["ingesta"]["errores"] == 0 else "yellow"
        console.print(Panel(
            f"Step 1 en {res['ingesta']['duracion_seg']}s\n\n"
            f"  🆕 Nuevas: [green bold]{res['ingesta']['nuevas']}[/] | "
            f"🔄 Duplicadas: [yellow]{res['ingesta']['duplicadas']}[/] | "
            f"❌ Errores: [red]{res['ingesta']['errores']}[/]",
            title="📥 Ingesta", border_style=color, box=box.ROUNDED,
        ))

    if ejecutar_step2:
        console.print(f"\n[bold]▶ Step 2 — Descarga de PDFs[/bold]\n")
        res["pdfs"] = ejecutar_descarga_pdfs(args.limite, args.dry_run, db)
        color = "green" if res["pdfs"]["errores"] == 0 else "yellow"
        console.print(Panel(
            f"Step 2 en {res['pdfs']['duracion_seg']}s\n\n"
            f"  ✅ Descargados: [green bold]{res['pdfs']['descargados']}[/] | "
            f"❌ Errores: [red]{res['pdfs']['errores']}[/]",
            title="📄 Descarga PDFs", border_style=color, box=box.ROUNDED,
        ))

    if ejecutar_step3:
        console.print(f"\n[bold]▶ Step 3 — Extracción de texto[/bold]\n")
        res["extraccion"] = ejecutar_extraccion(args.limite, args.dry_run, db)
        color = "green" if res["extraccion"]["errores"] == 0 else "yellow"
        console.print(Panel(
            f"Step 3 en {res['extraccion']['duracion_seg']}s\n\n"
            f"  📝 Extraídos: [green bold]{res['extraccion']['extraidos']}[/] | "
            f"❌ Errores: [red]{res['extraccion']['errores']}[/]",
            title="📝 Extracción", border_style=color, box=box.ROUNDED,
        ))

    if ejecutar_step4:
        console.print(f"\n[bold]▶ Step 4 — Análisis de relevancia[/bold]\n")
        res["analisis"] = ejecutar_analisis(args.limite, args.dry_run, db)
        color = "green" if res["analisis"]["errores"] == 0 else "yellow"
        console.print(Panel(
            f"Step 4 en {res['analisis']['duracion_seg']}s\n\n"
            f"  ⭐ Relevantes:  [green bold]{res['analisis']['relevantes']}[/]\n"
            f"  🗑️  Descartadas: [yellow]{res['analisis']['descartadas']}[/]\n"
            f"  ❌ Errores:     [red]{res['analisis']['errores']}[/]",
            title="🤖 Análisis IA", border_style=color, box=box.ROUNDED,
        ))

    with db.session() as session:
        recientes = db.obtener_recientes(session, limite=args.limite)
        stats = db.estadisticas(session)

    if recientes:
        mostrar_tabla_licitaciones(recientes, f"🐕 {len(recientes)} Últimas Licitaciones")

    mostrar_estadisticas(stats)

    total_errores = sum(r.get("errores", 0) for r in res.values())
    return 0 if total_errores == 0 else 1


if __name__ == "__main__":
    sys.exit(main())