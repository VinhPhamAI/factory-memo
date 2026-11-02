# Đề xuất thay đổi schema PostgreSQL

Trạng thái: **đề xuất**, chưa triển khai. Schema hiện tại: [db-schema.md](db-schema.md).

## Mục tiêu

- Tách hai luồng có chuyên gia tham gia:
  - **Luồng A – Xử lý sự cố:** người vận hành hỏi agent, gửi inquiry, chuyên gia xác thực giải pháp.
  - **Luồng B – Trích xuất và xác thực tri thức:** nguồn → trích xuất → bản ghi draft → chuyên gia duyệt.
- Đưa file, trạng thái xử lý file và catalog thiết bị vào Postgres.
- Có người dùng và phân quyền.

Nguyên tắc: chỉ thêm bảng khi cần, dùng lại bảng và cột đang có.

## Tóm tắt thay đổi

| Bảng | Thay đổi | Phục vụ |
|---|---|---|
| `app_users` | **mới** | Đăng nhập, vai trò, thiết bị phụ trách |
| `sources` | **đổi tên** từ `raw_conversations`, thêm cột | Mọi nguồn tri thức (luồng B) |
| `files` | **mới** | File gốc, audio, ảnh đính kèm, ảnh thiết bị |
| `catalog_entries` | **mới** | Catalog thiết bị và từ vựng chuẩn |
| `chat_messages` | **mới** | Lịch sử hỏi đáp với agent (luồng A) |
| `inquiries` | **mới** | Yêu cầu chuyên gia xác thực giải pháp (luồng A) |
| Các bảng còn lại | giữ nguyên | |

Tất cả bảng nằm trong DB của từng KB (`kb_<name>`), nên các cột user (`submitted_by`, `uploaded_by`, `user_id`, `requested_by`, `assigned_to`) có khóa ngoại tới `app_users`.

## Bảng mới và thay đổi

### `app_users`

| Cột | Ý nghĩa |
|---|---|
| `id`, `username`, `display_name` | |
| `password_hash` hoặc `sso_subject` | Đăng nhập |
| `role` | `engineer` (gồm người vận hành), `expert`, `admin` |
| `scopes` (jsonb) | Thiết bị/lĩnh vực phụ trách, ví dụ `[{"machine_model":"MR-JE"}]` |
| `is_active`, `created_at` | |

`scopes` dùng cùng khóa với `knowledge_items.context`, để route inquiry, phân draft cho chuyên gia và giới hạn quyền.

### `sources` (đổi tên từ `raw_conversations`)

Tại sao đổi tên thay vì tạo bảng tài liệu mới:

- Bảng đã chứa cả tài liệu, chỉ có tên là sai.
- `knowledge_items`, `extraction_runs`, `content_signatures` đều trỏ khóa ngoại vào bảng này. Tách hai bảng thì phải nhân đôi logic chặn trùng, trích xuất và trích dẫn.
- Đổi tên giữ nguyên khóa ngoại và index. Cột `evidence_id` ở bảng khác và trong API giữ nguyên tên.

| Cột | Lấy từ | Ý nghĩa |
|---|---|---|
| `id` | giữ | |
| `kind` | `metadata.source_kind` | `conversation`, `text`, `document`, `image`, `audio`, `inquiry` |
| `title` | `metadata.name` | |
| `text` | đổi tên từ `transcript` | Văn bản để trích xuất. NULL khi file chưa convert, ghi rồi thì không sửa |
| `text_sha256` | `metadata.transcript_sha256` | Chặn trùng |
| `file_id` | `metadata.upload_id` | File gốc (→ `files`) |
| `stated_by` | giữ | Người nói / tác giả |
| `submitted_by` | mới | User nộp nguồn |
| `status` | thay `pending/processed/failed` | `received` → `ready` → `processing` → `processed` / `skipped` / `failed` |
| `processing` (jsonb) | gộp `processing.json`, `metadata.management`, cột `error` | Chi tiết convert và extract, lỗi |
| `metadata` (jsonb) | giữ, thu gọn | Phần riêng theo loại: `speakers`, `turns`, `cleaning`… |
| `created_at`, `updated_at` | | |

Thay đổi trong luồng: tạo dòng `sources` ngay khi upload (`received`); job convert ghi `text` rồi chuyển sang `ready`. Job luôn trỏ tới `sources.id`.

### `files`

| Cột | Ý nghĩa |
|---|---|
| `id`, `filename`, `content_type`, `size_bytes`, `sha256` | |
| `storage_key` | Nội dung file vẫn trên volume/object storage |
| `uploaded_by`, `created_at` | |

Dùng chung cho file nguồn, WAV thu âm, ảnh/voice gửi trong chat, đính kèm inquiry, ảnh/sơ đồ/3D thiết bị. Trạng thái xử lý không lưu ở đây mà ở `sources` hoặc `chat_messages`.

### `catalog_entries`

Mỗi dòng là một **giá trị chuẩn của một khóa scope**, là các khóa dùng trong `knowledge_items.context`. Bảng thay cho `catalog_types.yaml`, `catalog_assets.yaml` và phần giá trị của `ontology.yaml`.

