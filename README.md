# Sales assignment: normalización y extracción de notas

El proyecto conserva los CSV fuente, genera artefactos reproducibles en `normalized/` y permite cargar PostgreSQL mediante un reinicio local controlado. El motor ofrece tres métodos de asignación mediante FastAPI; la interfaz Streamlit incluye Dashboard, Registros, Vendedores, Asignación, Auditoría, Carga y simulación y Prueba histórica. PostgreSQL es accesible por los componentes únicamente mediante la API de base de datos.

> Uso exclusivo de evaluación. Consulta [LICENSE](LICENSE): no se autoriza uso productivo, comercial, redistribución ni modificaciones sin permiso escrito.

## Instalación, ejecución e inicialización

Requisitos: Docker Engine con Docker Compose v2. Para trabajar con notebooks o ejecutar pruebas fuera de contenedores, también necesitas Python 3.11 y Conda o `venv`.

Primero crea la configuración local. No subas `.env` al repositorio: contiene las claves de la base de datos.

```bash
cp .env.example .env
```

Arranca la aplicación completa desde la raíz. Docker descarga PostgreSQL y Ollama cuando no existen y construye los servicios Python.

```bash
docker compose --env-file .env -f project/docker-compose.yml up -d --build --pull always --wait
```

En el primer arranque, `database_api` detecta una base vacía, crea el esquema e importa automáticamente los archivos de `normalized/`. La API inicia la preparación de `OLLAMA_MODEL`; si ese modelo no existe en Ollama, descarga `OLLAMA_DEFAULT_MODEL`. El panel puede mostrar “preparando modelo” mientras la descarga termina.

Abre los servicios locales:

- Interfaz: http://localhost:8501
- API de asignación: http://localhost:8000/docs
- API de base de datos: http://localhost:8001/docs

Para restaurar los datos normalizados en cualquier momento, usa **Administración de datos → Inicializar y reiniciar base de datos** en la barra lateral. Esta acción elimina las asignaciones, propuestas y cambios operativos actuales antes de importar los datos de ejemplo. También puedes hacerlo desde terminal:

```bash
python scripts/reset_database.py --confirm-reset
```

Para detener la aplicación sin eliminar la información persistida:

```bash
docker compose --env-file .env -f project/docker-compose.yml down
```

Para eliminar también la base y los modelos descargados, usa `down -v`; el siguiente arranque volverá a inicializar la base y descargar el modelo.

### Autoarranque opcional de Ollama desde la API

Compose es el mecanismo recomendado para crear los contenedores. Si un despliegue local necesita que la API detecte la ausencia del contenedor de Ollama, descargue la imagen y lo inicie, usa el archivo adicional:

```bash
docker compose --env-file .env \
  -f project/docker-compose.yml \
  -f project/docker-compose.bootstrap.yml up -d --build --pull always --wait
```

Este modo comparte `/var/run/docker.sock` con la API y debe activarse solo en hosts donde esa capacidad esté permitida. El estado aparece en `GET /services`.

## Ejecutar desde el notebook (flujo principal)

Desde la raíz del repositorio, crea y activa el entorno Conda:

```bash
conda env create -f environment.yml
conda activate sales-assignment
python -m ipykernel install --user --name sales-assignment --display-name "Python (sales-assignment)"
jupyter lab data/normalization_workflow.ipynb
```

Selecciona el kernel `Python (sales-assignment)`. Si ya tienes un entorno Python, instala las mismas dependencias con `python -m pip install -r requirements.txt`. Docker y Ollama se administran por separado mediante Docker Compose.

Para actualizar un entorno Conda existente:

```bash
conda env update -f environment.yml
```

`requirements.txt` reúne las dependencias del notebook, normalización, Streamlit, FastAPI, PostgreSQL, motor de asignación, servicio LLM y pruebas.

Estas dependencias preparan el desarrollo de los componentes descritos en la arquitectura; no implementan ni arrancan los servicios. PostgreSQL y Ollama son servicios externos. Docker Engine/Compose, `kubectl` y un clúster Kubernetes se instalan por separado; no son paquetes Python. La API de base de datos usa Psycopg y un pool de conexiones. Los rangos de dependencias permiten actualizaciones compatibles; no constituyen un lockfile.

