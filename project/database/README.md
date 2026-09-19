# API de base de datos

PostgreSQL pertenece exclusivamente a la red interna `database_private` y no publica puertos. Solo `database_api` comparte esa red. La API de asignación y los demás componentes acceden por HTTP; no reciben credenciales PostgreSQL. El administrador de Docker conserva acceso administrativo a los contenedores.

## Arranque

Desde la raíz, configura `.env` a partir de `.env.example`, con contraseña y dos tokens aleatorios distintos. `LOCAL_UID` y `LOCAL_GID` deben corresponder al propietario de `normalized/` para escribir reportes. Mantén el archivo fuera de Git.

```bash
docker compose --env-file .env -f project/docker-compose.yml up -d --build --wait database_api api
```

El volumen `postgres_data` conserva los datos. Arrancar servicios no reinicia el esquema. Para la primera carga, o un reinicio explícito del esquema propio:

```bash
python scripts/reset_database.py --confirm-reset
```

Este cliente envía `POST /admin/reset` con el token administrativo; no conecta a PostgreSQL. El reinicio elimina intencionalmente los datos operativos del esquema `sales_assignment`. Se ejecuta en una única transacción, protege dependencias externas con `RESTRICT` y rechaza esquemas ajenos. Solo se admiten entornos `local`/`test`, nombres de base correspondientes y loopback para pruebas o `postgres:5432` dentro de Docker. El directorio de entradas lo fija el servidor, no el cliente.

## HTTP

Documentación: http://localhost:8001/docs. `GET /health` es público. Las rutas de datos requieren `Authorization: Bearer <DATABASE_API_TOKEN>`; `/admin/reset` requiere exclusivamente `DATABASE_API_ADMIN_TOKEN`.

- `GET /summary`: conteos y estados.
- `GET /data/{table}?limit=50&offset=0`: consulta paginada de las veinte tablas; tabla validada, máximo 200 filas.
- `POST /state`: snapshot de registros, perfiles, carga actual y hash.
- `POST /previews`, `GET /previews/{id}`: validación y persistencia.
- `POST /previews/{id}/execute`: revalidación, asignaciones, estados y auditoría en una transacción; ejecución repetida idempotente.
- `GET /records/{id}/assignment-explanation`: traza determinista.
- `POST /records/{id}/llm-explanations`: explicación complementaria sin cambiar asignaciones.
- `POST /admin/reset`, cuerpo `{"confirm_reset":true}`: importación controlada desde `normalized/`.

## Datos y auditoría

Se conservan las veinte tablas, claves foráneas, datos fuente completos, reglas de normalización, checksums y versiones de prompt. El equipo inexistente 99 permanece como `source_team_id`, con FK nula. No se fusionan NIT duplicados. Los eventos de asignación rechazan UPDATE/DELETE mediante trigger.

Los artefactos se generan desde `data/normalization_workflow.ipynb`. Los reportes de importación se guardan en `normalized/database_import_report.json` y `normalized/database_import_runs/`. Si falla una transacción, se conserva el estado anterior. No uses `docker compose down -v` para recuperarlo: elimina volúmenes.

## Pruebas

```bash
python scripts/run_database_tests.py
```

Crea y elimina una base PostgreSQL temporal independiente. Comprueba importación, rollback, protección de esquemas, autenticación, ejecución idempotente, previews obsoletos, auditoría y rollback a mitad de lote. El acceso SQL directo en estas pruebas es exclusivo de esa infraestructura desechable.
