# ai-services-api: the single FastAPI app and port in front of the AI service
# workers (see services.yaml). It runs no models and needs no GPU.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

WORKDIR /app
COPY services.yaml /app/services.yaml
COPY ai_services_api /app/ai_services_api
RUN useradd --uid 1000 --create-home gateway
USER gateway

EXPOSE 8080
CMD ["python", "-m", "ai_services_api.app"]
