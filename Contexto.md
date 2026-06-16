# Sabueso de Licitaciones — Explicación Técnica Completa
**Fecha:** Marzo 2026 | **Versión:** Step 1 + Step 2

---

## Índice

1. [Arquitectura general](#1-arquitectura-general)
2. [Flujo de datos de principio a fin](#2-flujo-de-datos-de-principio-a-fin)
3. [config.py — La fuente de verdad](#3-configpy--la-fuente-de-verdad)
4. [src/logger.py — El sistema de logs](#4-srcloggerpy--el-sistema-de-logs)
5. [src/models.py — Las estructuras de datos](#5-srcmodelspy--las-estructuras-de-datos)
6. [src/utils.py — Las funciones puras](#6-srcutilspy--las-funciones-puras)
7. [src/scraper_atom.py — El motor de ingesta](#7-srcscraper_atompy--el-motor-de-ingesta)
8. [src/db_manager.py — La capa de base de datos](#8-srcdb_managerpy--la-capa-de-base-de-datos)
9. [src/downloader.py — El descargador de PDFs](#9-srcdownloaderpy--el-descargador-de-pdfs)
10. [main.py — El orquestador](#10-mainpy--el-orquestador)
11. [Errores conocidos y fixes pendientes](#11-errores-conocidos-y-fixes-pendientes)

---

## 1. Arquitectura general

El proyecto está organizado según el principio de **responsabilidad única**: cada módulo hace exactamente una cosa y no se mete en lo de los demás. Esto es lo que permite que cuando algo falla, sepas exactamente en qué fichero buscar.

```
sabueso_licitaciones/
├── config.py          ← Toda la configuración. Una sola línea para cambiar una URL.
├── main.py            ← El director de orquesta. No contiene lógica de negocio.
└── src/
    ├── logger.py      ← Configura logs una vez. Los demás módulos lo usan.
    ├── models.py      ← Define qué es una licitación en Python y en SQLite.
    ├── utils.py       ← Funciones matemáticas del proyecto. Sin efectos secundarios.
    ├── scraper_atom.py← Habla con la PCSP. Solo produce LicitacionSchema.
    ├── db_manager.py  ← Habla con SQLite. Solo recibe LicitacionSchema y objetos ORM.
    └── downloader.py  ← Descarga PDFs. Guarda en disco y actualiza la BD.
```

**Regla de oro de importaciones — nunca hay ciclos:**
```
config → logger → models → utils → scraper_atom → db_manager → downloader → main
```
Cada módulo solo importa a los que están a su izquierda en esa cadena. Si models.py intentara importar db_manager.py, tendríamos un ciclo (A importa B, B importa A) y Python lanzaría un ImportError.

---

## 2. Flujo de datos de principio a fin

Antes de entrar en el código línea a línea, es importante entender el recorrido completo de una licitación desde el servidor de la PCSP hasta el disco duro:

```
PCSP (servidor externo)
        │
        │  Petición HTTP GET al feed ATOM
        ▼
feedparser.parse(url)
        │
        │  feed.entries[] — lista de entradas crudas de feedparser
        │  Cada entry tiene campos como: title, summary, cbc_uri, cac_budgetamount...
        ▼
AtomScraper._parsear_entrada(entry)
        │  Llama a: limpiar_texto(), parsear_fecha(),
        │           parsear_summary_pcsp(), parsear_budget_amount()
        │
        │  LicitacionSchema (dataclass Python puro)
        │  Ya contiene: título, órgano, presupuesto, url_pdf, nombre_pdf...
        ▼
yield schema    ← generador, entrega UNA a la vez
        │
        ▼
DatabaseManager.insertar_licitacion(session, schema)
        │  Comprueba si ya existe (idempotencia)
        │  Crea objeto Licitacion (ORM) con estado inicial:
        │    → PDF_PENDIENTE  si hay url_pdf_directo
        │    → NUEVA          si no hay URL de PDF
        │  session.add() + commit automático
        │
        ▼
sabueso.db — tabla licitaciones
        │
        ▼
DatabaseManager.obtener_pendientes_pdf(session)
        │  SELECT WHERE estado_proceso = 'PDF_PENDIENTE' AND url_pdf_ppt IS NOT NULL
        │
        │  Lista de objetos Licitacion (ORM)
        ▼
PDFDownloader.descargar_lote(licitaciones, db)
        │  Para cada licitación:
        │    - GET a la URL del PDF (cbc_uri del feed)
        │    - Verifica magic bytes (%PDF)
        │    - Escribe en data/pdfs/YYYYMMDD_NombreDoc.pdf
        │    - db.marcar_pdf_descargado() → estado = PDF_DESCARGADO
        │
        ▼
data/pdfs/*.pdf   ←  listos para el Step 3 (Gemini OCR)
```

---

## 3. config.py — La fuente de verdad

```python
BASE_DIR = Path(__file__).parent.resolve()
```
`__file__` es la ruta del propio fichero `config.py`. `.parent` sube un nivel (a la carpeta del proyecto). `.resolve()` convierte la ruta relativa en absoluta (por ejemplo, de `../sabueso` a `C:\Users\Usuario\Desktop\Proyectos\sabueso_licitaciones`). Esto hace que el proyecto funcione desde cualquier directorio de trabajo, no solo desde donde está instalado.

```python
DATA_DIR = BASE_DIR / "data" / "pdfs"
DATABASE_DIR = BASE_DIR / "database"
LOGS_DIR = BASE_DIR / "logs"

for _dir in (DATA_DIR, DATABASE_DIR, LOGS_DIR):
    _dir.mkdir(parents=True, exist_ok=True)
```
El operador `/` de `pathlib.Path` es equivalente a `os.path.join()` pero más legible. El bucle crea los tres directorios si no existen. `parents=True` crea también directorios intermedios (como `mkdir -p` en Linux). `exist_ok=True` no lanza error si ya existen. Esto se ejecuta en el momento en que se importa `config`, así que las carpetas están garantizadas antes de que cualquier otro módulo intente escribir en ellas.

```python
load_dotenv(BASE_DIR / ".env")
```
Lee el fichero `.env` si existe y carga sus variables en el entorno del proceso. Permite sobreescribir cualquier configuración sin tocar el código: si pones `LOG_LEVEL=DEBUG` en `.env`, el sistema loguea todo en modo verbose. Si no existe `.env`, `load_dotenv` no hace nada (no lanza error).

```python
DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    f"sqlite:///{DATABASE_DIR / 'sabueso.db'}",
)
```
`os.getenv("DATABASE_URL", valor_por_defecto)` intenta leer la variable de entorno. Si no existe, usa el valor por defecto. La URL `sqlite:///ruta/absoluta.db` es el formato que entiende SQLAlchemy. Los tres barras son parte del protocolo (`sqlite://` + `/ruta`). Cuando migremos a PostgreSQL para producción con múltiples clientes, bastará con cambiar esta variable a `postgresql://usuario:password@host/bd` sin tocar nada más.

```python
PCSP_FEEDS: dict[str, str] = {
    "completo": os.getenv(
        "PCSP_FEED_COMPLETO",
        "https://contrataciondelsectorpublico.gob.es/sindicacion/sindicacion_643/"
        "licitacionesPerfilesContratanteCompleto3.atom",
    ),
}
ACTIVE_FEED: str = os.getenv("ACTIVE_FEED", "completo")
```
⚠️ **BUG PENDIENTE**: La versión actual de `config.py` en disco todavía tiene las URLs antiguas (`contrataciondelestado.es`). Deben ser las nuevas (`contrataciondelsectorpublico.gob.es/sindicacion/sindicacion_643/`). Ver sección 11.

```python
HTTP_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; SabuesoLicitaciones/1.0; "
        "+https://sabueso-licitaciones.es/bot)"
    ),
    ...
}
```
El servidor de la PCSP bloquea peticiones que no llevan User-Agent. Este formato (`Mozilla/5.0 (compatible; NombreBot/version; URL)` es el estándar para bots legítimos: identifica al bot, su versión y una URL de contacto. Es la razón por la que `--ping` funciona pero el navegador da Forbidden: el navegador manda cabeceras que el servidor distingue del acceso programático esperado.

```python
class EstadoLicitacion:
    NUEVA               = "NUEVA"
    PDF_PENDIENTE       = "PDF_PENDIENTE"
    PDF_DESCARGADO      = "PDF_DESCARGADO"
    ANALISIS_PENDIENTE  = "ANALISIS_PENDIENTE"
    ANALIZADA           = "ANALIZADA"
    RELEVANTE           = "RELEVANTE"
    DESCARTADA          = "DESCARTADA"
    NOTIFICADA          = "NOTIFICADA"
    ERROR               = "ERROR"
```
Es una clase sin métodos, usada solo como contenedor de constantes (patrón "enum manual"). La alternativa sería `from enum import Enum`, pero las clases simples son más cómodas de usar con SQLAlchemy. El valor del string es exactamente lo que se guarda en la columna `estado_proceso` de SQLite. Si usas `config.EstadoLicitacion.PDF_PENDIENTE` en lugar del literal `"PDF_PENDIENTE"` en el código, un typo como `"PDF_Pendiente"` nunca llegará a producción: Python lanzaría un AttributeError inmediatamente.

---

## 4. src/logger.py — El sistema de logs

```python
_LOG_CONFIGURED: set[str] = set()
```
Variable de módulo (vive mientras el proceso esté corriendo). Guarda los nombres de los loggers ya configurados. Esto evita el problema clásico de añadir handlers duplicados: si `get_logger("src.scraper_atom")` se llama dos veces, la segunda vez detecta que el nombre ya está en el set y devuelve el logger existente sin añadir nuevos handlers. Sin esto, cada línea de log aparecería duplicada en la consola.

```python
def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if name in _LOG_CONFIGURED:
        return logger
```
`logging.getLogger(name)` es parte del sistema de logging estándar de Python. Los loggers son un árbol: `src.scraper_atom` es hijo de `src`, que es hijo del logger raíz. El nombre determina la jerarquía. Por eso en el output ves `src.scraper_atom | INFO | Descargando feed...` — el nombre del logger identifica exactamente de qué módulo vino el mensaje.

```python
    _root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_root))
    import config as _cfg
```
Importamos config dentro de la función para evitar una importación circular. Si lo importáramos al inicio del fichero (`import config`), tendríamos: `logger.py` importa `config.py`, y `config.py` podría en algún momento importar `logger.py`. El import dentro de la función se ejecuta solo cuando se llama a `get_logger`, momento en que `config` ya está completamente cargado.

```python
    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)-22s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
```
Define el formato de cada línea de log. `%(levelname)-8s` significa: imprimir `levelname` alineado a la izquierda en un campo de 8 caracteres (así `INFO    ` y `WARNING ` quedan alineados visualmente). `%(name)-22s` lo mismo para el nombre del módulo. El resultado es lo que ves en consola: `2026-03-07 22:34:57 | INFO     | src.scraper_atom       | Descargando feed...`

```python
    sh = logging.StreamHandler(sys.stdout)     # → consola
    fh = logging.handlers.RotatingFileHandler( # → fichero
        filename=_cfg.LOG_DIR / "sabueso.log",
        maxBytes=_cfg.LOG_MAX_BYTES,           # 10 MB
        backupCount=_cfg.LOG_BACKUP_COUNT,     # 5 ficheros
        encoding="utf-8",
    )
```
Dos handlers: uno escribe en consola, otro en disco. `RotatingFileHandler` crea automáticamente `sabueso.log.1`, `sabueso.log.2`... cuando el fichero principal llega a 10 MB, manteniendo un máximo de 5 ficheros históricos (50 MB total). Sin rotación, en producción corriendo diariamente durante un año acabarías con un fichero de cientos de megabytes.

```python
    logger.propagate = False
```
Sin esta línea, el mensaje subiría por el árbol de loggers hasta el logger raíz y aparecería dos veces. Con `propagate = False`, el mensaje se queda en este logger y no sube.

---

## 5. src/models.py — Las estructuras de datos

Este fichero define dos cosas muy distintas que viven en el mismo sitio porque están íntimamente relacionadas: cómo representar una licitación en Python (dataclass) y cómo guardarla en SQLite (ORM).

### La clase base ORM

```python
class Base(DeclarativeBase):
    pass
```
`DeclarativeBase` es la forma moderna de SQLAlchemy 2.0 de declarar el sistema de mapeo. Todos los modelos ORM deben heredar de esta `Base`. Cuando llames a `Base.metadata.create_all(engine)` en `db_manager.py`, SQLAlchemy inspecciona todas las clases que heredan de `Base` y crea las tablas correspondientes en SQLite si no existen.

### LicitacionSchema — el dataclass de transferencia

```python
@dataclass
class LicitacionSchema:
    id: str
    titulo: str
    link_plataforma: Optional[str] = None
    presupuesto_base: Optional[float] = None
    url_pdf_directo: Optional[str] = None
    nombre_pdf: Optional[str] = None
    # ... más campos
```
`@dataclass` es un decorador de Python que automáticamente genera `__init__`, `__repr__` y `__eq__` a partir de las anotaciones de tipo. Sin él tendrías que escribir un `__init__` de 20 parámetros a mano. Los campos con `= None` son opcionales y tienen ese valor por defecto si no se proporcionan.

Este dataclass **no importa SQLAlchemy**. Es Python puro. Representa una licitación como objeto en memoria, antes de que toque la base de datos. El scraper produce `LicitacionSchema`, el `db_manager` los consume. Si mañana quieres enviar licitaciones a una API REST en lugar de a SQLite, el scraper no cambia ni una línea.

### Licitacion — el modelo ORM

```python
class Licitacion(Base):
    __tablename__ = "licitaciones"

    id: Mapped[str] = mapped_column(String(512), primary_key=True)
    titulo: Mapped[str] = mapped_column(Text, nullable=False)
    presupuesto_base: Mapped[Optional[float]] = mapped_column(Float)
    estado_proceso: Mapped[str] = mapped_column(
        String(30), nullable=False, default="NUEVA"
    )
    url_pdf_ppt: Mapped[Optional[str]] = mapped_column(Text)
    nombre_pdf: Mapped[Optional[str]] = mapped_column(Text)
    ruta_pdf_local: Mapped[Optional[str]] = mapped_column(Text)
    # ...
```
`Mapped[str]` es la sintaxis moderna de SQLAlchemy 2.0 para indicar el tipo Python. `mapped_column(String(512))` indica el tipo SQLite y restricciones. `primary_key=True` en `id` significa que SQLite usa ese campo como clave única: no puede haber dos licitaciones con el mismo ID, y las búsquedas por ID son O(1) gracias al índice implícito.

`nullable=False` significa que SQLite rechazará cualquier INSERT que no incluya ese campo. `default="NUEVA"` es un valor por defecto a nivel Python (SQLAlchemy lo aplica antes del INSERT), no a nivel SQLite.

La columna `url_pdf_ppt` almacena la URL del PDF del Pliego de Prescripciones Técnicas. Aunque en el Step 2 la estemos usando para la URL directa que viene del campo `cbc_uri` del feed, el nombre `_ppt` estaba pensado para el Step original donde usaríamos Playwright. La semántica actual es "URL del primer documento PDF disponible".

```python
    __table_args__ = (
        Index("ix_licitaciones_estado_proceso", "estado_proceso"),
        Index("ix_licitaciones_fecha_pub", "fecha_publicacion"),
        Index("ix_licitaciones_organo", "organo_contratacion"),
        Index("ix_licitaciones_cpv", "cpv_codigo"),
    )
```
Los índices son estructuras de datos adicionales que SQLite mantiene en paralelo a la tabla. Son como el índice de un libro: sin él tienes que leer página por página (full table scan); con él vas directamente a la página correcta. El índice en `estado_proceso` es especialmente importante: la query `SELECT ... WHERE estado_proceso = 'PDF_PENDIENTE'` que se ejecuta en cada run iría de O(n) a O(log n). Con 100.000 licitaciones acumuladas, la diferencia es de segundos a milisegundos.

### LogEjecucion — la auditoría

```python
class LogEjecucion(Base):
    __tablename__ = "log_ejecuciones"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    feed_url: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp_inicio: Mapped[datetime] = mapped_column(DateTime)
    timestamp_fin: Mapped[Optional[datetime]] = mapped_column(DateTime)
    nuevas_insertadas: Mapped[Optional[int]] = mapped_column(Integer)
    estado_run: Mapped[str] = mapped_column(String(20), default="EN_CURSO")
    mensaje: Mapped[Optional[str]] = mapped_column(Text)
```
Cada ejecución del sistema genera una fila en esta tabla. Es lo que permite responder preguntas como "¿a qué hora falló el sistema el martes pasado?" o "¿cuántas licitaciones se han ingestado esta semana?". En un contexto B2B, esta tabla es el contrato de nivel de servicio: puedes demostrarle al cliente que el sistema corrió correctamente los últimos 30 días.

---

## 6. src/utils.py — Las funciones puras

El principio que rige todo este fichero: **una función que recibe datos y devuelve datos transformados, sin tocar nada externo**. No escribe en disco, no hace HTTP, no modifica variables globales. Esto significa que puedes testearlas de forma aislada: `assert parsear_fecha("2026-01-15") == datetime(2026, 1, 15)`.

### limpiar_texto

```python
def limpiar_texto(texto: Optional[str]) -> Optional[str]:
    if not texto:
        return None
    texto = re.sub(r"<[^>]+>", " ", texto)        # elimina etiquetas HTML
    texto = re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]", "", texto)  # caracteres de control
    texto = re.sub(r"\s+", " ", texto).strip()     # colapsa espacios múltiples
    return texto or None
```
Tres expresiones regulares en cascada. La primera `<[^>]+>` coincide con cualquier cosa que empiece con `<`, tenga al menos un carácter que no sea `>`, y termine con `>` — es decir, cualquier etiqueta HTML. La segunda elimina caracteres de control ASCII (rangos hexadecimales que corresponden a caracteres no imprimibles como BEL, BS, DEL). La tercera convierte cualquier secuencia de espacios/tabs/saltos de línea en un único espacio. El `or None` final devuelve `None` si el string quedó vacío después de la limpieza (en Python, `"" or None` evalúa a `None`).

### parsear_fecha

```python
_FORMATOS_FECHA = [
    "%Y-%m-%dT%H:%M:%SZ",    # ISO 8601 UTC: 2026-03-07T10:30:00Z
    "%Y-%m-%dT%H:%M:%S%z",   # ISO 8601 con timezone: 2026-03-07T10:30:00+01:00
    "%Y-%m-%dT%H:%M:%S",     # ISO 8601 sin tz: 2026-03-07T10:30:00
    "%Y-%m-%d",              # Solo fecha: 2026-03-07
    "%d/%m/%Y %H:%M",        # Formato español con hora: 07/03/2026 10:30
    "%d/%m/%Y",              # Formato español: 07/03/2026
    "%d-%m-%Y",              # Con guiones: 07-03-2026
]

def parsear_fecha(fecha_str):
    for fmt in _FORMATOS_FECHA:
        try:
            return datetime.strptime(fecha_limpia, fmt).replace(tzinfo=None)
        except ValueError:
            continue
    # Fallback: dateutil
    from dateutil import parser as dateutil_parser
    return dateutil_parser.parse(fecha_limpia, ignoretz=True)
```
Estrategia de dos niveles. Primero intenta los formatos conocidos con `strptime` en orden de frecuencia (los más comunes primero para salir antes del bucle). `strptime` es muy rápido porque sabe exactamente qué formato espera. Si ninguno funciona, `dateutil.parser.parse` puede interpretar prácticamente cualquier representación de fecha en cualquier idioma, a costa de ser más lento. El `.replace(tzinfo=None)` elimina la información de zona horaria para homogeneizar todos los datetimes: SQLite los almacena sin timezone y mezclar naive y aware datetimes causaría errores.

### parsear_summary_pcsp

```python
def parsear_summary_pcsp(summary: Optional[str]) -> dict:
    partes = [p.strip() for p in summary.split(";") if p.strip()]
    for parte in partes:
        if ":" not in parte:
            continue
        clave, _, valor = parte.partition(":")
        clave = clave.strip().lower()
        valor = valor.strip()
        if "id licitaci" in clave:
            resultado["expediente"] = valor
        elif "rgano" in clave or "contrataci" in clave:
            resultado["organo_contratacion"] = valor
        elif "importe" in clave:
            resultado["importe_summary"] = valor
        elif "estado" in clave:
            resultado["estado_contrato"] = valor
```
El summary del feed tiene este formato verificado en producción: `"Id licitación: 4/2025; Órgano de Contratación: Alcaldía del Ayuntamiento de Cobeja; Importe: 154163.5 EUR; Estado: ADJ"`.

`split(";")` divide el string en partes. `partition(":")` divide cada parte en tres: lo que está antes del primer `:`, el propio `:`, y lo que está después. Es mejor que `split(":")` porque si el valor contiene dos puntos (como en una URL `https://...`), `partition` solo divide en el primero y no rompe el valor.

Las comparaciones de clave usan búsqueda de subcadena (`"rgano" in clave`) en lugar de igualdad exacta. Esto es deliberado: "Órgano" y "organo" y "Órgano de Contratación" todas contienen "rgano", lo que hace el parser robusto a variaciones de mayúsculas y acentos del feed.

### parsear_budget_amount

```python
def parsear_budget_amount(raw: Optional[str]) -> Optional[float]:
    for linea in str(raw).splitlines():
        linea = linea.strip()
        if not linea:
            continue
        try:
            valor = float(linea.replace(",", "."))
            if 1.0 <= valor <= 10_000_000_000:
                return valor
        except ValueError:
            continue
```
El campo `cac_budgetamount` del feed UBL llega como un string multilínea con tres valores:
```
154163.5        ← base sin IVA (queremos este)
186537.83       ← con IVA
154163.5        ← repetición
```
`splitlines()` divide en líneas, tomamos la primera no vacía que se pueda convertir a float. El rango `1.0 <= valor <= 10_000_000_000` filtra valores absurdos (un importe de 0.5€ o de 50 billones de euros probablemente es un error de parsing).

### generar_nombre_pdf

```python
def generar_nombre_pdf(licitacion_id: str, nombre_original: Optional[str] = None) -> str:
    timestamp = datetime.now().strftime("%Y%m%d")
    if nombre_original:
        nombre_limpio = re.sub(r"[^\w\s-]", "", nombre_original).strip()
        nombre_limpio = re.sub(r"\s+", "_", nombre_limpio)[:60]
        return f"{timestamp}_{nombre_limpio}.pdf"
    id_corto = re.sub(r"[^\w]", "_", licitacion_id[-20:])
    return f"{timestamp}_{id_corto}.pdf"
```
`[^\w\s-]` coincide con cualquier carácter que NO sea letra/número (`\w`), espacio (`\s`) o guión (`-`). La negación `^` dentro de `[]` invierte el conjunto. Esto elimina caracteres especiales como `/`, `:`, `(`, `)` que romperían un nombre de fichero en Windows o Linux. El `[:60]` limita el nombre a 60 caracteres para evitar paths demasiado largos. El timestamp al inicio garantiza que los ficheros estén ordenados cronológicamente en el explorador de ficheros.

---

## 7. src/scraper_atom.py — El motor de ingesta

### PCPSFeedError

```python
class PCPSFeedError(Exception):
    pass
```
Excepción personalizada. Permite captura selectiva en `main.py`: `except PCPSFeedError` captura solo errores del feed, no todos los errores de Python. Sin ella tendríamos que capturar `Exception` (demasiado amplio, puede silenciar bugs) o `requests.RequestException` (insuficiente, no cubre errores de parsing XML).

### _crear_sesion_http

```python
def _crear_sesion_http(self) -> requests.Session:
    session = requests.Session()
    session.headers.update(config.HTTP_HEADERS)
    transport_retry = Retry(
        total=2,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=transport_retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session
```
`requests.Session` reutiliza la conexión TCP subyacente entre peticiones (HTTP keep-alive), lo que es más eficiente que crear una nueva conexión por cada petición. `Retry` de urllib3 (la librería HTTP que está debajo de requests) gestiona reintentos a nivel de transporte: si el servidor devuelve 429 (demasiadas peticiones), 503 (servicio no disponible), etc., reintenta automáticamente. `backoff_factor=0.5` significa que el tiempo de espera sigue la fórmula `{backoff} * (2 ^ (intento-1))`: 0.5s, 1s entre reintentos. `session.mount("https://", adapter)` aplica esta configuración a todas las URLs que empiecen por `https://`.

### _descargar_feed — las 3 estrategias

```python
@retry(
    stop=stop_after_attempt(config.MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((requests.RequestException, PCPSFeedError)),
    before_sleep=before_sleep_log(logger, 30),
    reraise=True,
)
def _descargar_feed(self):
```
El decorador `@retry` de tenacity envuelve la función entera. `stop_after_attempt(3)` significa que si la función lanza una excepción, tenacity la ejecuta hasta 3 veces antes de rendirse. `wait_exponential(multiplier=1, min=2, max=30)` calcula el tiempo de espera entre intentos: 2s, 4s, 8s... con un máximo de 30s. `retry_if_exception_type(...)` especifica qué tipos de excepción activan un reintento (no todos: un `ValueError` de parsing no debería reintentar). `before_sleep_log` escribe el warning que ves en el log: `Retrying ... in 2.0 seconds`. `reraise=True` relanza la última excepción después de agotar los intentos, en lugar de devolver `None`.

**Estrategia 1 — feedparser nativo:**
```python
feed = feedparser.parse(
    self.feed_url,
    agent=config.HTTP_HEADERS["User-Agent"],
    request_headers={"Accept-Language": "es-ES,es;q=0.9"},
)
if feed.entries:
    return feed
```
`feedparser.parse(url)` hace él mismo la petición HTTP Y el parsing. Tiene su propio parser XML tolerante que fue diseñado en 2002 específicamente para manejar feeds mal formados. `feed.bozo = True` indica XML imperfecto pero no impide el parsing. Si hay entradas, devolvemos directamente.

**Estrategia 2 — lxml recover:**
```python
parser = etree.XMLParser(recover=True, resolve_entities=False, no_network=True)
arbol = etree.fromstring(contenido, parser)
return etree.tostring(arbol, encoding="utf-8", xml_declaration=True)
```
Si feedparser no consigue entradas (XML tan roto que ni siquiera el parser tolerante puede), descargamos los bytes crudos con requests y usamos lxml con `recover=True`. Este modo hace lo mismo que los navegadores cuando renderizan HTML roto: intenta reconstruir la estructura más razonable posible. `resolve_entities=False` y `no_network=True` son restricciones de seguridad: evitan que un XML malicioso haga que lxml descargue recursos externos (ataque "XML bomb" o "billion laughs").

**Estrategia 3 — BeautifulSoup:**
Solo si lxml también falla. Trata el contenido como HTML (no XML), extrae las etiquetas `<entry>` y construye un XML mínimo válido para feedparser. Es el último recurso porque puede perder algunos atributos UBL.

### _parsear_entrada — jerarquía de extracción

```python
def _parsear_entrada(self, entry):
    entry_id = entry.get("id") or entry.get("link")
    titulo = limpiar_texto(entry.get("title")) or "Sin título"
    summary_raw = entry.get("summary", "") or ""
    campos_summary = parsear_summary_pcsp(summary_raw)
    presupuesto = parsear_budget_amount(entry.get("cac_budgetamount"))
    if presupuesto is None:
        presupuesto = campos_summary.get("presupuesto_base")
    organo = campos_summary.get("organo_contratacion")
    url_pdf = limpiar_texto(entry.get("cbc_uri"))
    nombre_pdf = limpiar_texto(entry.get("cbc_filename"))
```
Tres fuentes de datos en orden de fiabilidad:
1. **Campos UBL directos** (`cbc_*`, `cac_*`): son los más estructurados. El presupuesto de `cac_budgetamount` tiene prioridad sobre el del summary.
2. **Summary** (texto plano semicolón-separado): de aquí sacamos el órgano de contratación y el estado del expediente.
3. **Campos ATOM estándar** (`title`, `link`, `updated`): siempre presentes, base mínima garantizada.

El patrón `entry.get("campo") or entry.get("campo_alternativo")` devuelve el primer valor que sea truthy. En Python, `None or "otro"` devuelve `"otro"`. `"" or "otro"` también devuelve `"otro"`. Esto gestiona el caso en que el campo existe pero está vacío.

### iterar_licitaciones — el generador

```python
def iterar_licitaciones(self, limite=20) -> Iterator[LicitacionSchema]:
    feed = self._descargar_feed()
    entradas = feed.entries[:limite] if limite else feed.entries
    for i, entry in enumerate(entradas, start=1):
        schema = self._parsear_entrada(entry)
        if schema:
            yield schema
        if i < len(entradas) and self.delay > 0:
            time.sleep(self.delay)
```
`yield` es lo que convierte esta función en un generador. La diferencia con `return` es fundamental: cuando Python ejecuta `yield schema`, pausa la función, devuelve el valor al llamante (main.py), y espera. Cuando main.py pide el siguiente valor, la función se reanuda exactamente donde se paró.

El resultado práctico: main.py empieza a persistir licitaciones en la BD mientras el generador sigue parseando. Con una lista normal, parsearías todas las entradas a memoria y luego insertarías. Con un generador de 500 entradas, la primera licitación está en SQLite antes de haber parseado la segunda.

`time.sleep(self.delay)` entre entradas es rate limiting voluntario: esperamos 1 segundo entre licitaciones. La PCSP no tiene un límite documentado de peticiones, pero el download del PDF (Step 2) hace una petición HTTP por cada licitación, y sin espera estaríamos golpeando el servidor con 20 peticiones en 2 segundos. En producción con 500 licitaciones diarias, eso podría disparar un bloqueo de IP.

---

## 8. src/db_manager.py — La capa de base de datos

### Inicialización

```python
self._engine = create_engine(
    database_url,
    echo=False,
    pool_pre_ping=True,
    connect_args=(
        {"check_same_thread": False} if "sqlite" in database_url else {}
    ),
)
```
`create_engine` crea el "motor" de SQLAlchemy: el objeto que sabe cómo hablar con la base de datos específica. `echo=False` desactiva el log de todas las queries SQL (actívalo con `True` para debuggear). `pool_pre_ping=True` envía un `SELECT 1` antes de cada operación para verificar que la conexión sigue viva, importante cuando el proceso corre mucho tiempo. `check_same_thread: False` es necesario para SQLite en entornos con múltiples hilos: por defecto SQLite solo permite acceso desde el hilo que creó la conexión; esta opción relaja esa restricción (es seguro porque nuestro código gestiona manualmente la concurrencia).

```python
Base.metadata.create_all(self._engine)
```
SQLAlchemy inspecciona todas las clases que heredan de `Base` y crea las tablas que no existen todavía. Si las tablas ya existen, no las toca. Esto es lo que hace que el schema de la BD se cree automáticamente en la primera ejecución sin necesidad de ejecutar SQL manualmente.

### El context manager de sesión

```python
@contextmanager
def session(self) -> Generator[Session, None, None]:
    sess = self._SessionFactory()
    try:
        yield sess
        sess.commit()
    except SQLAlchemyError as exc:
        sess.rollback()
        raise
    finally:
        sess.close()
```
Un context manager es lo que permite usar la sintaxis `with db.session() as session:`. El bloque `try/yield/except/finally` garantiza tres propiedades:

**Atomicidad**: si el código dentro del `with` termina sin excepción, el `sess.commit()` escribe todos los cambios en disco de una vez. Si algo falla, el `sess.rollback()` deshace todo lo que se haya hecho en esa sesión. Nunca hay licitaciones a medias en la BD.

**Manejo de errores**: `except SQLAlchemyError` captura errores específicos de BD (no todos los errores Python), hace rollback, loguea, y relanza la excepción. El llamante (main.py) puede decidir qué hacer con ella.

**Liberación de recursos**: el bloque `finally` se ejecuta siempre, incluso si hubo excepción. `sess.close()` devuelve la conexión al pool. Sin esto, cada error en producción dejaría una conexión colgada, y SQLite (que solo permite una escritura simultánea) acabaría bloqueado.

### insertar_licitacion

```python
def insertar_licitacion(self, session, schema):
    if session.get(Licitacion, schema.id):
        return False, "DUPLICADA"

    estado_inicial = (
        config.EstadoLicitacion.PDF_PENDIENTE
        if schema.url_pdf_directo
        else config.EstadoLicitacion.NUEVA
    )

    licitacion = Licitacion(
        id=schema.id,
        titulo=schema.titulo,
        url_pdf_ppt=schema.url_pdf_directo,
        nombre_pdf=schema.nombre_pdf,
        estado_proceso=estado_inicial,
        # ...
    )
    session.add(licitacion)
    return True, "INSERTADA"
```
`session.get(Licitacion, schema.id)` es la forma idiomática de buscar por primary key en SQLAlchemy 2.0. Es equivalente a `SELECT * FROM licitaciones WHERE id = ?`. Si devuelve algo, la licitación ya existe y retornamos `DUPLICADA` sin intentar insertar (idempotencia: ejecutar el mismo run dos veces da el mismo resultado).

La lógica de `estado_inicial` es una pequeña máquina de estados: si el feed ya nos da la URL del PDF, no tiene sentido pasar por `NUEVA` (que no tiene URL) para llegar a `PDF_PENDIENTE`. Entramos directamente en `PDF_PENDIENTE`.

`session.add(licitacion)` registra el objeto en la sesión (en memoria). El `INSERT` real a SQLite no ocurre hasta el `sess.commit()` en el context manager. Esto permite acumular múltiples objetos en una sesión y confirmarlos todos a la vez (aunque en nuestro caso usamos micro-transacciones: una sesión por licitación).

### obtener_pendientes_pdf

```python
def obtener_pendientes_pdf(self, session, limite=50):
    return list(
        session.scalars(
            select(Licitacion)
            .where(
                Licitacion.estado_proceso == config.EstadoLicitacion.PDF_PENDIENTE,
                Licitacion.url_pdf_ppt.is_not(None),
            )
            .order_by(Licitacion.fecha_publicacion.desc())
            .limit(limite)
        ).all()
    )
```
SQLAlchemy Core SQL construido en Python. `select(Licitacion)` es `SELECT * FROM licitaciones`. `.where(...)` añade condiciones. `.order_by(Licitacion.fecha_publicacion.desc())` ordena de más reciente a más antigua (si una licitación tiene fecha de publicación None, `.desc().nulls_last()` la pondría al final). `.limit(limite)` es el `LIMIT` SQL. `session.scalars(...).all()` ejecuta la query y devuelve una lista de objetos `Licitacion`.

---

## 9. src/downloader.py — El descargador de PDFs

### _crear_sesion_http

```python
session.headers.update({
    **config.HTTP_HEADERS,
    "Accept": "application/pdf,application/octet-stream,*/*;q=0.8",
    "Referer": "https://contrataciondelsectorpublico.gob.es/",
})
```
El `**config.HTTP_HEADERS` desempaqueta el diccionario de cabeceras base y luego añade/sobreescribe con las específicas para descargar PDFs. `Accept: application/pdf` indica al servidor que esperamos un PDF. El `Referer` es crítico: el servidor de la PCSP comprueba que la petición "viene desde" su propio portal. Sin Referer, puede devolver una página de error HTML en lugar del PDF.

### _es_pdf_valido — magic bytes

```python
_PDF_MAGIC = b"%PDF"

def _es_pdf_valido(self, contenido: bytes) -> bool:
    return contenido[:4] == _PDF_MAGIC
```
Todos los ficheros PDF comienzan con los bytes `25 50 44 46` en hexadecimal, que en ASCII son `%PDF`. Esto se llama "magic bytes" o "file signature": una convención del sistema de ficheros para identificar el tipo real del contenido, independientemente de la extensión. Sin esta verificación, si el servidor devuelve una página HTML de error (que empieza con `<!DOCTYPE html>`), la guardaríamos con extensión `.pdf` y el Step 3 intentaría parsearla como PDF y fallaría de forma confusa.

### descargar — stream y verificación

```python
resp = self._http.get(url, timeout=self.timeout, stream=True)
contenido = b""
for chunk in resp.iter_content(chunk_size=8192):
    if chunk:
        contenido += chunk
```
`stream=True` en requests indica que no descargue el cuerpo completo inmediatamente, sino que lo deje disponible como un stream. `iter_content(chunk_size=8192)` lee el cuerpo en trozos de 8 KB. Aunque al final acumulamos todo en `contenido`, el uso de stream es buena práctica para PDFs: si el servidor tarda en responder, el timeout se aplica por chunk en lugar de al tiempo total de descarga.

El flujo descarga en memoria primero (`contenido += chunk`) para poder verificar los magic bytes antes de escribir en disco. Si escribiéramos directamente al fichero y luego descubriéramos que no es un PDF, tendríamos un fichero corrupto a medias. Así, si la verificación falla, simplemente no creamos el fichero.

```python
ruta_destino.write_bytes(contenido)
```
`pathlib.Path.write_bytes()` es atómico a nivel de Python: o escribe todo el contenido o falla. Es más seguro que `open(path, "wb")` seguido de múltiples `write()`.

### descargar_lote

```python
for i, licit in enumerate(licitaciones, start=1):
    ruta = self.descargar(url=licit.url_pdf_ppt, ...)
    if ruta:
        with db.session() as session:
            db.marcar_pdf_descargado(session, licit.id, str(ruta))
        stats["descargados"] += 1
    else:
        with db.session() as session:
            db.actualizar_estado(session, licit.id, config.EstadoLicitacion.ERROR)
        stats["errores"] += 1
    if i < len(licitaciones):
        time.sleep(self.delay)
```
Micro-transacción por PDF: cada resultado (éxito o error) se persiste inmediatamente en la BD. Si el proceso se interrumpe después del PDF número 7, los 7 primeros están marcados como `PDF_DESCARGADO` en la BD y en la próxima ejecución `obtener_pendientes_pdf()` solo devuelve los restantes. El sistema es resiliente a interrupciones.

`time.sleep(self.delay)` — 1.5 segundos entre descargas. El servidor de la PCSP sirve documentos legales y tiene limitaciones de ancho de banda. En producción con 500 licitaciones, 1.5s × 500 = 12.5 minutos para descargar todos los PDFs. Es razonable para un proceso que corre una vez al día de madrugada.

---

## 10. main.py — El orquestador

### parse_args

```python
parser.add_argument("--feed", choices=list(config.PCSP_FEEDS.keys()), default=config.ACTIVE_FEED)
parser.add_argument("--limite", type=int, default=config.MAX_LICITACIONES_POR_RUN)
parser.add_argument("--dry-run", action="store_true")
parser.add_argument("--stats", action="store_true")
parser.add_argument("--ping", action="store_true")
parser.add_argument("--solo-ingesta", action="store_true")
parser.add_argument("--solo-pdfs", action="store_true")
```
`argparse` construye la interfaz de línea de comandos. `choices=list(config.PCSP_FEEDS.keys())` hace que `--feed` solo acepte valores que existen como claves del diccionario: si pasas `--feed inexistente`, argparse lanza un error antes de que el código llegue a ejecutarse. `action="store_true"` significa que el flag no toma un valor: `--dry-run` pone `args.dry_run = True`, sin `--dry-run` queda `False`.

### ejecutar_ingesta

```python
def ejecutar_ingesta(feed_key, limite, dry_run, db):
    scraper = AtomScraper(feed_url=config.PCSP_FEEDS[feed_key])
    with db.session() as session:
        log = db.iniciar_log_ejecucion(session, feed_url=feed_url)
        log_id = log.id
    for schema in scraper.iterar_licitaciones(limite=limite):
        if dry_run:
            logger.info("[DRY-RUN] %s | %s€", schema.titulo[:60], schema.presupuesto_base)
            continue
        with db.session() as session:
            _, resultado = db.insertar_licitacion(session, schema)
```
El `with db.session() as session:` fuera del bucle es para el log de ejecución. El `with db.session() as session:` dentro del bucle es para cada licitación. Esto implementa las micro-transacciones: una licitación que falla no afecta a las demás.

El modo `--dry-run` es especialmente valioso en producción: después de un cambio de código, puedes verificar que el sistema parsea correctamente (ves el output en consola) sin tocar la BD. Si hay un bug en el parser, los datos existentes quedan intactos.

### La lógica de qué steps ejecutar

```python
ejecutar_step1 = not args.solo_pdfs
ejecutar_step2 = not args.solo_ingesta

if ejecutar_step1:
    res_ingesta = ejecutar_ingesta(...)

if ejecutar_step2:
    res_pdfs = ejecutar_descarga_pdfs(...)
```
Tabla de combinaciones:
- Sin flags → `ejecutar_step1=True, ejecutar_step2=True` → pipeline completo
- `--solo-ingesta` → `ejecutar_step2=False` → solo ingestar, no descargar PDFs
- `--solo-pdfs` → `ejecutar_step1=False` → solo descargar PDFs pendientes
- `--solo-ingesta --solo-pdfs` → ambos False → no hace nada (caso degenerado)

---

## 11. Errores conocidos y fixes pendientes

### Bug en config.py — URLs antiguas

La versión actual de `config.py` en el repo todavía tiene las URLs del dominio antiguo:
```python
# INCORRECTO (dominio antiguo, da Forbidden)
"https://contrataciondelestado.es/sindicacion/sindicacion_1044/..."
"https://contrataciondelestado.es/sindicacion/sindicacion_1045/..."

# CORRECTO (dominio actual, verificado)
"https://contrataciondelsectorpublico.gob.es/sindicacion/sindicacion_643/
licitacionesPerfilesContratanteCompleto3.atom"
```
Reemplaza manualmente o añade al fichero `.env`:
```
PCSP_FEED_COMPLETO=https://contrataciondelsectorpublico.gob.es/sindicacion/sindicacion_643/licitacionesPerfilesContratanteCompleto3.atom
```

### Errores en descarga de PDFs — pendiente diagnóstico

Hay errores en la descarga de PDFs que aún no hemos visto. Los errores más probables dado lo que sabemos del servidor son:

1. **HTTP 403 Forbidden** — el servidor rechaza la petición aunque tengamos Referer correcto. Posible solución: añadir más cabeceras HTTP que imiten un navegador real.
2. **Contenido no es PDF** — el servidor devuelve una página de autenticación o redirección. La verificación de magic bytes lo detectará y logueará el inicio del HTML recibido.
3. **Timeout** — algunos PDFs son grandes (>5 MB) y el timeout de 30s no es suficiente. Solución: aumentar `REQUEST_TIMEOUT` en config.py o en `.env`.
4. **SSL Error** — el dominio `contrataciondelestado.es` de los IDs de las licitaciones tiene certificados distintos al dominio nuevo donde están los PDFs.

Pega el log de los errores de descarga para diagnosticar cuál es el caso.

---

## Resumen visual de dependencias

```
config.py
    │
    ├── logger.py
    │       │
    │       └── (todos los módulos importan get_logger)
    │
    ├── models.py (solo Python estándar + SQLAlchemy)
    │
    ├── utils.py
    │       │
    │       └── importa logger
    │
    ├── scraper_atom.py
    │       │
    │       ├── importa config, logger, models, utils
    │       └── produce LicitacionSchema[]
    │
    ├── db_manager.py
    │       │
    │       ├── importa config, logger, models
    │       └── consume LicitacionSchema, produce/lee Licitacion (ORM)
    │
    ├── downloader.py
    │       │
    │       ├── importa config, logger, utils
    │       └── consume Licitacion (ORM), escribe PDFs en disco
    │
    └── main.py
            │
            └── importa todo, no contiene lógica de negocio
```
