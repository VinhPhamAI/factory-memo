import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { MachinesPage } from "./machines";

vi.mock("@/components/machine-3d/machine-scene", () => ({
  default: () => <div data-testid="machine-scene" />,
}));

const renderAt = (url: string) =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <MachinesPage />
    </MemoryRouter>,
  );

describe("MachinesPage", () => {
  it("shows the CNC machine without a fault or close control by default", async () => {
    renderAt("/machines");
    expect(await screen.findByTestId("machine-scene")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: /Trung tâm gia công CNC/ })).toBeInTheDocument();
    expect(screen.getByTestId("diagnosis-machine-ok")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Đóng chẩn đoán" })).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("opens the machine and fault named in the URL", () => {
    renderAt("/machines?machine=robot-cell&scenario=robot-grip");
    expect(screen.getByTestId("diagnosis-suspect")).toHaveTextContent("Tay gắp khí nén");
  });
});
