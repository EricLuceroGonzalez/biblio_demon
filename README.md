# biblio-demon

Demonio local que vigila una carpeta de entrada y, por cada PDF científico que llega:

1. **Extrae el DOI** (o el identificador arXiv) de los metadatos embebidos, de los enlaces y del texto de las 3 primeras páginas.
2. **Resuelve los metadatos canónicos** vía API: Crossref → DataCite → Semantic Scholar. No se infiere nada del cuerpo del PDF.
3. **Verifica** que el título devuelto por la API aparezca en la portada. Así se evita catalogar un DOI de la bibliografía.
4. **Renombra y archiva** el PDF: `(2016)-Deep_Residual_Learning_for_Image_Recognition-(He-EtAl).pdf`.
5. **Sincroniza** con Zotero (el PDF queda como *linked file*, sin consumir cuota) y con Notion.

Los PDFs sin DOI, ilegibles o que no se pueden resolver van a `INBOX/manual_review/`, cada uno con un `.txt` que explica el motivo. Las copias idénticas de un PDF ya archivado van a `INBOX/duplicates/`.

El diagrama completo del flujo está en [`docs/diagrams/flujo.pdf`](docs/diagrams/flujo.pdf) (fuente TikZ: `flujo.tex`).

## Inicio rápido

```bash
make setup                 # entorno + dependencias (uv)
cp .env.example .env       # y rellena rutas y credenciales
uv run biblio-demon check-config     # valida rutas, API key de Zotero y esquema de Notion
uv run biblio-demon process ~/Downloads/paper.pdf --dry-run   # simula sin tocar nada
uv run biblio-demon run              # modo demonio en primer plano (Ctrl+C para salir)
```

