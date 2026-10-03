# biblio-demon

Python managed bibliography with metadata-centered organization. Syncronizing local files and Zotero.

## Inicio rápido

```bash
make setup     # entorno + dependencias (uv)
make test      # tests con cobertura
make format    # ruff: formato + orden de imports
make diagrams  # UML con pyreverse (necesita graphviz)
make help      # lista todos los comandos
```

## Estructura

```
src/biblio_demon/   código del paquete
tests/unit/              tests unitarios
tests/integration/       tests de integración
logs/                    logs en runtime (no se versiona)
docs/diagrams/           diagramas generados
```

## Logging

Cada módulo usa `logger = logging.getLogger(__name__)`.
`setup_logging()` se llama **una sola vez**, en el punto de entrada.
