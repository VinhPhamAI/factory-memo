# Báo cáo xử lý bất thường


| Số phiếu | Vị trí | Thiết bị                                      | Sự cố                                | Thời gian           | Tình trạng |
| -------- | ------ | --------------------------------------------- | ------------------------------------ | ------------------- | ---------- |
| K110041  | DC3    | VEH 0019 (Thổi khí No.1)                      | Cửa shutter mở chậm, giật            | 07:45–16:45 (9,0 h) | Hoàn thành |
| K110043  | CVTF2  | VIM 0200 (Máy kiểm tra tính năng No.6)        | NG dòng điện                         | 08:10–10:10 (2,0 h) | Hoàn thành |
| K110044  | LINE2  | Robot SCARA Omron i4L (trạm gắp đặt Line 2)   | Không bật được High Power sau sụt áp | 07:40–09:10 (1,5 h) | Hoàn thành |
| K110045  | SL6    | VALA 0001 (Máy gia công MC20 số 1)            | Sập master ON                        | 10:40–16:40 (6,0 h) | Hoàn thành |
| K110046  | VCT4   | VLA 0029 (Máy gia công bề mặt Rô-to A-10, A1) | Lỗi 2117, turret không lùi           | 12:00–16:00 (4,0 h) | Hoàn thành |
| K110047  | VCT4   | VSC 0009 (Máy rửa Rô-to No.4)                 | Nước làm mát quá nhiệt               | Từ 09:13            | Đang xử lý |
| K110048  | LINE3  | Robot SCARA Omron i4L (cell lắp ráp C)        | Không nhận khóa liên động cửa        | Từ 13:20            | Đang xử lý |
| K110049  | PACK2  | Robot SCARA Omron i4L (cell xếp pallet)       | Lỗi -924, đèn High Power không nháy  | Từ ca trước         | Đang xử lý |

## K110041: Cửa shutter mở chậm, giật cục, VEH 0019

Thông tin chung

| Trường           | Nội dung                                       |
| ---------------- | ---------------------------------------------- |
| Số phiếu         | K110041                                        |
| Vị trí           | DC3                                            |
| Thiết bị         | VEH 0019 (Thổi khí No.1)                       |
| Loại công trình  | PM                                             |
| Mức độ ưu tiên   | Bình thường                                    |
| Tên công việc    | Xử lý bất thường cửa shutter di chuyển mở chậm |
| Nhóm bảo dưỡng   | Cơ khí                                         |
| Tình trạng       | Hoàn thành                                     |

Thời gian

| Trường              | Nội dung         |
| ------------------- | ---------------- |
| Bắt đầu             | 11-11-2026 07:45 |
| Hoàn thành          | 11-11-2026 16:45 |
| Thời gian bảo dưỡng | 9,0 h            |

Phân tích hư hỏng

| Trường            | Nội dung                                                                                                                                                                                                                        |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Hiện trạng        | Tuần tra phát hiện cửa shutter di chuyển mở với tốc độ chậm và bị giật cục, có lúc dừng giữa hành trình. Dầu bôi trơn thanh dẫn đã khô. Không có tiếng kêu bất thường từ động cơ. Cảm biến vị trí mở và đóng đều tác động đúng. |
| Vùng thao tác     | Cửa shutter, thanh dẫn                                                                                                                                                                                                          |
| Linh kiện đồng bộ | Thanh dẫn shutter, động cơ truyền động                                                                                                                                                                                          |
| Hiện tượng        | Chuyển động giật, tốc độ chậm                                                                                                                                                                                                   |
| Biện pháp xử lý   | Vệ sinh, cấp dầu                                                                                                                                                                                                                |
| Nguyên nhân       | Dầu bôi trơn thanh dẫn khô, ma sát tăng.                                                                                                                                                                                        |
| Nguyên nhân gốc   | Thanh dẫn chưa nằm trong chu kỳ tra dầu định kỳ (giả thuyết).                                                                                                                                                                   |
| Tại sao 1W        | Ma sát giữa shutter và thanh dẫn tăng nên cửa di chuyển chậm và giật.                                                                                                                                                           |
| Tại sao 2W        | Lớp dầu trên thanh dẫn đã khô.                                                                                                                                                                                                  |
| Tại sao 3W        | Chưa có lịch tra dầu thanh dẫn trong bảo trì định kỳ (giả thuyết).                                                                                                                                                              |
| Ý kiến chỉ đạo    | Đưa việc tra dầu thanh dẫn vào lịch bảo trì tuần. Nếu còn giật sau 1 tuần thì kiểm tra tốc độ động cơ.                                                                                                                          |

Công việc

| Hạng mục                | Nội dung xử lý                 | Kết quả                                                 | Tình trạng   |
| ----------------------- | ------------------------------ | ------------------------------------------------------- | ------------ |
| Thanh dẫn shutter       | Lau sạch thanh dẫn, tra dầu    | Thanh dẫn sạch, không có vết xước đáng kể               | Đã thực hiện |
| Cảm biến vị trí mở/đóng | Kiểm tra tác động của cảm biến | Tác động đúng ở cả hai vị trí                           | Đã thực hiện |
| Chạy thử                | Cho shutter mở đóng 10 lần     | Chuyển động trơn hơn rõ rệt, không dừng giữa hành trình | Đã thực hiện |

