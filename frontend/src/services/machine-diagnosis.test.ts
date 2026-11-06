import { describe, expect, it } from "vitest";
import { detectDiagnosisScenario, formatMetric, SCENARIOS } from "./machine-diagnosis";

describe("detectDiagnosisScenario", () => {
  it.each([
    ["Máy gia công số 3 bị quá nhiệt", "overheating"],
    ["Spindle is overheating on line A", "overheating"],
    ["Robot rung mạnh khi gắp phôi", "vibration"],
    ["Feeder bị kẹt phôi liên tục", "feeder-jam"],
    ["Camera kiểm tra báo NG nhiều", "inspection-reject"],
    ["Chẩn đoán giúp tôi dây chuyền này", "overheating"],
  ])("maps %j to %s", (message, id) => {
    expect(detectDiagnosisScenario(message)?.id).toBe(id);
  });

  it("ignores messages that are not about a machine fault", () => {
    expect(detectDiagnosisScenario("Tóm tắt kết quả cuộc họp hôm qua")).toBeNull();
    expect(detectDiagnosisScenario("Xin chào")).toBeNull();
  });
});

describe("formatMetric", () => {
  it("prefixes changes against a target", () => {
    const [temperature, , cycle] = SCENARIOS.overheating.metrics;
    expect(formatMetric(temperature)).toBe("92 °C");
    expect(formatMetric(cycle)).toBe("+18 %");
  });
});
