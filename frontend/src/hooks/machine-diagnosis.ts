import { useCallback, useEffect, useRef, useState } from "react";
import { diagnosisVizEnabled } from "@/services/config";
import { detectDiagnosisScenario, type DiagnosisScenario } from "@/services/machine-diagnosis";

/** How long the mock "tool call" runs before the panel shows its result. */
export const MOCK_DIAGNOSIS_DELAY_MS = 1100;

export type DiagnosisState = {
  scenario: DiagnosisScenario | null;
  /** The chat turn that triggered the latest diagnosis. */
  turnKey?: string;
  open: boolean;
  phase: "running" | "ready";
};

/**
 * Drive the mock machine-diagnosis panel from chat messages.
 *
 * `trigger` opens the panel when the feature flag is on and the message asks
 * about a machine fault; it returns the chosen scenario, or null when the
 * message does not trigger a diagnosis.
 */
export function useMachineDiagnosis(enabled = diagnosisVizEnabled) {
  const [state, setState] = useState<DiagnosisState>({ scenario: null, open: false, phase: "ready" });
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);

  const trigger = useCallback(
    (message: string, turnKey?: string): DiagnosisScenario | null => {
      if (!enabled) return null;
      const scenario = detectDiagnosisScenario(message);
      if (!scenario) return null;
      clearTimeout(timer.current);
      setState({ scenario, turnKey, open: true, phase: "running" });
      timer.current = setTimeout(() => setState((current) => ({ ...current, phase: "ready" })), MOCK_DIAGNOSIS_DELAY_MS);
      return scenario;
    },
    [enabled],
  );
  const show = useCallback((scenario: DiagnosisScenario, turnKey?: string) => {
    setState((current) => ({ scenario, turnKey, open: true, phase: current.scenario?.id === scenario.id ? current.phase : "ready" }));
  }, []);
  const close = useCallback(() => setState((current) => ({ ...current, open: false })), []);
  const reset = useCallback(() => {
    clearTimeout(timer.current);
    setState({ scenario: null, open: false, phase: "ready" });
  }, []);

  return { enabled, state, trigger, show, close, reset };
}
