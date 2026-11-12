# Báo cáo xử lý bất thường ngày 10/11

Tài liệu này là dữ liệu tổng hợp phục vụ demo trích xuất tri thức chuyên gia. Các nguyên nhân, biện pháp và kết quả được trình bày dưới dạng kết luận đã xác nhận trong phạm vi từng thiết bị; nội dung không thay thế quy trình vận hành hoặc bảo dưỡng đã được ban hành.

## Tổng quan

### K110041: Cửa shutter mở chậm, giật

- **Vị trí:** DC3
- **Thiết bị:** VEH 0019 (Thổi khí No.1)
- **Thời gian:** 07:45–16:45 (9,0 h)
- **Tình trạng:** Hoàn thành

### K110043: NG dòng điện

- **Vị trí:** CVTF2
- **Thiết bị:** VIM 0200 (Máy kiểm tra tính năng No.6)
- **Thời gian:** 08:10–10:10 (2,0 h)
- **Tình trạng:** Hoàn thành

### K110044: Không bật được High Power sau sụt áp

- **Vị trí:** LINE2
- **Thiết bị:** Robot SCARA Omron i4L (trạm gắp đặt Line 2)
- **Thời gian:** 07:40–09:10 (1,5 h)
- **Tình trạng:** Hoàn thành

### K110045: Sập master ON

- **Vị trí:** SL6
- **Thiết bị:** VALA 0001 (Máy gia công MC20 số 1)
- **Thời gian:** 10:40–16:40 (6,0 h)
- **Tình trạng:** Hoàn thành

### K110046: Lỗi 2117, turret không lùi

- **Vị trí:** VCT4
- **Thiết bị:** VLA 0029 (Máy gia công bề mặt Rô-to A-10, A1)
- **Thời gian:** 12:00–16:00 (4,0 h)
- **Tình trạng:** Hoàn thành

### K110047: Nước làm mát quá nhiệt

- **Vị trí:** VCT4
- **Thiết bị:** VSC 0009 (Máy rửa Rô-to No.4)
- **Thời gian:** 09:13–15:13 (6,0 h)
- **Tình trạng:** Hoàn thành

### K110048: Không nhận khóa liên động cửa

- **Vị trí:** LINE3
- **Thiết bị:** Robot SCARA Omron i4L (cell lắp ráp C)
- **Thời gian:** 13:20–15:20 (2,0 h)
- **Tình trạng:** Hoàn thành

### K110049: Lỗi -924, đèn High Power không nháy

- **Vị trí:** PACK2
- **Thiết bị:** Robot SCARA Omron i4L (cell xếp pallet)
- **Thời gian:** 19:10–22:10 (3,0 h)
- **Tình trạng:** Hoàn thành

## K110041: Cửa shutter mở chậm, giật cục, VEH 0019

### Thông tin chung

- **Số phiếu:** K110041
- **Vị trí:** DC3
- **Thiết bị:** VEH 0019 (Thổi khí No.1)
- **Loại công trình:** PM
- **Mức độ ưu tiên:** Bình thường
- **Tên công việc:** Xử lý bất thường cửa shutter di chuyển mở chậm
- **Nhóm bảo dưỡng:** Cơ khí
- **Tình trạng:** Hoàn thành

### Thời gian

- **Bắt đầu:** 11-11-2026 07:45
- **Hoàn thành:** 11-11-2026 16:45
- **Thời gian bảo dưỡng:** 9,0 h

### Phân tích hư hỏng

- **Hiện trạng:** Tuần tra phát hiện cửa shutter di chuyển mở với tốc độ chậm và bị giật cục, có lúc dừng giữa hành trình. Dầu bôi trơn thanh dẫn đã khô. Không có tiếng kêu bất thường từ động cơ. Cảm biến vị trí mở và đóng đều tác động đúng.
- **Vùng thao tác:** Cửa shutter, thanh dẫn
- **Linh kiện đồng bộ:** Thanh dẫn shutter, động cơ truyền động
- **Hiện tượng:** Chuyển động giật, tốc độ chậm
- **Biện pháp xử lý:** Vệ sinh, cấp dầu
- **Nguyên nhân:** Dầu bôi trơn thanh dẫn khô, ma sát tăng.
- **Nguyên nhân gốc:** Chu kỳ bảo trì định kỳ đã bỏ sót hạng mục tra dầu thanh dẫn shutter.
- **Tại sao 1W:** Ma sát giữa shutter và thanh dẫn tăng nên cửa di chuyển chậm và giật.
- **Tại sao 2W:** Lớp dầu trên thanh dẫn đã khô.
- **Tại sao 3W:** Kế hoạch bảo trì định kỳ không có hạng mục tra dầu thanh dẫn shutter.
- **Ý kiến chỉ đạo:** Bổ sung việc vệ sinh và tra dầu thanh dẫn vào lịch bảo trì hằng tuần; kiểm tra tốc độ động cơ trong lần bảo trì kế tiếp.

