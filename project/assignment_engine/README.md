# Motor de asignación

La API pública calcula decisiones y utiliza exclusivamente la Database API para leer o persistir. Los modelos comparten `preview(records, sellers, configuration)` y producen asignaciones, no asignados, candidatos excluidos, factores y reglas activadas.

- `capacity_aware`: menor carga relativa, con desempates por capacidad restante e ID.
- `fuzzy_optimal`: pertenencias difusas y reglas Sugeno para zona, segmento, antigüedad, experiencia sectorial, respuesta, complejidad y capacidad. Se determina primero la máxima cobertura y luego se optimiza el lote con programación entera: carga relativa, montos conocidos por capacidad y compatibilidad difusa. Un límite de 10 segundos produce una advertencia si no se demuestra optimalidad global.
- `ai_assisted`: extracción estructurada en Ollama y el mismo optimizador. Las restricciones deterministas nunca se relajan. Las etiquetas conocidas aprobadas prevalecen ante discrepancias; un fallo con nota desconocida requiere revisión.

## Uso

```bash
curl http://localhost:8000/assignment-models
curl -X POST http://localhost:8000/assignment-previews \
  -H 'Content-Type: application/json' \
  -d '{"record_ids":[1,2,3],"method":"capacity_aware","configuration":{}}'
```

Endpoints: `GET /health`, `GET /assignment-models`, `POST /assignment-previews`, `GET /assignment-previews/{id}`, `POST /assignment-previews/{id}/execute`, `GET /records/{id}/assignment-explanation`, `POST /records/{id}/generate-ai-explanation`.

Ejecutar requiere cuerpo `{"approved":true}`. Un cambio en los datos relevantes o en la fecha efectiva invalida el preview con HTTP 409. Las asignaciones, estados y eventos se escriben atómicamente; repetir la ejecución no duplica asignaciones. Los endpoints locales están publicados en loopback y no incluyen autenticación de usuario final; añadirla antes de exponer esta API a una red externa.

`configuration` admite `balance_weight` (0–1, predeterminado 0.85), `amount_weight` (0–1, predeterminado 0.5 dentro del equilibrio) y `weights` para los siete factores. No permite cambiar restricciones ni la fecha efectiva del servidor (`America/Mexico_City`).

## Políticas y límites de los datos

Inactividad, rol distinto de vendedor, equipo/zona ausente, ausencia inclusiva, capacidad indefinida/cero/agotada, registro no nuevo, duplicado, no contactar y revisión pendiente impiden asignación. La carga se calcula por registros activos, no por cantidad de actividades.

Seniority solicitado exige tres años de antigüedad. La experiencia sectorial se aproxima por cuentas históricas con actividad, no certificaciones. Sin evidencia de habilidades técnicas se requiere revisión y no se asigna una nota que las exija. Segmento u otros factores desconocidos reciben valor neutral 0.5 y advertencias. Estas políticas son explícitas para poder revisarlas con negocio.

Ollama usa `OLLAMA_BASE_URL`, `OLLAMA_MODEL` y timeout. Las extracciones conservan salida cruda, validación y error. La aprobación del prompt no acredita el modelo: su evaluación completa sigue pendiente. La explicación determinista es oficial; el texto generado por IA es complementario y puede fallar sin impedir consultar la decisión original.

Pruebas unitarias: `python -m unittest discover -s tests -v`. Pruebas transaccionales: `python scripts/run_database_tests.py`.

Si Docker no expone GPU, conserva la configuración principal y aplica el override CPU:

```bash
docker compose --env-file .env -f project/docker-compose.yml -f project/docker-compose.cpu.yml up -d --wait llm_service
```

El override usa `!reset` de Docker Compose. Para GPU, usa únicamente el archivo principal en un entorno con soporte NVIDIA. Los modelos se conservan en `ollama_data`; descarga el configurado si todavía no existe: `docker compose --env-file .env -f project/docker-compose.yml exec llm_service ollama pull llama3.2:1b`.

### GPU en este equipo Linux

Se verificó una NVIDIA RTX 3050 de 6 GB y driver funcional. El contexto `desktop-linux` usa Docker Desktop, cuyo soporte GPU documentado se limita a Windows/WSL2. En Linux se necesita Docker Engine nativo con NVIDIA Container Toolkit. Ambos están instalados en este equipo, pero el usuario actual no tiene permisos para `/var/run/docker.sock`; `/etc/docker/daemon.json` ya declara el runtime NVIDIA, pero no se pudo verificar su funcionamiento dentro de un contenedor sin autenticación sudo.

Configura y verifica el Engine nativo en una terminal:

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
sudo docker --context default run --rm --gpus all ubuntu nvidia-smi
```

El reinicio afecta a los contenedores del Engine nativo. Los volúmenes e imágenes de `desktop-linux` y `default` son independientes: antes de mover este proyecto, respalda y migra PostgreSQL y los modelos de Ollama. No basta con cambiar el contexto y ejecutar Compose, pues aparecería una base distinta. Una vez migrado, el archivo principal incluye `gpus: all`; omite `docker-compose.cpu.yml`.

Referencias: [soporte GPU de Docker Desktop](https://docs.docker.com/desktop/features/gpu/) y [configuración NVIDIA del runtime Docker](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html#configuring-docker).
