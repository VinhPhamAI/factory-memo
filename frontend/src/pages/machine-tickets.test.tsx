import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { createMockMachineTicketsApi } from "@/services/machine-tickets";
import { MachineTicketsPage } from "./machine-tickets";

vi.mock("@/components/machine-3d/machine-scene", () => ({
  default: (props: { fault?: { id: string }; selected: string | null }) => (
    <div data-testid="machine-scene" data-fault={props.fault?.id ?? ""} data-selected={props.selected ?? ""} />
  ),
}));

function renderAt(url: string) {
  const api = createMockMachineTicketsApi({ latencyMs: 0 });
  render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/machines/tickets/:ticketId?" element={<MachineTicketsPage api={api} />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("MachineTicketsPage", () => {
  it("opens the newest ticket with its faulty device and description", async () => {
    renderAt("/machines/tickets");
    expect(await screen.findByRole("heading", { level: 1, name: "CNC VMC-850 #3 dừng giữa chu kỳ" })).toBeInTheDocument();
    expect(await screen.findByTestId("machine-scene")).toHaveAttribute("data-fault", "cnc-spindle-overheat");
    const device = screen.getByTestId("ticket-device");
    expect(device).toHaveTextContent("Thiết bị lỗi");
    expect(device).toHaveTextContent("Trục chính");
    expect(device).toHaveTextContent("Quá nhiệt trục chính");
    expect(screen.getByRole("region", { name: "Mô tả" })).toHaveTextContent("vòi làm mát phun yếu");
  });

  it("flies to the faulty device when its name is clicked", async () => {
    renderAt("/machines/tickets/mt-2479");
    const scene = await screen.findByTestId("machine-scene");
    fireEvent.click(within(screen.getByTestId("ticket-device")).getByRole("button", { name: /Cảm biến quang điểm loại/ }));
    expect(scene).toHaveAttribute("data-selected", "pe-divert");
  });

  it("names the device only after the AI diagnosis runs", async () => {
    renderAt("/machines/tickets/mt-2476");
    expect(await screen.findByTestId("machine-scene")).toHaveAttribute("data-fault", "");
    expect(screen.getByTestId("ticket-device")).toHaveTextContent("Chưa xác định thiết bị lỗi");
    fireEvent.click(screen.getByRole("button", { name: "Chạy chẩn đoán AI" }));
    expect(screen.getByTestId("ticket-diagnosing")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("ticket-device")).toHaveTextContent("Servo khớp J2"), { timeout: 3000 });
    expect(screen.getByTestId("machine-scene")).toHaveAttribute("data-fault", "robot-j2-vibration");
    expect(screen.getByTestId("machine-scene")).toHaveAttribute("data-selected", "j2-motor");
  });

  it("walks a repair to done and compares before and after", async () => {
    renderAt("/machines/tickets/mt-2481");
    fireEvent.click(await screen.findByRole("button", { name: "Bắt đầu sửa" }));
    fireEvent.click(await screen.findByRole("button", { name: "Đánh dấu đã sửa" }));
    const after = await screen.findByRole("radio", { name: "Sau khi sửa" });
    expect(after).toHaveAttribute("aria-checked", "true");
    expect(screen.getByTestId("ticket-device")).toHaveTextContent("Đã sửa");
    expect(screen.getByTestId("machine-scene")).toHaveAttribute("data-fault", "");

    fireEvent.click(screen.getByRole("radio", { name: "Khi báo lỗi" }));
    expect(screen.getByTestId("machine-scene")).toHaveAttribute("data-fault", "cnc-spindle-overheat");

    fireEvent.click(screen.getByRole("button", { name: "Xác nhận chạy ổn" }));
    expect(await screen.findByRole("button", { name: "Mở lại" })).toBeInTheDocument();
    expect(screen.getByText("Hoàn tất")).toBeInTheDocument();
  });

  it("shows what was done on a closed ticket", async () => {
    renderAt("/machines/tickets/mt-2470");
    expect(await screen.findByTestId("ticket-device")).toHaveTextContent("Thay đầu nối khí bị nứt");
  });

  it("reports an unknown ticket and links to the newest one", async () => {
    renderAt("/machines/tickets/nope");
    expect(await screen.findByText("Không tìm thấy ticket này.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Mở ticket mới nhất" })).toBeInTheDocument();
  });
});