Para el uso diario no hace falta ejecutar nada a mano: instálalo como servicio (ver [Servicio en segundo plano](#servicio-en-segundo-plano)) y basta con dejar PDFs en la bandeja.

| Comando | Qué hace |
|---|---|
| `run [--dry-run]` | Vigila `INBOX_PATH`. Al arrancar procesa los PDFs que ya estén ahí y reintenta las sincronizaciones pendientes. |
| `process PDF... [--dry-run]` | Procesa archivos concretos y termina. Devuelve exit 1 si alguno va a revisión manual o queda con sincronización pendiente. |
| `retry` | Reintenta las sincronizaciones con Zotero o Notion que fallaron. |
| `check-config` | Valida la configuración, las credenciales y el esquema de Notion. |

`--dry-run` resuelve metadatos (solo lecturas) y muestra el nombre y los payloads que se usarían, sin mover archivos ni escribir en Zotero o Notion.

## Configuración

Todas las variables están documentadas en [`.env.example`](.env.example). Las obligatorias son `INBOX_PATH`, `ARCHIVE_PATH` y `CROSSREF_EMAIL`. Zotero y Notion son opcionales: si faltan sus credenciales, esa etapa se omite.

### Formato del `.env`

El `.env` lo lee `python-dotenv`, no una shell:

| Escritura | Resultado |
|---|---|
| `INBOX_PATH=~/Documents/Papers/_Inbox` | ✅ |
| `INBOX_PATH=${HOME}/Documents/Papers/_Inbox` | ✅ |
| `INBOX_PATH="$HOME"/Documents/...` | ❌ línea descartada (comillas seguidas de texto) |
| `INBOX_PATH={$HOME}/Documents/...` | ❌ rechazada: no es una ruta absoluta |
| `STATE_DB_PATH="~/Library/Application Support/biblio-demon/state.sqlite3"` | ✅ comillas en todo el valor si hay espacios |

`INBOX_PATH`, `ARCHIVE_PATH` y `STATE_DB_PATH` deben ser rutas absolutas; si no, `check-config` falla con un mensaje claro en vez de crear carpetas en lugares inesperados. Protege el archivo: `chmod 600 .env`.

### Metadatos que se guardan

| Dato | Fuente | Zotero | Notion |
|---|---|---|---|
| Título, autores, año, DOI | Crossref / DataCite / S2 | campos del ítem | `Name`, `Autores`, `Año`, `DOI` |
| Revista / congreso | `container-title`, `venue` | `publicationTitle` / `proceedingsTitle` | `Revista` (Select) |
| Volumen, número, páginas | Crossref, DataCite, S2 | `volume`, `issue`, `pages` | — |
| Áreas temáticas | Crossref `subject`, DataCite; si faltan, Semantic Scholar `fieldsOfStudy` | **tags** | `Áreas` (Multi-select) |
| Citas | Crossref `is-referenced-by-count` (o S2) | — | `Citas` (Number) |
| Abstract | API | `abstractNote` | — |

Crossref ya casi no publica áreas temáticas, así que, si la fuente principal no las da, se hace **una** consulta extra a Semantic Scholar. Es opcional: si falla, el artículo se archiva igual, sin áreas. Las citas son las del día en que se procesa el PDF; no se actualizan solas.

### Zotero

- Crea una API key con permiso de escritura en <https://www.zotero.org/settings/keys/new>. En esa página también aparece tu `userID` (un número, no tu nombre de usuario).
- El PDF se adjunta como **archivo enlazado** con su ruta absoluta en `ARCHIVE_PATH`. No consume cuota, pero:
  - en otro Mac solo se abre si la ruta es idéntica (mismo usuario y misma carpeta de iCloud);
  - **Zotero para iPad/iPhone no abre archivos enlazados**: ahí abre el PDF desde la app Archivos (`_Ready`).
- Opcional: con `ZOTERO_COLLECTION_KEY` (8 caracteres, aparece en la URL de la colección) los elementos se crean dentro de esa colección.
- Con `ZOTERO_ATTACH_PDF=false` Zotero recibe solo los metadatos (título, autores, revista, volumen, número, páginas, DOI, abstract y tags), sin adjunto. Es lo recomendable si mueves los PDFs a mano fuera de `ARCHIVE_PATH`.
- Con `ZOTERO_ATTACH_PDF=true` (por defecto) el PDF se adjunta como **archivo enlazado** con su ruta absoluta en `ARCHIVE_PATH`. No consume cuota, pero el enlace se rompe si mueves o renombras el archivo, y Zotero para iPad/iPhone no abre archivos enlazados.
  
### Notion

La base de datos necesita estas propiedades. Los nombres se pueden cambiar con `NOTION_PROP_*`; las tres últimas son opcionales (deja la variable vacía para desactivarlas).

| Propiedad | Tipo | Valor |
|---|---|---|
| `Name` | Title | `Título (Apellido, Año)` |
| `DOI` | URL | `https://doi.org/...` |
| `Año` | Number | año |
| `Estado` | Status | `Inbox` (la opción debe existir) |
| `Autores` | Text | `Nombre Apellido, ...` |
| `Revista` | Select | nombre de la revista o congreso |
| `Citas` | Number | número de citas al procesar |
| `Áreas` | Multi-select | áreas temáticas |

Las opciones de `Revista` y `Áreas` se crean solas la primera vez que aparecen.

1. Crea una integración interna en <https://www.notion.so/profile/integrations>.
2. Abre la base de datos **como página completa** → `•••` → *Conexiones* → añade la integración.
3. `NOTION_DATABASE_ID` es la parte de la URL **antes** de `?v=` (lo que va después es el ID de la vista). Las *linked databases* no sirven: usa la base original.

El cliente usa la API `2025-09-03`: el `NOTION_DATABASE_ID` se traduce automáticamente a su *data source*. Si `check-config` dice `Could not find database`, casi siempre falta el paso 2.

## Uso con iCloud Drive

Los papers pueden vivir en iCloud (para verlos en otros dispositivos); el **código y los archivos internos no**:

| Qué | Dónde | Por qué |
|---|---|---|
| `_Inbox`, `_Ready` | iCloud (`~/Documents/...`) | Los quieres en todos tus dispositivos |
| Repo + `.venv` | `~/Developer/biblio_demon` | launchd no puede ejecutar programas dentro de `~/Documents` (error 78), y el código ya está en git |
| Base de estado | `~/Library/Application Support/biblio-demon/` | iCloud puede corromper una SQLite mientras se escribe |
| Logs | `~/Library/Logs/biblio-demon/` | Se ven en la app Consola; no ensucian iCloud |

En el `.env`:

```bash
INBOX_PATH=~/Documents/Academic/00-Papers/_Inbox
ARCHIVE_PATH=~/Documents/Academic/00-Papers/_Ready
STATE_DB_PATH="~/Library/Application Support/biblio-demon/state.sqlite3"
LOG_DIR=~/Library/Logs/biblio-demon
```

En Finder, clic derecho sobre `_Inbox` y `_Ready` → **"Mantener descargado"**, para que iCloud no convierta los PDFs en archivos solo-nube. Con esto, un PDF guardado en `_Inbox` desde el iPad (app Archivos) se procesa en cuanto se sincroniza con el Mac.

## Servicio en segundo plano

### Cómo funciona

- El servicio es **permanente**: se instala una vez y arranca solo cada vez que inicias sesión en el Mac. No hay que ejecutar nada a diario.
- Mientras corre, el sistema operativo le avisa cuando aparece un PDF en `_Inbox`; no recorre la carpeta en bucle, así que casi no consume CPU ni batería.
- Si falla, launchd lo reinicia (mínimo cada 30 s). Si lo detienes tú, no.
- Si el Mac está apagado o dormido, los PDFs que lleguen a `_Inbox` (p. ej. desde iCloud) se procesan al volver: al arrancar revisa la bandeja y reintenta las sincronizaciones pendientes.
- Tras actualizar el código (`git pull`, `uv sync`) o editar el `.env`, hay que **reiniciarlo** (ver tabla de uso diario).

### macOS (launchd)

El demonio se instala como **LaunchAgent**: corre con tu usuario (sin privilegios de administrador), arranca al iniciar sesión y se reinicia solo si falla.

`deploy/com.biblio-demon.plist` es una **plantilla**: contiene `__REPO__` y así debe quedarse en git. El que lee launchd es la copia generada en `~/Library/LaunchAgents/`, con las rutas reales. Si esa copia conserva algún `__REPO__`, launchd falla con `78: EX_CONFIG`.

**1. Prepara el repo fuera de `~/Documents` y comprueba que funciona en primer plano.**

```bash
cd ~/Developer/biblio_demon
uv sync                                # crea .venv/bin/biblio-demon
uv run biblio-demon check-config       # debe terminar sin ✗
```

**2. Da acceso a tus carpetas de papers.** Obligatorio si `INBOX_PATH` o `ARCHIVE_PATH` están en `~/Documents`, `~/Downloads` o `~/Desktop`.

```bash
realpath .venv/bin/python   # p. ej. ~/.local/share/uv/python/cpython-3.11.x-macos-aarch64-none/bin/python3.11
```

En *Ajustes del Sistema → Privacidad y seguridad → Acceso total al disco*, pulsa **+**, con ⌘⇧G pega la ruta que imprimió el comando y actívala. Si cambias la versión de Python o recreas el entorno con otra, repite este paso.

**3. Genera el plist con tus rutas e instálalo.**

```bash
mkdir -p ~/Library/LaunchAgents ~/Library/Logs/biblio-demon "$HOME/Library/Application Support/biblio-demon"
sed -e "s|__REPO__/logs/|$HOME/Library/Logs/biblio-demon/|g" \
    -e "s|__REPO__|$PWD|g" \
    deploy/com.biblio-demon.plist > ~/Library/LaunchAgents/com.biblio-demon.plist

grep -c __REPO__ ~/Library/LaunchAgents/com.biblio-demon.plist   # → 0
plutil -lint ~/Library/LaunchAgents/com.biblio-demon.plist       # → OK
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.biblio-demon.plist
```

macOS mostrará el aviso *"Ítem en segundo plano añadido"*. Es normal: el servicio aparece en *Ajustes → General → Ítems de inicio*.

**4. Verifica.**

```bash
launchctl print gui/$(id -u)/com.biblio-demon | grep -E "state|pid|last exit"
tail -f ~/Library/Logs/biblio-demon/biblio_demon.log    # "Vigilando …/_Inbox"
```

`state = running` con un `pid` significa que está funcionando. Para probarlo, copia un PDF a la bandeja y míralo aparecer en el log y en `_Ready`.

**Uso diario**

| Acción | Comando |
|---|---|
| Ver estado | `launchctl print gui/$(id -u)/com.biblio-demon \| grep -E "state\|pid\|last exit"` |
| Reiniciar (tras editar `.env`, `git pull` o `uv sync`) | `launchctl kickstart -k gui/$(id -u)/com.biblio-demon` |
| Detener hasta el próximo inicio de sesión | `launchctl bootout gui/$(id -u)/com.biblio-demon` |
| Volver a arrancarlo | `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.biblio-demon.plist` |
| Desinstalar | `bootout` y luego `rm ~/Library/LaunchAgents/com.biblio-demon.plist` |
| Logs de la app | `tail -f ~/Library/Logs/biblio-demon/biblio_demon.log` |
| Errores de arranque | `cat ~/Library/Logs/biblio-demon/launchd.err.log` |
| Mensajes de launchd | `log show --last 1h --style compact --predicate 'process == "launchd"' \| grep -i biblio \| grep -v "penalty box"` |

Si modificas el `.plist`, haz `bootout` y después `bootstrap` (`kickstart` no relee el archivo). Si mueves el repo de carpeta, repite los pasos 1–3.

**Problemas frecuentes**

| Síntoma | Causa | Solución |
|---|---|---|
| `last exit code = 78: EX_CONFIG`, sin `launchd.err.log` | launchd no pudo lanzar el programa: quedó `__REPO__` en el plist instalado, la ruta del ejecutable no existe o el repo está en `~/Documents` | `grep __REPO__` en el plist instalado; `ls -l <ruta>/.venv/bin/biblio-demon`; repo fuera de `Documents` |
| `cannot spawn: service is in penalty box` | launchd pausa los reintentos tras varios fallos seguidos; no es la causa real | Corrige la causa y haz `bootout` + `bootstrap` |
| `Bootstrap failed: 5: Input/output error` | El servicio ya estaba cargado | `bootout` y vuelve a hacer `bootstrap` |
| `last exit code = 2` y reinicios cada 30 s | Configuración inválida | `uv run biblio-demon check-config` y revisa `launchd.err.log` |
| `PermissionError` / `Operation not permitted` | Falta *Acceso total al disco*, o se dio a otro binario | Repite el paso 2 con la ruta exacta de `realpath` |
| No pasa nada al dejar un PDF | El servicio no está `running`, o vigila otra carpeta | `launchctl print …` y busca `Vigilando` en el log |
| `.venv/bin/python` no existe tras `uv sync` | `UV_PROJECT_ENVIRONMENT` apunta a otro nombre (p. ej. `.venv.nosync`) | Quita esa variable de `~/.zshrc`, `unset` y `uv sync` de nuevo |

### Linux (systemd de usuario)

`deploy/biblio-demon.service` es una plantilla con `__REPO__` y `__UV__` (salida de `which uv`). No se usa en macOS.

```bash
mkdir -p ~/.config/systemd/user
sed -e "s|__REPO__|$PWD|g" -e "s|__UV__|$(which uv)|g" deploy/biblio-demon.service \
  > ~/.config/systemd/user/biblio-demon.service
systemctl --user daemon-reload && systemctl --user enable --now biblio-demon
journalctl --user -u biblio-demon -f
```

## Seguridad

- **Privilegios:** corre como tu usuario, nunca como root. No puede hacer nada que tú no puedas hacer.
- **Red:** solo HTTPS hacia Crossref, DataCite, Semantic Scholar, Zotero y Notion.
- **Archivos:** solo mueve PDFs entre `_Inbox`, sus subcarpetas y `_Ready`. Nunca borra ni sobrescribe.
- **PDFs:** solo se leen con PyMuPDF; nunca se ejecuta su contenido. Mantén las dependencias actualizadas (`uv lock --upgrade && uv sync`).
- **Credenciales:** viven solo en `.env` (excluido de git). Los logs no registran URLs de las librerías HTTP, porque algunas incluyen la API key. Si una key se filtra, revócala en Zotero o Notion y crea otra.
- ***Acceso total al disco*:** es el permiso más amplio del sistema y se concede al intérprete de Python de `uv`, no solo a biblio-demon: cualquier script que un servicio en segundo plano ejecute con ese mismo binario también lo tendría. Desde la Terminal no cambia nada. Si algún día quieres evitarlo, mueve `_Inbox` y `_Ready` fuera de `~/Documents`, `~/Desktop` y `~/Downloads`.

## Cómo funciona

```
src/biblio_demon/
├── __main__.py      CLI (run / process / retry / check-config)
├── config.py        pydantic-settings (rutas absolutas, ~ y $VAR expandidos)
├── models.py        PaperMetadata (esquema normalizado)
├── extractor.py     DOI / arXiv desde el PDF (PyMuPDF)
├── metadata.py      Crossref, DataCite, Semantic Scholar + reintentos (tenacity) + enriquecimiento
├── naming.py        nombre de archivo (ASCII, sanitizado, ≤200 bytes)
├── storage.py       hash, mover sin sobrescribir, cuarentena
├── state.py         SQLite: idempotencia y sincronizaciones pendientes
├── pipeline.py      orquestación de un PDF
├── watcher.py       watchdog + estabilización + cola con un worker
├── logging_setup.py logs a archivo y consola (sin URLs de librerías HTTP)
└── sync/            zotero_sync.py, notion_sync.py
```

- **Extracción.** Los candidatos se ordenan así: metadatos XMP/Info, luego página (1 → 3), luego enlace sobre texto, luego frecuencia. Se prueban hasta `MAX_DOI_CANDIDATES` y se acepta el primero cuyo título aparezca en la portada (similitud ≥ `TITLE_MATCH_THRESHOLD`). Si la portada casi no tiene texto (PDF escaneado), la verificación se omite y queda un aviso en el log.
- **Reintentos.** Solo se reintentan los errores 429, 5xx, timeouts y fallos de conexión, con backoff exponencial que respeta `Retry-After`. Un 404 pasa directamente a la siguiente fuente.
- **Idempotencia.** Antes de crear algo, se comprueba si el DOI ya existe en Zotero y en Notion. Si una sincronización falla, el PDF queda archivado y `retry` (o el próximo arranque) completa solo lo que falta.
- **Duplicados.** Mismo hash que un archivo ya archivado → `duplicates/`. Mismo DOI pero otro archivo (otra versión) → se archiva con el sufijo `-<hash8>` y el registro original no se toca.

### Limitaciones conocidas

- Si las APIs no están accesibles (por ejemplo, sin conexión), el PDF va a `manual_review/` después de los reintentos. Para reprocesarlo, muévelo de nuevo a la bandeja.
- Semantic Scholar devuelve los autores como nombre completo. Su apellido se toma del último token, así que "de la Cruz" queda como "Cruz". Crossref y DataCite sí separan nombre y apellido.
- El número de citas no se actualiza después de procesar el PDF.
- Sin DOI ni arXiv no hay catalogación automática. Opciones futuras: ISBN (Open Library), OCR (`ocrmypdf`) para PDFs escaneados y GROBID, este último solo como sugerencia para revisión manual.

## Desarrollo

```bash
make test      # tests con cobertura (HTTP y APIs simuladas; no requiere red)
make check     # lint + tests
make format
make diagrams  # UML con pyreverse; el diagrama de flujo: pdflatex docs/diagrams/flujo.tex
```

Cada módulo usa `logger = logging.getLogger(__name__)`. `setup_logging()` se llama una sola vez, en el punto de entrada.