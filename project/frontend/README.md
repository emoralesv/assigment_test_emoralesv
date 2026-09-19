# Frontend Streamlit

Cinco vistas: Dashboard, Registros, Vendedores, Asignación y Auditoría. Streamlit solo consulta FastAPI mediante `API_BASE_URL`; no importa el motor, no abre archivos de datos y no recibe credenciales de base de datos.

## Docker

Desde la raíz, con `.env` configurado:

```bash
docker compose --env-file .env -f project/docker-compose.yml up -d --build --wait frontend
```

Abre http://localhost:8501. Compose inicia las APIs y PostgreSQL como dependencias, conservando el volumen. Ollama es opcional y se arranca por separado con la configuración GPU o el override CPU existente. El health check consulta `/_stcore/health`; las vistas muestran los errores de FastAPI si el backend deja de responder.

## Desarrollo local

Se conserva un único archivo de dependencias, `requirements.txt` en la raíz, compartido con `environment.yml`. La imagen selecciona desde él Streamlit, pandas y httpx sin mantener otro listado de versiones.

```bash
python -m pip install -r requirements.txt
export API_BASE_URL=http://localhost:8000
PYTHONPATH=. streamlit run project/frontend/streamlit_app/app.py
```

Compose configura `API_BASE_URL=http://api:8000`. `FRONTEND_PORT` permite cambiar el puerto local (8501 por defecto).

## Flujo de revisión

1. Revisa indicadores, registros y perfiles. Búsqueda, filtros y paginación se resuelven en FastAPI.
2. En Registros, inspecciona la nota, las señales y las advertencias. El editor JSON exige motivo; FastAPI valida el esquema y comprueba la versión antes de guardar. Conserva las revisiones en `record_signals.source_payload._signal_edits`, sin modificar el texto original. La demo no atribuye estas revisiones a un usuario autenticado.
3. En Asignación, selecciona hasta 100 registros ofrecidos por el backend, elige un modelo y genera un preview. Las propuestas no cambian propietarios.
4. Revisa propuestas, exclusiones y trazas; marca la aprobación y ejecuta. Cambiar selección/modelo deshabilita la ejecución anterior. HTTP 409 exige un nuevo preview. Un timeout no implica rollback: consulta el preview por su ID antes de reintentar; la ejecución del servidor es idempotente.
5. Auditoría muestra asignaciones y todos los tipos de evento existentes, incluidas reasignaciones si existen. No crea un flujo nuevo de reasignación. Las explicaciones deterministas siempre se conservan y las explicaciones IA son opcionales.

El estado del preview se guarda en `st.session_state`; recargar una sesión del navegador puede perder ese estado local, pero el preview persiste en FastAPI. No se cachean respuestas operativas. La elegibilidad, indicadores, restricciones, edición de señales y asignación se calculan y validan en los servicios backend.

Nuevas rutas de la API de negocio: `GET /dashboard`, `GET /records`, `GET /records/{id}`, `PATCH /records/{id}/signals`, `GET /sellers`, `GET /audit`. La Database API proporciona sus operaciones internas protegidas bajo `/ui/`.

## Pruebas

```bash
python -m unittest discover -s tests -p 'test_frontend.py' -v
python scripts/run_database_tests.py
```

Las pruebas comprueban errores y timeout del cliente, aprobación obligatoria, rechazo de previews obsoletos y el recorrido real de widgets usando [Streamlit AppTest](https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest). Las pruebas PostgreSQL usan una base desechable para verificar edición concurrente y que editar señales invalide previews.

El panel lateral consulta `GET /services`: API, Database y `llm_service`, disponibilidad del modelo y CPU/GPU observada en `/api/ps` de Ollama. Un modelo descargado pero sin cargar aparece como tal; no se presupone uso de GPU. La explicación IA informa el error concreto y dispone de `OLLAMA_EXPLANATION_TIMEOUT_SECONDS=180`, independiente del timeout de extracción.

## Carga y simulación

Esta pestaña muestra registros activos, utilización y suma de ingresos estimados de empresas por persona; permite filtrar equipo, zona y disponibilidad. Expone carga asignada/en gestión, propietarios inferidos, montos faltantes y registros sin propietario. No interpreta los ingresos estimados como ventas ni presupone moneda o periodicidad.

Selecciona hasta 100 pendientes y ejecuta los tres modelos. FastAPI obtiene un único snapshot PostgreSQL con aislamiento REPEATABLE READ, ejecuta cada método sobre copias independientes y publica resultados parciales con progreso. Las gráficas usan ejes comunes. El peso de equilibrio afecta a los modelos óptimos; Capacity-Aware conserva sus reglas. Fuzzy e IA ahora optimizan carga relativa y montos conocidos por capacidad, además de compatibilidad. El equilibrio tiene prioridad 0.85 por defecto, con igual peso para carga y montos (0.5); ambos pesos se pueden ajustar. Capacity-Aware conserva su criterio de carga.

