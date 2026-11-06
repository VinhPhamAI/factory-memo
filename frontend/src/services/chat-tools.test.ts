import { describe, expect, it } from "vitest";
import { parseChatTool, parseChatTools } from "./chat-tools";

const ID = "3f2b8c1e-5d4a-4e6f-9a7b-0c1d2e3f4a5b";

describe("parseChatTool inquiry tools", () => {
  it("accepts new names, states and fields", () => {
    for (const [name, state] of [
      ["create_inquiry_draft", "results"],
      ["update_inquiry_draft", "results"],
      ["create_inquiry_draft", "needs_choice"],
      ["update_inquiry_draft", "not_draft"],
    ]) {
      const call = parseChatTool({
        id: "t1",
        name,
        state,
        inquiry_id: ID,
        title: "Lỗi servo",
        catalog: [],
        results: [],
      });
      expect(call).toMatchObject({ name, state, inquiry_id: ID, title: "Lỗi servo" });
    }
  });

  it("keeps an empty title and drops an invalid inquiry id", () => {
    const call = parseChatTool({
      id: "t1",
      name: "create_inquiry_draft",
      state: "results",
      inquiry_id: "../x",
      title: "",
    });
    expect(call?.inquiry_id).toBeUndefined();
    expect(call?.title).toBe("");
  });

  it("drops unknown names and states", () => {
    expect(parseChatTool({ id: "t", name: "other", state: "results" })).toBeNull();
    expect(
      parseChatTool({ id: "t", name: "create_inquiry_draft", state: "weird" }),
    ).toBeNull();
    expect(parseChatTools([{ id: "t", name: "x", state: "running" }])).toEqual([]);
  });
});
