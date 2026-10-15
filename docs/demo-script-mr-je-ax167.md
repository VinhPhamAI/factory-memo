# Kịch bản video demo Factory memo: case MR-JE-AX167 (v4)

| | |
|---|---|
| Thời lượng | khoảng 100 giây, 8 cảnh |
| Người xem | Ban giám khảo hackathon |
| Ngôn ngữ | Mọi chữ trên màn hình là tiếng Việt. Dữ liệu (hội thoại, tài liệu, nội dung card, trích dẫn) giữ tiếng Anh như dữ liệu gốc. Không có giọng đọc hay phụ đề: chữ trên màn hình chính là kịch bản |
| Giao diện | Cảnh 1–6 và 8 là cảnh dựng bằng hình động (HTML) trên nền xanh navy công nghệ. Riêng cảnh 7 (hỏi đáp) là đoạn quay màn hình app thật (`frontend/`) |
| Nguồn case | `data/case-mr-je-ax167.json`, `conversation-mr-je-ax167.txt`, manual MELSERVO-JE trang 71 |
| Dự án video | `videos/voice-agent-demo/` (HyperFrames). Quay màn hình: `videos/voice-agent-demo/scripts/record-flow.mjs` |

Thông điệp chính: Factory memo biến cuộc trao đổi và tài liệu sửa máy thành knowledge card có bằng chứng, được chuyên gia duyệt, để người sau hỏi và nhận câu trả lời kèm nguồn.

Các phần đánh dấu **[GIẢ LẬP]** không có trong dữ liệu gốc. STT, nhận diện người nói, OCR và bước duyệt là mô phỏng phía frontend (xem bảng cuối tài liệu).

---

## Dòng thời gian

| Cảnh | Thời gian | Nội dung trên màn hình |
|---|---|---|
| 1 Mở đầu | 0:00–0:06 | "Kinh nghiệm sửa máy thường chỉ nằm trong đầu một chuyên gia." → "Factory memo biến nó thành tri thức có bằng chứng." |
| 2 Tổng quan | 0:06–0:13 | 5 bước nối nhau: Ghi âm → Tài liệu (AI OCR) → Phân tích AI → Chuyên gia duyệt → Hỏi đáp. Chip "Case: MR-JE-AX167 · tiếng mài trục Z · Line 11" |
| 3 Bước 1 · Ghi âm | 0:13–0:27 | Hình động: hai công nhân đứng cạnh máy, người đang nói sáng lên kèm sóng âm; transcript hiện từng lượt (T01, T03, T04, T06, T09, T10) |
| 4 Bước 2 · Tài liệu + AI OCR | 0:27–0:38 | Hình động: 4 tài liệu xếp chồng; phiếu báo lỗi FR-L11-1007 được tia quét AI OCR đọc, văn bản trích xuất hiện từng dòng, nhận diện trường, lưu vào thư viện |
| 5 Bước 3 · Phân tích AI | 0:38–0:56 | Hình động: đầu vào chảy vào lõi AI; AI trả lời 7 câu hỏi cố định, mỗi câu kèm nguồn; đối chiếu 8/8 trích dẫn; các câu trả lời ghép thành knowledge card |
| 6 Bước 4 · Card + chuyên gia duyệt | 0:56–1:12 | Hình động: knowledge card 7 dòng; mở trích dẫn T10; chuyên gia tick checklist, bấm duyệt; card bay vào cơ sở dữ liệu tri thức |
| 7 Bước 5 · Hỏi đáp | 1:12–1:33 | Quay màn hình: câu hỏi tiếng Việt, câu trả lời tiếng Việt kèm [1][2][3]; bấm [1] mở card đã duyệt, bấm T10 mở đúng câu nói gốc |
| 8 Kết | 1:33–1:40 | "Factory memo" · "Giọng nói & tài liệu → Knowledge card đã duyệt → Câu trả lời có nguồn" · ghi chú "STT, OCR mô phỏng" |

## Chữ trên màn hình (kịch bản)

Bản đã duyệt ngày 2026-10-12. Bảng so sánh với bản cũ: `plans/261012-1734-demo-video-real-ui/reports/copy-proposal.md`. Nguyên tắc: văn phong tự nhiên, câu ngắn, không thuật ngữ chuyên ngành, không giả định người xem đã biết bối cảnh.

**Cảnh 1 · Mở đầu**
- Máy hỏng, thường chỉ người làm lâu năm biết cách sửa.
- Factory memo ghi lại cách họ sửa, để người khác dùng lại.

