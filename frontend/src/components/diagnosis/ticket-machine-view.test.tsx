import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TicketMachineView } from "./ticket-machine-view";

vi.mock("@/components/machine-3d/machine-scene", () => ({
  default: ({ selected }: { selected: string | null }) => <div data-testid="machine-scene">{selected}</div>,
}));

describe("TicketMachineView", () => {
  it("names the suspected component from the ticket text and shows the model at once", async () => {
    render(<TicketMachineView enabled text={"Băng tải line 2\nXi lanh đẩy không ra hết hành trình"} />);
    expect(screen.getByTestId("ticket-machine-view")).toHaveTextContent("Nghi vấn: Xi lanh đẩy loại · Xi lanh đẩy không ra hết hành trình");
    expect(await screen.findByTestId("machine-scene")).toHaveTextContent("pusher");
    await userEvent.click(screen.getByRole("button", { name: /Thu gọn/ }));
    expect(screen.queryByTestId("machine-scene")).toBeNull();
  });

  it("loads the model only on request when it starts collapsed", async () => {
    render(<TicketMachineView enabled defaultOpen={false} text={"Băng tải line 2\nXi lanh đẩy không ra hết hành trình"} />);
    expect(screen.queryByTestId("machine-scene")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /Xem 3D/ }));
    expect(await screen.findByTestId("machine-scene")).toHaveTextContent("pusher");
  });

  it("shows the CNC spindle fault for the scripted demo ticket", async () => {
    render(
      <TicketMachineView
        enabled
        text={[
          "Máy phay CNC VMC-850 Line 3 báo quá nhiệt trục chính sau khoảng 2 giờ chạy",
          "Máy phay CNC VMC-850 ở Line 3 đang gia công vỏ nhôm, chạy khoảng 2 giờ thì báo quá nhiệt trục chính và dừng.",
          "Tia dung dịch làm mát ở vòi phun yếu hơn bình thường, bể có nhiều phoi nhôm.",
        ].join("\n")}
      />,
    );
    expect(screen.getByTestId("ticket-machine-view")).toHaveTextContent("Trung tâm gia công CNC");
    expect(screen.getByTestId("ticket-machine-view")).toHaveTextContent("Nghi vấn: Trục chính · Quá nhiệt trục chính");
    expect(await screen.findByTestId("machine-scene")).toHaveTextContent("spindle");
  });

  it("renders nothing for tickets without a machine or with the flag off", () => {
    const { container, rerender } = render(<TicketMachineView enabled text="Hỏi về lịch nghỉ phép" />);
    expect(container).toBeEmptyDOMElement();
    rerender(<TicketMachineView enabled={false} text="Máy CNC quá nhiệt" />);
    expect(container).toBeEmptyDOMElement();
  });
});
