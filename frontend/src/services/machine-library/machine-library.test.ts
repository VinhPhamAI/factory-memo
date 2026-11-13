import { describe, expect, it } from "vitest";
import { CNC_MACHINE } from "./machines";
import { componentStatus, getScenario, listMachines, listScenarios, registerMachine, resolveView } from "./registry";
import { foldText, resolveMachineView } from "./resolve";

describe("machine library", () => {
  it("ships the three priority machine types with at least three faults each", () => {
    expect(listMachines().map((machine) => machine.type)).toEqual(["cnc", "conveyor", "robot-cell"]);
    for (const machine of listMachines()) expect(listScenarios(machine.id).length).toBeGreaterThanOrEqual(3);
  });

  it("rejects a scenario that names a missing component", () => {
    const machine = { ...CNC_MACHINE, id: "broken-cnc" };
    const fault = { ...getScenario("cnc-atc")!, id: "broken", machine: "broken-cnc", component: "turbo" };
    expect(() => registerMachine(machine, [fault])).toThrow(/unknown component turbo/);
  });

  it("rejects a mount on a missing component", () => {
    const machine = {
      ...CNC_MACHINE,
      id: "broken-mount",
      components: [{ id: "m", kind: "motor" as const, label: "M", group: "drive" as const, mount: { component: "nope", socket: "x" } }],
    };
    expect(() => registerMachine(machine)).toThrow(/unknown mount nope/);
  });

  it("reports suspected, related and normal components", () => {
    const fault = getScenario("cnc-spindle-overheat");
    expect(componentStatus(fault, "spindle")).toBe("fault");
    expect(componentStatus(fault, "coolant")).toBe("related");
    expect(componentStatus(fault, "servo-x")).toBe("normal");
    expect(componentStatus(undefined, "spindle")).toBe("normal");
  });

  it("ignores a scenario that belongs to another machine", () => {
    expect(resolveView({ machine: "robot-cell", scenario: "cnc-atc" })?.fault).toBeUndefined();
    expect(resolveView({ machine: "unknown" })).toBeNull();
  });
});

describe("resolveMachineView", () => {
  it.each([
    ["Máy gia công số 3 bị quá nhiệt", "cnc-vmc", "cnc-spindle-overheat"],
    ["Spindle is overheating on line A", "cnc-vmc", "cnc-spindle-overheat"],
    ["Máy CNC báo quá tải servo trục X", "cnc-vmc", "cnc-x-servo"],
    ["Thay dao không xong, ổ dao dừng giữa chừng", "cnc-vmc", "cnc-atc"],
    ["Robot rung mạnh khi gắp phôi", "robot-cell", "robot-j2-vibration"],
    ["Tay gắp làm rơi phôi liên tục", "robot-cell", "robot-grip"],
    ["Robot dừng do màn chắn sáng", "robot-cell", "robot-safety-stop"],
    ["Feeder bị kẹt phôi liên tục", "conveyor-line", "conveyor-jam"],
    ["Băng tải trượt, động cơ nóng", "conveyor-line", "conveyor-drive"],
    ["Xi lanh đẩy không ra hết hành trình", "conveyor-line", "conveyor-pusher"],
    ["Camera kiểm tra báo NG nhiều", "conveyor-line", "conveyor-vision"],
  ])("maps %j to %s / %s", (message, machine, scenario) => {
    expect(resolveMachineView(message)).toEqual({ machine, scenario });
  });

  it("prefers a fault on the machine the text names", () => {
    // "quá tải" also matches the conveyor drive; the named machine decides.
    expect(resolveMachineView("Băng tải báo quá tải")?.scenario).toBe("conveyor-drive");
    expect(resolveMachineView("Máy CNC báo quá tải")?.scenario).toBe("cnc-x-servo");
  });

  it("opens a machine without a fault when no symptom is named", () => {
    expect(resolveMachineView("Cho tôi xem robot cell")).toEqual({ machine: "robot-cell" });
    expect(resolveMachineView("Chẩn đoán giúp tôi")).toEqual({ machine: "cnc-vmc" });
  });

  it("takes the symptom from history when the message only asks for a diagnosis", () => {
    const history = ["Băng tải số 2 bị kẹt sản phẩm ở điểm loại"];
    expect(resolveMachineView("Chẩn đoán giúp tôi", history)).toEqual({ machine: "conveyor-line", scenario: "conveyor-jam" });
    expect(resolveMachineView("Xin chào", history)).toBeNull();
  });

  it("does not read 'gặp' (meet) as a gripper symptom", () => {
    expect(resolveMachineView("Robot gặp sự cố")).toEqual({ machine: "robot-cell" });
  });

  it("ignores messages that are not about a machine", () => {
    expect(resolveMachineView("Tóm tắt kết quả cuộc họp hôm qua")).toBeNull();
    expect(resolveMachineView("Xin chào")).toBeNull();
  });

  it("folds Vietnamese diacritics", () => {
    expect(foldText("Quá NHIỆT Đường")).toBe("qua nhiet duong");
  });
});
