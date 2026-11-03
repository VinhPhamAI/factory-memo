#!/usr/bin/env bash
# End-to-end check of the embedding service (ai-embedding, Text Embeddings
# Inference) through ai-services-api and on its own port.
# Usage: bash deployments/embedding/smoke_test.sh
set -euo pipefail
ENV_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/.env"
env_value() { [ -f "$ENV_FILE" ] && sed -n -E "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$ENV_FILE" | tail -n 1 || true; }
gateway_port="${AI_SERVICES_API_PORT:-$(env_value AI_SERVICES_API_PORT)}"
tei_port="${EMBEDDING_TEI_PORT:-$(env_value EMBEDDING_TEI_PORT)}"
GATEWAY="${AI_SERVICES_API_URL:-http://127.0.0.1:${gateway_port:-8080}}"
DIRECT="http://127.0.0.1:${tei_port:-8001}"
die() { printf '[embedding] ERROR: %s\n' "$*" >&2; exit 1; }
step() { printf '\n== %s\n' "$*"; }

step "ready through $GATEWAY; model"
curl -fsS "$GATEWAY/v1/services/embedding/ready" -o /dev/null || die "embedding is not ready"
curl -fsS "$GATEWAY/v1/models" | python3 -c '
import json, sys
models = [m for m in json.load(sys.stdin)["data"] if m["service"] == "embedding"]
assert models, "no embedding model listed"
print(json.dumps(models[0], ensure_ascii=False))'

for base in "$GATEWAY" "$DIRECT"; do
    step "OpenAI-compatible /v1/embeddings ($base)"
    curl -fsS "$base/v1/embeddings" -H 'content-type: application/json' \
        -d '{"input": ["Trục Z kêu rè khi chạy nhanh.", "grinding noise on the Z axis"]}' |
        python3 -c '
import json, math, sys
data = [d["embedding"] for d in json.load(sys.stdin)["data"]]
assert len(data) == 2, data
norms = [math.sqrt(sum(x * x for x in v)) for v in data]
print(f"{len(data)} vectors of {len(data[0])} dimensions, norms {norms[0]:.3f} {norms[1]:.3f}")'
done

step "tokenizer and limits through the gateway"
curl -fsS "$GATEWAY/v1/embeddings/tokenize" -H 'content-type: application/json' \
    -d '{"inputs": "xin chào"}' |
    python3 -c 'import json, sys; t = json.load(sys.stdin)[0]; assert t, t; print(f"{len(t)} tokens")'
curl -fsS "$GATEWAY/v1/embeddings/info" |
    python3 -c 'import json, sys; i = json.load(sys.stdin); print(i["model_id"], "max batch", i["max_client_batch_size"])'
printf '\n[embedding] all checks passed\n'
