# 🐕 Sabueso de Licitaciones

> Monitorización de licitaciones públicas españolas (PCSP) para PYMES, procesado con IA local (Ollama/Llama 4).

## Estructura del Proyecto

```
sabueso_licitaciones/
│
├── src/
│   ├── __init__.py
│   ├── logger.py          # Fábrica de loggers (rotación de ficheros)
│   ├── models.py          # LicitacionSchema (transferencia) + ORM (SQLAlchemy)
│   ├── db_manager.py      # Única capa de acceso a BD
│   ├── scraper_atom.py    # Ingesta del feed ATOM de la PCSP
│   └── utils.py           # Funciones puras: fechas, HTML, importes
│
├── data/pdfs/             # PDFs descargados (Step 2)
├── database/              # sabueso.db — generado automáticamente
├── logs/                  # sabueso.log — rotación automática
│
├── config.py              # Fuente única de verdad de configuración
├── main.py                # Orquestador CLI (entry point)
├── requirements.txt
├── .env.example           # Plantilla de variables de entorno
├── .gitignore
└── README.md
```

## Responsabilidades por Módulo

| Módulo | Responsabilidad | NO hace |
|---|---|---|
| `logger.py` | Configura handlers de log | Nada más |
| `models.py` | Define estructuras de datos | No accede a BD ni HTTP |
| `utils.py` | Transforma datos (puro) | No escribe en BD ni hace HTTP |
| `scraper_atom.py` | Descarga y parsea el feed | No escribe en BD |
| `db_manager.py` | Lee y escribe en BD | No parsea HTML ni hace HTTP |
| `main.py` | Orquesta los módulos | No contiene lógica de negocio |

## Instalación

```bash
# 1. Crear entorno virtual
python -m venv .venv
source .venv/bin/activate      # Linux/Mac
.venv\Scripts\activate         # Windows

# 2. Instalar dependencias del Step 1
pip install feedparser beautifulsoup4 lxml sqlalchemy requests \
            tenacity python-dateutil python-dotenv rich

# 3. Configurar entorno (opcional)
cp .env.example .env
# Editar .env si quieres cambiar algo

# 4. Ejecutar
python main.py
```

## Uso CLI

```bash
python main.py                      # 20 licitaciones (defecto)
python main.py --limite 100         # hasta 100 licitaciones
python main.py --feed novedades     # feed más ligero
python main.py --dry-run            # simula sin tocar la BD
python main.py --stats              # estadísticas de la BD
python main.py --ping               # verifica conectividad con la PCSP
```

## Pipeline Completo (Roadmap)

```
[Step 1] Feed ATOM → SQLite          ← ESTE MÓDULO
[Step 2] Playwright → URLs de PDFs
[Step 3] pymupdf + Gemini → texto
[Step 4] Llama 4 local → relevancia
[Step 5] GPT-5 → notificación al cliente
```

## Estados del Pipeline

| Estado | Descripción |
|---|---|
| `NUEVA` | Recién ingestada del feed |
| `PDF_PENDIENTE` | Link detectado, PDF sin descargar |
| `PDF_DESCARGADO` | PDF guardado en `data/pdfs/` |
| `ANALISIS_PENDIENTE` | En cola para la IA |
| `ANALIZADA` | Llama 4 la procesó |
| `RELEVANTE` | IA: interesante para el cliente |
| `DESCARTADA` | IA: no relevante |
| `NOTIFICADA` | Enviada al cliente vía GPT-5 |
| `ERROR` | Falló en algún paso |
