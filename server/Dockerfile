FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PLANT_DB=/data/plant.db \
    GIGACHAT_CA_BUNDLE=/srv/certs/russian_trusted_root_ca.pem
WORKDIR /srv

COPY requirements.txt requirements-voice.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# Голос (V1): torch — только CPU-сборка (иначе +2 ГБ CUDA). VOICE=0 — образ без голоса.
ARG VOICE=1
RUN if [ "$VOICE" = "1" ]; then \
      pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.8.0 && \
      pip install --no-cache-dir -r requirements-voice.txt; \
    fi

COPY certs ./certs
COPY app ./app

VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"

CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