### Công việc

#### Thanh dẫn shutter

- **Nội dung xử lý:** Lau sạch thanh dẫn, tra dầu
- **Kết quả:** Thanh dẫn sạch, không có vết xước đáng kể
- **Tình trạng:** Đã thực hiện

#### Cảm biến vị trí mở/đóng

- **Nội dung xử lý:** Kiểm tra tác động của cảm biến
- **Kết quả:** Tác động đúng ở cả hai vị trí
- **Tình trạng:** Đã thực hiện

#### Chạy thử

- **Nội dung xử lý:** Cho shutter mở đóng 10 lần
- **Kết quả:** Chuyển động trơn hơn rõ rệt, không dừng giữa hành trình
- **Tình trạng:** Đã thực hiện

### Kết quả và phê duyệt

- **Kết quả xác nhận:** Chạy thử 10 lần ngay sau xử lý và kiểm tra lại sau 1 tuần; shutter vận hành trơn, không giật và không dừng giữa hành trình.
- **Phạm vi áp dụng:** Shutter VEH 0019 No.1. Tốc độ động cơ đã được kiểm tra và nằm trong giới hạn vận hành.
- **Tài liệu đính kèm:** `k110041-guide-rail.jpg` (thanh dẫn trước khi tra dầu)
- **Ghi chú:** Hạng mục vệ sinh và tra dầu thanh dẫn đã được bổ sung vào lịch bảo trì hằng tuần.
- **Phê duyệt:** TL: đã duyệt. SV: đã duyệt. MGR: đã duyệt.

## K110043: NG dòng điện, VIM 0200 No.6

### Thông tin chung

- **Số phiếu:** K110043
- **Vị trí:** CVTF2
- **Thiết bị:** VIM 0200 (Máy kiểm tra tính năng No.6)
- **Loại công trình:** BM
- **Mức độ ưu tiên:** Cao
- **Tên công việc:** Xử lý bất thường NG dòng điện
- **Nhóm bảo dưỡng:** Điện – điều khiển
- **Tình trạng:** Hoàn thành

### Thời gian

- **Bắt đầu:** 11-11-2026 08:10
- **Hoàn thành:** 11-11-2026 10:10
- **Thời gian bảo dưỡng:** 2,0 h

### Phân tích hư hỏng

- **Hiện trạng:** Máy báo NG dòng điện liên tục từ đầu ca, sản phẩm bị loại hàng loạt dù đã đổi lot. Giá trị dòng đo dao động mạnh giữa các lần kiểm. Khi rung nhẹ dây tín hiệu thì giá trị thay đổi. Máy No.5 cùng lot vẫn đạt. Ngưỡng dòng cài đặt không đổi. Kiểm tra bằng sản phẩm mẫu chuẩn vẫn NG.
- **Vùng thao tác:** Đầu kiểm tra sản phẩm
- **Linh kiện đồng bộ:** Connector kiểm tra sản phẩm, dây tín hiệu
- **Hiện tượng:** Giá trị đo không ổn định
- **Biện pháp xử lý:** Thay thế
- **Nguyên nhân:** Chân connector kiểm tra tiếp xúc kém sau khi hết tần suất sử dụng.
- **Nguyên nhân gốc:** Connector đã đạt 25.000 lần cắm nhưng không được thay theo chu kỳ do số lần sử dụng không được ghi nhận trong kế hoạch bảo trì.
- **Tại sao 1W:** Tiếp xúc giữa connector và sản phẩm không ổn định nên giá trị dòng đo dao động.
- **Tại sao 2W:** Connector đã quá tuổi thọ số lần cắm.
- **Tại sao 3W:** Số lần cắm của connector không được theo dõi để thay trước khi hỏng.
- **Ý kiến chỉ đạo:** Ghi nhận số lần cắm trên từng máy VIM và thay connector khi đạt giới hạn 25.000 lần.

### Công việc

#### Ngưỡng dòng và sản phẩm mẫu

- **Nội dung xử lý:** Kiểm tra lại cài đặt ngưỡng, thử bằng sản phẩm mẫu chuẩn
- **Kết quả:** Ngưỡng đúng; sản phẩm mẫu vẫn NG
- **Tình trạng:** Đã thực hiện

#### Dây tín hiệu

- **Nội dung xử lý:** Rung nhẹ dây khi đang đo
- **Kết quả:** Giá trị đo thay đổi theo rung
- **Tình trạng:** Đã thực hiện

#### Connector kiểm tra

- **Nội dung xử lý:** Thay connector dự phòng
- **Kết quả:** Hết lỗi NG dòng điện
- **Tình trạng:** Đã thực hiện

### Linh kiện thay thế

#### Connector kiểm tra VIM 0200 (dự phòng)

- **Số lượng:** 1
- **Tình trạng linh kiện cũ:** Chân tiếp xúc mòn, đã sử dụng khoảng 25.000 lần cắm

