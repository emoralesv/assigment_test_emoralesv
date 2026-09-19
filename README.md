# Sistema de asignación comercial

> Uso exclusivo de evaluación. Consulta [LICENSE](LICENSE): no se autoriza uso productivo, comercial, redistribución ni modificaciones sin permiso escrito.

Plataforma para revisar registros comerciales, generar propuestas de asignación y guardar trazabilidad auditable. Los registros entran desde datos normalizados o desde el portal externo; nunca se asignan al recibirlos.

## Producto y supuestos

El sistema conserva el racional de las decisiones: registra los cambios de un registro, las propuestas, las asignaciones ejecutadas y sus eventos de auditoría. La explicación se construye a partir de esa traza determinista. Un LLM puede convertirla en lenguaje natural, pero no sustituye el registro oficial de la decisión.

El panel tiene siete áreas operativas: Resumen, Registros, Vendedores, Asignación, Auditoría, Simulación de lote y Comparación histórica. Además, incluye un panel compacto para comprobar la disponibilidad de base de datos, API y servicio de IA.

## Racional de diseño

Se analizaron los datos proporcionados, su estructura y la cantidad de historial disponible. No había evidencia suficiente para entrenar de forma confiable un modelo supervisado que reprodujera decisiones históricas, ni para justificar un enfoque de aprendizaje autosupervisado cuya función objetivo no estuviera validada por el negocio.

Por ello, se eligió una arquitectura determinista con reglas verificables y una optimización explícita de dos variables: equilibrio de carga relativa y distribución de montos estimados. Ambas variables se controlan mediante parámetros visibles en la simulación y en la asignación.

Los LLM son adecuados para interpretar texto, extraer patrones de notas y redactar explicaciones basadas en contexto. No se utilizan como optimizador principal: un algoritmo especializado permite menor costo, tiempos más predecibles, mantenimiento más sencillo y explicaciones verificables.

## Métodos de asignación

Se mantienen tres métodos para contrastar resultados y entender sus compromisos:

- **Capacity-aware**: aplica restricciones de elegibilidad y favorece capacidad disponible y menor utilización.
- **Fuzzy optimal**: utiliza reglas graduales de afinidad, seniority, experiencia y capacidad antes de equilibrar el lote.
- **AI-assisted**: incorpora señales estructuradas extraídas de notas, sin omitir restricciones obligatorias ni sustituir la aprobación humana.

Los métodos generan previews comparables. El método de IA puede tardar más y consumir más recursos porque debe interpretar notas; no sustituye los algoritmos de optimización ni ejecuta decisiones por su cuenta.

## Calidad de registros, vendedores y auditoría

El análisis de datos mostró registros incompletos o no normalizados. Por eso los registros externos entran primero en una bandeja de revisión. Una heurística identifica notas y campos que requieren atención; Ollama puede proponer requisitos como vendedor senior, experiencia sectorial o especialidad técnica. La propuesta no modifica el registro hasta que un usuario interno la acepta o la edita.

La pestaña **Vendedores** permite revisar restricciones que afectan la elegibilidad: ausencias, capacidad, zona, equipo, actividad y habilidades técnicas verificables. Esto permite distinguir un problema de datos de una restricción operativa real.

La pestaña **Auditoría** muestra asignaciones, cambios, eventos y la traza oficial. El usuario puede pedir una explicación asistida por IA a partir de la traza y una consulta concreta; si el modelo falla, la explicación determinista permanece disponible.

## Arquitectura

```text
Usuario externo → Portal externo → FastAPI → Database API → PostgreSQL privado
Usuario interno → Streamlit      → FastAPI → Motor de asignación / Ollama
```

FastAPI coordina reglas de negocio e integración. Database API es el único servicio conectado a PostgreSQL. El motor evalúa elegibilidad, compatibilidad y equilibrio; Ollama propone señales y explicaciones sin bloquear el flujo determinista.

Consulta el diagrama de [secuencia](/diagrams/sales_assignment_sequence.html) y la [arquitectura](/diagrams/sales_assignment_system_architecture.md).

