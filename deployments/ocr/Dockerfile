# OCR API: the PaddleOCR-VL-1.6 pipeline (PP-DocLayoutV3 + VLM over vLLM).
# The base image is PaddleOCR's offline pipeline image: PaddlePaddle 3.2.1 (GPU),
# PaddleOCR 3.6.0, PaddleX 3.6.1 and the pipeline's models, so nothing is
# downloaded at runtime. The VLM itself runs in the ocr-vlm container.
ARG BASE_IMAGE=ccr-2vdh3abv-pub.cnc.bj.baidubce.com/paddlepaddle/paddleocr-vl:0006f78-ppocr3.6-pdx3.6-nvidia-gpu-offline
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True

# FastAPI form uploads; the base image has FastAPI, uvicorn and httpx.
RUN pip install python-multipart==0.0.20

WORKDIR /home/paddleocr/app
COPY ocr_server ./ocr_server

EXPOSE 8080
CMD ["python", "-m", "ocr_server.app"]
