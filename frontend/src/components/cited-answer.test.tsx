import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CitedAnswer } from "@/components/cited-answer";
import type { Citation } from "@/services/conversations-api";

const citation: Citation = {
  marker: "S1",
  evidence_id: "e1",
  title: "Manual",
  excerpt: "Kiểm tra cáp encoder.",
  location: "tr. 12",
};

describe("CitedAnswer markdown", () => {
  it("renders lists, tables, task lists and code blocks", () => {
    const { container } = render(
      <CitedAnswer
        text={[
          "## Cách xử lý",
          "",
          "1. Tắt nguồn",
          "2. Kiểm tra cáp",
          "",
          "| Mã | Ý nghĩa |",
          "| --- | --- |",
          "| AL16 | Lỗi encoder |",
          "",
          "- [x] Đã kiểm tra",
          "- [ ] Chưa thay",
          "",
          "```",
          "SERVO ON",
          "```",
        ].join("\n")}
        citations={[]}
      />,
    );
    expect(
      screen.getByRole("heading", { name: "Cách xử lý" }),
    ).toBeInTheDocument();
    expect(container.querySelectorAll("ol > li")).toHaveLength(2);
    const table = container.querySelector(".answer-table table");
    expect(table).not.toBeNull();
    expect(
      within(table as HTMLElement).getByRole("columnheader", { name: "Mã" }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("checkbox")).toHaveLength(2);
    expect(container.querySelector("pre code")).toHaveTextContent("SERVO ON");
  });

  it("turns saved markers into buttons but leaves markers in code alone", () => {
    render(
      <CitedAnswer
        text={"Thay cáp [S1].\n\n`[S1]`\n\n```\n[S1]\n```"}
        citations={[citation]}
      />,
    );
    expect(screen.getAllByRole("button", { name: "Mở nguồn S1" })).toHaveLength(
      1,
    );
    expect(screen.getAllByText("[S1]")).toHaveLength(3);
  });
});
