"""
scripts/crear_cliente_prueba.py — Sabueso de Licitaciones
===========================================================
Script de utilidad para crear un cliente de prueba en la BD.

Ejecutar desde la raíz del proyecto:
    python scripts/crear_cliente_prueba.py

Personaliza los datos del cliente y el perfil antes de ejecutarlo.
Puedes ejecutarlo varias veces — no crea duplicados si el email ya existe.
"""

import sys
from pathlib import Path

# Añadimos la raíz al path para poder importar el proyecto
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db_manager import DatabaseManager
from src.models import Cliente, PerfilInteres
from sqlalchemy import select

db = DatabaseManager()

# ─────────────────────────────────────────────────────────────────────────────
# PERSONALIZA ESTOS DATOS ANTES DE EJECUTAR
# ─────────────────────────────────────────────────────────────────────────────

CLIENTE_EMAIL = "prueba@miempresa.es"
CLIENTE_NOMBRE = "Empresa de Prueba S.L."

PERFIL_NOMBRE = "Contratos IT y desarrollo software"

# Palabras clave: si alguna aparece en el título o texto → pasa el filtro A
# Ponlas en minúsculas, separadas por comas
PALABRAS_CLAVE = "software,aplicación,sistema,desarrollo,plataforma,digital,informática,tecnología"

# Prefijos CPV: los primeros 2 dígitos del código CPV
# 72 = servicios informáticos, 48 = software
# Déjalo vacío ("") para no filtrar por CPV
CPV_PREFIJOS = "72,48"

# Rango de presupuesto en euros (None = sin límite)
PRESUPUESTO_MIN = 10_000.0   # No nos interesan contratos menores de 10.000€
PRESUPUESTO_MAX = None        # Sin límite máximo

# Provincias de interés (vacío = toda España)
PROVINCIAS = ""

# Descripción para la IA: explica qué tipo de empresa eres y qué buscas.
# Cuanto más específica, mejores resultados dará Gemini.
DESCRIPCION_IA = """
Somos una empresa de desarrollo de software con 15 años de experiencia,
especializada en aplicaciones web y móviles para el sector público.
Nuestros principales servicios son:
- Desarrollo de portales ciudadanos y sedes electrónicas
- Sistemas de gestión documental y expedientes
- Mantenimiento y evolución de aplicaciones existentes
- Integración con sistemas de administración electrónica (Cl@ve, @firma)

Buscamos licitaciones de administraciones públicas (ayuntamientos,
diputaciones, comunidades autónomas) para desarrollo, implantación o
mantenimiento de software. Presupuestos entre 10.000€ y 2.000.000€.
No nos interesan contratos de hardware, infraestructura física,
limpieza, catering u otros servicios no relacionados con IT.
"""

# ─────────────────────────────────────────────────────────────────────────────

with db.session() as session:
    # Comprobamos si el cliente ya existe
    cliente_existente = session.scalar(
        select(Cliente).where(Cliente.email == CLIENTE_EMAIL)
    )

    if cliente_existente:
        print(f"⚠️  El cliente '{CLIENTE_EMAIL}' ya existe (id={cliente_existente.id})")
        cliente = cliente_existente
    else:
        # Creamos el cliente
        cliente = Cliente(
            nombre=CLIENTE_NOMBRE,
            email=CLIENTE_EMAIL,
            activo=True,
        )
        session.add(cliente)
        session.flush()  # Para que cliente.id esté disponible antes del commit
        print(f"✅ Cliente creado: {CLIENTE_NOMBRE} (id={cliente.id})")

    # Comprobamos si el perfil ya existe para este cliente
    perfil_existente = session.scalar(
        select(PerfilInteres).where(
            PerfilInteres.cliente_id == cliente.id,
            PerfilInteres.nombre == PERFIL_NOMBRE,
        )
    )

    if perfil_existente:
        print(f"⚠️  El perfil '{PERFIL_NOMBRE}' ya existe (id={perfil_existente.id})")
    else:
        perfil = PerfilInteres(
            cliente_id=cliente.id,
            nombre=PERFIL_NOMBRE,
            palabras_clave=PALABRAS_CLAVE or None,
            cpv_prefijos=CPV_PREFIJOS or None,
            presupuesto_min=PRESUPUESTO_MIN,
            presupuesto_max=PRESUPUESTO_MAX,
            provincias=PROVINCIAS or None,
            descripcion_ia=DESCRIPCION_IA.strip(),
            activo=True,
        )
        session.add(perfil)
        print(f"✅ Perfil creado: {PERFIL_NOMBRE}")

print()
print("Ahora puedes ejecutar el análisis de relevancia con:")
print("  python main.py --solo-analisis --limite 5")