**Cảnh 2 · Tổng quan:** "Một lần sửa máy thật, qua 5 bước" · chip "Động cơ MR-JE-AX167 kêu rít · dây chuyền 11" · 5 bước: Ghi âm (Lời nói thành chữ), Giấy tờ (AI đọc bản scan), AI phân tích (Rút ra cách sửa), Chuyên gia duyệt (Xác nhận đúng), Hỏi đáp (Trả lời có nguồn) · nhóm: Đầu vào, AI xử lý, Kết quả.

**Cảnh 3 · Bước 1 · Ghi lại cuộc trao đổi**
1. Động cơ kêu rít. Kỹ thuật viên trẻ hỏi chuyên gia ngay tại máy.
2. Factory memo nghe và chép lại lời từng người.
3. Mỗi câu được đánh số. Câu đoán sai cũng được giữ lại.
4. Lời dặn an toàn được chép đúng từng chữ.

**Cảnh 4 · Bước 2 · Đọc giấy tờ liên quan**
1. Phiếu báo hỏng, biên bản họp, ghi chú chuyên gia, sổ tay máy.
2. AI đọc bản scan và chép lại từng dòng.
3. AI nhận ra tên máy, dây chuyền và ngày báo hỏng.

**Cảnh 5 · Bước 3 · AI rút ra cách sửa**
1. AI đọc cả bản ghi âm lẫn giấy tờ.
2. AI trả lời 7 câu: hỏng gì, vì sao, kiểm tra và xử lý thế nào…
3. Câu trả lời nào cũng ghi rõ lấy từ đâu.
4. AI dò lại với bản ghi gốc: 8 trên 8 câu khớp.
5. Kết quả là một thẻ kinh nghiệm (knowledge card), chờ chuyên gia duyệt.

**Cảnh 6 · Bước 4 · Chuyên gia duyệt**
1. Thẻ kinh nghiệm có 7 mục, mục nào cũng có nguồn.
2. Chuyên gia mở lại đúng câu mình đã nói để đối chiếu.
3. Kiểm tra xong, chuyên gia bấm duyệt.
4. Thẻ được lưu lại để cả nhà máy tra cứu.

**Cảnh 7 · Bước 5 · Hỏi và nhận câu trả lời** (quay app thật)
1. Vài tuần sau, ca đêm gặp lại tiếng kêu này.
2. Kỹ thuật viên gõ câu hỏi bằng tiếng Việt.
3. Factory memo chỉ tìm trong dữ liệu của đúng máy này.
4. Câu trả lời lấy từ thẻ đã duyệt và sổ tay của hãng.
5. Bấm vào số nguồn để xem đúng câu chuyên gia đã dặn.

**Cảnh 8 · Kết:** Factory memo · "Ghi lại kinh nghiệm sửa máy." · "Trả lời người đến sau, có dẫn nguồn." · ghi chú "Bản demo: phần ghi âm và đọc giấy tờ đang mô phỏng."

---

## Dữ liệu knowledge card (bước 3 và 4)

**Hình:** Knowledge card chiếm giữa màn hình. Bảy dòng hiện lần lượt từ trên xuống, mỗi dòng khoảng 3 giây. Bên phải mỗi dòng có chip nguồn. Khi một chip sáng, câu trích tương ứng hiện nhỏ bên cạnh.

Tiêu đề card:
```
MR-JE-AX167 servo motor · Case
Line 11 · Machining cell B · Failure mode: wear
Status: Verified (8/8 quotes) · Result recorded 2026-10-09
```

| # | Bước | Nội dung trên card | Nguồn |
|---|---|---|---|
| 1 | **Symptom** (Hiện tượng) | Grinding noise from the HG servo motor during Z-axis rapid traverse, about 3 times a shift since Monday. No alarm. Quiet when the axis is idle. | T01, T03, Fault report |
| 2 | **Context** (Bối cảnh) | MR-JE-AX167, Line 11, machining cell B. Motor from 2019, about 21,000 running hours. | T01, T03 |
| 3 | **Cause** (Nguyên nhân) | Worn motor bearing. On this cell, bearing noise on MR-JE-AX167 first shows up at 18,000–22,000 running hours. Encoder failure was considered but not confirmed. | T04, Expert note, F03–F04 |
| 4 | **Check** (Cách kiểm tra) | 1) Before removing the load, jog at 50 mm/min and listen with a stethoscope at the motor flange. 2) Disconnect the motor power cable, wait 15 minutes, then rotate the shaft by hand. Noise means a worn bearing. No noise means the machine needs maintenance. | T06, T10, T12, Manual p.71 |
| 5 | **Countermeasure** (Đối sách) | Tag the motor and send it to the Binh Duong workshop for replacement, not local repair. Do not change the gain to hide the noise. | T08, T14, Meeting minutes |
| 6 | **Result** (Kết quả) | Hand rotation confirmed the grinding. Motor sent to Binh Duong. The replacement motor ran two shifts with no noise. | F03, F05 [GIẢ LẬP] |
| 7 | **Applies when** (Điều kiện áp dụng) | MR-JE-AX167 on Line 11, machining cell B. Motor at 18,000–22,000 running hours. Noise only while the axis moves, with no alarm. Safety: never rotate the shaft while the power cable is connected. | T04, T10 |

