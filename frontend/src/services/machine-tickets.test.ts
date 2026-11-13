import { describe, expect, it } from "vitest";
import { MACHINE_TICKET_FIXTURES } from "@/fixtures/machine-tickets";
import { resolveView } from "@/services/machine-library/registry";
import {
  actionsFor,
  applyAction,
  createMockMachineTicketsApi,
  fixNote,
  isRepaired,
  showsFault,
  TicketActionError,
} from "./machine-tickets";

const NOW = new Date("2026-11-13T09:00:00Z");
const fixtures = () => MACHINE_TICKET_FIXTURES(NOW);

describe("machine ticket fixtures", () => {
  it("point at existing machines and scenarios", () => {
    for (const ticket of fixtures()) expect(resolveView(ticket.view)?.fault?.id, ticket.id).toBe(ticket.view.scenario);
  });
});

describe("repair flow", () => {
  it("allows only the next step at each stage", () => {
    expect(actionsFor("reported")).toEqual(["diagnose"]);
    expect(actionsFor("diagnosed")).toEqual(["start"]);
    expect(actionsFor("repairing")).toEqual(["fix"]);
    expect(actionsFor("verifying")).toEqual(["verify", "reopen"]);
    expect(actionsFor("closed")).toEqual(["reopen"]);
  });

  it("shows the fault from diagnosis until the repair is marked done", () => {
    expect(showsFault("reported")).toBe(false);
    expect(showsFault("diagnosed")).toBe(true);
    expect(showsFault("repairing")).toBe(true);
    expect(showsFault("verifying")).toBe(false);
    expect(isRepaired("verifying")).toBe(true);
    expect(isRepaired("closed")).toBe(true);
  });

  it("records each step without changing the original ticket", () => {
    const reported = fixtures().find((ticket) => ticket.stage === "reported")!;
    const next = applyAction(reported, "diagnose", "Trợ lý AI", NOW.toISOString());
    expect(next.stage).toBe("diagnosed");
    expect(next.events.at(-1)).toMatchObject({ kind: "diagnosed", actor: "Trợ lý AI", at: NOW.toISOString() });
    expect(reported.stage).toBe("reported");
  });

  it("keeps what the technician did as the fix note", () => {
    const repairing = fixtures().find((ticket) => ticket.stage === "repairing")!;
    expect(fixNote(repairing)).toBeUndefined();
    expect(fixNote(applyAction(repairing, "fix", "KTV", NOW.toISOString(), "Vệ sinh cảm biến."))).toBe("Vệ sinh cảm biến.");
  });

  it("rejects a step that does not follow the current stage", () => {
    expect(() => applyAction(fixtures()[0], "verify", "KTV", NOW.toISOString())).toThrow(TicketActionError);
  });
});

describe("mock tickets API", () => {
  it("keeps changes in memory and returns copies", async () => {
    const api = createMockMachineTicketsApi({ latencyMs: 0, now: () => NOW });
    const [first] = await api.list();
    first.title = "changed locally";
    expect((await api.get(first.id)).title).not.toBe("changed locally");
    expect((await api.act(first.id, "start")).stage).toBe("repairing");
    expect((await api.get(first.id)).stage).toBe("repairing");
    await expect(api.get("missing")).rejects.toThrow(TicketActionError);
  });
});
