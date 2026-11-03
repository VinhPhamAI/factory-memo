# OCR service (PaddleOCR-VL-1.6)

Document OCR for PDF, PNG and JPEG files with the
[PaddleOCR-VL-1.6](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6) pipeline.
It is sized for one or two people testing, not for production load.
The ingestion pipeline does not call it yet.

| Worker | Role | Image |
| --- | --- | --- |
| `ai-ocr` | FastAPI app ([`ocr_server/`](ocr_server/)) running the full PaddleOCR-VL pipeline: PP-DocLayoutV3 layout regions and reading order, region recognition, Markdown | Built on `paddlepaddle/paddleocr-vl` (PaddlePaddle 3.2.1, PaddleOCR 3.6.0, PaddleX 3.6.1) |
| `ai-ocr-vlm` | vLLM 0.10.2 server for the PaddleOCR-VL-1.6-0.9B model; the pipeline sends each layout region to it | `paddlepaddle/paddleocr-genai-vllm-server` |

Both images come from PaddleOCR's registry
(`ccr-2vdh3abv-pub.cnc.bj.baidubce.com/paddlepaddle/`). The tag is pinned to
`0006f78-ppocr3.6-pdx3.6-nvidia-gpu-offline` (override with `OCR_IMAGE_TAG`).
The `offline` images include the models, so nothing is downloaded at runtime.
The model is Apache-2.0.

## Run

Both workers are in the AI overlay [`../ai.compose.yml`](../ai.compose.yml),
without host ports. Clients reach `/v1/ocr` through
[`ai-services-api`](../ai-services-api/README.md) on `127.0.0.1:8080`. Run from
the repository root (an NVIDIA GPU with compute capability 8.0 or later and the
NVIDIA Container Toolkit are required):

```sh
bash deployments/ai-services-api/up.sh ocr   # gateway + ai-ocr (+ ai-ocr-vlm)
curl -s http://127.0.0.1:8080/v1/services/ocr/ready
bash deployments/ocr/smoke_test.sh [document.pdf]
```

The first start pulls about 25 GB of images. After that, `ai-ocr-vlm` becomes
healthy in about 80 s, and `ai-ocr` starts once it is. The gateway has no
authentication.

## Resources

Measured on an A40 (46 GB):

| | GPU memory | RAM |
| --- | --- | --- |
| `ai-ocr-vlm` | 5.4 GB (`gpu-memory-utilization: 0.12`: weights 1.8 GB, KV cache 2.9 GB) | 2.8 GB |
| `ai-ocr` | 0.9–1.4 GB | 0.7–1.5 GB |

These limits keep the service small:

- **vLLM:** [`vllm_config.yaml`](vllm_config.yaml) overrides PaddleOCR's
  defaults (0.5 of GPU memory, 4 API processes) with 0.12 of GPU memory,
  8 sequences at a time, one API process and no image cache. Raise
  `gpu-memory-utilization` on a smaller GPU: it is a share of the card's total
  memory.
- **Queue:** one document is processed at a time. Up to `OCR_MAX_QUEUE` (4)
  more wait, and further requests get HTTP 503.