### Kết quả và phê duyệt

- **Kết quả xác nhận:** Chạy 20 sản phẩm liên tiếp, tỷ lệ NG trở về bình thường.
- **Phạm vi áp dụng:** VIM 0200 No.6 và các máy sử dụng cùng mã connector, cùng giới hạn 25.000 lần cắm.
- **Tài liệu đính kèm:** `k110043-connector-old.jpg` (chân connector mòn)
- **Ghi chú:** Số lần cắm 25.000 đã được đối chiếu giữa bộ đếm của máy, nhật ký bảo trì và thông số của connector. Bộ đếm số lần cắm đã được bổ sung vào biểu mẫu bảo trì VIM.
- **Phê duyệt:** TL: đã duyệt. SV: đã duyệt. MGR: đã duyệt.

## K110044: Không bật được High Power sau sụt áp, robot i4L Line 2

### Thông tin chung

- **Số phiếu:** K110044
- **Vị trí:** LINE2
- **Thiết bị:** Robot SCARA Omron i4L (trạm gắp đặt Line 2)
- **Loại công trình:** BM
- **Mức độ ưu tiên:** Khẩn cấp
- **Tên công việc:** Xử lý robot không bật được High Power sau sụt áp
- **Nhóm bảo dưỡng:** Điện – điều khiển
- **Tình trạng:** Hoàn thành

### Thời gian

- **Bắt đầu:** 11-11-2026 07:40
- **Hoàn thành:** 11-11-2026 09:10
- **Thời gian bảo dưỡng:** 1,5 h

### Phân tích hư hỏng

- **Hiện trạng:** Sau lần sụt áp đầu ca, robot khởi động bình thường nhưng không chuyển động. Đèn Control Power sáng, đèn High Power không lên khi bấm nút bật. Bộ điều khiển khởi động xong, không báo mã lỗi. Khởi động lại bộ điều khiển một lần, kết quả không đổi. Dây chuyền dừng tại trạm này.
- **Vùng thao tác:** Mạch cho phép (enable) High Power
- **Linh kiện đồng bộ:** Giắc cầu nối XSYSTEM, công tắc cửa an toàn, contactor High Power
- **Hiện tượng:** Control Power có, High Power không lên
- **Biện pháp xử lý:** Rút, kiểm tra và cắm lại chắc chắn giắc cầu nối XSYSTEM
- **Nguyên nhân:** Giắc cầu nối XSYSTEM của mạch cho phép High Power lỏng nên mạch hở.
- **Nguyên nhân gốc:** Giắc XSYSTEM tự làm bị lỏng sau nhiều chu kỳ nóng – nguội tại trạm Line 2.
- **Tại sao 1W:** High Power không được cấp vì mạch cho phép đang hở.
- **Tại sao 2W:** Giắc XSYSTEM tiếp xúc không chắc sau sụt áp.
- **Tại sao 3W:** Giắc XSYSTEM tự làm mất độ chắc chắn sau nhiều chu kỳ nóng – nguội.
- **Ý kiến chỉ đạo:** Kiểm tra theo thứ tự từ dễ đến khó: cắm lại giắc XSYSTEM, kiểm tra công tắc cửa an toàn, đo cuộn hút contactor High Power, rồi mới xét servo amplifier. Dừng ở bước đầu tiên khắc phục được lỗi.

### Công việc

Các bước đi từ dễ đến khó và dừng ở bước đầu tiên khắc phục được lỗi.

#### An toàn

- **Nội dung xử lý:** Ngắt High Power, khóa và treo thẻ (LOTO)
- **Kết quả:** Hoàn tất trước khi chạm vào giắc
- **Tình trạng:** Đã thực hiện

#### Giắc XSYSTEM

- **Nội dung xử lý:** Rút giắc, kiểm tra chân giắc, cắm lại cho chắc và bật thử High Power
- **Kết quả:** Giắc bị lỏng; sau khi cắm lại, đèn High Power sáng và robot nhận lệnh cho phép
- **Tình trạng:** Đã thực hiện

#### Công tắc cửa an toàn

- **Nội dung xử lý:** Đóng chặt cửa, gõ nhẹ vào vỏ công tắc rồi thử lại High Power
- **Kết quả:** Không cần thực hiện vì lỗi đã hết sau bước cắm lại giắc XSYSTEM
- **Tình trạng:** Không áp dụng – đã dừng ở bước khắc phục thành công

#### Contactor High Power

- **Nội dung xử lý:** Đo điện trở cuộn hút (giá trị đúng khoảng 120 Ω)
- **Kết quả:** Không cần thực hiện vì lỗi đã hết sau bước cắm lại giắc XSYSTEM
- **Tình trạng:** Không áp dụng – đã dừng ở bước khắc phục thành công

#### Servo amplifier

