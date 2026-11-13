# Detector Silero VAD na CPU, sem ASR local.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt /servico/requirements.txt
RUN pip install --no-cache-dir -r /servico/requirements.txt

# Sem root; nenhum volume de modelo ASR.
RUN useradd --create-home --uid 1000 whisper
USER whisper

COPY servico.py /servico/servico.py
EXPOSE 18765
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18765/status', timeout=3)"
CMD ["python", "/servico/servico.py"]
