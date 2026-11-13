import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { MachineDiagnosisPanel } from "@/components/diagnosis/machine-diagnosis-panel";
import type { DiagnosisState } from "@/hooks/machine-diagnosis";
import { CNC_MACHINE } from "@/services/machine-library/machines";

const noop = () => {};

/**
 * Standalone 3D machine library at `/machines`, outside the chat.
 *
 * `?machine=<id>&scenario=<id>` opens a machine and an optional simulated
 * fault; the page then works like the expanded chat panel.
 */
export function MachinesPage() {
  const [params] = useSearchParams();
  const machine = params.get("machine") ?? CNC_MACHINE.id;
  const scenario = params.get("scenario") ?? undefined;
  // The panel resets when its view object changes, so keep it stable per URL.
  const state = useMemo<DiagnosisState>(
    () => ({ view: { machine, scenario }, open: true, phase: "ready" }),
    [machine, scenario],
  );
  return <MachineDiagnosisPanel variant="page" state={state} onClose={noop} onOpen={noop} />;
}
