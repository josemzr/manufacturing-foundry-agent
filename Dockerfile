FROM python:3.13-slim
LABEL com.azure.containerizationassist.createdby="containerization-assist"
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home appuser
COPY --chown=appuser:appuser server.py foundry.py domain.py auth.py config.example.json ./
COPY --chown=appuser:appuser web/ ./web/
COPY --chown=appuser:appuser data/scenario.json ./data/scenario.json
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 RESULTS_DIR=/tmp/manufacturing-results
EXPOSE 8098
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD ["python","-c","import urllib.request; urllib.request.urlopen('http://127.0.0.1:8098/api/health', timeout=4).read()"]
USER 10001
CMD ["python","server.py","--host","0.0.0.0","--port","8098"]