Kết quả và phê duyệt

| Trường            | Nội dung                                                                          |
| ----------------- | --------------------------------------------------------------------------------- |
| Kết quả xác nhận  | Chạy thử 10 lần không giật, không dừng giữa hành trình. Chưa theo dõi sau 1 tuần. |
| Phạm vi áp dụng   | Shutter VEH 0019 No.1. Chưa kiểm tra tốc độ động cơ.                              |
| Tài liệu đính kèm | `k110041-guide-rail.jpg` (thanh dẫn trước khi tra dầu)                            |
| Ghi chú           | Theo dõi lại sau 1 tuần; nếu còn giật, kiểm tra tốc độ động cơ.                   |
| Phê duyệt         | TL: đã duyệt. SV: đã duyệt. MGR: đã duyệt.                                        |

## K110043: NG dòng điện, VIM 0200 No.6

Thông tin chung

| Trường           | Nội dung                               |
| ---------------- | -------------------------------------- |
| Số phiếu         | K110043                                |
| Vị trí           | CVTF2                                  |
| Thiết bị         | VIM 0200 (Máy kiểm tra tính năng No.6) |
| Loại công trình  | BM                                     |
| Mức độ ưu tiên   | Cao                                    |
| Tên công việc    | Xử lý bất thường NG dòng điện          |
| Nhóm bảo dưỡng   | Điện – điều khiển                      |
| Tình trạng       | Hoàn thành                             |

Thời gian

| Trường              | Nội dung         |
| ------------------- | ---------------- |
| Bắt đầu             | 11-11-2026 08:10 |
| Hoàn thành          | 11-11-2026 10:10 |
| Thời gian bảo dưỡng | 2,0 h            |

Phân tích hư hỏng

| Trường            | Nội dung                                                                                                                                                                                                                                                                               |
| ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Hiện trạng        | Máy báo NG dòng điện liên tục từ đầu ca, sản phẩm bị loại hàng loạt dù đã đổi lot. Giá trị dòng đo dao động mạnh giữa các lần kiểm. Khi rung nhẹ dây tín hiệu thì giá trị thay đổi. Máy No.5 cùng lot vẫn đạt. Ngưỡng dòng cài đặt không đổi. Kiểm tra bằng sản phẩm mẫu chuẩn vẫn NG. |
| Vùng thao tác     | Đầu kiểm tra sản phẩm                                                                                                                                                                                                                                                                  |
| Linh kiện đồng bộ | Connector kiểm tra sản phẩm, dây tín hiệu                                                                                                                                                                                                                                              |
| Hiện tượng        | Giá trị đo không ổn định                                                                                                                                                                                                                                                               |
| Biện pháp xử lý   | Thay thế                                                                                                                                                                                                                                                                               |
| Nguyên nhân       | Chân connector kiểm tra tiếp xúc kém sau khi hết tần suất sử dụng.                                                                                                                                                                                                                     |
| Nguyên nhân gốc   | Connector vượt số lần cắm quy định (khoảng 25.000 lần), chưa được thay theo chu kỳ.                                                                                                                                                                                                    |
| Tại sao 1W        | Tiếp xúc giữa connector và sản phẩm không ổn định nên giá trị dòng đo dao động.                                                                                                                                                                                                        |
| Tại sao 2W        | Connector đã quá tuổi thọ số lần cắm.                                                                                                                                                                                                                                                  |
| Tại sao 3W        | Số lần cắm của connector không được theo dõi để thay trước khi hỏng.                                                                                                                                                                                                                   |
| Ý kiến chỉ đạo    | Bổ sung theo dõi số lần cắm cho connector kiểm tra của các máy VIM.                                                                                                                                                                                                                    |

Công việc

| Hạng mục                    | Nội dung xử lý                                           | Kết quả                          | Tình trạng   |
| --------------------------- | -------------------------------------------------------- | -------------------------------- | ------------ |
| Ngưỡng dòng và sản phẩm mẫu | Kiểm tra lại cài đặt ngưỡng, thử bằng sản phẩm mẫu chuẩn | Ngưỡng đúng; sản phẩm mẫu vẫn NG | Đã thực hiện |
| Dây tín hiệu                | Rung nhẹ dây khi đang đo                                 | Giá trị đo thay đổi theo rung    | Đã thực hiện |
| Connector kiểm tra          | Thay connector dự phòng                                  | Hết lỗi NG dòng điện             | Đã thực hiện |

Linh kiện thay thế

| Mã linh kiện                           | Số lượng | Tình trạng linh kiện cũ                             |
| -------------------------------------- | -------- | --------------------------------------------------- |
| Connector kiểm tra VIM 0200 (dự phòng) | 1        | Chân tiếp xúc mòn, đã sử dụng khoảng 25.000 lần cắm |

Kết quả và phê duyệt