## Panel interno

| Área | Propósito |
|---|---|
| Resumen | Indicadores operativos y servicios. |
| Registros | Pendientes, revisión de notas y aceptación explícita de propuestas IA. |
| Vendedores | Capacidad, zona, disponibilidad y habilidades verificadas. |
| Asignación | Comparación de métodos y corrección manual de previews. |
| Auditoría | Decisiones, eventos y explicaciones. |
| Simulación de lote | Prueba propuestas sin modificar datos. |
| Comparación histórica | Evalúa métodos sin ejecutar asignaciones. |

Los métodos son Capacity-aware, Fuzzy optimal y AI-assisted. Los controles ajustan afinidad frente a equilibrio y carga relativa frente a montos estimados.

## Inicio rápido

Requisitos: Docker Engine y Docker Compose v2.

```bash
cp .env.example .env
```

Edita .env y configura valores distintos:

```dotenv
DATABASE_PASSWORD=una-clave-local-segura
DATABASE_API_TOKEN=token-interno
DATABASE_API_ADMIN_TOKEN=token-administrativo-distinto
EXTERNAL_INGEST_API_TOKEN=token-para-el-portal-externo
```

Inicia todos los servicios desde la raíz:

```bash
docker compose --env-file .env -f project/docker-compose.yml up -d --build --pull always --wait
```

Una base nueva se inicializa desde normalized. La API descarga OLLAMA_MODEL o, si no existe, OLLAMA_DEFAULT_MODEL.

| Servicio | URL local |
|---|---|
| Panel interno | http://localhost:8501 |
| Portal externo | http://localhost:8600 |
| API de negocio | http://localhost:8000/docs |
| Database API | http://localhost:8001/docs |

Detén los servicios sin borrar datos:

```bash
docker compose --env-file .env -f project/docker-compose.yml down
```

Para eliminar datos y modelos descargados usa el mismo comando con -v.

## Inicializar datos

Para restaurar los datos de ejemplo usa **Administración de datos → Inicializar y reiniciar base de datos**. Esta operación elimina asignaciones, previews y cambios operativos antes de importar normalized.

También puedes ejecutar:

```bash
python scripts/reset_database.py --confirm-reset
```

## Portal externo

El portal captura empresa, referencia externa, sector, ubicación, monto y nota. Envía POST /external/records a FastAPI con Authorization Bearer y EXTERNAL_INGEST_API_TOKEN.

external_reference es idempotente: un reintento devuelve el registro existente. FastAPI valida la entrada y la envía a Database API. El portal no accede a vendedores, asignaciones, auditoría, Database API ni PostgreSQL.

El registro se guarda con origen portal_externo, estado nuevo y señales neutrales. Luego aparece en Registros para revisión humana antes de una propuesta de asignación.

En producción utiliza un token externo aleatorio y publica el portal y FastAPI detrás de HTTPS. No expongas PostgreSQL ni Database API.

## Flujo de asignación

1. Se revisan registros y notas con heurística y Ollama.
2. Las propuestas IA se aceptan, editan o rechazan explícitamente.
3. Se comparan previews con carga, montos, compatibilidad, exclusiones y motivos.
4. Los cambios manuales se revalidan contra capacidad, zona, equipo, ausencia, seniority, sector y habilidades.
5. Solo un preview vigente y aprobado puede ejecutarse; la ejecución es atómica y auditable.

## Desarrollo y pruebas

Python 3.11 para notebooks y utilidades locales:

```bash
conda env create -f environment.yml
conda activate sales-assignment
jupyter lab data/normalization_workflow.ipynb
```

Alternativa:

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

normalization contiene el pipeline; normalized contiene los datos generados; project contiene APIs, motor, frontend y portal externo; diagrams contiene los diagramas del sistema.

## Límites conocidos

- Las habilidades técnicas se aplican solo cuando están verificadas.
- Los montos faltantes no se inventan.
- La IA propone señales y explicaciones, pero no aprueba ni ejecuta asignaciones.
- La comparación histórica no modifica asignaciones reales.
