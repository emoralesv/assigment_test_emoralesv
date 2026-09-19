FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
COPY scripts/export_runtime_requirements.py /tmp/export_requirements.py
RUN python /tmp/export_requirements.py database > /tmp/runtime.txt && pip install --no-cache-dir -r /tmp/runtime.txt
COPY normalization normalization
COPY project/database project/database
COPY project/assignment_engine/contracts.py project/assignment_engine/domain.py project/assignment_engine/
COPY project/llm_service/promps project/llm_service/promps
COPY project/llm_service/schemas project/llm_service/schemas
CMD ["uvicorn", "project.database.api:app", "--host", "0.0.0.0", "--port", "8000"]