| Trường            | Nội dung                                                                                                       |
| ----------------- | -------------------------------------------------------------------------------------------------------------- |
| Kết quả xác nhận  | Chạy 20 sản phẩm liên tiếp, tỷ lệ NG trở về bình thường.                                                       |
| Phạm vi áp dụng   | VIM 0200 No.6. Không suy ra cho máy dùng loại connector khác.                                                  |
| Tài liệu đính kèm | `k110043-connector-old.jpg` (chân connector mòn)                                                               |
| Ghi chú           | Số lần cắm 25.000 lấy từ ghi nhận bảo trì của máy VIM 0196; chưa đối chiếu với thông số kỹ thuật của máy No.6. |
| Phê duyệt         | TL: đã duyệt. SV: đã duyệt. MGR: đã duyệt.                                                                     |

## K110044: Không bật được High Power sau sụt áp, robot i4L Line 2

Thông tin chung

| Trường           | Nội dung                                         |
| ---------------- | ------------------------------------------------ |
| Số phiếu         | K110044                                          |
| Vị trí           | LINE2                                            |
| Thiết bị         | Robot SCARA Omron i4L (trạm gắp đặt Line 2)      |
| Loại công trình  | BM                                               |
| Mức độ ưu tiên   | Khẩn cấp                                         |
| Tên công việc    | Xử lý robot không bật được High Power sau sụt áp |
| Nhóm bảo dưỡng   | Điện – điều khiển                                |
| Tình trạng       | Hoàn thành                                       |

Thời gian

| Trường              | Nội dung         |
| ------------------- | ---------------- |
| Bắt đầu             | 11-11-2026 07:40 |
| Hoàn thành          | 11-11-2026 09:10 |
| Thời gian bảo dưỡng | 1,5 h            |

Phân tích hư hỏng

| Trường            | Nội dung                                                                                                                                                                                                                                                                            |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Hiện trạng        | Sau lần sụt áp đầu ca, robot khởi động bình thường nhưng không chuyển động. Đèn Control Power sáng, đèn High Power không lên khi bấm nút bật. Bộ điều khiển khởi động xong, không báo mã lỗi. Khởi động lại bộ điều khiển một lần, kết quả không đổi. Dây chuyền dừng tại trạm này. |
| Vùng thao tác     | Mạch cho phép (enable) High Power                                                                                                                                                                                                                                                   |
| Linh kiện đồng bộ | Giắc cầu nối XSYSTEM, công tắc cửa an toàn, contactor High Power                                                                                                                                                                                                                    |
| Hiện tượng        | Control Power có, High Power không lên                                                                                                                                                                                                                                              |
| Biện pháp xử lý   | Điều chỉnh (cắm lại giắc)                                                                                                                                                                                                                                                           |
| Nguyên nhân       | Giắc cầu nối XSYSTEM của mạch cho phép High Power lỏng nên mạch hở.                                                                                                                                                                                                                 |
| Nguyên nhân gốc   | Giắc XSYSTEM là giắc tự làm, lỏng sau nhiều chu kỳ nóng – nguội (giả thuyết, theo ghi nhận sửa chữa trước đây tại trạm này).                                                                                                                                                        |
| Tại sao 1W        | High Power không được cấp vì mạch cho phép đang hở.                                                                                                                                                                                                                                 |
| Tại sao 2W        | Giắc XSYSTEM tiếp xúc không chắc sau sụt áp.                                                                                                                                                                                                                                        |
| Tại sao 3W        | Giắc tự làm, không chắc chắn bằng giắc chính hãng (giả thuyết).                                                                                                                                                                                                                     |
| Ý kiến chỉ đạo    | Dừng ở bước đầu tiên khắc phục được. Không thay servo amplifier khi mạch cho phép chưa được loại trừ. Chạy thử một chu trình tốc độ thấp trước khi trả máy.                                                                                                                         |

Công việc

Các bước đi từ dễ đến khó và dừng ở bước đầu tiên khắc phục được lỗi.

| Hạng mục             | Nội dung xử lý                                                     | Kết quả                                         | Tình trạng     |
| -------------------- | ------------------------------------------------------------------ | ----------------------------------------------- | -------------- |
| An toàn              | Ngắt High Power, khóa và treo thẻ (LOTO)                           | Hoàn tất trước khi chạm vào giắc                | Đã thực hiện   |
| Giắc XSYSTEM         | Rút giắc, kiểm tra chân giắc, cắm lại cho chắc, bật thử High Power | Giắc lỏng; sau khi cắm lại, đèn High Power sáng | Đã thực hiện   |
| Công tắc cửa an toàn | Đóng chặt cửa, gõ nhẹ vỏ công tắc                                  | Không cần: lỗi đã hết sau bước giắc XSYSTEM     | Chưa thực hiện |
| Contactor High Power | Đo điện trở cuộn hút (giá trị đúng khoảng 120 Ω)                   | Không cần: lỗi đã hết sau bước giắc XSYSTEM     | Chưa thực hiện |
| Servo amplifier      | Chưa xét đến                                                       | Không có dấu hiệu hỏng                          | Chưa thực hiện |
| Chạy thử             | Chạy một chu trình ở tốc độ thấp                                   | Robot chạy bình thường                          | Đã thực hiện   |

