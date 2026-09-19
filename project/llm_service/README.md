# Servicio de extracción — revisión v1

Estado: prompt v1 y referencias aprobados por el usuario el 2026-09-18. Nota de aprobación en `../../normalized/prompt_review_status.json`. La evaluación del modelo es independiente de esta aprobación.

- Prompt: `promps/note_extraction_v1.md` (se conserva el nombre `promps` solicitado).
- Contrato: `schemas/note_extraction_v1.schema.json`.
- Referencias: `../../normalized/note_golden_review_v1.jsonl`, 15 notas originales aprobadas junto con el prompt.
- `details` contiene los campos tipados; `action`, `priority` y `needs_review` son espejos. Listas y `reason_codes` resumen las señales positivas.
- La precedencia entre acciones y los defaults de confianza/complexidad son propuestas explícitas para revisión.
- Para conocimiento sectorial, la salida depende de si el contexto incluye sector; la regla figura en cada referencia.

Desde la raíz del repositorio:

```bash
docker compose -f project/docker-compose.yml up -d llm_service
docker compose -f project/docker-compose.yml ps
```

Ollama usa un volumen persistente y escucha localmente en el puerto 11434. Se utiliza la imagen oficial directamente; el Dockerfile vacío no interviene. No hay todavía endpoints personalizados `/extract-signals`.

Después de aprobar el prompt, elegir/descargar un modelo, configurar `LLM_API_URL`, `LLM_MODEL` y `LLM_TIMEOUT_SECONDS` según `.env.example` y continuar con pipeline y evaluación. El consumidor debe enviar el prompt como mensaje system y únicamente record_id/note/sector como JSON en el mensaje user, con `stream: false` y el JSON Schema como `format` de `POST /api/chat`. Nunca incluir la respuesta esperada en la solicitud.

No interpretar la validez del esquema como precisión semántica o resistencia probada a inyección. Esas pruebas están pendientes. 

Fuentes: https://docs.ollama.com/docker y https://docs.ollama.com/api/chat