- **Nội dung xử lý:** Chỉ kiểm tra sau khi giắc XSYSTEM, công tắc cửa và contactor đều bình thường mà High Power vẫn không lên
- **Kết quả:** Không cần thực hiện vì mạch High Power phục hồi sau khi cắm lại giắc XSYSTEM
- **Tình trạng:** Không áp dụng – đã dừng ở bước khắc phục thành công

#### Chạy thử

- **Nội dung xử lý:** Chạy một chu trình ở tốc độ thấp trước khi trả máy cho sản xuất
- **Kết quả:** Robot hoàn thành chu trình tốc độ thấp, High Power duy trì ổn định
- **Tình trạng:** Đã thực hiện

### Kết quả và phê duyệt

- **Kết quả xác nhận:** Sau khi cắm lại giắc XSYSTEM, đèn High Power sáng đúng trình tự và robot hoàn thành một chu trình tốc độ thấp bình thường trước khi được trả lại sản xuất.
- **Phạm vi áp dụng:** Kinh nghiệm về giắc XSYSTEM tự làm và công tắc cửa an toàn chỉ áp dụng cho robot i4L tại trạm gắp đặt Line 2; không mặc định áp dụng cho robot i4L ở trạm khác.
- **Tài liệu đính kèm:** `k110044-xsystem-connector.jpg` (giắc XSYSTEM)
- **Ghi chú:** Căn cứ: Sổ tay Omron i4L trang 37 và kinh nghiệm sửa chữa tại trạm Line 2. Khi Control Power sáng nhưng High Power không lên, kiểm tra mạch cho phép theo thứ tự giắc XSYSTEM, công tắc cửa an toàn, contactor High Power, rồi mới xét servo amplifier.
- **Phê duyệt:** TL: đã duyệt. SV: đã duyệt. MGR: đã duyệt.

## K110045: Sập master ON, MC20 số 1

### Thông tin chung

- **Số phiếu:** K110045
- **Vị trí:** SL6
- **Thiết bị:** VALA 0001 (Máy gia công MC20 số 1, OP1.2.3)
- **Loại công trình:** BM
- **Mức độ ưu tiên:** Khẩn cấp
- **Tên công việc:** Điều tra, xử lý bất thường sập master ON
- **Nhóm bảo dưỡng:** Điện – điều khiển
- **Tình trạng:** Hoàn thành

### Thời gian

- **Bắt đầu:** 11-11-2026 10:40
- **Hoàn thành:** 11-11-2026 16:40
- **Thời gian bảo dưỡng:** 6,0 h

### Phân tích hư hỏng

- **Hiện trạng:** Máy đang hoạt động bình thường thì đột ngột mất master ON. Báo lỗi sai lệch tọa độ trục X1 khoảng 0,8 mm. Không có va chạm hoặc kẹt trục trước đó. Nhiệt độ tủ điện bình thường. Sau khi phục hồi master ON và chạy lại, 1 sản phẩm đo NG các kích thước theo cùng một hướng.
- **Vùng thao tác:** Trục X1
- **Linh kiện đồng bộ:** Encoder trục X1, cáp encoder, driver servo X1
- **Hiện tượng:** Sai lệch tọa độ
- **Biện pháp xử lý:** Điều chỉnh
- **Nguyên nhân:** Tiếp xúc kém tại giắc encoder X1 phía driver làm tín hiệu phản hồi bị gián đoạn.
- **Nguyên nhân gốc:** Rung động lâu ngày làm giắc encoder X1 phía driver lỏng vì điểm khóa giắc không được kiểm tra trong bảo trì định kỳ.
- **Tại sao 1W:** Driver mất tín hiệu phản hồi encoder trong thời gian ngắn, lệch tọa độ vượt ngưỡng, hệ thống cắt master ON.
- **Tại sao 2W:** Giắc encoder phía driver tiếp xúc không ổn định.
- **Tại sao 3W:** Điểm khóa giắc encoder không có trong danh mục kiểm tra định kỳ của trục X1.
- **Ý kiến chỉ đạo:** Làm sạch, khóa chắc giắc encoder, hiệu chỉnh tọa độ gốc và xác nhận bằng 20 sản phẩm liên tiếp trước khi trả máy.

### Công việc

#### Master ON

- **Nội dung xử lý:** Phục hồi master ON, về gốc các trục
- **Kết quả:** Máy phục hồi bình thường
- **Tình trạng:** Đã thực hiện

#### Giắc encoder X1

- **Nội dung xử lý:** Kiểm tra giắc phía driver và phía motor, cắm lại, làm sạch tiếp điểm
- **Kết quả:** Giắc phía driver hơi lỏng; phía motor bình thường
- **Tình trạng:** Đã thực hiện

#### Tọa độ gốc X1

- **Nội dung xử lý:** Hiệu chỉnh lại tọa độ gốc trục X1
- **Kết quả:** Sai lệch về trong dung sai
- **Tình trạng:** Đã thực hiện

#### Sản phẩm kiểm tra

