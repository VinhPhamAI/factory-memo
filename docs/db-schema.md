# Schema PostgreSQL hiện tại

Mô tả theo code hiện tại (`backend/src/storage/`, `backend/src/agents/rag/store.py`).
Bảng được tạo tự động khi khởi động (`CREATE TABLE IF NOT EXISTS`), không có migration riêng.

## Tổng quan

Có hai lớp database:

- **DB dùng chung** (`DATABASE_URL`): danh sách KB và hàng đợi job.
- **Một DB cho mỗi KB** (`kb_<name>`, ví dụ `kb_industrial_qa`): nguồn, tri thức, chunk tài liệu.

Hai lớp khác database nên không có khóa ngoại giữa chúng.

```
raw_conversations ──< knowledge_items          (evidence_id)
        │        ──< extraction_runs           (evidence_id)
        │        ──1 content_signatures        (evidence_id)
        └ (metadata) chunks_<label>            (metadata.evidence_id)
knowledge_items   ──< knowledge_conflicts      (a_id, b_id)
```

## DB dùng chung

| Bảng | Cột chính | Vai trò |
|---|---|---|
| `knowledge_base_registry` | `name`, `title`, `description`, `template`, `created_at` | Danh sách KB. KB tạo từ UI dùng template `general`. |
| `ingestion_jobs` | `kb`, `stage` (convert/extract/conflict), `source_kind`, `source_id`, `mode`, `next_stages`, `status` (queued/running/done/failed), `attempts`, `locked_by`, `heartbeat_at`, `options`, `result`, `error` | Hàng đợi job cho worker. Mỗi nguồn chỉ có một job đang chạy. |

## DB của mỗi KB

| Bảng | Cột chính | Vai trò |
|---|---|---|
| `raw_conversations` | `id`, `transcript`, `stated_by`, `metadata`, `status` (pending/processed/failed), `error` | **Bằng chứng gốc**: transcript thu giọng nói, text dán vào, và cả văn bản convert từ tài liệu. Chi tiết nằm trong `metadata` (`source_kind`, `speakers`, `turns`, `audio`, `upload_id`, `management`…). |
| `knowledge_items` | `id`, `subject`, `claim`, `type`, `context`, `embedding`, `search_vector`, `evidence_id`, `stated_by`, `status`, `valid_from/to`, `supported_by`, `contradicted_by`, `conflict_with`, `superseded_by`, `revision` | **Tri thức có cấu trúc**, tìm kiếm bằng vector và full-text. |
| `extraction_runs` | `evidence_id`, `content_sha256`, `mode`, `fingerprint`, `settings`, `status`, `job_id`, `result` | Mỗi lần trích xuất một nguồn. Chặn trích xuất trùng nội dung. |
| `content_signatures` | `evidence_id`, `bands`, `shingle_count` | Chữ ký MinHash để cảnh báo nguồn gần trùng. |
| `knowledge_conflicts` | `a_id`, `b_id`, `relation`, `aspect`, `confidence`, `reason`, `quotes`, `status`, `linked`, `review` | Cặp tri thức nghi mâu thuẫn và quyết định của người review. |
| `chunks_<label>` | `id`, `doc_id`, `chunk_index`, `title`, `content`, `metadata`, `embedding`, `search_vector` | Chunk tài liệu/manual cho RAG. Trạng thái review nằm trong `metadata.review_status`. |
| `chunks_<label>_terms` | `lexeme`, `chunk_id`, `tf` | Từ khóa cho chấm điểm BM25. |
| `rag_indexes` | `table_name`, `config` | Cấu hình chunking/embedding của mỗi bảng chunk. |

### Chi tiết `knowledge_items`

- `type`: `fact`, `rule`, `case`, `procedure`, `heuristic`.
- `status`: `draft` (mới, vẫn tìm được) → `approved`, hoặc `deprecated` (giữ lại để trích dẫn, không tìm nữa).
- `context`: phạm vi áp dụng (`machine_model`, `machine_id`, `component_model`, `line_id`, `station`, `failure_mode`…), cùng `case_card`, `steps`, `source_turns`.
- `revision`: lịch sử review, verify, consolidation (`history`, `verify`, `consolidation`).
- `superseded_by`: bản ghi thay thế; khi sửa thì tạo bản ghi mới, không ghi đè.

## Dữ liệu chưa nằm trong DB

| Dữ liệu | Nơi lưu hiện tại |
|---|---|
| File upload và trạng thái convert | Đĩa: `data/uploads/<kb>/<id>/` (`content`, `metadata.json`, `processing.json`, `converted.txt`) |
| Audio thu âm | File WAV trên đĩa, đường dẫn trong `raw_conversations.metadata.audio` |
| Catalog thiết bị, ontology | YAML: `backend/knowledge_bases/industrial_qa/catalog/` |
| Lịch sử chat với agent | RAM của API, theo `thread_id`; mất khi restart |
| Người dùng, phân quyền | Chưa có; người review ghi cứng là `"knowledge management UI (unauthenticated)"` |
