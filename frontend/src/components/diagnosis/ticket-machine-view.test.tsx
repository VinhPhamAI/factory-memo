import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TicketMachineView } from "./ticket-machine-view";

vi.mock("@/components/machine-3d/machine-scene", () => ({
  default: ({ selected }: { selected: string | null }) => <div data-testid="machine-scene">{selected}</div>,
}));

describe("TicketMachineView", () => {
  it("names the suspected component from the ticket text and opens the model on request", async () => {
    render(<TicketMachineView enabled text={"Băng tải line 2\nXi lanh đẩy không ra hết hành trình"} />);
    expect(screen.getByTestId("ticket-machine-view")).toHaveTextContent("Nghi vấn: Xi lanh đẩy loại · Xi lanh đẩy không ra hết hành trình");
    expect(screen.queryByTestId("machine-scene")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /Xem 3D/ }));
    expect(await screen.findByTestId("machine-scene")).toHaveTextContent("pusher");
  });

  it("renders nothing for tickets without a machine or with the flag off", () => {
    const { container, rerender } = render(<TicketMachineView enabled text="Hỏi về lịch nghỉ phép" />);
    expect(container).toBeEmptyDOMElement();
    rerender(<TicketMachineView enabled={false} text="Máy CNC quá nhiệt" />);
    expect(container).toBeEmptyDOMElement();
  });
});