La dispersión de utilización se calcula sobre el mismo grupo disponible al inicio. La dispersión de montos solo incluye carteras con datos completos; se muestra el número de personas comparables. Estas métricas no determinan por sí solas un ganador: también deben compararse cobertura, restricciones, compatibilidad y datos faltantes. Las métricas no cambian con los filtros visuales.

«Llevar a revisión» guarda el resultado exacto como preview únicamente si el snapshot sigue vigente. Después se requiere revisión y aprobación explícita en Asignación. Simular por sí solo no escribe previews, asignaciones ni eventos; las llamadas IA tampoco se guardan en la base hasta promover el resultado.

## Prueba histórica

Reutiliza **todos los registros existentes**, incluidos pendientes, asignados, en gestión y descartados, como ejemplos ficticios. Restablece su estado a pendiente y la carga de todas las personas a cero exclusivamente en memoria. Mantiene perfiles, capacidades, fecha efectiva, ausencias, duplicados y señales restrictivas. Los perfiles contienen experiencia del mismo conjunto histórico: no es una evaluación independiente ni prueba de superioridad comercial.

Compara los tres métodos con idéntico punto de partida. Los resultados históricos no pueden convertirse en previews ejecutables; el servidor también lo impide. La referencia inicial y su tabla parten de cero. La tabla «Después de asignar» permite elegir un modelo y muestra carga inicial, nuevas asignaciones, carga final, montos y capacidad restante.

Rutas: `GET /load-analysis`, `POST /simulations`, `POST /historical-simulations`, `GET /simulations/{id}`, `POST /simulations/{id}/{method}/preview`. Los trabajos se retienen en memoria del proceso (máximo 20, limpieza al crear nuevos tras 1 hora, sin eliminar trabajos activos). Reiniciar la API los pierde; el despliegue actual usa un solo proceso. IA puede tardar varios minutos en CPU; sus fallos o fallback no impiden ver los otros resultados.

Las simulaciones usan dos trabajadores para Capacity-Aware/Fuzzy y uno independiente para IA. Las solicitudes lentas a Ollama no ocupan los trabajadores de Fuzzy. El progreso distingue `queued`, `running`, `completed` y `failed`; un trabajo perdido tras reiniciar la API solicita generar otra simulación y deja de consultarse repetidamente. Los errores internos de modelos incluyen traceback en el log de la API.


La optimización conserva la máxima cantidad asignable y usa programación entera para equilibrar carga/capacidad y subtotal conocido/capacidad. No inventa montos faltantes. El cálculo tiene un límite de 10 segundos; si no prueba optimalidad global, conserva una solución válida que no empeore su objetivo inicial y muestra advertencia. La dispersión de subtotales por capacidad no demuestra equilibrio de valores desconocidos.

Ollama prepara el modelo en segundo plano al arrancar la API. Si no está descargado, lo descarga; ante HTTP 404 del modelo configurado, prueba `OLLAMA_DEFAULT_MODEL` (por defecto `llama3.2:1b`). Errores de conexión o autorización no se interpretan como modelo inexistente. El panel muestra descarga, error o modelo seleccionado. Los errores se reintentan al consultar servicios o solicitar IA, con un intervalo mínimo de 60 segundos. Los modelos se conservan en el volumen de Ollama.

## Preguntar por una simulación

En Carga y simulación (también Prueba histórica), elige el modelo de la tabla posterior y usa «Preguntar sobre esta asignación». Puedes comparar hasta tres personas y preguntar, por ejemplo, por qué Santiago recibe ocho registros y otra persona ninguno.

FastAPI construye el contexto desde la simulación almacenada: carga inicial, nuevas asignaciones, carga final, capacidad, elegibilidad, exclusiones agregadas, objetivos y advertencias. No acepta una inspección alterada desde el navegador ni consulta datos más recientes que cambien el contexto. Se envía un resumen, sin notas crudas; puedes inspeccionar los datos enviados junto a la respuesta. La explicación no modifica asignaciones y se conserva en la sesión de la interfaz, vinculada al ID y al modelo de la simulación.

La IA debe corregir premisas numéricas incorrectas y pedir identificar a «el otro» si es ambiguo. El texto es complementario; los conteos deterministas permanecen visibles. Si el modelo falla, se muestran esos datos verificados. Endpoint: `POST /simulations/{id}/{method}/explanation`, con `question` obligatoria y `person_ids` opcionales.

Para evitar que un modelo pequeño confunda registros con ventas, la respuesta usa generación restringida: Ollama selecciona y ordena identificadores de hechos preparados por el servidor. Se valida que cada identificador exista, y el servidor compone el texto con esos hechos. Los conteos de las personas seleccionadas y la definición de carga siempre se incluyen. El modelo puede solicitar una aclaración, pero no introducir cifras o explicaciones causales nuevas. Esto limita las preguntas a la evidencia disponible en la inspección.
