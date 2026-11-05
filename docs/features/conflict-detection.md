# Conflict detection

## Application knowledge bases

The API and worker use `APP_DATABASE_URL` for General-template KBs. The conflict
stage reads `knowledge_chunks` and stores each verdict in `knowledge_conflicts`
with its `ingestion_jobs` ID. The review API uses the same table. Chunk links and
review history commit with the corresponding verdict or decision.

General uses the shared listwise judge with pairwise confirmation, a
[General prompt](../../backend/knowledge_bases/general/prompts/conflict.md), and
`applies_to` and `condition` as ranking signals. The judge uses
`CONSOLIDATION_*` provider settings with `OPENROUTER_*` fallbacks for the model,
API key and base URL. Run `uv run python -m backend.worker --stage conflict`
from the repo root when running stage workers individually.
See [conflict review](knowledge-management.md#conflict-review) for the UI and
how to queue a check for existing application records.

## Industrial QA

> **Trạng thái:** giai đoạn 1 đã triển khai (2026-10-23): stage `conflict`, bảng
> `knowledge_conflicts`, judge `list` + xác nhận và `pair`, cấu hình, unit test
> bằng fake. Đã bổ sung API review/recheck và tab **Mâu thuẫn** trong **Quản lý
> tri thức**, có test frontend bằng API giả lập. Chưa có bộ đánh giá list/pair
> hoặc backfill. Chưa chạy
> với LLM thật; các ngưỡng và ước lượng chi phí dưới đây là giả định ban đầu,
> cần đo lại trên bộ đánh giá.

## 1. Mục tiêu và quyết định đã chốt

Khi một source vừa có kết quả Extract, một job nền tìm các knowledge item đã có
trong KB mà item mới có thể mâu thuẫn, rồi ghi nhận để người review xử lý. Không
so sánh brute-force toàn KB.

Quyết định đã chốt:

1. **Chỉ một nơi ghi conflict.** Khi stage `conflict` được bật, consolidation
   inline (`backend/src/services/knowledge_consolidation.py`,
   `detect_conflicts=False`) chỉ còn `add / merge / supersede` và link `supports`
   của case: prompt `PROMPT_NO_CONFLICT` không có action `conflict` và
   `contradicts`; nếu model vẫn trả `conflict` thì thành `add` (ghi
   `suggested: "conflict"` trong `revision.consolidation`), `contradicts` bị bỏ qua.
   Runner benchmark (`benchmark/ingest.py`) vẫn dùng consolidation cũ
   (`detect_conflicts=True`) và không chạy stage `conflict`.
2. **Không so case × case** ở giai đoạn 1.
3. **Link tự động nhưng vẫn là draft.** Chỉ phán quyết đã được xác nhận và đủ
   confidence mới ghi `conflict_with` / `contradicted_by`; không đổi status,
   không deprecate bên nào.
4. **Judge mặc định là listwise + xác nhận theo cặp** (mục 4). So từng cặp đầy đủ
   chỉ giữ làm baseline để đánh giá.

## 2. Hiện trạng trước giai đoạn 1

Consolidation chạy đồng bộ trong job Extract: mỗi record mới lấy top-5
lesson theo cosine ≥ 0.3 (không lọc scope), rồi một LLM call cho cả conversation
quyết định `add / merge / conflict / supersede` và `supports / contradicts`.
Hạn chế: ứng viên chỉ dựa trên vector, một prompt gánh mọi quyết định, lỗi LLM
biến mọi thứ thành `add`, không có bộ nhớ "cặp đã so", và conflict không hiển
thị ở API/UI quản lý tri thức.

## 3. Kiến trúc

```text
convert → extract ──(cùng transaction, next_stages)──► conflict
                                                      │
          records mới của evidence này ───────────────┤
          L1 lọc theo metadata (SQL, index)           │
          L2 chọn theo ngữ nghĩa + anchor, cap K      │  → ≤ K ứng viên/record
          L3 judge listwise (1 call/record mới)       │  → phán quyết cho từng ứng viên
          L4 xác nhận theo cặp (chỉ ca bị gắn)        │  → thường 0–2/source
          lưu knowledge_conflicts + link draft        ▼
```

- `STAGES = ("convert", "extract", "conflict")` trong `backend/src/storage/job_queue.py`;
  `SourcePipeline.run_conflict` (`backend/src/services/source_pipeline.py`) gọi
  `ConflictDetector.adetect` với knowledge store và `ConflictStore` của KB.
- KB bật stage qua `KnowledgeBaseSpec.conflict_factory` (spec → detector, hoặc
  `None` khi `[conflicts] enabled = false`); `industrial_qa` dựng detector từ
  `load_config().conflict_detector()`.
- `KnowledgeManagementService.extract` thêm `"conflict"` vào `next_stages` khi mode
  là `knowledge` và KB có detector (cả Auto upload/ghi âm). Đường voice capture cũ
  (extract trong API, không có `extract_mode`) gọi `on_ingested` →
  `queue_conflicts` sau khi lưu record; lỗi enqueue chỉ được log.
- Job dùng source của job (upload → `evidence_id` trong trạng thái xử lý), nạp
  record searchable có `evidence_id` đó. Extract bị skip (`duplicate`,
  `near_duplicate`) hoặc không có record → hoàn tất với `skipped`.
- Kết quả (đếm theo status, `llm_calls`, `truncated`, fingerprint) được ghi vào
  kết quả job và vào metadata `conflicts` của evidence. Quản lý tri thức trả job
  conflict riêng (`processing.conflict_job`) và tóm tắt (`processing.conflicts`);
  job conflict không đổi trạng thái xử lý của source.
- Lỗi request LLM (`ConflictRequestError`) được retry với backoff; dữ liệu sai
  định dạng được hỏi lại `validation_retries` lần rồi ghi `pending`.
- `backend.worker` thêm `--conflict N` / `WORKER_CONFLICT_PROCESSES`;
  heartbeat/retry/backoff dùng lại cơ chế có sẵn.
- Hai source extract đồng thời không sót cặp: mỗi job conflict chạy sau khi record
  của chính nó đã lưu, nên ít nhất một trong hai job thấy bên kia; cặp trùng bị
  loại bởi cache.

## 4. Thu hẹp không gian tìm kiếm

### L0. Điều kiện chung

- Chỉ record searchable (không deprecated, không superseded).
- Cùng isolation scope: không trộn record có `benchmark_run_id` với record thường.
- Bỏ record cùng `evidence_id`.
- Bỏ cặp đã có trong cache với cùng content hash và cùng `judge_fingerprint`
  (trừ `pending`); cặp người review đã quyết (`confirmed`, `dismissed`,
  `resolved`) không bao giờ được judge lại.
- Các điều kiện L0/L1 được kiểm lại bằng Python trên kết quả truy vấn
  (`ConflictDetector.comparable`).

### L1. Lọc metadata (SQL)

| Cặp | So không? |
|---|---|
| lesson × lesson | Có |
| case mới × lesson cũ, lesson mới × case cũ | Có (`case_exception` → `contradicted_by`) |
| case × case | Không (giai đoạn 1) |

**Scope chồng lấn:** với mỗi *hard key* (`machine_model` đã chuẩn hóa qua
catalog, `machine_model_specific`, `machine_id`, `component_model`,
`product_model`, `configuration`, `line_id`, `station`), hai giá trị phải bằng
nhau hoặc một bên null:

```sql
AND (k.context->>'machine_model' IS NULL OR :mm IS NULL OR k.context->>'machine_model' = :mm)
-- lặp cho từng hard key; machine_model, machine_id, machine_model_specific đã có index
```

Khác family hoặc khác `machine_id` là khác phạm vi, không phải mâu thuẫn.
`condition`, `failure_mode` là *soft key*, chỉ dùng xếp hạng. Hiệu lực không giao
nhau (`valid_to` của một bên < `valid_from` của bên kia) thì bỏ qua.

### L2. Chọn ứng viên trong vùng đã lọc

Gộp ba nguồn rồi giữ top-K (mặc định K = 5, tối đa 8):

- **Vector:** `similar_knowledge(..., filters=<L1>)`, `min_similarity` ≈ 0.5.
- **FTS** (`lexical_knowledge`, không gọi embedding) trên `search_vector` với
  query từ subject và anchor.
- **Anchor:** mã tham số (`PA01`, `Pr.`), mã alarm (`AL.16`), thiết bị PLC
  (`SD/SM/D…`), số kèm đơn vị. Regex ở `industrial_qa/conflicts.py`. Ứng viên có
  chung anchor với record mới được xếp trước.

Vector và FTS gộp bằng RRF, soft key cộng điểm nhỏ. Giới hạn cứng
`max_pairs_per_source` (mặc định 40), lấy lần lượt ứng viên tốt nhất của từng
record mới. Mọi cặp đều tính vào giới hạn, kể cả cặp đã cache, để job retry chọn
đúng tập cặp cũ. Vượt thì kết quả job ghi `truncated: true` và số cặp bị bỏ
(`dropped`).

## 5. Judge: listwise và so từng cặp

### So sánh hai chế độ

Ví dụ một source có 5 record mới, mỗi record còn K = 5 ứng viên:

| | `pair` (baseline) | `list` + xác nhận (mặc định) |
|---|---|---|
| Đơn vị một call | 1 record mới × 1 ứng viên | 1 record mới × ≤ K ứng viên |
| Số LLM call | Σ cặp = 25 | N_new + số ca bị gắn ≈ 5 + 0–2 |
| Token | record mới và hướng dẫn lặp K lần | gửi một lần mỗi record mới |
| Độ chính xác từng phán quyết | tập trung, ít nhiễu | rủi ro nhầm label, bỏ sót, thiên lệch vị trí |
| Precision của link được ghi | 1 lần judge | 2 lần (list rồi pair) phải đồng ý |
| Recall | tham chiếu | có thể thấp hơn, **cần đo** |
| Một call lỗi thì mất | 1 cặp | K cặp của một record |

Không gộp nhiều record mới × nhiều record cũ vào một prompt (cách consolidation
hiện tại): ánh xạ label dễ sai, khó biết cặp nào bị bỏ sót, và một lỗi làm hỏng
cả source.

### L3. Judge listwise (`judge_mode = "list"`)

Mỗi record mới một call. Đầu vào là record mới và ≤ K ứng viên (type, status,
scope, validity, claim, steps hoặc case_card rút gọn, vài evidence quote ngắn;
không có transcript gốc). Record là dữ liệu, không phải chỉ dẫn. Đầu ra (JSON
schema, validate bằng `llm.validated`):

```json
{"verdicts": [
  {"candidate": "E1",
   "relation": "contradiction | case_exception | scope_difference | refinement | equivalent | unrelated",
   "aspect": "value | procedure_order | cause | action | applicability | other",
   "overlap": "phạm vi chung hai record cùng nói tới",
   "quote_new": "...", "quote_old": "...", "confidence": 0.0, "reason": "..."}
]}
```

Giảm rủi ro của listwise:

1. **Nhầm label:** `quote_new` / `quote_old` phải là chuỗi con của đúng record
   mới / đúng ứng viên; sai thì phán quyết thành `needs_review`, không tạo link.
2. **Bỏ sót hoặc trả thiếu:** phải có đúng một phán quyết cho mỗi label được đưa
   vào; thiếu/thừa thì retry, hết retry thì ghi `pending` (không mặc định là
   "không mâu thuẫn").
3. **Thiên lệch vị trí, cố tìm mâu thuẫn:** xáo thứ tự ứng viên (seed ghi vào kết
   quả), prompt nói rõ `unrelated` / `scope_difference` là đáp án bình thường.
4. **Loãng chú ý:** K ≤ 8, rút gọn case_card, giới hạn số quote mỗi ứng viên.

Nguyên tắc trong `prompts/conflict.md` (thư mục KB): giả thuyết cạnh nguyên nhân đã xác
nhận chưa phải mâu thuẫn; giá trị khác dưới điều kiện khác là `scope_difference`;
thêm điều kiện là `refinement`; workaround cục bộ chỉ mâu thuẫn với quy định
chung khi cùng phạm vi.

### L4. Xác nhận theo cặp

Chỉ cho phán quyết `contradiction` / `case_exception` có
`confidence ≥ confirm_min_confidence`. Một call tập trung vào đúng cặp đó, cùng
schema (một phán quyết). Ghi link khi cả hai bước cùng gắn mâu thuẫn và
confidence của bước xác nhận ≥ `link_min_confidence`. Bất đồng thì giữ trong bảng
với `status = open`, `confirmed_by_pair = false`, không tạo link.

### Chế độ `pair` (baseline)

Mỗi cặp sau L2 một call, cùng prompt nguyên tắc và schema một phán quyết, không
có bước xác nhận. Dùng để đo recall/precision tham chiếu cho `list`.

## 6. Lưu trữ và vòng đời

Bảng `knowledge_conflicts` trong DB `kb_<name>`:

| Cột | Ý nghĩa |
|---|---|
| `a_id, b_id` | cặp record, sắp thứ tự (`a_id < b_id`) |
| `a_hash, b_hash` | hash subject + claim + type + context + validity; đổi thì cặp được so lại |
| `judge_fingerprint` | hash prompt + model + `judge_mode` (+ `confirm` ở chế độ list) |
| `new_id` | record mới dẫn tới phán quyết |
| `relation, aspect, confidence, reason, quotes` | phán quyết cuối (của bước xác nhận nếu có) |
| `list_verdict, pair_verdict` | phán quyết từng bước (label, seed; lỗi nếu thiếu) |
| `status` | máy: `no_conflict, open, pending, needs_review`; người review: `confirmed, dismissed, resolved` |
| `linked` | đã ghi link vào record hay chưa |
| `job_id, created_at, updated_at` | provenance |

Unique `(a_id, b_id, judge_fingerprint)`. Bảng vừa là cache (job retry không gọi
lại LLM cho cặp đã xong), vừa là hàng đợi review, vừa là dữ liệu đánh giá. Phán
quyết mới không ghi đè status của người review. Link được ghi trước khi lưu dòng
và chỉ thêm khi còn thiếu, nên job chết giữa chừng rồi chạy lại không tạo link
trùng.

Link ghi qua `KnowledgeLinkUpdate` kèm event trong `revision.history`; cả hai
record vẫn là draft. Kết quả của listwise phụ thuộc phần nào vào các ứng viên
cùng prompt, nên chỉ phán quyết đã qua L4 mới được coi là xác nhận bởi máy; xác
nhận của người review ghi riêng trong `status`.

### Cấu hình (`industrial_qa/config.toml`)

```toml
[conflicts]
enabled = true
judge_mode = "list"            # list | pair (baseline đánh giá)
candidates_per_record = 5      # K, tối đa 8
min_similarity = 0.5
max_pairs_per_source = 40
confirm = true                 # L4, chỉ áp dụng cho judge_mode = "list"
confirm_min_confidence = 0.5
link_min_confidence = 0.7
validation_retries = 1

[llm.conflict]
env_prefix = "CONSOLIDATION"
model = "deepseek-v4.1-flash"
max_tokens = 4096
```

`[conflicts]` có thể bỏ trống (dùng mặc định); `[llm.conflict]` và prompt
`conflict = "prompts/conflict.md"` là bắt buộc. Engine chung ở
`backend/src/services/knowledge_conflicts.py`, bảng ở
`backend/src/storage/knowledge_conflicts.py`; KB khai báo policy (hard/soft key,
regex anchor trong `industrial_qa/conflicts.py`, prompt `prompts/conflict.md`) qua
`ConflictPolicy` và `KnowledgeBaseSpec.conflict_factory`, để KB `general` có thể
dùng sau với policy khác. Fingerprint và cấu hình đã resolve được ghi vào kết
quả job; `[conflicts]` cũng nằm trong provenance của cấu hình.

## 7. Đánh giá list và pair

Benchmark hiện tại không có nhãn conflict. Tạo bộ riêng từ lesson có sẵn (không
lấy từ gold QA):

- **Mâu thuẫn cài sẵn:** đổi giá trị/đơn vị, đảo thứ tự bước, đổi nguyên nhân.
- **Hard negative:** cùng claim nhưng khác family/condition, hoặc chỉ refinement.

Tách tập tuning và tập kiểm tra; chốt prompt và ngưỡng trên tập tuning trước khi
chạy tập kiểm tra. Đo:

| Chỉ số | Ý nghĩa |
|---|---|
| Blocking recall | cặp mâu thuẫn có lọt qua L0–L2 tới judge không (chung cho hai chế độ) |
| Judge recall / precision | trên các cặp đã tới judge, theo `list`, `list+confirm`, `pair` |
| Link precision | trong các link được ghi, bao nhiêu là mâu thuẫn thật |
| Chi phí | số call, token, độ trễ mỗi source |

Tiêu chí giữ `list` làm mặc định (đặt trước khi chạy tập kiểm tra): recall của
`list+confirm` không thấp hơn `pair` quá một ngưỡng chọn trước, với số call giảm
rõ. Nếu không đạt thì chuyển mặc định sang `pair`.

## 8. Phân giai đoạn

1. **Giai đoạn 1 (đã triển khai, `backend/tests/test_knowledge_conflicts.py`):** stage `conflict`, bảng `knowledge_conflicts`, L0–L2, judge
   `list` + xác nhận, chế độ `pair`, config; bỏ conflict khỏi consolidation inline
   khi stage bật; unit test bằng fake (lọc scope, giới hạn cặp, cache, idempotency
   khi retry, quote sai label, phán quyết thiếu → `pending`, bất đồng list/pair).
2. **Giai đoạn 2 (review đã triển khai):** API
   `GET /v1/knowledge-management/{kb}/conflicts`, POST review và recheck;
   tab **Mâu thuẫn** trong **Quản lý tri thức** hiển thị hai claim, quote,
   scope, lý do, trạng thái, lịch sử, lọc và phân trang. Có thao tác xác nhận /
   bỏ qua / chọn bản ghi giữ lại để supersede; bản ghi stale cần kiểm tra lại
   trước khi duyệt. Xem [hướng dẫn review](knowledge-management.md#conflict-review).
   Bộ đánh giá và so sánh list/pair vẫn chưa triển khai.
3. **Giai đoạn 3:** backfill KB cũ (cùng blocking), so lại khi record được sửa,
   cảnh báo trong câu trả lời khi trích dẫn record có conflict mở, cân nhắc case × case.
