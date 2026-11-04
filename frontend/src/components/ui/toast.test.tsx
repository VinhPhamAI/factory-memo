import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ToastStack,
  TOAST_DURATION_MS,
  useToasts,
  type ToastKind,
} from "./toast";

let show: (kind: ToastKind, message: string) => void = () => {};

function Harness() {
  const toasts = useToasts();
  show = toasts.show;
  return <ToastStack toasts={toasts.toasts} onDismiss={toasts.dismiss} />;
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("toasts", () => {
  it("hide success after 4 s and errors after 8 s without a reload", () => {
    render(<Harness />);
    act(() => {
      show("success", "Đã tải lên “a.pdf”.");
      show("error", "“b.txt”: sai loại file");
    });
    expect(screen.getAllByTestId("toast")).toHaveLength(2);

    act(() => vi.advanceTimersByTime(TOAST_DURATION_MS.success));
    expect(screen.getAllByTestId("toast")).toHaveLength(1);
    expect(screen.getByRole("alert")).toHaveTextContent("b.txt");

    act(() =>
      vi.advanceTimersByTime(
        TOAST_DURATION_MS.error - TOAST_DURATION_MS.success,
      ),
    );
    expect(screen.queryByTestId("toast")).not.toBeInTheDocument();
  });

  it("closes a toast with its close button", () => {
    render(<Harness />);
    act(() => show("success", "Đã xóa “a.pdf”."));
    fireEvent.click(screen.getByRole("button", { name: "Đóng thông báo" }));
    expect(screen.queryByTestId("toast")).not.toBeInTheDocument();
  });
});