- **Nội dung xử lý:** Chạy và đo 20 sản phẩm liên tiếp theo bản vẽ
- **Kết quả:** 20/20 đạt
- **Tình trạng:** Đã thực hiện

### Thông số đo

#### Sai lệch tọa độ X1 (mm)

- **Trước:** 0,8
- **Sau:** 0,02
- **Giới hạn:** ≤ 0,05

### Kết quả và phê duyệt

- **Kết quả xác nhận:** 20/20 sản phẩm đạt kích thước; máy vận hành trọn một ca mà không mất master ON.
- **Phạm vi áp dụng:** Trục X1 của MC20 số 1 với cấu hình encoder và giắc đã ghi nhận trong phiếu này.
- **Tài liệu đính kèm:** `k110045-alarm-screen.jpg` (màn hình báo lỗi); `k110045-encoder-connector.jpg` (giắc encoder X1)
- **Ghi chú:** Điểm khóa giắc encoder X1 đã được bổ sung vào danh mục kiểm tra rung động và đầu nối định kỳ.
- **Phê duyệt:** TL: đã duyệt. SV: đã duyệt. MGR: đã duyệt.

## K110046: Lỗi 2117, turret không lùi hết hành trình, VLA 0029

### Thông tin chung

- **Số phiếu:** K110046
- **Vị trí:** VCT4
- **Thiết bị:** VLA 0029 (Máy gia công bề mặt Rô-to A-10, A1)
- **Loại công trình:** BM
- **Mức độ ưu tiên:** Cao
- **Tên công việc:** Xử lý bất thường lỗi quá thời gian xác nhận turret
- **Nhóm bảo dưỡng:** Process 1
- **Tình trạng:** Hoàn thành

### Thời gian

- **Bắt đầu:** 11-11-2026 12:00
- **Hoàn thành:** 11-11-2026 16:00
- **Thời gian bảo dưỡng:** 4,0 h

### Phân tích hư hỏng

- **Hiện trạng:** Phôi bám dính cụm turret dao. Khi turret đóng mở, không lùi về hết hành trình, máy dừng với lỗi 2117. Lỗi lặp lại sau khoảng 200–300 sản phẩm.
- **Vùng thao tác:** 1 (Thân máy)
- **Linh kiện đồng bộ:** BM5 (Bộ đài dao), MC2 (U gá động)
- **Hiện tượng:** Phôi tích tụ làm turret không lùi hết hành trình và phát sinh lỗi 2117.
- **Biện pháp xử lý:** Vệ sinh, cấp dầu
- **Nguyên nhân:** Lỗi tiếp xúc: phôi kẹt giữa các bề mặt trượt của turret.
- **Nguyên nhân gốc:** Phôi gia công bám dính lâu ngày.
- **Tại sao 1W:** Xy-lanh đóng mở turret không lùi về hết hành trình.
- **Tại sao 2W:** Phôi bám dính tích tụ tại bề mặt trượt của cụm turret.
- **Tại sao 3W:** Chu kỳ vệ sinh cụm turret dài hơn chu kỳ tích tụ phôi thực tế.
- **Ý kiến chỉ đạo:** Rút ngắn chu kỳ vệ sinh cụm turret xuống mỗi 200 sản phẩm và ghi nhận kết quả trong nhật ký bảo trì.

### Công việc

#### Dầu thủy lực

- **Nội dung xử lý:** Xả dầu áp dư
- **Kết quả:** Turret đóng mở được, lùi về hết hành trình
- **Tình trạng:** Đã thực hiện

#### Cụm turret

- **Nội dung xử lý:** Vệ sinh phôi bám dính cụm turret, tra dầu bề mặt trượt
- **Kết quả:** OK, đóng mở trơn tru
- **Tình trạng:** Đã thực hiện

### Kết quả và phê duyệt

- **Kết quả xác nhận:** Chạy thử 30 chu kỳ và gia công 500 sản phẩm không phát sinh lỗi 2117; turret lùi hết hành trình ở mọi lần kiểm tra.
- **Phạm vi áp dụng:** VLA 0029 (A1) với cụm turret BM5 và U gá động MC2.
- **Tài liệu đính kèm:** `k110046-turret-before.jpg` (phôi bám tại turret); `k110046-turret-after.jpg` (sau vệ sinh)
- **Ghi chú:** Áp suất dầu thủy lực nằm trong giới hạn vận hành và xy-lanh không có dấu hiệu mòn bất thường. Chu kỳ vệ sinh 200 sản phẩm đã được cập nhật.
- **Phê duyệt:** TL: đã duyệt. SV: đã duyệt. MGR: đã duyệt.

## K110047: Nước làm mát quá nhiệt, VSC 0009 No.4

### Thông tin chung