Ở dòng 3, hiện thêm một khung nhỏ màu xám tên `Ruled out`:
```
✗ Encoder failure — not confirmed (T03 → T04)
✗ Adjust servo gain — rejected, hides the noise (T13 → T14)
```




---

## Dữ liệu hỏi đáp (bước 5)

Câu hỏi trong video là tiếng Việt: "MR-JE-AX167 trục Z kêu rít khi chạy nhanh, không có alarm. Do vòng bi hay do máy? Cần làm gì?". Backend thật trả lời theo ngôn ngữ của câu hỏi (`answer.md`), nên câu trả lời hiển thị là bản tiếng Việt của câu trả lời tiếng Anh dưới đây, cùng dữ kiện (`frontend/src/fixtures/ax167.ts`, `ANSWER_VI`).

**Hình:** Màn hình **Ask**. Góc trên ghi `Night shift · Technician junior_02` [GIẢ LẬP]. Câu hỏi được gõ từng chữ:

```
MR-JE-AX167 Z-axis is grinding on rapid traverse, no alarm.
Is it the bearing or the machine? What should I do?
```

Dòng trạng thái `Finding relevant sources…` hiện khoảng 1 giây, kèm chip `Scope: MR-JE-AX167`. Câu trả lời chạy ra:

> Check the motor bearing first, and don't adjust the gain to hide the noise [1]. Before removing the load, jog MR-JE-AX167 at 50 mm/min and listen with a stethoscope at the motor flange [1]. To confirm, disconnect the motor power cable and wait 15 minutes to release the brake safely, then turn the shaft by hand [1][2]. If it grinds, the bearing is worn: tag the motor and send it to the Binh Duong workshop for replacement, not local repair [1]. If it is quiet, maintain the machine instead [2]. Last time on this asset, hand rotation confirmed a worn bearing and the replacement motor ran two shifts with no noise [3].

Bảng **Sources** bên phải hiện ba thẻ:

| # | Loại | Tiêu đề | Đoạn trích hiển thị | Vị trí |
|---|---|---|---|---|
| [1] | Knowledge card | MR-JE-AX167 servo motor · Case | "Always disconnect the motor power cable and wait 15 minutes before rotating the shaft by hand to release the brake safely." | Turn T10 |
| [2] | Manual | MELSERVO-JE Troubleshooting Manual · 1.6 Trouble which does not trigger an alarm | "When a noise occurs, the bearing is at the end of its life. Replace the servo motor. When no noise occurs, maintain the machine." | Page 71 |
| [3] | Recording | Follow-up 2026-10-09 [GIẢ LẬP] | "Replacement motor installed, Z-axis rapid traverse ran two full shifts with no grinding noise." | Turn F05 |

Con trỏ bấm vào [1]. Card mở ra và dòng **Check** sáng lên. Bấm tiếp vào chip `T10` thì transcript gốc cuộn tới lượt T10, lượt này được tô vàng.




---

## Phụ lục: tài liệu mẫu [GIẢ LẬP]

Các tài liệu dưới đây được viết cho video. Mọi chi tiết kỹ thuật lấy từ hội thoại gốc. Chi tiết mới chỉ gồm ngày tháng, mã phiếu và kết quả follow-up.

### Phụ lục A — Fault report

```
FAULT REPORT            FR-L11-1007                     [SIMULATED]
Date reported   2026-10-07
Reported by     junior_01 (operator)
Asset           MR-JE-AX167 · HG servo motor, Z axis
Location        Line 11 · Machining cell B
Symptom         Grinding noise during Z-axis rapid traverse
Frequency       About 3 times per shift since Monday 2026-10-05
Alarm           None. Drive runs normally otherwise.
When            Only while the axis moves. Quiet when idle.
Motor age       Built 2019, about 21,000 running hours
Request         Senior technician review before any part is removed
```

### Phụ lục B — Meeting minutes

