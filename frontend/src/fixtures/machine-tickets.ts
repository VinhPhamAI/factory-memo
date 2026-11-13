/**
 * Synthetic repair tickets for the `/machines/tickets` demo.
 *
 * People and descriptions are invented for the demo; they are not factory
 * records, alarm codes or an approved procedure. Times are set relative to the
 * page load so the demo always looks recent.
 */
import type { MachineTicket } from "@/services/machine-tickets";

const ago = (now: Date, minutes: number) => new Date(now.getTime() - minutes * 60_000).toISOString();

const OPERATOR = "Nguyễn Thu Hà";
const TECHNICIAN = "Trần Minh Khoa";
const SHIFT_LEAD = "Lê Quốc Bảo";

/** Fresh copies of the demo tickets, newest first. */
export function MACHINE_TICKET_FIXTURES(now: Date): MachineTicket[] {
  return [
    {
      id: "mt-2481",
      key: "TK-2481",
      title: "CNC VMC-850 #3 dừng giữa chu kỳ",
      stage: "diagnosed",
      view: { machine: "cnc-vmc", scenario: "cnc-spindle-overheat" },
      location: "Line 2 · Gia công · VMC-850 #3",
      createdAt: ago(now, 42),
      updatedAt: ago(now, 31),
      description: "HMI báo quá nhiệt trục chính ở chu kỳ 14. Vỏ trục chính nóng, vòi làm mát phun yếu.",
      events: [
        { id: "mt-2481-e1", kind: "reported", actor: OPERATOR, at: ago(now, 42) },
        { id: "mt-2481-e2", kind: "diagnosed", actor: "Trợ lý AI", at: ago(now, 31) },
      ],
    },
    {
      id: "mt-2479",
      key: "TK-2479",
      title: "Băng tải phân loại kẹt hàng",
      stage: "repairing",
      view: { machine: "conveyor-line", scenario: "conveyor-jam" },
      location: "Line 4 · Đóng gói · Băng tải phân loại",
      createdAt: ago(now, 3 * 60 + 10),
      updatedAt: ago(now, 50),
      description: "Sản phẩm dồn trước xi lanh đẩy loại, băng dừng 7 lần trong một giờ. Đèn cảm biến sáng liên tục.",
      events: [
        { id: "mt-2479-e1", kind: "reported", actor: SHIFT_LEAD, at: ago(now, 3 * 60 + 10) },
        { id: "mt-2479-e2", kind: "diagnosed", actor: "Trợ lý AI", at: ago(now, 3 * 60) },
        { id: "mt-2479-e3", kind: "started", actor: TECHNICIAN, at: ago(now, 50) },
      ],
    },
    {
      id: "mt-2476",
      key: "TK-2476",
      title: "Robot gắp phôi rung mạnh",
      stage: "reported",
      view: { machine: "robot-cell", scenario: "robot-j2-vibration" },
      location: "Line 1 · Lắp ráp · Robot cell A",
      createdAt: ago(now, 12),
      updatedAt: ago(now, 12),
      description: "Tay robot rung và có tiếng gõ khi vươn xa để đặt phôi. Đã giảm tốc độ xuống 50 %.",
      events: [{ id: "mt-2476-e1", kind: "reported", actor: OPERATOR, at: ago(now, 12) }],
    },
    {
      id: "mt-2470",
      key: "TK-2470",
      title: "CNC VMC-850 #1 thay dao lỗi",
      stage: "closed",
      view: { machine: "cnc-vmc", scenario: "cnc-atc" },
      location: "Line 2 · Gia công · VMC-850 #1",
      createdAt: ago(now, 26 * 60),
      updatedAt: ago(now, 22 * 60),
      description: "Máy dừng ở bước thay dao T05 → T12; ổ dao chưa về vị trí. Có tiếng xì ở đầu nối khí.",
      events: [
        { id: "mt-2470-e1", kind: "reported", actor: SHIFT_LEAD, at: ago(now, 26 * 60) },
        { id: "mt-2470-e2", kind: "diagnosed", actor: "Trợ lý AI", at: ago(now, 26 * 60 - 5) },
        { id: "mt-2470-e3", kind: "started", actor: TECHNICIAN, at: ago(now, 25 * 60) },
        { id: "mt-2470-e4", kind: "fixed", actor: TECHNICIAN, at: ago(now, 23 * 60), text: "Thay đầu nối khí bị nứt; áp suất về 0.6 MPa." },
        { id: "mt-2470-e5", kind: "verified", actor: SHIFT_LEAD, at: ago(now, 22 * 60) },
      ],
    },
  ];
}
