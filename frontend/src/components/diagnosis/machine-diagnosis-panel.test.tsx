import { act, render, renderHook, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MOCK_DIAGNOSIS_DELAY_MS, useMachineDiagnosis, type DiagnosisState } from "@/hooks/machine-diagnosis";
import { SCENARIOS } from "@/services/machine-diagnosis";
import { MachineDiagnosisPanel } from "./machine-diagnosis-panel";

// jsdom has no WebGL; the panel's behaviour does not depend on the scene.
vi.mock("./production-line-scene", () => ({
  default: () => <div data-testid="diagnosis-scene" />,
}));

const ready: DiagnosisState = { scenario: SCENARIOS.overheating, open: true, phase: "ready" };

describe("useMachineDiagnosis", () => {
  afterEach(() => vi.useRealTimers());

  it("does nothing while the feature flag is off", () => {
    const { result } = renderHook(() => useMachineDiagnosis(false));
    act(() => {
      expect(result.current.trigger("Máy bị quá nhiệt")).toBeNull();
    });
    expect(result.current.state.open).toBe(false);
  });

  it("opens a running diagnosis that becomes ready after the mock delay", () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useMachineDiagnosis(true));
    act(() => {
      result.current.trigger("Robot rung bất thường", "turn-1");
    });
    expect(result.current.state).toMatchObject({ open: true, phase: "running", turnKey: "turn-1" });
    expect(result.current.state.scenario?.id).toBe("vibration");
    act(() => vi.advanceTimersByTime(MOCK_DIAGNOSIS_DELAY_MS));
    expect(result.current.state.phase).toBe("ready");
  });

  it("leaves the panel closed for unrelated messages", () => {
    const { result } = renderHook(() => useMachineDiagnosis(true));
    act(() => {
      result.current.trigger("Xin chào");
    });
    expect(result.current.state.scenario).toBeNull();
  });
});

describe("MachineDiagnosisPanel", () => {
  it("shows the suspected station, symptoms and a checklist labelled as mock data", async () => {
    render(<MachineDiagnosisPanel state={ready} onClose={() => {}} onOpen={() => {}} />);
    expect(await screen.findByTestId("diagnosis-scene")).toBeInTheDocument();
    expect(screen.getByTestId("diagnosis-suspect")).toHaveTextContent("Máy gia công");
    expect(screen.getByTestId("diagnosis-suspect")).toHaveTextContent("Quá nhiệt");
    expect(screen.getByText("Dữ liệu mô phỏng")).toBeInTheDocument();
    expect(screen.getAllByTestId("diagnosis-metric")).toHaveLength(3);
    await userEvent.click(screen.getByText("Kiểm tra tình trạng bôi trơn"));
    expect(screen.getByText("1/5")).toBeInTheDocument();
  });

  it("switches to a healthy station and back to the suspect", async () => {
    render(<MachineDiagnosisPanel state={ready} onClose={() => {}} onOpen={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: /Robot gắp đặt/ }));
    expect(screen.getByTestId("diagnosis-station-ok")).toHaveTextContent("Robot gắp đặt");
    expect(screen.queryByTestId("diagnosis-metric")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Xem trạm nghi vấn/ }));
    expect(screen.getByTestId("diagnosis-suspect")).toBeInTheDocument();
  });

  it("covers the findings while the mock tool is running", () => {
    render(<MachineDiagnosisPanel state={{ ...ready, phase: "running" }} onClose={() => {}} onOpen={() => {}} />);
    expect(screen.getByTestId("diagnosis-running")).toHaveTextContent("Đang chạy chẩn đoán");
    // Covered checklist controls cannot take keyboard focus.
    expect(screen.getByText("Kiểm tra tình trạng bôi trơn").closest("[inert]")).not.toBeNull();
  });

  it("closes with Escape and leaves a reopen button", async () => {
    const onClose = vi.fn();
    const onOpen = vi.fn();
    const { rerender } = render(<MachineDiagnosisPanel state={ready} onClose={onClose} onOpen={onOpen} />);
    await userEvent.click(screen.getByText("Kiểm tra tình trạng bôi trơn"));
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
    rerender(<MachineDiagnosisPanel state={{ ...ready, open: false }} onClose={onClose} onOpen={onOpen} />);
    await userEvent.click(screen.getByTestId("diagnosis-reopen"));
    expect(onOpen).toHaveBeenCalled();
  });

  it("holds focus in an overlay and returns it to the opener on close", async () => {
    const outside = document.createElement("button");
    outside.textContent = "Xem 3D";
    document.body.append(outside);
    outside.focus();
    const { rerender } = render(
      <MachineDiagnosisPanel variant="overlay" state={ready} onClose={() => {}} onOpen={() => {}} />,
    );
    const panel = screen.getByRole("dialog", { name: "Chẩn đoán máy" });
    const close = screen.getByRole("button", { name: "Đóng chẩn đoán" });
    expect(close).toHaveFocus();
    await userEvent.tab({ shift: true });
    expect(panel).toContainElement(document.activeElement as HTMLElement);
    rerender(
      <MachineDiagnosisPanel variant="overlay" state={{ ...ready, open: false }} onClose={() => {}} onOpen={() => {}} />,
    );
    expect(outside).toHaveFocus();
    outside.remove();
  });

  it("keeps Tab inside the overlay after a click on non-interactive content", async () => {
    render(<MachineDiagnosisPanel variant="overlay" state={ready} onClose={() => {}} onOpen={() => {}} />);
    const panel = screen.getByRole("dialog", { name: "Chẩn đoán máy" });
    await userEvent.click(screen.getByText("Triệu chứng phát hiện"));
    expect(panel).toHaveFocus();
    await userEvent.tab();
    expect(panel).toContainElement(document.activeElement as HTMLElement);
    expect(panel).not.toBe(document.activeElement);
  });

  it("stays a nonmodal region when docked beside the chat", () => {
    render(<MachineDiagnosisPanel state={ready} onClose={() => {}} onOpen={() => {}} />);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByTestId("diagnosis-panel")).not.toContainElement(
      document.activeElement as HTMLElement,
    );
  });

  it("renders nothing before any diagnosis", () => {
    const { container } = render(
      <MachineDiagnosisPanel state={{ scenario: null, open: false, phase: "ready" }} onClose={() => {}} onOpen={() => {}} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