Kết quả và phê duyệt

| Trường            | Nội dung                                                                                                                                                                                                                                                                                                      |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Kết quả xác nhận  | Đèn High Power sáng, robot chạy một chu trình tốc độ thấp bình thường rồi trả máy cho sản xuất. Chưa theo dõi lâu hơn.                                                                                                                                                                                        |
| Phạm vi áp dụng   | Trạm gắp đặt Line 2. Việc giắc XSYSTEM hay lỏng là ghi nhận riêng của trạm này, chưa được chuyên gia duyệt; không áp dụng mặc định cho robot i4L ở trạm khác.                                                                                                                                                 |
| Tài liệu đính kèm | `k110044-xsystem-connector.jpg` (giắc XSYSTEM)                                                                                                                                                                                                                                                                |
| Ghi chú           | Căn cứ: Sổ tay Omron i4L trang 37 (robot chỉ chuyển động khi High Power được cấp và cho phép; Control Power phải có trước). Câu hỏi chờ chuyên gia: có nên thay giắc XSYSTEM tự làm bằng giắc chính hãng để tránh lặp lại không; nếu lỗi tái diễn thì xét công tắc cửa an toàn và contactor theo thứ tự trên. |
| Phê duyệt         | TL: đã duyệt. SV: chờ duyệt. MGR: chờ duyệt.                                                                                                                                                                                                                                                                  |

## K110045: Sập master ON, MC20 số 1

Thông tin chung

| Trường           | Nội dung                                    |
| ---------------- | ------------------------------------------- |
| Số phiếu         | K110045                                     |
| Vị trí           | SL6                                         |
| Thiết bị         | VALA 0001 (Máy gia công MC20 số 1, OP1.2.3) |
| Loại công trình  | BM                                          |
| Mức độ ưu tiên   | Khẩn cấp                                    |
| Tên công việc    | Điều tra, xử lý bất thường sập master ON    |
| Nhóm bảo dưỡng   | Điện – điều khiển                           |
| Tình trạng       | Hoàn thành                                  |

Thời gian

| Trường              | Nội dung         |
| ------------------- | ---------------- |
| Bắt đầu             | 11-11-2026 10:40 |
| Hoàn thành          | 11-11-2026 16:40 |
| Thời gian bảo dưỡng | 6,0 h            |

Phân tích hư hỏng

| Trường            | Nội dung                                                                                                                                                                                                                                                                      |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Hiện trạng        | Máy đang hoạt động bình thường thì đột ngột mất master ON. Báo lỗi sai lệch tọa độ trục X1 khoảng 0,8 mm. Không có va chạm hoặc kẹt trục trước đó. Nhiệt độ tủ điện bình thường. Sau khi phục hồi master ON và chạy lại, 1 sản phẩm đo NG các kích thước theo cùng một hướng. |
| Vùng thao tác     | Trục X1                                                                                                                                                                                                                                                                       |
| Linh kiện đồng bộ | Encoder trục X1, cáp encoder, driver servo X1                                                                                                                                                                                                                                 |
| Hiện tượng        | Sai lệch tọa độ                                                                                                                                                                                                                                                               |
| Biện pháp xử lý   | Điều chỉnh                                                                                                                                                                                                                                                                    |
| Nguyên nhân       | Tiếp xúc kém tại giắc encoder X1 phía driver (giả thuyết, phù hợp với việc giắc hơi lỏng khi kiểm tra).                                                                                                                                                                       |
| Nguyên nhân gốc   | Giắc encoder lỏng do rung động lâu ngày (giả thuyết).                                                                                                                                                                                                                         |
| Tại sao 1W        | Driver mất tín hiệu phản hồi encoder trong thời gian ngắn, lệch tọa độ vượt ngưỡng, hệ thống cắt master ON.                                                                                                                                                                   |
| Tại sao 2W        | Giắc encoder phía driver tiếp xúc không ổn định.                                                                                                                                                                                                                              |
| Tại sao 3W        | Chưa xác định.                                                                                                                                                                                                                                                                |
| Ý kiến chỉ đạo    | Đo 5 sản phẩm liên tiếp trước khi trả máy cho sản xuất. Nếu sai lệch lặp lại, xem xét thay encoder.                                                                                                                                                                           |

Công việc

| Hạng mục          | Nội dung xử lý                                                       | Kết quả                                           | Tình trạng   |
| ----------------- | -------------------------------------------------------------------- | ------------------------------------------------- | ------------ |
| Master ON         | Phục hồi master ON, về gốc các trục                                  | Máy phục hồi bình thường                          | Đã thực hiện |
| Giắc encoder X1   | Kiểm tra giắc phía driver và phía motor, cắm lại, làm sạch tiếp điểm | Giắc phía driver hơi lỏng; phía motor bình thường | Đã thực hiện |
| Tọa độ gốc X1     | Hiệu chỉnh lại tọa độ gốc trục X1                                    | Sai lệch về trong dung sai                        | Đã thực hiện |
| Sản phẩm kiểm tra | Chạy và đo 5 sản phẩm liên tiếp theo bản vẽ                          | 5/5 đạt                                           | Đã thực hiện |

