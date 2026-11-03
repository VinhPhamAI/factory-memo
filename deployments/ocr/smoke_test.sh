#!/usr/bin/env bash
# End-to-end check of the OCR service (ai-ocr, ai-ocr-vlm) behind ai-services-api.
# Usage: bash deployments/ocr/smoke_test.sh [document.pdf|.png|.jpg]
#
# Always: health, readiness and refusal of unsupported files. With a document:
# OCR as JSON (pages, regions, Markdown) and as Markdown only.
set -euo pipefail
ENV_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/.env"
port="${AI_SERVICES_API_PORT:-$( [ -f "$ENV_FILE" ] && sed -n -E 's/^[[:space:]]*AI_SERVICES_API_PORT[[:space:]]*=[[:space:]]*//p' "$ENV_FILE" | tail -n 1)}"
URL="${AI_SERVICES_API_URL:-http://127.0.0.1:${port:-8080}}"
die() { printf '[ocr] ERROR: %s\n' "$*" >&2; exit 1; }
step() { printf '\n== %s\n' "$*"; }

step "health / OCR ready ($URL)"
curl -fsS "$URL/health"; echo
curl -fsS "$URL/v1/services/ocr/ready" || die "the OCR service is not ready"; echo

step "unsupported file refused"
code=$(printf 'GIF89a' | curl -s -o /dev/null -w '%{http_code}' -X POST "$URL/v1/ocr" -F "file=@-;filename=a.gif")
[ "$code" = 400 ] || die "expected HTTP 400 for a GIF, got $code"
echo "GIF: HTTP 400"

if [ -n "${1:-}" ]; then
    step "OCR as JSON ($1)"
    out="$(mktemp)"
    curl -fsS -X POST "$URL/v1/ocr" -F "file=@$1" -o "$out" || die "OCR request failed"
    python3 - "$out" <<'PY'
import json, sys
body = json.load(open(sys.argv[1]))
print(f"{body['file_type']}, {body['page_count']} page(s), {body['elapsed_s']} s")
for page in body["pages"]:
    labels = [r["label"] for r in page["regions"]]
    assert [r["order"] for r in page["regions"]] == list(range(1, len(labels) + 1))
    print(f"  page {page['page_number']}: {len(labels)} regions {labels[:6]}")
assert [p["page_number"] for p in body["pages"]] == list(range(1, body["page_count"] + 1))
assert body["markdown"].startswith("<!-- page 1 -->"), body["markdown"][:80]
print(body["markdown"][:600])
PY
    step "OCR as Markdown"
    curl -fsS -X POST "$URL/v1/ocr" -F "file=@$1" -F response_format=markdown | head -c 400; echo
fi
printf '\n[ocr] all checks passed\n'
