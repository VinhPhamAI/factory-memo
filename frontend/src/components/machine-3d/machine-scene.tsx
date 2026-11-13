import { useEffect, useRef, type RefObject } from "react";
import type { FaultScenario, MachineDefinition } from "@/services/machine-library/types";
import { rendererPreference } from "./renderer";
import { MachineViewer, type ViewerStats } from "./viewer";
import "./machine-3d.css";

/** Camera commands the panel toolbar sends to the scene. */
export type SceneApi = { resetView(): void; focus(component: string | null, options?: { minDistance?: number }): void };

type Props = {
  machine: MachineDefinition;
  fault?: FaultScenario;
  selected: string | null;
  /** Components the user chose not to display. */
  hidden?: readonly string[];
  xray: boolean;
  motion: boolean;
  autoRotate: boolean;
  onSelect(component: string): void;
  onError(message: string): void;
  onReady?(backend: ViewerStats["backend"]): void;
  onStats?(stats: ViewerStats): void;
  apiRef?: RefObject<SceneApi | null>;
};

/**
 * React host for one `MachineViewer`. A new machine builds a new viewer;
 * fault, selection and view toggles update the running one. Loaded lazily so
 * three.js downloads only when a 3D view opens.
 */
export default function MachineScene(props: Props) {
  const { machine, fault, selected, hidden, xray, motion, autoRotate, apiRef } = props;
  const host = useRef<HTMLDivElement>(null);
  const viewer = useRef<MachineViewer | null>(null);
  // Latest props for the async creation step and viewer callbacks.
  const latest = useRef(props);
  useEffect(() => {
    latest.current = props;
  });

  useEffect(() => {
    const container = host.current;
    if (!container) return;
    let cancelled = false;
    let created: MachineViewer | null = null;
    MachineViewer.create(
      container,
      machine,
      {
        onSelect: (id) => latest.current.onSelect(id),
        onStats: (stats) => latest.current.onStats?.(stats),
      },
      rendererPreference(),
    )
      .then((instance) => {
        if (cancelled) {
          instance.dispose();
          return;
        }
        created = instance;
        viewer.current = instance;
        const current = latest.current;
        instance.setFault(current.fault);
        instance.setSelected(current.selected);
        instance.setHidden(current.hidden ?? []);
        instance.setXray(current.xray);
        instance.setMotion(current.motion);
        instance.setAutoRotate(current.autoRotate);
        if (apiRef) apiRef.current = { resetView: () => instance.resetView(), focus: (id, options) => instance.focus(id, options) };
        current.onReady?.(instance.backend);
      })
      .catch(() => {
        if (!cancelled) latest.current.onError("Trình duyệt không hỗ trợ WebGPU hoặc WebGL nên không hiển thị được mô hình 3D.");
      });
    return () => {
      cancelled = true;
      created?.dispose();
      viewer.current = null;
      if (apiRef) apiRef.current = null;
    };
  }, [machine, apiRef]);

  useEffect(() => viewer.current?.setFault(fault), [fault]);
  useEffect(() => viewer.current?.setSelected(selected), [selected]);
  useEffect(() => viewer.current?.setHidden(hidden ?? []), [hidden]);
  useEffect(() => viewer.current?.setXray(xray), [xray]);
  useEffect(() => viewer.current?.setMotion(motion), [motion]);
  useEffect(() => viewer.current?.setAutoRotate(autoRotate), [autoRotate]);

  return <div ref={host} className="m3-scene" data-testid="machine-scene" />;
}
