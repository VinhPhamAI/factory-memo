import { useCallback, useEffect, useRef, useState } from "react";
import { diagnosisVizEnabled } from "@/services/config";
import { resolveMachineView } from "@/services/machine-library/resolve";
import type { MachineView } from "@/services/machine-library/types";

/** How long the mock "tool call" runs before the panel shows its result. */
export const MOCK_DIAGNOSIS_DELAY_MS = 1100;

export type DiagnosisState = {
  view: MachineView | null;
  /** The chat turn that triggered the latest diagnosis. */
  turnKey?: string;
  open: boolean;
  phase: "running" | "ready";
};

/**
 * Drive the 3D machine-diagnosis panel from chat messages, agent answers and
 * tickets.
 *
 * `trigger` resolves a message (with optional conversation history) to a
 * machine and fault and opens the panel; it returns the view, or null when
 * the feature flag is off or the text is not about a machine. With
 * `requireFault`, only a specific fault opens the panel, which keeps general
 * agent answers from opening it. `show` opens a known view directly, e.g.
 * from a ticket or the chat card.
 */
export function useMachineDiagnosis(enabled = diagnosisVizEnabled) {
  const [state, setState] = useState<DiagnosisState>({ view: null, open: false, phase: "ready" });
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);

  const trigger = useCallback(
    (message: string, turnKey?: string, options: { history?: string[]; requireFault?: boolean } = {}): MachineView | null => {
      if (!enabled) return null;
      const view = resolveMachineView(message, options.history);
      if (!view || (options.requireFault && !view.scenario)) return null;
      clearTimeout(timer.current);
      setState({ view, turnKey, open: true, phase: "running" });
      timer.current = setTimeout(() => setState((current) => ({ ...current, phase: "ready" })), MOCK_DIAGNOSIS_DELAY_MS);
      return view;
    },
    [enabled],
  );
  const show = useCallback((view: MachineView, turnKey?: string) => {
    setState((current) => ({
      view,
      turnKey,
      open: true,
      phase: current.view?.machine === view.machine && current.view?.scenario === view.scenario ? current.phase : "ready",
    }));
  }, []);
  const close = useCallback(() => setState((current) => ({ ...current, open: false })), []);
  const reset = useCallback(() => {
    clearTimeout(timer.current);
    setState({ view: null, open: false, phase: "ready" });
  }, []);

  return { enabled, state, trigger, show, close, reset };
}