| Cột | Ý nghĩa |
|---|---|
| `key` | `machine_model`, `component_model`, `machine_id`, `site`, `line_id`, `station`, `failure_mode` |
| `value` | Cách viết chuẩn. Khóa chính `(key, value)` |
| `aliases` (text[]) | Cách viết khác |
| `description` | |
| `implies` (jsonb) | Khóa suy ra được, ví dụ `FX5-PLC138` → `{machine_model: FX5, line_id: Line 6}` |
| `attributes` (jsonb) | Loại thiết bị, tiền tố mã, manual, ảnh/sơ đồ/3D (id trong `files`), thông số |
| `origin` | `curated` (nhập tay) hoặc `generated` (sinh từ hội thoại) |
| `created_at`, `updated_at` | |

- Policy vẫn nằm trong code: có những khóa nào, khóa nào có danh sách đóng, mô tả dùng trong prompt.
- YAML giữ lại làm seed cho benchmark; lần chạy benchmark ghi hash catalog vào manifest.

### `chat_messages` (luồng A)

| Cột | Ý nghĩa |
|---|---|
| `id`, `thread_id`, `user_id` | Thread = nhóm tin nhắn cùng `thread_id` |
| `role` | `user` / `assistant` |
| `content` | Câu hỏi (giọng nói thì là transcript) hoặc câu trả lời |
| `attachments` (jsonb) | `[{file_id, kind, text}]`, `text` là kết quả STT hoặc mô tả ảnh của VLM |
| `scope` (jsonb) | Thiết bị, loại lỗi của lượt hỏi |
| `citations` (jsonb) | Bản chụp bằng chứng lúc trả lời: tri thức (id, claim, status), đoạn tài liệu, mâu thuẫn |
| `created_at` | |

Lưu bản chụp vì tri thức có thể bị sửa sau đó, mà chuyên gia cần thấy đúng những gì AI đã dùng. Không lưu trace thô hay reasoning của model.

### `inquiries` (luồng A)

| Cột | Ý nghĩa |
|---|---|
| `id` | |
| `thread_id`, `message_id` | Hội thoại và câu trả lời AI cần xác thực |
| `problem` | Mô tả sự cố của người vận hành |
| `scope` (jsonb) | Thiết bị, loại lỗi; dùng để route và lọc |
| `attachments` (uuid[]) | File đính kèm |
| `requested_by`, `assigned_to` | User gửi và chuyên gia phụ trách |
| `status` | `open` → `in_review` → `needs_info` → `answered` → `closed` |
| `response` (jsonb) | Phản hồi của chuyên gia: `decision` (`approved`/`revised`/`rejected`), giải pháp đã xác thực, ghi chú, người và thời điểm |
| `outcome` (jsonb) | Người vận hành báo kết quả sau khi xử lý: đã khắc phục chưa, ghi chú |
| `source_id` | Nguồn tạo ra khi đưa inquiry vào KB (→ `sources`) |
| `activity` (jsonb[]) | Lịch sử trao đổi, giao việc, sửa phản hồi, quyết định |
| `created_at`, `updated_at` | |

Gói thông tin gửi chuyên gia lấy thẳng từ `chat_messages`, không chép lại:

- **Problem context:** `problem`, `scope` + thông tin thiết bị từ `catalog_entries` + các tin nhắn trong thread.
- **Retrieved information:** `citations` của câu trả lời.
- **Proposed solution:** `content` của câu trả lời.

## Luồng B: không thêm bảng

- Đóng góp kinh nghiệm: tạo dòng `sources` có `submitted_by`.
- Duyệt tri thức: `knowledge_items.status` + `revision.history`, ghi `actor` là id user.
- Sửa tri thức: tạo bản ghi mới, bản cũ có `superseded_by`.
- Phân việc: chuyên gia thấy draft có `context` khớp `scopes` của mình.
- Mâu thuẫn: giữ `knowledge_conflicts`, `review` ghi `actor` là id user.

## Nối hai luồng

Inquiry đã trả lời có thể đưa vào KB: tạo một dòng `sources` (`kind = inquiry`), trích xuất như mọi nguồn khác, ra bản ghi **draft**, chuyên gia duyệt ở luồng B.

Không tạo thẳng bản ghi `approved` từ inquiry: chuyên gia mới duyệt giải pháp cho một sự cố, chưa duyệt tri thức dùng chung. Nếu tạo thẳng, cách xử lý tạm tại chỗ dễ bị biến thành chỉ dẫn chung.

## Tác động khi triển khai

- Migration:
  - Đổi tên `raw_conversations` → `sources` và lấy dữ liệu cho cột mới từ `metadata`.
  - Nhập `metadata.json`, `processing.json` vào `files`, `sources`.
  - Nạp YAML catalog vào `catalog_entries`.
- Code cần sửa: `knowledge_store.py`, `source_pipeline.py`, `document_uploads.py`, `api/voice_capture.py`, bộ nạp catalog/ontology, `snapshot.py` (tên bảng khi export/restore snapshot), các test liên quan.
- Không đổi: tên cột `evidence_id`, định dạng citation `[K:]`/`[D:]`, endpoint và stream event hiện có.