Thông số đo

| Thông số                | Trước | Sau  | Giới hạn |
| ----------------------- | ----- | ---- | -------- |
| Sai lệch tọa độ X1 (mm) | 0,8   | 0,02 | ≤ 0,05   |

Kết quả và phê duyệt

| Trường            | Nội dung                                                                                         |
| ----------------- | ------------------------------------------------------------------------------------------------ |
| Kết quả xác nhận  | 5/5 sản phẩm đạt kích thước. Theo dõi 2 giờ sau khi trả máy, không mất master ON lại.            |
| Phạm vi áp dụng   | MC20 số 1. Nguyên nhân gốc chưa được xác nhận bằng thử nghiệm riêng.                             |
| Tài liệu đính kèm | `k110045-alarm-screen.jpg` (màn hình báo lỗi); `k110045-encoder-connector.jpg` (giắc encoder X1) |
| Ghi chú           | Nếu lỗi lặp lại, thay cáp encoder X1 trước khi xem xét thay encoder.                             |
| Phê duyệt         | TL: đã duyệt. SV: đã duyệt. MGR: chờ duyệt.                                                      |

## K110046: Lỗi 2117, turret không lùi hết hành trình, VLA 0029

Thông tin chung

| Trường           | Nội dung                                           |
| ---------------- | -------------------------------------------------- |
| Số phiếu         | K110046                                            |
| Vị trí           | VCT4                                               |
| Thiết bị         | VLA 0029 (Máy gia công bề mặt Rô-to A-10, A1)      |
| Loại công trình  | BM                                                 |
| Mức độ ưu tiên   | Cao                                                |
| Tên công việc    | Xử lý bất thường lỗi quá thời gian xác nhận turret |
| Nhóm bảo dưỡng   | Process 1                                          |
| Tình trạng       | Hoàn thành                                         |

Thời gian

| Trường              | Nội dung         |
| ------------------- | ---------------- |
| Bắt đầu             | 11-11-2026 12:00 |
| Hoàn thành          | 11-11-2026 16:00 |
| Thời gian bảo dưỡng | 4,0 h            |

Phân tích hư hỏng

| Trường            | Nội dung                                                                                                                                       |
| ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| Hiện trạng        | Phôi bám dính cụm turret dao. Khi turret đóng mở, không lùi về hết hành trình, máy dừng với lỗi 2117. Lỗi lặp lại sau khoảng 200–300 sản phẩm. |
| Vùng thao tác     | 1 (Thân máy)                                                                                                                                   |
| Linh kiện đồng bộ | BM5 (Bộ đài dao), MC2 (U gá động)                                                                                                              |
| Hiện tượng        | Không xác nhận: sau khi vệ sinh, máy chạy lại bình thường.                                                                                     |
| Biện pháp xử lý   | Vệ sinh, cấp dầu                                                                                                                               |
| Nguyên nhân       | Lỗi tiếp xúc: phôi kẹt giữa các bề mặt trượt của turret.                                                                                       |
| Nguyên nhân gốc   | Phôi gia công bám dính lâu ngày.                                                                                                               |
| Tại sao 1W        | Xy-lanh đóng mở turret không lùi về hết hành trình.                                                                                            |
| Tại sao 2W        | Phôi bám dính tích tụ tại bề mặt trượt của cụm turret.                                                                                         |
| Tại sao 3W        | Chu kỳ vệ sinh phôi hiện tại dài hơn tốc độ tích tụ phôi (giả thuyết).                                                                         |
| Ý kiến chỉ đạo    | Rút ngắn chu kỳ vệ sinh cụm turret. Theo dõi tần suất lỗi 2117 trong 1 tuần.                                                                   |

Công việc

| Hạng mục     | Nội dung xử lý                                         | Kết quả                                    | Tình trạng   |
| ------------ | ------------------------------------------------------ | ------------------------------------------ | ------------ |
| Dầu thủy lực | Xả dầu áp dư                                           | Turret đóng mở được, lùi về hết hành trình | Đã thực hiện |
| Cụm turret   | Vệ sinh phôi bám dính cụm turret, tra dầu bề mặt trượt | OK, đóng mở trơn tru                       | Đã thực hiện |

Kết quả và phê duyệt

| Trường            | Nội dung                                                                                    |
| ----------------- | ------------------------------------------------------------------------------------------- |
| Kết quả xác nhận  | Chạy thử 30 chu kỳ đóng mở turret không lỗi. Chưa theo dõi lâu hơn một ca.                  |
| Phạm vi áp dụng   | VLA 0029 (A1). Chưa kiểm tra các máy VLA khác.                                              |
| Tài liệu đính kèm | `k110046-turret-before.jpg` (phôi bám tại turret); `k110046-turret-after.jpg` (sau vệ sinh) |
| Ghi chú           | Chưa đo áp suất dầu thủy lực; chưa loại trừ mòn xy-lanh.                                    |
| Phê duyệt         | TL: đã duyệt. SV: chờ duyệt. MGR: chờ duyệt.                                                |

