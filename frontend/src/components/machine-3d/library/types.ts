import type * as THREE from "three";
import type { ComponentStatus } from "@/services/machine-library/types";
import type { Kit, Params } from "../kit";

/** Machine-wide state every component may reflect, e.g. a red tower lamp. */
export type MachineState = {
  /** The machine has a suspected fault. */
  fault: boolean;
  /** Short issue label for screens, e.g. "Spindle overheating". */
  issue?: string;
};

/**
 * Result of building one component.
 *
 * Conventions: the object is built at the origin of its mount point. Floor
 * items stand on y = 0. Mounted items (motors, sensors) are documented by
 * their builder; sockets carry the orientation a mounted item expects.
 */
export type BuiltComponent = {
  object: THREE.Object3D;
  /** Named attachment points for other components. */
  sockets?: Record<string, THREE.Object3D>;
  /** Advance motion; called only while motion is enabled. */
  update?(time: number, dt: number, status: ComponentStatus): void;
  /** Reflect a status change on lamps, LEDs or screens. */
  setStatus?(status: ComponentStatus, machine: MachineState): void;
};

export type ComponentBuilder = (params: Params, kit: Kit) => BuiltComponent;
