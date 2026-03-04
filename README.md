# 🐕 Sabueso de Licitaciones

> Sistema de monitorización de licitaciones públicas españolas (PCSP) para PYMES.
> Stack: Python 3.10+ · SQLAlchemy 2.0 · SQLite → PostgreSQL ready · Ollama/Llama 4 ready

---

## Estructura del Proyecto

```
sabueso_licitaciones/
├── config.py                    # Fuente única de verdad de configuración
├── main.py                      # Orquestador principal (CLI)
├── requirements.txt
├── .env.example                 # Plantilla de variables de entorno
│
├── src/
│   ├── __init__.py
│   ├── logger.py               # Logging centralizado con rotación de archivos
│   ├── models.py               # SQLAlchemy ORM + Pydantic schemas
│   ├── db_manager.py           # Gestión de BD (context managers, CRUD)
│   ├── scraper_atom.py         # Motor de ingesta del feed ATOM PCSP
│   └── utils.py                # Parsers y funciones auxiliares puras
│
├── tests/
│   ├── test_utils.py           # Tests unitarios (sin red ni BD)
│   └── test_db_manager.py      # Tests de integración (SQLite :memory:)
│
├── database/
│   ├── sabueso.db              # Generado automáticamente (en .gitignore)
│   └── README.md               # Instrucciones de Alembic
│
├── data/pdfs/                  # PDFs descargados (Paso 2)
└── logs/                       # Logs rotativos diarios
```

---

## Instalación

```bash
# 1. Crear entorno virtual
python3 -m venv .venv
source .venv/bin/activate        # Linux/Mac
# .venv\Scripts\activate         # Windows

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Configurar entorno
cp .env.example .env
# Editar .env si necesitas cambiar URLs o configuración

# 4. Verificar instalación
python main.py --stats
```

---

## Uso

```bash
# Ingestar las últimas 20 licitaciones (por defecto)
python main.py

# Procesar hasta 100 licitaciones
python main.py --limite 100

# Usar el feed de novedades (más liviano, ideal para polling frecuente)
python main.py --feed novedades

# Dry-run: procesar sin escribir en BD (testing / debug)
python main.py --dry-run

# Ver estadísticas de la BD
python main.py --stats
```

---

## Ejecutar Tests

```bash
# Todos los tests con cobertura
pytest tests/ -v --cov=src --cov-report=term-missing

# Solo tests unitarios (rápidos, sin BD)
pytest tests/test_utils.py -v

# Solo tests de integración
pytest tests/test_db_manager.py -v
```

---

## Arquitectura del Pipeline (5 Pasos)

```
Feed ATOM (PCSP)
      │
      ▼
┌─────────────┐    ┌──────────────┐    ┌─────────────┐
│  AtomScraper│───▶│ DatabaseMgr  │───▶│  SQLite DB  │
│  (Paso 1)   │    │  (Paso 1)    │    │  sabueso.db │
└─────────────┘    └──────────────┘    └─────────────┘
                          │
      ┌───────────────────┼───────────────────┐
      ▼                   ▼                   ▼
┌──────────┐     ┌─────────────┐     ┌─────────────┐
│ Scraper  │     │  Gemini 1.5 │     │  Llama 4    │
│  PDFs    │     │  Pro (OCR)  │     │  (Filtrado) │
│ (Paso 2) │     │  (Paso 3)   │     │  (Paso 4)   │
└──────────┘     └─────────────┘     └─────────────┘
                                            │
                                            ▼
                                    ┌─────────────┐
                                    │   GPT-5     │
                                    │ (Engagement)│
                                    │  (Paso 5)   │
                                    └─────────────┘
```

## Estados del Ciclo de Vida

| Estado | Descripción |
|--------|-------------|
| `NUEVA` | Ingestada del feed, pendiente de procesar |
| `PDF_PENDIENTE` | URLs de PDF detectadas, pendiente descarga |
| `PDF_DESCARGADO` | PDF en disco, pendiente análisis IA |
| `ANALISIS_PENDIENTE` | Listo para procesamiento IA |
| `ANALIZADA` | Llama 4 ha procesado el contenido |
| `RELEVANTE` | IA marcó como interesante para el cliente |
| `DESCARTADA` | IA descartó como no relevante |
| `NOTIFICADA` | Enviada al cliente vía GPT-5 |
| `ERROR` | Falló algún paso del pipeline |

---

## Feeds PCSP Disponibles

| Feed | URL | Uso Recomendado |
|------|-----|-----------------|
| `completo` | `.../sindicacion_1044/...` | Información completa con XML enriquecido |
| `novedades` | `.../sindicacion_1045/...` | Solo últimas novedades, más liviano |

---

## Configuración de Producción (Cron)

```bash
# Ejecutar cada 30 minutos con el feed de novedades
*/30 * * * * cd /opt/sabueso && .venv/bin/python main.py --feed novedades >> logs/cron.log 2>&1

# Ejecutar feed completo 1 vez al día (madrugada)
0 3 * * * cd /opt/sabueso && .venv/bin/python main.py --limite 500 >> logs/cron_diario.log 2>&1
```

---

## Migración a PostgreSQL

Cambia una sola línea en `.env`:

```env
# Antes (desarrollo)
DATABASE_URL=sqlite:///database/sabueso.db

# Después (producción)
DATABASE_URL=postgresql+psycopg2://user:password@host:5432/sabueso
```

Instala el driver y aplica migraciones:
```bash
pip install psycopg2-binary
alembic -c database/alembic.ini upgrade head
```