## K110047: Nước làm mát quá nhiệt, VSC 0009 No.4

Thông tin chung

| Trường           | Nội dung                                  |
| ---------------- | ----------------------------------------- |
| Số phiếu         | K110047                                   |
| Vị trí           | VCT4                                      |
| Thiết bị         | VSC 0009 (Máy rửa Rô-to No.4)             |
| Loại công trình  | BM                                        |
| Mức độ ưu tiên   | Cao                                       |
| Tên công việc    | Điều tra, xử lý nước làm mát bị quá nhiệt |
| Nhóm bảo dưỡng   | Cơ khí                                    |
| Tình trạng       | Đang xử lý                                |

Thời gian

| Trường              | Nội dung          |
| ------------------- | ----------------- |
| Bắt đầu             | 11-11-2026 09:13  |
| Hoàn thành          | (chưa hoàn thành) |
| Thời gian bảo dưỡng | Đang tính         |

Phân tích hư hỏng

| Trường            | Nội dung                                                                                                                              |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| Hiện trạng        | Máy báo nước làm mát quá nhiệt, dừng chu trình rửa. Đầu ca nhiệt độ nước tăng dần theo số chu kỳ. Chưa xác định vị trí tắc hoặc hỏng. |
| Vùng thao tác     | Hệ thống nước làm mát                                                                                                                 |
| Linh kiện đồng bộ | Bơm tuần hoàn, bộ trao đổi nhiệt, lọc nước                                                                                            |
| Hiện tượng        | Nhiệt độ nước vượt ngưỡng cài đặt                                                                                                     |
| Biện pháp xử lý   | Đang điều tra                                                                                                                         |
| Nguyên nhân       | Chưa xác định. Đang nghi lọc nước hoặc bộ trao đổi nhiệt bám cặn, giảm lưu lượng (giả thuyết).                                        |
| Nguyên nhân gốc   | Chưa xác định.                                                                                                                        |
| Tại sao 1W        | Nước làm mát không thải đủ nhiệt (giả thuyết).                                                                                        |
| Tại sao 2W        | Chưa xác định.                                                                                                                        |
| Tại sao 3W        | Chưa xác định.                                                                                                                        |
| Ý kiến chỉ đạo    | Kiểm tra lưu lượng và nhiệt độ vào/ra bộ trao đổi nhiệt trước khi thay bơm. Báo cáo trước khi mở bộ trao đổi nhiệt.                   |

Công việc

| Hạng mục          | Nội dung xử lý                                | Kết quả                                  | Tình trạng     |
| ----------------- | --------------------------------------------- | ---------------------------------------- | -------------- |
| Nhiệt độ nước     | Đo nhiệt độ nước làm mát tại bể và đầu ra bơm | Nhiệt độ bể cao hơn mức cài đặt cảnh báo | Đã thực hiện   |
| Lọc nước          | Kiểm tra, súc rửa lọc nước                    | Lọc có cặn, đã súc rửa                   | Đã thực hiện   |
| Lưu lượng         | Đo lưu lượng sau bơm                          | Chưa thực hiện (chờ dụng cụ đo)          | Chưa thực hiện |
| Bộ trao đổi nhiệt | Kiểm tra cặn bám                              | Chưa thực hiện                           | Chưa thực hiện |

Kết quả và phê duyệt

| Trường            | Nội dung                                                                             |
| ----------------- | ------------------------------------------------------------------------------------ |
| Kết quả xác nhận  | Chưa xác nhận. Đã súc rửa lọc nhưng chưa chạy lại máy để xem nhiệt độ có giảm không. |
| Phạm vi áp dụng   | VSC 0009 No.4.                                                                       |
| Tài liệu đính kèm | `k110047-filter.jpg` (lọc nước trước khi súc rửa)                                    |
| Ghi chú           | Máy còn dừng. Cần đo lưu lượng và kiểm tra bộ trao đổi nhiệt trước khi kết luận.     |
| Phê duyệt         | TL: chưa trình. SV: chưa trình. MGR: chưa trình.                                     |

## K110048: Không nhận khóa liên động cửa sau khi thay tool, robot i4L cell lắp ráp C

Thông tin chung

| Trường           | Nội dung                                                       |
| ---------------- | -------------------------------------------------------------- |
| Số phiếu         | K110048                                                        |
| Vị trí           | LINE3                                                          |
| Thiết bị         | Robot SCARA Omron i4L (cell lắp ráp C)                         |
| Loại công trình  | BM                                                             |
| Mức độ ưu tiên   | Cao                                                            |
| Tên công việc    | Điều tra lỗi không nhận khóa liên động cửa sau khi thay tool   |
| Nhóm bảo dưỡng   | Điện – điều khiển                                              |
| Tình trạng       | Đang xử lý                                                     |

Thời gian

