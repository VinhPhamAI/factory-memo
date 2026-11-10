import { describe, expect, it } from "vitest";
import { joinProposal, splitProposal } from "@/services/tickets";

describe("joinProposal", () => {
  it("returns the own text when there is no proposal", () => {
    expect(joinProposal("sửa van", null)).toBe("sửa van");
  });

  it("round-trips through splitProposal", () => {
    for (const own of ["sửa van", ""]) {
      const joined = joinProposal(own, "Các bước:\n1. Dừng máy");
      expect(splitProposal(joined)).toEqual({
        own,
        proposal: "Các bước:\n1. Dừng máy",
      });
    }
  });
});