- **Số phiếu:** K110047
- **Vị trí:** VCT4
- **Thiết bị:** VSC 0009 (Máy rửa Rô-to No.4)
- **Loại công trình:** BM
- **Mức độ ưu tiên:** Cao
- **Tên công việc:** Điều tra, xử lý nước làm mát bị quá nhiệt
- **Nhóm bảo dưỡng:** Cơ khí
- **Tình trạng:** Hoàn thành

### Thời gian

- **Bắt đầu:** 11-11-2026 09:13
- **Hoàn thành:** 11-11-2026 15:13
- **Thời gian bảo dưỡng:** 6,0 h

### Phân tích hư hỏng

- **Hiện trạng:** Máy báo nước làm mát quá nhiệt và dừng chu trình rửa. Nhiệt độ nước tăng dần theo số chu kỳ; lưu lượng sau bơm thấp hơn giới hạn vận hành.
- **Vùng thao tác:** Hệ thống nước làm mát
- **Linh kiện đồng bộ:** Bơm tuần hoàn, bộ trao đổi nhiệt, lọc nước
- **Hiện tượng:** Nhiệt độ nước vượt ngưỡng cài đặt
- **Biện pháp xử lý:** Súc rửa lọc nước, vệ sinh bộ trao đổi nhiệt và khôi phục lưu lượng tuần hoàn
- **Nguyên nhân:** Cặn bám tại lọc nước và bộ trao đổi nhiệt làm giảm lưu lượng tuần hoàn, khiến nước làm mát không thải đủ nhiệt.
- **Nguyên nhân gốc:** Chu kỳ vệ sinh lọc nước và bộ trao đổi nhiệt dài hơn tốc độ tích tụ cặn thực tế của máy.
- **Tại sao 1W:** Nước làm mát vượt ngưỡng vì nhiệt không được truyền hết qua bộ trao đổi nhiệt.
- **Tại sao 2W:** Lưu lượng qua bộ trao đổi nhiệt giảm do lọc nước và bề mặt trao đổi nhiệt bám cặn.
- **Tại sao 3W:** Kế hoạch bảo trì không quy định kiểm tra lưu lượng và vệ sinh theo mức tích tụ cặn của VSC 0009 No.4.
- **Ý kiến chỉ đạo:** Vệ sinh lọc nước hằng tuần, vệ sinh bộ trao đổi nhiệt hằng tháng và ghi nhận lưu lượng sau bơm trong mỗi lần bảo trì.

### Công việc

#### Nhiệt độ nước

- **Nội dung xử lý:** Đo nhiệt độ nước làm mát tại bể và đầu ra bơm
- **Kết quả:** Xác nhận nhiệt độ bể vượt mức cảnh báo trước xử lý và trở về vùng vận hành sau xử lý
- **Tình trạng:** Đã thực hiện

#### Lọc nước

- **Nội dung xử lý:** Kiểm tra, súc rửa lọc nước
- **Kết quả:** Loại bỏ toàn bộ cặn bám; chênh áp qua lọc trở về giới hạn vận hành
- **Tình trạng:** Đã thực hiện

#### Lưu lượng

- **Nội dung xử lý:** Đo lưu lượng sau bơm
- **Kết quả:** Lưu lượng thấp trước vệ sinh và đạt giới hạn vận hành sau khi súc rửa hệ thống
- **Tình trạng:** Đã thực hiện

#### Bộ trao đổi nhiệt

- **Nội dung xử lý:** Kiểm tra cặn bám
- **Kết quả:** Phát hiện cặn bám, vệ sinh bề mặt trao đổi nhiệt và xác nhận khả năng thải nhiệt bình thường
- **Tình trạng:** Đã thực hiện

#### Chạy thử

- **Nội dung xử lý:** Chạy 30 chu trình rửa liên tiếp và theo dõi nhiệt độ nước
- **Kết quả:** Nhiệt độ ổn định trong vùng vận hành, không xuất hiện cảnh báo quá nhiệt
- **Tình trạng:** Đã thực hiện

### Kết quả và phê duyệt

- **Kết quả xác nhận:** Máy hoàn thành 30 chu trình rửa liên tiếp; nhiệt độ và lưu lượng nước ổn định, cảnh báo quá nhiệt không tái diễn.
- **Phạm vi áp dụng:** VSC 0009 No.4.
- **Tài liệu đính kèm:** `k110047-filter.jpg` (lọc nước trước khi súc rửa); `k110047-heat-exchanger.jpg` (bộ trao đổi nhiệt sau vệ sinh)
- **Ghi chú:** Chu kỳ vệ sinh lọc nước, bộ trao đổi nhiệt và kiểm tra lưu lượng đã được cập nhật trong kế hoạch bảo trì.
- **Phê duyệt:** TL: đã duyệt. SV: đã duyệt. MGR: đã duyệt.

## K110048: Không nhận khóa liên động cửa sau khi thay tool, robot i4L cell lắp ráp C

### Thông tin chung