| Trường              | Nội dung          |
| ------------------- | ----------------- |
| Bắt đầu             | 11-11-2026 13:20  |
| Hoàn thành          | (chưa hoàn thành) |
| Thời gian bảo dưỡng | Đang tính         |

Phân tích hư hỏng

| Trường            | Nội dung                                                                                                                                                                                                                                                       |
| ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Hiện trạng        | Sau khi mở cửa cell để thay tool rồi đóng lại, hệ thống không nhận khóa liên động cửa nên robot không cấp được High Power. Công tắc liên động ở vị trí đóng nhưng đèn tín hiệu không sáng. Cánh cửa hơi lệch so với khung khi nhìn từ phía trên. Mở đóng lại hai lần không khắc phục được. |
| Vùng thao tác     | Mạch liên động an toàn cửa cell                                                                                                                                                                                                                                 |
| Linh kiện đồng bộ | Cánh cửa, bản lề, chìa tác động, công tắc liên động và giá đỡ                                                                                                                                                                                                  |
| Hiện tượng        | Cửa đã đóng nhưng tín hiệu khóa liên động không ON; High Power không được phép cấp                                                                                                                                                                              |
| Biện pháp xử lý   | Đang điều tra; chưa điều chỉnh hoặc thay linh kiện                                                                                                                                                                                                              |
| Nguyên nhân       | Chưa xác định. Cánh cửa lệch có thể làm chìa tác động không vào hết công tắc liên động (giả thuyết).                                                                                                                                                           |
| Nguyên nhân gốc   | Chưa xác định. Nhật ký ca ghi nhận hiện tượng tương tự khoảng 4 lần trong 6 tháng; có thể liên quan đến bản lề hoặc giá đỡ dịch chuyển (giả thuyết).                                                                                                            |
| Tại sao 1W        | High Power không được cấp vì mạch liên động cửa chưa xác nhận trạng thái an toàn.                                                                                                                                                                               |
| Tại sao 2W        | Công tắc liên động không phát tín hiệu dù cửa ở vị trí đóng.                                                                                                                                                                                                    |
| Tại sao 3W        | Chưa xác định; cần đo độ lệch cửa và kiểm tra chìa tác động, dây dẫn, công tắc.                                                                                                                                                                                  |
| Ý kiến chỉ đạo    | Không đấu tắt mạch an toàn. Thực hiện LOTO trước khi kiểm tra cơ khí hoặc dây dẫn. Đo độ lệch và khe hở trước khi chỉnh giá đỡ; chưa chèn tấm đệm khi chưa có kích thước được chuyên gia xác nhận.                                                                |

Công việc

| Hạng mục                  | Nội dung xử lý                                                      | Kết quả                                                            | Tình trạng     |
| ------------------------- | ------------------------------------------------------------------- | ------------------------------------------------------------------ | -------------- |
| Cửa cell                  | Mở và đóng lại cửa hai lần                                          | Đèn tín hiệu khóa liên động vẫn không sáng                         | Đã thực hiện   |
| Cánh cửa và khung         | Kiểm tra trực quan từ phía trên                                     | Cánh cửa có dấu hiệu lệch so với khung                             | Đã thực hiện   |
| Chìa và công tắc liên động | Đo độ lệch, khe hở; kiểm tra dây và đầu nối theo tài liệu của thiết bị | Chưa thực hiện                                                     | Chưa thực hiện |
| Giá đỡ công tắc           | Căn chỉnh hoặc chèn đệm theo kích thước đã được chuyên gia xác nhận | Chưa thực hiện; đang chờ xác nhận phương án và độ dày tấm đệm      | Chưa thực hiện |

Kết quả và phê duyệt

| Trường            | Nội dung                                                                                                                                                                        |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Kết quả xác nhận  | Chưa xác nhận. Robot chưa được cấp High Power và cell chưa trả lại sản xuất.                                                                                                    |
| Phạm vi áp dụng   | Cell lắp ráp C. Không áp dụng kích thước căn chỉnh hoặc tấm đệm của cell khác khi chưa đo và đối chiếu tài liệu thiết bị.                                                        |
| Tài liệu đính kèm | Chưa có. Cần bổ sung ảnh vị trí công tắc, chìa tác động và độ lệch cửa trước khi điều chỉnh.                                                                                    |
| Ghi chú           | Nhật ký ca ghi nhận lỗi tương tự khoảng 4 lần trong 6 tháng, nhưng chưa có kết quả đo hoặc nguyên nhân đã được xác nhận. Câu hỏi chờ chuyên gia: có cần chèn đệm không, và độ dày cho phép là bao nhiêu? |
| Phê duyệt         | TL: chưa trình. SV: chưa trình. MGR: chưa trình.                                                                                                                                |

## K110049: Lỗi -924, đèn High Power không nháy vàng, robot i4L cell xếp pallet

Thông tin chung

| Trường           | Nội dung                                                    |
| ---------------- | ----------------------------------------------------------- |
| Số phiếu         | K110049                                                     |
| Vị trí           | PACK2                                                       |
| Thiết bị         | Robot SCARA Omron i4L (cell xếp pallet)                     |
| Loại công trình  | BM                                                          |
| Mức độ ưu tiên   | Khẩn cấp                                                    |
| Tên công việc    | Điều tra lỗi -924 khi yêu cầu cấp High Power                |
| Nhóm bảo dưỡng   | Điện – điều khiển                                           |
| Tình trạng       | Đang xử lý                                                  |

