# VieGem-ASR speech server. The model and encoder weights are not baked in:
# deployments/speech/setup.sh downloads them to models/, mounted read-only.
FROM python:3.12-slim

ARG TORCH_VERSION=2.14.1
ARG TORCH_INDEX=https://download.pytorch.org/whl/cu126

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_HOME=/tmp/hf

RUN pip install --index-url "$TORCH_INDEX" "torch==$TORCH_VERSION"
COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

WORKDIR /app
COPY viegem_server /app/viegem_server
RUN useradd --uid 1000 --create-home speech
USER speech

EXPOSE 8080
CMD ["python", "-m", "viegem_server.app"]