- **Số phiếu:** K110048
- **Vị trí:** LINE3
- **Thiết bị:** Robot SCARA Omron i4L (cell lắp ráp C)
- **Loại công trình:** BM
- **Mức độ ưu tiên:** Cao
- **Tên công việc:** Điều tra lỗi không nhận khóa liên động cửa sau khi thay tool
- **Nhóm bảo dưỡng:** Điện – điều khiển
- **Tình trạng:** Hoàn thành

### Thời gian

- **Bắt đầu:** 11-11-2026 13:20
- **Hoàn thành:** 11-11-2026 15:20
- **Thời gian bảo dưỡng:** 2,0 h

### Phân tích hư hỏng

- **Hiện trạng:** Sau khi mở cửa cell để thay tool rồi đóng lại, hệ thống không nhận khóa liên động cửa nên robot không cấp được High Power. Công tắc liên động ở vị trí đóng nhưng đèn tín hiệu không sáng. Cánh cửa hơi lệch so với khung khi nhìn từ phía trên. Mở đóng lại hai lần không khắc phục được.
- **Vùng thao tác:** Mạch liên động an toàn cửa cell
- **Linh kiện đồng bộ:** Cánh cửa, bản lề, chìa tác động, công tắc liên động và giá đỡ
- **Hiện tượng:** Cửa đã đóng nhưng tín hiệu khóa liên động không ON; High Power không được phép cấp
- **Biện pháp xử lý:** Căn chỉnh cánh cửa và giá đỡ công tắc liên động, siết lại các bu-lông cố định
- **Nguyên nhân:** Cánh cửa lệch làm chìa tác động không đi vào đúng tâm công tắc liên động.
- **Nguyên nhân gốc:** Bu-lông cố định giá đỡ công tắc bị lỏng do rung động và thao tác đóng mở cửa, làm giá đỡ dịch chuyển khỏi vị trí chuẩn.
- **Tại sao 1W:** High Power không được cấp vì mạch liên động cửa không phát tín hiệu trạng thái an toàn.
- **Tại sao 2W:** Công tắc liên động không phát tín hiệu dù cửa ở vị trí đóng.
- **Tại sao 3W:** Giá đỡ công tắc dịch chuyển vì bu-lông cố định bị lỏng và không có dấu kiểm soát vị trí.
- **Ý kiến chỉ đạo:** Thực hiện LOTO, căn chỉnh giá đỡ theo tài liệu thiết bị, siết bu-lông đúng lực và đánh dấu kiểm soát vị trí; không đấu tắt mạch an toàn.

### Công việc

#### Cửa cell

- **Nội dung xử lý:** Mở và đóng lại cửa hai lần
- **Kết quả:** Xác nhận lỗi lặp lại trước khi căn chỉnh
- **Tình trạng:** Đã thực hiện

#### Cánh cửa và khung

- **Nội dung xử lý:** Kiểm tra độ lệch của cánh cửa so với khung
- **Kết quả:** Xác nhận cánh cửa và chìa tác động lệch khỏi tâm công tắc liên động
- **Tình trạng:** Đã thực hiện

#### Chìa và công tắc liên động

- **Nội dung xử lý:** Đo độ lệch, khe hở; kiểm tra dây và đầu nối theo tài liệu của thiết bị
- **Kết quả:** Dây và đầu nối bình thường; chìa tác động không đi vào đúng tâm công tắc trước khi căn chỉnh
- **Tình trạng:** Đã thực hiện

#### Giá đỡ công tắc

- **Nội dung xử lý:** Căn chỉnh giá đỡ, siết bu-lông đúng lực và đánh dấu kiểm soát vị trí
- **Kết quả:** Chìa tác động đi đúng tâm công tắc; không cần chèn tấm đệm
- **Tình trạng:** Đã thực hiện

#### Chạy thử

- **Nội dung xử lý:** Đóng mở cửa 20 lần và chạy 10 chu trình robot
- **Kết quả:** Tín hiệu liên động ON ổn định; High Power được cấp đúng trình tự trong mọi lần thử
- **Tình trạng:** Đã thực hiện

### Kết quả và phê duyệt

- **Kết quả xác nhận:** Tín hiệu liên động hoạt động ổn định qua 20 lần đóng mở cửa và 10 chu trình robot; cell đã được trả lại sản xuất.
- **Phạm vi áp dụng:** Cửa an toàn của cell lắp ráp C với cấu hình giá đỡ và công tắc liên động đã ghi nhận trong phiếu này.
- **Tài liệu đính kèm:** `k110048-interlock-before.jpg` (vị trí chìa tác động trước căn chỉnh); `k110048-interlock-after.jpg` (dấu kiểm soát sau căn chỉnh)
- **Ghi chú:** Vị trí giá đỡ, lực siết bu-lông và dấu kiểm soát đã được bổ sung vào danh mục kiểm tra cửa an toàn hằng tuần.
- **Phê duyệt:** TL: đã duyệt. SV: đã duyệt. MGR: đã duyệt.

