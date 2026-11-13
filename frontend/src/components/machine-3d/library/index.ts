import type { ComponentKind } from "@/services/machine-library/types";
import { conveyor, gearbox, gripper, linearAxis, motor, pneumaticCylinder, rollerConveyor, spindle, toolChanger, workpieceFlow } from "./motion";
import { controlPanel, lightCurtain, plc, sensor, signalTower, valveManifold } from "./control";
import { baseFrame, cabinet, coolantUnit, enclosure, fixture, safetyFence, worktable } from "./structure";
import { robotArm } from "./robot";
import type { ComponentBuilder } from "./types";

/**
 * Builders for every component kind. A new kind needs a builder here and a
 * label in `KIND_LABELS`; machines then place it by name.
 */
export const COMPONENT_BUILDERS: Record<ComponentKind, ComponentBuilder> = {
  "base-frame": baseFrame,
  enclosure,
  cabinet,
  "safety-fence": safetyFence,
  worktable,
  fixture,
  "coolant-unit": coolantUnit,
  "linear-axis": linearAxis,
  spindle,
  "tool-changer": toolChanger,
  motor,
  gearbox,
  conveyor,
  "roller-conveyor": rollerConveyor,
  "robot-arm": robotArm,
  gripper,
  "pneumatic-cylinder": pneumaticCylinder,
  "valve-manifold": valveManifold,
  plc,
  "control-panel": controlPanel,
  "signal-tower": signalTower,
  sensor,
  "light-curtain": lightCurtain,
  "workpiece-flow": workpieceFlow,
};