Abre [`normalization_workflow.ipynb`](data/normalization_workflow.ipynb) en VS Code o Jupyter. Instala `requirements.txt` en el entorno del kernel y ejecuta sus celdas en orden. Puedes revisar las entradas, modificar la fecha efectiva y generar todos los datasets en `normalized/` desde allí. No hace falta que el agente vuelva a ejecutar el pipeline.

Las llamadas al LLM están desactivadas por defecto. El notebook no inicia Docker ni descarga modelos automáticamente. Cambia las opciones de evaluación cuando tú quieras. Las respuestas parciales de pruebas anteriores se conservan; no hay un modelo aceptado ni una evaluación completa finalizada.

## Reproducir desde terminal (alternativa)

Python 3.10+ y Docker Compose:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python3 scripts/normalize_data.py
python3 scripts/build_note_dataset.py
python3 -m unittest discover -s tests -v

docker compose --env-file .env -f project/docker-compose.yml up -d llm_service
docker compose --env-file .env -f project/docker-compose.yml exec -T llm_service ollama pull llama3.2
export LLM_API_URL=http://localhost:11434
export LLM_MODEL=llama3.2
export LLM_TIMEOUT_SECONDS=600
python3 scripts/evaluate_note_prompt.py
python3 scripts/evaluate_note_prompt.py --examples normalized/note_holdout.jsonl --output normalized/note_extraction_results_holdout.csv
python3 scripts/evaluate_note_prompt.py --examples normalized/note_security_evaluation.jsonl --output normalized/note_extraction_results_security.csv
python3 scripts/evaluate_note_prompt.py --examples normalized/note_context_evaluation.jsonl --output normalized/note_extraction_results_context.csv
```

`LLM_API_URL` es la URL base de Ollama. Las solicitudes usan `/api/chat`, mensajes separados system/user y el esquema JSON como `format`. Las respuestas esperadas nunca se envían al modelo. No se necesitan credenciales. La imagen de Ollama es configurable con `OLLAMA_IMAGE`; para reproducir un despliegue exacto usar un digest. El modelo y sus opciones se registran con los resultados.

## Auditoría y contratos

- `data/*.csv`: entradas originales, nunca modificadas. `--source` permite otra carpeta con los mismos cinco archivos. El conjunto suministrado ya está extraído; no requiere ZIP.
- `normalized/*csv`: se retienen todas las filas y columnas originales. Cada campo tiene valor original, valor normalizado, regla y bandera de inferencia. CSV representa null mediante celda vacía; JSON usa null.
- `--effective-date 2026-09-18`: fecha predeterminada, ausencia inclusiva, fin vacío abierto. Fechas futuras permanecen en los datos.
- `--config path.json`: configuración opcional `{"city_to_zone":{"bogotá":"Centro"}}`; las claves son ciudades en minúscula. Sin configuración no se infieren zonas por ciudad.
- Capacidad nula: regla de negocio pendiente. Capacidad cero: ninguna nueva asignación. Números inválidos o negativos se reportan y se conserva el original.
- `historical_ownership.csv`: un usuario distinto en actividad permite inferir propietario; múltiples usuarios o ID de registro duplicado impiden atribución. No prueba propiedad actual ni asignación óptima.
- `seller_workload.csv`: cuenta registros asignados/en gestión por propietario inferido, nunca cantidad de actividades. Incluye líderes y vendedores porque ambos aparecen como usuarios comerciales. Inactividad, ausencias, capacidad y datos faltantes se exponen como motivos; no se ejecuta elegibilidad ni asignación.
- `normalization_report.json`: hashes, conteos reales/esperados, incidencias y registros activos no atribuibles. Los resultados no se fuerzan a los valores esperados.
- `note_examples.csv`: 167 filas, incluidos los 27 vacíos. `note_examples.jsonl`, `note_training.jsonl` y `note_evaluation.jsonl`: 15 notas únicas, sin inflar el dataset mediante duplicación. Variantes por sector se registran y la falta de sector tiene prueba separada.
- `normalization/reference_data/` conserva las referencias fuente aprobadas y su manifest. El notebook puede generar `normalized/` desde cero. `normalized/note_golden_review_v1.jsonl` y la aprobación se restauran desde esas referencias solo si faltan; nunca se reemplazan las revisiones existentes. El usuario aprobó el prompt y etiquetas el 2026-09-18; ver `prompt_review_status.json`.
- `note_holdout.jsonl`: 15 paráfrasis sintéticas, revisadas por el agente contra las interpretaciones aprobadas; no se atribuye revisión humana independiente. No se incorporan al prompt ni al entrenamiento. Inyección y contexto se evalúan por separado.
- `project/llm_service/promps/note_extraction_v1.md`: prompt aprobado, preservado. Cambios posteriores se guardan en versiones nuevas. `schemas/` contiene el contrato completo.
- Cada evaluación conserva un directorio único en `normalized/evaluation_runs/` con prompt, ejemplos, esquema, CSV y métricas. Las rutas de resultados de nivel superior son la ejecución más reciente. Una evaluación interrumpida conserva sus respuestas parciales, sin resumen de aceptación.
- Precisión por campo compara las listas como conjuntos ordenados, pero valida duplicados mediante JSON Schema. Ausentes/extra cuentan como discrepancias. Acciones se comparan en ambos campos espejo. Se reportan afirmaciones no respaldadas respecto a la referencia, además de errores y omisiones.
- Cero hechos inventados significa cero afirmaciones discrepantes detectadas contra la referencia cerrada; no constituye garantía universal. Las métricas originales son desempeño de desarrollo, no generalización. Holdout sintético e inyección tampoco constituyen una auditoría completa de seguridad.

La aprobación del prompt no equivale a aprobar el modelo. Consultar los resúmenes de evaluación antes de usarlo.


## PostgreSQL: reinicio e importación local

La primera ejecución no requiere un comando de importación adicional: `database_api` inicializa una base nueva desde `normalized/`. Para un reinicio manual, ejecuta desde la raíz:

```bash
docker compose --env-file .env -f project/docker-compose.yml up -d --build --wait database_api api
python scripts/reset_database.py --confirm-reset
```

El destino HTTP local es `http://127.0.0.1:8001`; PostgreSQL no publica ningún puerto. La base es `sales_assignment_local`, esquema `sales_assignment`. El script envía la solicitud de reinicio a la API con el token administrativo. Las credenciales están únicamente en `.env`, ignorado por Git. El reinicio es intencionalmente destructivo dentro de ese esquema; las validaciones rechazan entornos no locales/de pruebas y esquemas ajenos. Un fallo revierte también el reemplazo del esquema anterior.

Consulta [configuración, tablas, verificación, pruebas y recuperación](project/database/README.md). El resultado queda en [`normalized/database_import_report.json`](normalized/database_import_report.json).


## API de asignación

Configura los dos tokens distintos de `.env.example` y arranca los servicios con el comando anterior. Documentación interactiva: [asignación](http://localhost:8000/docs) y [base de datos](http://localhost:8001/docs).

Los métodos `capacity_aware`, `fuzzy_optimal` y `ai_assisted` generan previews persistidos. Ejecutar un preview requiere `{"approved":true}`; se revalidan el snapshot y las restricciones antes de una transacción atómica. La generación de un preview no asigna registros.

Consulta [modelos, endpoints y limitaciones](project/assignment_engine/README.md). Toda dependencia Python se mantiene en `requirements.txt`; las imágenes seleccionan sus paquetes desde ese mismo archivo.


## Interfaz Streamlit

Ejecuta `docker compose --env-file .env -f project/docker-compose.yml up -d --build --wait frontend` y abre http://localhost:8501. Consulta [configuración y flujo de aprobación](project/frontend/README.md). La interfaz se comunica exclusivamente con la API de negocio.