Thời gian

| Trường              | Nội dung          |
| ------------------- | ----------------- |
| Bắt đầu             | 10-11-2026 19:10  |
| Hoàn thành          | (chưa hoàn thành) |
| Thời gian bảo dưỡng | Đang tính         |

Phân tích hư hỏng

| Trường            | Nội dung                                                                                                                                                                                                                                       |
| ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Hiện trạng        | Robot báo lỗi -924 mỗi khi yêu cầu cấp High Power. Đèn High Power trên bảng điều khiển phía trước không nháy vàng và không có mã lỗi khác. Hai tuần trước đèn từng chớp tắt bất thường. Khởi động lại bộ điều khiển không khắc phục được. Dây chuyền xếp pallet dừng từ ca trước. |
| Vùng thao tác     | Bảng điều khiển phía trước và mạch yêu cầu cấp High Power                                                                                                                                                                                       |
| Linh kiện đồng bộ | Cụm đèn High Power, cáp XFP, nút dừng khẩn cấp và khóa liên động cửa                                                                                                                                                                            |
| Hiện tượng        | Lỗi -924; đèn High Power không nháy vàng khi có yêu cầu                                                                                                                                                                                        |
| Biện pháp xử lý   | Đang điều tra; chưa thay linh kiện                                                                                                                                                                                                              |
| Nguyên nhân       | Chưa xác định. Cụm đèn hoặc tiếp xúc cáp XFP có thể bất thường (giả thuyết).                                                                                                                                                                   |
| Nguyên nhân gốc   | Chưa xác định. Ghi nhận sửa chữa trước đây tại cell này cho rằng rung từ băng tải có thể làm cụm đèn hỏng sau 12–18 tháng; đây là tri thức nháp, chưa được chuyên gia duyệt.                                                                   |
| Tại sao 1W        | Robot không hoàn tất trình tự cấp High Power khi lỗi -924 xuất hiện.                                                                                                                                                                            |
| Tại sao 2W        | Tín hiệu hoặc cụm hiển thị High Power ở bảng điều khiển có dấu hiệu bất thường (giả thuyết).                                                                                                                                                  |
| Tại sao 3W        | Chưa xác định; cần kiểm tra cáp XFP và cụm đèn trước khi kết luận.                                                                                                                                                                              |
| Ý kiến chỉ đạo    | Thực hiện LOTO trước khi thao tác. Kiểm tra và cắm lại cáp XFP trước; chỉ thay cụm đèn bằng linh kiện đúng mã sau khi được chuyên gia xác nhận. Chưa có căn cứ để thay servo amplifier.                                                        |

Công việc

| Hạng mục                   | Nội dung xử lý                                                             | Kết quả                                                   | Tình trạng     |
| -------------------------- | -------------------------------------------------------------------------- | --------------------------------------------------------- | -------------- |
| Bộ điều khiển              | Khởi động lại                                                              | Lỗi -924 vẫn xuất hiện khi yêu cầu cấp High Power         | Đã thực hiện   |
| Dừng khẩn và liên động cửa | Kiểm tra trạng thái                                                        | Không phát hiện bất thường                                | Đã thực hiện   |
| Cáp XFP                    | Sau LOTO, kiểm tra chân giắc, rút và cắm lại rồi thử cấp High Power        | Chưa thực hiện                                            | Chưa thực hiện |
| Cụm đèn High Power        | Nếu lỗi còn, đối chiếu mã và thay thử bằng cụm dự phòng trong tủ line      | Chưa thực hiện; đang chờ chuyên gia xác nhận              | Chưa thực hiện |
| Chạy thử                   | Khi hết lỗi, chạy một chu trình ở tốc độ thấp trước khi trả máy sản xuất   | Chưa thực hiện                                            | Chưa thực hiện |

Kết quả và phê duyệt

| Trường            | Nội dung                                                                                                                                                                                                |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Kết quả xác nhận  | Chưa xác nhận. Robot còn báo lỗi -924 và dây chuyền chưa hoạt động lại.                                                                                                                                |
| Phạm vi áp dụng   | Cell xếp pallet PACK2. Nhận định về cụm đèn chỉ dựa trên ghi nhận riêng của cell này; không áp dụng mặc định cho robot i4L ở vị trí khác.                                                             |
| Tài liệu đính kèm | Chưa có. Cần bổ sung ảnh màn hình lỗi, trạng thái đèn High Power và cáp XFP.                                                                                                                            |
| Ghi chú           | Câu hỏi chờ chuyên gia: có cho phép thay thử cụm đèn dự phòng không, và có cần bổ sung biện pháp chống rung nếu xác nhận cụm đèn hỏng không?                                                           |
| Phê duyệt         | TL: đã trình. SV: chờ duyệt. MGR: chờ duyệt.                                                                                                                                                            |