- **Layout batch:** one page per batch (`OCR_LAYOUT_BATCH`). Paddle keeps the
  GPU memory it has used: on 10 pages, a batch of 8 (PaddleOCR's default) held
  4.3 GB, a batch of 1 held 0.9 GB, and both took about the same time.
- **No visualization:** layout images are never drawn.

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `OCR_GPU_ID` | `0` | GPU for both services |
| `OCR_DOC_PREPROCESSING` | `0` | `1` loads the orientation (PP-LCNet_x1_0_doc_ori) and unwarping (UVDoc) models, so requests can use them |
| `OCR_MAX_UPLOAD_MB` | `50` | Largest file |
| `OCR_MAX_PAGES` | `100` | Most PDF pages |
| `OCR_MAX_QUEUE` | `4` | Documents that may wait while one is processed |
| `OCR_LAYOUT_BATCH` | `1` | Pages per layout-detection batch |
| `OCR_IMAGE_TAG` | see above | Tag of both PaddleOCR images |

## API

Paths are on the gateway (`http://127.0.0.1:8080`):

- `GET /v1/services/ocr/ready`: the worker's `/ready`. It returns 200 when the
  pipeline is loaded and `ai-ocr-vlm` answers, otherwise 503 with `status`
  `loading` or `vlm_unavailable`.
- `GET /v1/models` (all services) includes `PaddleOCR-VL-1.6-0.9B`.
- `POST /v1/ocr`: multipart form.

| Field | Default | Meaning |
| --- | --- | --- |
| `file` | required | PDF, PNG or JPEG; the type is read from the content, not the name |
| `response_format` | `json` | `json`, or `markdown` for the document's Markdown only (`text/markdown`) |
| `doc_orientation` | `false` | Detect 0/90/180/270° rotation and turn the page upright (needs `OCR_DOC_PREPROCESSING=1`, otherwise HTTP 400) |
| `doc_unwarping` | `false` | Flatten curved or skewed pages with UVDoc (same condition) |
| `include_images` | `false` | Add the cropped figures the Markdown links to, as base64 JPEG |

```sh
curl -s -X POST http://127.0.0.1:8080/v1/ocr -F file=@manual.pdf > manual.json
curl -s -X POST http://127.0.0.1:8080/v1/ocr -F file=@manual.pdf -F response_format=markdown
```

JSON response (abridged; real output for a 2-page test PDF):

```json
{
  "pipeline": "PaddleOCR-VL-1.6", "vlm_model": "PaddleOCR-VL-1.6-0.9B",
  "file_name": "sample.pdf", "file_type": "pdf", "page_count": 2,
  "settings": {"doc_orientation": false, "doc_unwarping": false},
  "elapsed_s": 1.25,
  "pages": [
    {"page_number": 1, "width": 1191, "height": 1684,
     "regions": [
       {"order": 3, "label": "paragraph_title", "bbox": [91, 321, 365, 365],
        "polygon": [[91.0, 321.0], [365.0, 321.0], [365.0, 365.0], [91.0, 365.0]],
        "content": "1. Bảng mã lỗi", "block_id": 2, "block_order": 3},
       {"order": 4, "label": "table", "bbox": [94, 389, 1096, 622], "polygon": [...],
        "content": "<table><tr><td>Mã lỗi</td>...</table>", "block_id": 3, "block_order": null}
     ],
     "markdown": "## HUÓNG DẪN XỬ LÝ LỒI TRỰC Z\n\n..."}
  ],
  "markdown": "<!-- page 1 -->\n\n## ...\n\n<!-- page 2 -->\n\n### 3. Ghi chú an toàn\n..."
}
```

- Pages are in document order; `page_number` starts at 1.
- `regions` are in reading order, and `order` is the position in that order.
  `block_order` is PaddleOCR's own numbering, which is `null` for tables,
  images and some other labels.
- `bbox` is `[x1, y1, x2, y2]` in pixels of the page image the pipeline used.
  PDF pages are rendered at about 144 dpi (an A4 page is 1191×1684). When
  preprocessing ran, the coordinates refer to the corrected page, and the page
  has `preprocessing: {orientation_angle, unwarped}`.
- Labels include `doc_title`, `paragraph_title`, `text`, `table` (HTML),
  `display_formula` (LaTeX), `image`, `figure_title`, `header`, `footer` and
  `number`.
- The page Markdown leaves out headers, footers, page numbers, footnotes and
  side text (PaddleOCR's `markdown_ignore_labels`); `regions` keeps them.
  The document Markdown joins the pages in order, each after `<!-- page N -->`.

## Tests

```sh
# API logic with a stub pipeline (no GPU, models or vLLM):
uv run python -m unittest discover -s deployments/ocr/tests -t deployments/ocr -v
# Running services:
bash deployments/ocr/smoke_test.sh [document.pdf]
```

Measured with the running services on synthetic Vietnamese A4 pages (text,
headings and a table), after the first request:

| Input | Time |
| --- | --- |
| 1-page PNG or JPEG | 1.0–1.4 s |
| 2-page PDF | 1.25 s |
| 10-page PDF | 3.3–3.9 s |
| Two 10-page PDFs sent together | 3.2 s and 6.8 s (the second waits) |

The first request after a start takes about 12 s while the models warm up.
On a page rotated by 90°, layout without preprocessing lost the title, and
`doc_orientation=true` detected 270° and recovered it. A 4° skew was read
correctly without preprocessing.

## Limitations

- Accuracy on real factory documents (scans, photos, handwriting, stamps) has
  not been evaluated.
- On the synthetic pages, Vietnamese diacritics were sometimes wrong, for
  example "HUÓNG DẪN XỬ LÝ LỒI TRỰC Z" for "HƯỚNG DẪN XỬ LÝ LỖI TRỤC Z" and
  "thay đổi bị đỡ" for "thay ổ bi đỡ". Review OCR text before treating it as
  evidence.
- Cross-page tables and paragraphs are not merged; each page stays separate.
- No authentication and no request timeout. Large PDFs keep the queue busy
  until they finish.