```
MEETING MINUTES        2026-10-08                       [SIMULATED]
Topic       Grinding noise, MR-JE-AX167, Line 11 machining cell B
Attendees   expert_01 (senior technician), junior_01 (operator)

Findings
- Motor is at about 21,000 h, inside the 18,000–22,000 h window in which
  bearing noise usually first appears on this asset.
- Encoder failure raised by the operator; not confirmed.
- Proposal to tune servo amplifier gain to suppress the noise: rejected.

Decisions / actions                                   Owner
1. Jog at 50 mm/min, stethoscope at motor flange,     junior_01
   before removing the load.
2. Disconnect motor power cable, wait 15 min,         junior_01
   rotate shaft by hand.
3. If the bearing is worn: tag the motor and send     junior_01
   to Binh Duong workshop for replacement.
   No local repair.
```

### Phụ lục C — Expert note

```
EXPERT NOTE — MR-JE servo motors, Line 11 machining cell B     [SIMULATED]
Author: expert_01 (senior technician)

- On this cell, bearing noise on MR-JE-AX167 usually first shows up between
  18,000 and 22,000 motor running hours.
- Bearing or machine? Jog at 50 mm/min before removing the load and listen
  at the motor flange with a stethoscope.
- Never rotate the shaft by hand while the motor is connected. Disconnect the
  motor power cable and wait 15 minutes first, to release the brake safely.
- Noise when turned by hand: bearing worn, replace the motor.
  No noise: the motor is fine, maintain the machine.
- Suspect bearing: tag the motor, send it to Binh Duong workshop for
  replacement. No local repair.
- Do not change servo gain to hide a noise.
```

### Phụ lục D — Follow-up recording (bổ sung trường Kết quả)

```
[F01] junior_01 (operator): Follow-up on MR-JE-AX167, Line 11, machining cell B.
      Jogged at 50 mm/min with the stethoscope on the motor flange. The
      grinding is clearly coming from the motor.
[F02] expert_01 (senior_technician): Did you check the shaft by hand?
[F03] junior_01 (operator): Yes. Disconnected the motor power cable, waited
      15 minutes, then turned the shaft by hand. It grinds when turned by hand.
[F04] expert_01 (senior_technician): Then the bearing's worn. Tag it and send
      it to the Binh Duong workshop.
[F05] junior_01 (operator): Done. Motor tagged and sent to Binh Duong.
      Replacement motor installed, Z-axis rapid traverse ran two full shifts
      with no grinding noise. Gain parameters untouched.
[F06] expert_01 (senior_technician): Good. Log it so the next person checks
      the bearing first.
```

---

## Ghi chú cho đội: video khác code backend ở đâu

Cảnh hỏi đáp quay frontend thật đang chạy; các cảnh còn lại là hình động. Frontend và video đều mô phỏng một số bước chưa có trong backend. Bảng này giúp đội trả lời nếu giám khảo hỏi phần nào đã chạy thật.

| Trong video | Frontend (`frontend/`) | Backend trên nhánh `feat/knowledge-base` |
|---|---|---|
| Ghi âm, chuyển giọng nói thành văn bản, nhận diện người nói | Mô phỏng: phát lại transcript thật T01–T14 theo từng lượt | Chưa có. Bản thử Whisper nằm ở nhánh `tronganh`, chưa gộp |
| AI OCR đọc tài liệu | Mô phỏng: quét trang và hiện văn bản từ Phụ lục A–C | Chưa có. Manual được chia chunk và index, không qua OCR hay LLM |
| AI trả lời 7 câu hỏi → knowledge card | Mô phỏng với dữ liệu từ `data/case-mr-je-ax167.json` | Đã có rút trích case/lesson, kiểm chứng trích dẫn, gắn phạm vi (`industrial_qa`). Backend ra 5 trường + scope; "Cách kiểm tra" và "Điều kiện áp dụng" là phần tách khi hiển thị |
| Kết quả từ buổi follow-up | Dữ liệu [GIẢ LẬP] Phụ lục D | Consolidate tạo case mới và liên kết `supports`/`contradicts`, không điền `outcome` vào case cũ |
| Chuyên gia duyệt → lưu CSDL | Mô phỏng: lưu trạng thái duyệt trong IndexedDB | Có trạng thái `draft`/`approved` trong `KnowledgeStatus`, chưa có API hay màn hình duyệt. Card nháp vẫn được dùng khi chat |
| Hỏi đáp có trích nguồn | Mô phỏng: câu trả lời viết sẵn | Đã có API `/v1/knowledge-agent/{kb}/stream`, citation `[K:uuid]`/`[D:doc_id]`. Frontend chưa nối với API |
