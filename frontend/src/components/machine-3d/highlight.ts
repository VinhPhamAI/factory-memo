import * as THREE from "three";
import { CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";
import type { ComponentStatus } from "@/services/machine-library/types";
import { componentBounds, type ComponentHandle } from "./assemble";
import { STATUS_COLORS } from "./kit";

/** How strongly a component is marked; later entries win. */
export type Mark = "none" | "hover" | "related" | "selected" | "fault";

const TINTS: Record<Exclude<Mark, "none">, { color: string; opacity: number }> = {
  hover: { color: STATUS_COLORS.selected, opacity: 0.16 },
  related: { color: STATUS_COLORS.related, opacity: 0.3 },
  selected: { color: STATUS_COLORS.selected, opacity: 0.28 },
  fault: { color: STATUS_COLORS.fault, opacity: 0.42 },
};

type Overlay = { mark: Mark; meshes: THREE.Mesh[]; bracket?: THREE.LineSegments };

/** Short line segments at the eight corners of a box, like a CAD selection. */
function bracketGeometry(box: THREE.Box3): THREE.BufferGeometry {
  const size = box.getSize(new THREE.Vector3());
  const arm = Math.max(0.04, Math.min(size.x, size.y, size.z) * 0.25);
  const points: number[] = [];
  for (const x of [box.min.x, box.max.x])
    for (const y of [box.min.y, box.max.y])
      for (const z of [box.min.z, box.max.z]) {
        const sx = x === box.min.x ? 1 : -1;
        const sy = y === box.min.y ? 1 : -1;
        const sz = z === box.min.z ? 1 : -1;
        points.push(x, y, z, x + sx * arm, y, z, x, y, z, x, y + sy * arm, z, x, y, z, x, y, z + sz * arm);
      }
  return new THREE.BufferGeometry().setAttribute("position", new THREE.Float32BufferAttribute(points, 3));
}

/**
 * Tints components by status and selection, draws selection brackets and
 * keeps HTML markers for the suspected and selected components.
 *
 * Tints are transparent overlays that reuse the component's geometry, so the
 * shared materials of the library never change.
 */
export class Highlighter {
  private readonly materials = new Map<Exclude<Mark, "none">, THREE.MeshBasicMaterial>();
  private readonly overlays = new Map<string, Overlay>();
  private readonly brackets = {
    selected: new THREE.LineBasicMaterial({ color: STATUS_COLORS.selected, transparent: true, opacity: 0.9, depthTest: false }),
    fault: new THREE.LineBasicMaterial({ color: STATUS_COLORS.fault, transparent: true, opacity: 0.9, depthTest: false }),
  };
  private readonly markers = new Map<string, CSS2DObject>();

  constructor() {
    for (const [mark, tint] of Object.entries(TINTS) as [Exclude<Mark, "none">, { color: string; opacity: number }][]) {
      this.materials.set(
        mark,
        new THREE.MeshBasicMaterial({
          color: tint.color,
          transparent: true,
          opacity: tint.opacity,
          depthWrite: false,
          polygonOffset: true,
          polygonOffsetFactor: -1,
          polygonOffsetUnits: -2,
          toneMapped: false,
        }),
      );
    }
  }

  /** Set the overlay of one component, rebuilding it only when the mark changes. */
  mark(handle: ComponentHandle, mark: Mark) {
    const id = handle.spec.id;
    const current = this.overlays.get(id);
    if ((current?.mark ?? "none") === mark) return;
    this.clear(id);
    if (mark === "none") return;
    const material = this.materials.get(mark)!;
    const meshes = handle.meshes
      .filter((mesh) => !(mesh.material as THREE.Material).userData.noPick)
      .map((mesh) => {
        const overlay =
          mesh instanceof THREE.InstancedMesh
            ? Object.assign(new THREE.InstancedMesh(mesh.geometry, material, mesh.count), { instanceMatrix: mesh.instanceMatrix })
            : new THREE.Mesh(mesh.geometry, material);
        overlay.userData.overlay = true;
        overlay.raycast = () => {};
        overlay.renderOrder = 2;
        overlay.frustumCulled = mesh.frustumCulled;
        mesh.add(overlay);
        return overlay;
      });
    let bracket: THREE.LineSegments | undefined;
    if (mark === "selected" || mark === "fault") {
      handle.root.updateMatrixWorld(true);
      const box = componentBounds(handle).applyMatrix4(handle.root.matrixWorld.clone().invert());
      box.expandByScalar(0.03);
      bracket = new THREE.LineSegments(bracketGeometry(box), this.brackets[mark]);
      bracket.renderOrder = 3;
      bracket.raycast = () => {};
      handle.root.add(bracket);
    }
    this.overlays.set(id, { mark, meshes, bracket });
  }

  private clear(id: string) {
    const overlay = this.overlays.get(id);
    if (!overlay) return;
    for (const mesh of overlay.meshes) mesh.removeFromParent();
    if (overlay.bracket) {
      overlay.bracket.removeFromParent();
      overlay.bracket.geometry.dispose();
    }
    this.overlays.delete(id);
  }

  /** Pulse the fault tint; `heat` adds an orange glow. */
  pulse(time: number, heat: boolean, reduceMotion: boolean) {
    const wave = reduceMotion ? 0.6 : 0.5 + 0.5 * Math.sin(time * 4);
    const fault = this.materials.get("fault")!;
    fault.opacity = 0.22 + 0.32 * wave;
    fault.color.set(heat ? (wave > 0.5 ? "#ff5a1f" : STATUS_COLORS.fault) : STATUS_COLORS.fault);
    this.materials.get("related")!.opacity = 0.18 + 0.14 * wave;
    this.brackets.fault.opacity = 0.5 + 0.5 * wave;
  }

  /**
   * Place or remove an HTML marker above a component.
   *
   * Args:
   *   handle: Component to mark.
   *   element: Marker element, or null to remove the marker.
   */
  marker(handle: ComponentHandle, element: HTMLElement | null) {
    const id = handle.spec.id;
    const existing = this.markers.get(id);
    if (existing) {
      existing.element.remove();
      existing.removeFromParent();
      this.markers.delete(id);
    }
    if (!element) return;
    handle.root.updateMatrixWorld(true);
    const box = componentBounds(handle);
    const top = new THREE.Vector3((box.min.x + box.max.x) / 2, box.max.y + 0.06, (box.min.z + box.max.z) / 2);
    const object = new CSS2DObject(element);
    object.position.copy(handle.root.worldToLocal(top));
    object.center.set(0.5, 1);
    handle.root.add(object);
    this.markers.set(id, object);
  }

  dispose() {
    for (const id of [...this.overlays.keys()]) this.clear(id);
    for (const object of this.markers.values()) {
      object.element.remove();
      object.removeFromParent();
    }
    this.markers.clear();
    for (const material of this.materials.values()) material.dispose();
    this.brackets.selected.dispose();
    this.brackets.fault.dispose();
  }
}

/** Overlay mark for a component given its fault status and the UI state. */
export function markFor(status: ComponentStatus, selected: boolean, hovered: boolean): Mark {
  if (status === "fault") return "fault";
  if (selected) return "selected";
  if (status === "related") return "related";
  return hovered ? "hover" : "none";
}