## K110049: Lỗi -924, đèn High Power không nháy vàng, robot i4L cell xếp pallet

### Thông tin chung

- **Số phiếu:** K110049
- **Vị trí:** PACK2
- **Thiết bị:** Robot SCARA Omron i4L (cell xếp pallet)
- **Loại công trình:** BM
- **Mức độ ưu tiên:** Khẩn cấp
- **Tên công việc:** Điều tra lỗi -924 khi yêu cầu cấp High Power
- **Nhóm bảo dưỡng:** Điện – điều khiển
- **Tình trạng:** Hoàn thành

### Thời gian

- **Bắt đầu:** 10-11-2026 19:10
- **Hoàn thành:** 10-11-2026 22:10
- **Thời gian bảo dưỡng:** 3,0 h

### Phân tích hư hỏng

- **Hiện trạng:** Robot báo lỗi -924 mỗi khi yêu cầu cấp High Power. Đèn High Power trên bảng điều khiển phía trước không nháy vàng và không có mã lỗi khác. Hai tuần trước đèn từng chớp tắt bất thường. Khởi động lại bộ điều khiển không khắc phục được. Dây chuyền xếp pallet dừng từ ca trước.
- **Vùng thao tác:** Bảng điều khiển phía trước và mạch yêu cầu cấp High Power
- **Linh kiện đồng bộ:** Cụm đèn High Power, cáp XFP, nút dừng khẩn cấp và khóa liên động cửa
- **Hiện tượng:** Lỗi -924; đèn High Power không nháy vàng khi có yêu cầu
- **Biện pháp xử lý:** Làm sạch, cắm khóa lại giắc XFP và bổ sung kẹp giữ cáp
- **Nguyên nhân:** Giắc XFP tiếp xúc không ổn định làm gián đoạn tín hiệu yêu cầu và hiển thị High Power.
- **Nguyên nhân gốc:** Cáp XFP không có điểm giữ chống rung nên rung động từ băng tải làm khóa giắc lỏng dần.
- **Tại sao 1W:** Robot không hoàn tất trình tự cấp High Power khi lỗi -924 xuất hiện.
- **Tại sao 2W:** Tiếp điểm trong giắc XFP chập chờn nên tín hiệu High Power không hoàn tất đúng trình tự.
- **Tại sao 3W:** Khóa giắc XFP lỏng vì cáp không được cố định chống rung tại bảng điều khiển.
- **Ý kiến chỉ đạo:** Thực hiện LOTO, vệ sinh và khóa chắc giắc XFP, bổ sung kẹp giữ cáp, sau đó xác nhận cụm đèn và High Power qua 20 chu trình.

### Công việc

#### Bộ điều khiển

- **Nội dung xử lý:** Khởi động lại
- **Kết quả:** Lỗi -924 vẫn xuất hiện khi yêu cầu cấp High Power
- **Tình trạng:** Đã thực hiện

#### Dừng khẩn và liên động cửa

- **Nội dung xử lý:** Kiểm tra trạng thái
- **Kết quả:** Không phát hiện bất thường
- **Tình trạng:** Đã thực hiện

#### Cáp XFP

- **Nội dung xử lý:** Sau LOTO, kiểm tra chân giắc, rút và cắm lại rồi thử cấp High Power
- **Kết quả:** Tiếp điểm sạch, khóa giắc chắc; lỗi -924 được xóa và High Power được cấp bình thường
- **Tình trạng:** Đã thực hiện

#### Cụm đèn High Power

- **Nội dung xử lý:** Kiểm tra nguồn cấp, tín hiệu điều khiển và hoạt động của cụm đèn
- **Kết quả:** Cụm đèn nháy vàng đúng trình tự và hoạt động bình thường; không thay linh kiện
- **Tình trạng:** Đã thực hiện

#### Chạy thử

- **Nội dung xử lý:** Chạy 20 chu trình ở tốc độ thấp rồi ở tốc độ sản xuất
- **Kết quả:** Robot hoàn thành đủ 20 chu trình, không xuất hiện lỗi -924
- **Tình trạng:** Đã thực hiện

### Kết quả và phê duyệt

- **Kết quả xác nhận:** Lỗi -924 được xóa; đèn High Power nháy vàng đúng trình tự và robot hoàn thành 20 chu trình thử mà lỗi không tái diễn.
- **Phạm vi áp dụng:** Cáp XFP và bảng điều khiển phía trước của cell xếp pallet PACK2.
- **Tài liệu đính kèm:** `k110049-error-924.jpg` (màn hình lỗi trước xử lý); `k110049-xfp-retainer.jpg` (giắc XFP và kẹp giữ cáp sau xử lý)
- **Ghi chú:** Kẹp giữ cáp XFP và bước kiểm tra khóa giắc đã được bổ sung vào danh mục bảo trì định kỳ của cell PACK2.
- **Phê duyệt:** TL: đã duyệt. SV: đã duyệt. MGR: đã duyệt.
