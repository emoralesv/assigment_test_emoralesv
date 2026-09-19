FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
COPY scripts/export_runtime_requirements.py /tmp/export_requirements.py
RUN python /tmp/export_requirements.py frontend > /tmp/runtime.txt && pip install --no-cache-dir -r /tmp/runtime.txt
COPY project/frontend project/frontend
ENV PYTHONPATH=/app
EXPOSE 8501
HEALTHCHECK --interval=10s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"
CMD ["streamlit", "run", "project/frontend/streamlit_app/app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", "--browser.gatherUsageStats=false"]
