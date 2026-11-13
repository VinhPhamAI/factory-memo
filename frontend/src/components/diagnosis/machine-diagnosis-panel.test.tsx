import { act, render, renderHook, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MOCK_DIAGNOSIS_DELAY_MS, useMachineDiagnosis, type DiagnosisState } from "@/hooks/machine-diagnosis";
import { formatMetric, MachineDiagnosisPanel } from "./machine-diagnosis-panel";
import { getScenario } from "@/services/machine-library/registry";

// jsdom has no WebGL or WebGPU; the panel's behaviour does not depend on the scene.
vi.mock("@/components/machine-3d/machine-scene", () => ({
  default: () => <div data-testid="machine-scene" />,
}));

const ready: DiagnosisState = { view: { machine: "cnc-vmc", scenario: "cnc-spindle-overheat" }, open: true, phase: "ready" };

describe("useMachineDiagnosis", () => {
  afterEach(() => vi.useRealTimers());

  it("does nothing while the feature flag is off", () => {
    const { result } = renderHook(() => useMachineDiagnosis(false));
    act(() => {
      expect(result.current.trigger("Máy CNC bị quá nhiệt")).toBeNull();
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
    expect(result.current.state.view).toEqual({ machine: "robot-cell", scenario: "robot-j2-vibration" });
    act(() => vi.advanceTimersByTime(MOCK_DIAGNOSIS_DELAY_MS));
    expect(result.current.state.phase).toBe("ready");
  });

  it("leaves the panel closed for unrelated messages", () => {
    const { result } = renderHook(() => useMachineDiagnosis(true));
    act(() => {
      result.current.trigger("Xin chào");
    });
    expect(result.current.state.view).toBeNull();
  });

  it("opens from an agent answer only for a specific fault", () => {
    const { result } = renderHook(() => useMachineDiagnosis(true));
    act(() => {
      expect(result.current.trigger("Hãy kiểm tra máy CNC trước.", "a", { requireFault: true })).toBeNull();
    });
    act(() => {
      result.current.trigger("Nguyên nhân có thể là xi lanh đẩy thiếu áp suất khí.", "a", { requireFault: true });
    });
    expect(result.current.state.view?.scenario).toBe("conveyor-pusher");
  });
});

describe("MachineDiagnosisPanel", () => {
  it("shows the suspected component, symptoms and a checklist labelled as mock data", async () => {
    render(<MachineDiagnosisPanel state={ready} onClose={() => {}} onOpen={() => {}} />);
    expect(await screen.findByTestId("machine-scene")).toBeInTheDocument();
    expect(screen.getByTestId("diagnosis-suspect")).toHaveTextContent("Trục chính");
    expect(screen.getByTestId("diagnosis-suspect")).toHaveTextContent("Quá nhiệt trục chính");
    expect(screen.getByText("Dữ liệu mô phỏng")).toBeInTheDocument();
    expect(screen.getAllByTestId("diagnosis-metric")).toHaveLength(3);
    await userEvent.click(screen.getByText("Kiểm tra tình trạng bôi trơn ổ trục"));
    expect(screen.getByText("1/6")).toBeInTheDocument();
  });

  it("marks related and healthy components and links back to the suspect", async () => {
    render(<MachineDiagnosisPanel state={ready} onClose={() => {}} onOpen={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Bể & bơm dung dịch làm mát" }));
    expect(screen.getByTestId("diagnosis-related")).toHaveTextContent("Có thể góp phần vào");
    await userEvent.click(screen.getByRole("button", { name: "Servo trục X" }));
    expect(screen.getByTestId("diagnosis-component-ok")).toHaveTextContent("Không có dấu hiệu bất thường");
    await userEvent.click(screen.getByRole("button", { name: /Xem bộ phận nghi vấn/ }));
    expect(screen.getByTestId("diagnosis-suspect")).toBeInTheDocument();
  });

  it("selects the component a checklist item refers to", async () => {
    render(<MachineDiagnosisPanel state={ready} onClose={() => {}} onOpen={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Xem Bể & bơm dung dịch làm mát trên mô hình" }));
    expect(screen.getByRole("button", { name: "Bể & bơm dung dịch làm mát", pressed: true })).toBeInTheDocument();
  });

  it("switches machines and simulates another scenario", async () => {
    render(<MachineDiagnosisPanel state={ready} onClose={() => {}} onOpen={() => {}} />);
    await userEvent.click(screen.getByRole("tab", { name: /Robot cell/ }));
    expect(screen.getByRole("heading", { level: 2, name: /Trạm robot gắp đặt/ })).toBeInTheDocument();
    expect(screen.getByTestId("diagnosis-machine-ok")).toBeInTheDocument();
    expect(screen.queryByTestId("diagnosis-metric")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Tay gắp làm rơi phôi" }));
    expect(screen.getByTestId("diagnosis-suspect")).toHaveTextContent("Tay gắp khí nén");
    await userEvent.click(screen.getByRole("tab", { name: /Máy CNC/ }));
    expect(screen.getByTestId("diagnosis-suspect")).toHaveTextContent("Trục chính");
  });

  it("covers the findings while the mock tool is running", () => {
    render(<MachineDiagnosisPanel state={{ ...ready, phase: "running" }} onClose={() => {}} onOpen={() => {}} />);
    expect(screen.getByTestId("diagnosis-running")).toHaveTextContent("Đang chạy chẩn đoán");
    // Covered checklist controls cannot take keyboard focus.
    expect(screen.getByText("Kiểm tra tình trạng bôi trơn ổ trục").closest("[inert]")).not.toBeNull();
  });

  it("closes with Escape and leaves a reopen button", async () => {
    const onClose = vi.fn();
    const onOpen = vi.fn();
    const { rerender } = render(<MachineDiagnosisPanel state={ready} onClose={onClose} onOpen={onOpen} />);
    await userEvent.click(screen.getByText("Kiểm tra tình trạng bôi trơn ổ trục"));
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

  it("becomes a dialog while expanded beside the chat", async () => {
    render(<MachineDiagnosisPanel state={ready} onClose={() => {}} onOpen={() => {}} />);
    expect(screen.queryByRole("dialog")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Mở rộng khung 3D" }));
    expect(screen.getByRole("dialog", { name: "Chẩn đoán máy" })).toHaveClass("expanded");
  });

  it("stays a nonmodal region when docked beside the chat", () => {
    render(<MachineDiagnosisPanel state={ready} onClose={() => {}} onOpen={() => {}} />);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByTestId("diagnosis-panel")).not.toContainElement(document.activeElement as HTMLElement);
  });

  it("renders nothing before any diagnosis", () => {
    const { container } = render(
      <MachineDiagnosisPanel state={{ view: null, open: false, phase: "ready" }} onClose={() => {}} onOpen={() => {}} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});

describe("formatMetric", () => {
  it("prefixes changes against a target", () => {
    const [temperature, , cycle] = getScenario("cnc-spindle-overheat")!.metrics;
    expect(formatMetric(temperature)).toBe("92 °C");
    expect(formatMetric(cycle)).toBe("+18 %");
  });
});
