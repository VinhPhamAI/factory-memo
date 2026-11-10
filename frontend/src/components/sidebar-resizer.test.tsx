import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { SidebarResizer } from "@/components/qa-shell";

function Harness() {
  const [width, setWidth] = useState(248);
  return <SidebarResizer width={width} onResize={setWidth} />;
}

describe("SidebarResizer", () => {
  it("resizes with the arrow keys within 248–496px", () => {
    render(<Harness />);
    const handle = screen.getByRole("separator", { name: "Độ rộng thanh bên" });
    fireEvent.keyDown(handle, { key: "ArrowLeft" });
    expect(handle).toHaveAttribute("aria-valuenow", "248");
    fireEvent.keyDown(handle, { key: "ArrowRight" });
    expect(handle).toHaveAttribute("aria-valuenow", "264");
    for (let press = 0; press < 30; press += 1)
      fireEvent.keyDown(handle, { key: "ArrowRight" });
    expect(handle).toHaveAttribute("aria-valuenow", "496");
  });

  it("resets to the default width on double click", () => {
    render(<Harness />);
    const handle = screen.getByRole("separator");
    fireEvent.keyDown(handle, { key: "ArrowRight" });
    fireEvent.doubleClick(handle);
    expect(handle).toHaveAttribute("aria-valuenow", "248");
  });
});
