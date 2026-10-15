import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ReviewSource } from "./review-source";
import { seedSources } from "@/fixtures/seed";

describe("Expert source review", () => {
  it("keeps edits visible when persistence fails and allows retry", async () => {
    const source = seedSources()[0];
    const save = vi
      .fn()
      .mockRejectedValueOnce(new DOMException("full", "QuotaExceededError"))
      .mockResolvedValueOnce(undefined);
    render(<ReviewSource source={source} onSave={save} onCancel={vi.fn()} />);
    expect(screen.getByRole("heading", { name: "Nguồn" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Máy" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Tóm tắt" })).toBeVisible();
    fireEvent.change(
      screen.getByLabelText("Quan sát chính, hành động và kết quả."),
      { target: { value: "The repair is still pending." } },
    );
    await userEvent.click(screen.getByRole("button", { name: "Lưu nháp" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("chưa được lưu");
    expect(
      screen.getByRole("textbox", {
        name: "Quan sát chính, hành động và kết quả.",
      }),
    ).toHaveValue("The repair is still pending.");
    await userEvent.click(screen.getByRole("button", { name: "Lưu nháp" }));
    await waitFor(() => expect(save).toHaveBeenCalledTimes(2));
    expect(save.mock.calls[1][0].summary).toBe("The repair is still pending.");
    expect(save.mock.calls[1][0].status).toBe("draft");
  });
  it("requires a title but allows unknown machines and an empty summary", async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    render(
      <ReviewSource
        source={{ ...seedSources()[0], title: "", machines: [], summary: "" }}
        onSave={save}
        onCancel={vi.fn()}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Lưu nháp" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("tiêu đề");
    expect(save).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Tiêu đề"), {
      target: { value: "New capture" },
    });
    await userEvent.click(screen.getByRole("button", { name: "Lưu nháp" }));
    expect(save).toHaveBeenCalledWith(
      expect.objectContaining({
        title: "New capture",
        machines: [],
        summary: "",
      }),
    );
  });
});
