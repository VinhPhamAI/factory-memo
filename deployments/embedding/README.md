# Embedding service (Text Embeddings Inference)

The `ai-embedding` worker in the AI overlay [`../ai.compose.yml`](../ai.compose.yml)
serves [microsoft/harrier-oss-v1-0.6b](https://huggingface.co/microsoft/harrier-oss-v1-0.6b)
with Hugging Face [Text Embeddings Inference](https://github.com/huggingface/text-embeddings-inference)
(TEI) 1.9 (`ghcr.io/huggingface/text-embeddings-inference:cuda-1.9`):

- 1024-dimensional vectors, normalized, last-token pooling, float16.
- Inputs longer than 8,192 tokens are truncated (the model accepts 32,768;
  `--max-batch-tokens 8192` limits them).
- At most 32 inputs per request (`max_client_batch_size`, see `/info`), which
  matches `EMBEDDING_BATCH_SIZE=32` in the backend.

It replaces the `tei-embeddings` container that was started by hand with
`docker run`, and keeps all its settings: GPU 0, 4 CPUs, 8 GB RAM, the same
model cache at `/mnt/data/embeddings/data`, `--max-batch-tokens 8192`,
`--max-concurrent-requests 256`, the same healthcheck and the same port
`0.0.0.0:8001`.

## Endpoints

Clients reach it through [`ai-services-api`](../ai-services-api/README.md) on
`127.0.0.1:8080`, or directly on port 8001, where existing clients (for example
a tunnel to this port) already connect. Port 8001 listens on all interfaces
without authentication, as before.

| Gateway path | TEI path | API |
| --- | --- | --- |
| `POST /v1/embeddings` | same | OpenAI-compatible: `{"model": "microsoft/harrier-oss-v1-0.6b", "input": [...]}`; `model` may be omitted, other names are ignored with a logged warning |
| `POST /v1/embeddings/tokenize` | `/tokenize` | Tokens of each input |
| `GET /v1/embeddings/info` | `/info` | Model and limits |
| `GET /v1/services/embedding/ready` | `/health` | Readiness |

Request fields, responses and errors: [AI services API reference](../ai-services-api/AI_SERVICES_API_DOCS.md#text-embeddings).
Port 8001 also serves TEI's other paths (`/embed`, `/embed_all`, `/decode`, …),
which the gateway does not expose.

Backend settings for this service (`.env`):

```sh
EMBEDDING_BASE_URL=http://127.0.0.1:8080/v1   # through ai-services-api (or :8001/v1 directly)
EMBEDDING_MODEL=microsoft/harrier-oss-v1-0.6b
EMBEDDING_API_KEY=
EMBEDDING_DIM=1024
EMBEDDING_BATCH_SIZE=32
```

Inside Compose, the API container uses `http://ai-services-api:8080/v1`.
Changing the model changes the vectors: re-embed every knowledge base
(`python -m backend.scripts.kb reembed <kb>`).

## Run

```sh
bash deployments/ai-services-api/up.sh embedding    # gateway + ai-embedding
bash deployments/embedding/smoke_test.sh
docker compose -f deployments/docker-compose.yml -f deployments/ai.compose.yml logs -f ai-embedding
```

Before the first start, remove the hand-started container, which holds port 8001:
`docker rm -f tei-embeddings`.

Starting takes about 5 minutes on the A40 host: loading the model ("Starting
FlashQwen3 model") runs on one CPU core before the server answers. Port 8001
is unavailable during that time. Recreating the container (changed settings,
`up --force-recreate`) causes the same interruption, while restarting the
gateway does not.

| Setting | Default | Meaning |
| --- | --- | --- |
| `EMBEDDING_TEI_MODEL` | `microsoft/harrier-oss-v1-0.6b` | Hugging Face model ID |
| `EMBEDDING_TEI_PORT` | `8001` | Direct host port |
| `EMBEDDING_TEI_BIND` | `0.0.0.0` | Host address of that port; `127.0.0.1` to keep it local |
| `EMBEDDING_TEI_DATA_DIR` | `/mnt/data/embeddings/data` | Hugging Face cache on the host (about 1.2 GB) |
| `EMBEDDING_TEI_GPU_ID` | `0` | GPU |
| `EMBEDDING_TEI_MAX_BATCH_TOKENS` | `8192` | Tokens per batch, and the longest input |
| `EMBEDDING_TEI_MAX_CONCURRENT_REQUESTS` | `256` | Requests TEI accepts at once |
| `EMBEDDING_TEI_IMAGE_TAG` | `cuda-1.9` | TEI image tag |

Resources measured: 1.46 GB of GPU memory and about 3.7 GB of RAM (limit 8 GB).

## Checks

Measured on 2026-11-03 when replacing `tei-embeddings`: vectors of the same
three Vietnamese and English inputs matched those of the old container (cosine
0.999999, largest difference 4.8e-4, the float16 run-to-run variation), both
directly and through the gateway. `smoke_test.sh` checks readiness, the model
listing, `/v1/embeddings` on both ports, and `/v1/embeddings/tokenize` and
`/v1/embeddings/info` through the gateway.

## Limitations

- Port 8001 has no authentication and listens on all interfaces, as the old
  container did. Set `EMBEDDING_TEI_BIND=127.0.0.1` once no external client
  needs it.
- The model is downloaded from Hugging Face on first start if the cache is
  empty; there is no pinned revision.
