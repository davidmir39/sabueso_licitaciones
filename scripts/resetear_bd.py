"""
scripts/resetear_bd.py — Sabueso de Licitaciones
===================================================
Script de utilidad para limpiar la BD de datos de PROCESO durante el desarrollo.

Borra:      matches, licitaciones, log_ejecuciones (datos regenerables)
CONSERVA:   clientes, perfiles_interes (tu configuración)

Uso:
    python scripts/resetear_bd.py

Pide confirmación explícita antes de borrar. Pensado para desarrollo,
NO para producción con datos reales de clientes.
"""

import sys
from pathlib import Path

# Añadimos la raíz al path para poder importar el proyecto
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db_manager import DatabaseManager
from src.models import Licitacion, MatchLicitacion, LogEjecucion
from sqlalchemy import func, select, delete

db = DatabaseManager()

# ── Tablas que se van a borrar, EN ORDEN (de hoja a raíz por las FK) ──────────
# matches referencia a licitaciones, así que matches va primero.
TABLAS_A_BORRAR = [
    ("matches", MatchLicitacion),
    ("licitaciones", Licitacion),
    ("log_ejecuciones", LogEjecucion),
]

# ── Paso 1: mostrar cuántas filas hay en cada tabla ───────────────────────────
print("\n=== Estado actual de la BD ===")
with db.session() as session:
    for nombre, modelo in TABLAS_A_BORRAR:
        cantidad = session.scalar(select(func.count()).select_from(modelo))
        print(f"  {nombre:20} {cantidad:>6} filas")

print("\n⚠️  Se BORRARÁN todas las filas de esas tablas.")
print("    Se CONSERVAN: clientes, perfiles_interes.")

# ── Paso 2: confirmación explícita de seguridad ───────────────────────────────
respuesta = input("\nEscribe 'BORRAR' (en mayúsculas) para confirmar: ").strip()

if respuesta != "BORRAR":
    print("❌ Cancelado. No se ha borrado nada.")
    raise SystemExit

# ── Paso 3: borrar en orden ───────────────────────────────────────────────────
with db.session() as session:
    for nombre, modelo in TABLAS_A_BORRAR:
        resultado = session.execute(delete(modelo))
        print(f"  🗑️  {nombre}: {resultado.rowcount} filas borradas")

print("\n✅ BD limpia. Clientes y perfiles conservados.")
print("Ahora puedes lanzar una ingesta nueva con:")
print("  python main.py --limite 50")