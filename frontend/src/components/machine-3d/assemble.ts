import * as THREE from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import type { ComponentSpec, MachineDefinition } from "@/services/machine-library/types";
import { Kit, Params } from "./kit";
import { COMPONENT_BUILDERS } from "./library";
import type { BuiltComponent } from "./library/types";

/** One placed component of an assembled machine. */
export type ComponentHandle = {
  spec: ComponentSpec;
  /** Wrapper placed in its parent; mounted components become its descendants. */
  root: THREE.Group;
  built: BuiltComponent;
  /** Meshes that belong to this component, excluding mounted components. */
  meshes: THREE.Mesh[];
  /** Root position before any run-time effect such as vibration. */
  rest: THREE.Vector3;
};

export type AssembledMachine = {
  root: THREE.Group;
  components: Map<string, ComponentHandle>;
  /** Machine bounds in world space, in metres. */
  bounds: THREE.Box3;
};

const KEPT_ATTRIBUTES = ["position", "normal", "uv"];

/** Copy a mesh's geometry into the owner's space with only the shared attributes. */
function bakedGeometry(mesh: THREE.Mesh, toOwner: THREE.Matrix4): THREE.BufferGeometry {
  const source = mesh.geometry;
  const geometry = new THREE.BufferGeometry();
  for (const name of KEPT_ATTRIBUTES) {
    const attribute = source.getAttribute(name);
    if (attribute) geometry.setAttribute(name, attribute.clone());
  }
  if (source.index) geometry.setIndex(source.index.clone());
  geometry.applyMatrix4(toOwner);
  return geometry;
}

/**
 * Merge static meshes that share a material into one mesh per rigid body.
 *
 * Meshes move with their nearest ancestor marked `userData.rigid` (a joint, a
 * moving carriage or an LOD level) or with the component root. Merging within
 * each rigid body keeps motion intact while cutting draw calls, typically by
 * five to ten times for a sheet-metal guard or a cabinet.
 *
 * Args:
 *   root: Component object at its own origin, before it is mounted.
 *
 * Returns:
 *   The number of meshes removed.
 */
export function mergeStaticMeshes(root: THREE.Object3D): number {
  root.updateMatrixWorld(true);
  const buckets = new Map<THREE.Object3D, Map<string, THREE.Mesh[]>>();
  root.traverse((object) => {
    if (!(object instanceof THREE.Mesh) || object instanceof THREE.InstancedMesh) return;
    if (Array.isArray(object.material) || object.userData.keep) return;
    if (!KEPT_ATTRIBUTES.every((name) => object.geometry.getAttribute(name))) return;
    let owner = object.parent;
    while (owner && owner !== root && !owner.userData.rigid) owner = owner.parent;
    if (!owner) return;
    const key = `${object.material.uuid}:${object.geometry.index ? "i" : "n"}`;
    const byMaterial = buckets.get(owner) ?? new Map<string, THREE.Mesh[]>();
    buckets.set(owner, byMaterial);
    byMaterial.set(key, [...(byMaterial.get(key) ?? []), object]);
  });
  let removed = 0;
  const inverse = new THREE.Matrix4();
  const relative = new THREE.Matrix4();
  for (const [owner, byMaterial] of buckets) {
    inverse.copy(owner.matrixWorld).invert();
    for (const meshes of byMaterial.values()) {
      if (meshes.length < 2) continue;
      const geometries = meshes.map((mesh) => bakedGeometry(mesh, relative.multiplyMatrices(inverse, mesh.matrixWorld)));
      const merged = mergeGeometries(geometries, false);
      geometries.forEach((geometry) => geometry.dispose());
      if (!merged) continue;
      merged.computeBoundingSphere();
      merged.computeBoundingBox();
      const mesh = new THREE.Mesh(merged, meshes[0].material);
      owner.add(mesh);
      for (const original of meshes) {
        original.removeFromParent();
        if (!original.geometry.userData.shared) original.geometry.dispose();
      }
      removed += meshes.length - 1;
    }
  }
  return removed;
}

/** Collect a component's own meshes and set their shadow flags. */
function ownMeshes(object: THREE.Object3D): THREE.Mesh[] {
  const meshes: THREE.Mesh[] = [];
  object.traverse((child) => {
    if (!(child instanceof THREE.Mesh)) return;
    const material = child.material as THREE.Material;
    const clear = material.transparent || material.userData.noShadow;
    child.castShadow = !clear;
    child.receiveShadow = !clear;
    meshes.push(child);
  });
  return meshes;
}

/**
 * Build a machine from its definition with the shared component library.
 *
 * Each component is built at its origin, its static meshes are merged, and it
 * is then placed on the floor or on another component's socket.
 *
 * Args:
 *   machine: Machine definition.
 *   kit: Shared materials and geometry owned by the caller.
 *
 * Returns:
 *   The machine root, component handles by id and the machine bounds.
 *
 * Raises:
 *   Error: A component kind has no builder or a mount names a missing socket.
 */
export function assembleMachine(machine: MachineDefinition, kit: Kit): AssembledMachine {
  const root = new THREE.Group();
  root.name = machine.id;
  const components = new Map<string, ComponentHandle>();
  for (const spec of machine.components) {
    const builder = COMPONENT_BUILDERS[spec.kind];
    if (!builder) throw new Error(`${machine.id}/${spec.id}: no builder for ${spec.kind}.`);
    const built = builder(new Params(spec.params), kit);
    mergeStaticMeshes(built.object);
    const wrapper = new THREE.Group();
    wrapper.name = spec.id;
    wrapper.userData.componentId = spec.id;
    wrapper.add(built.object);
    const meshes = ownMeshes(built.object);
    if (spec.selectable === false) for (const mesh of meshes) mesh.userData.noPick = true;
    if (spec.position) wrapper.position.set(...spec.position);
    if (spec.rotation) wrapper.rotation.set(...spec.rotation);
    components.set(spec.id, { spec, root: wrapper, built, meshes, rest: wrapper.position.clone() });
  }
  for (const handle of components.values()) {
    const mount = handle.spec.mount;
    if (!mount) {
      root.add(handle.root);
      continue;
    }
    const target = components.get(mount.component)?.built.sockets?.[mount.socket];
    if (!target) throw new Error(`${machine.id}/${handle.spec.id}: no socket ${mount.component}.${mount.socket}.`);
    target.add(handle.root);
  }
  root.updateMatrixWorld(true);
  const bounds = new THREE.Box3().setFromObject(root);
  return { root, components, bounds };
}

/** Bounds of a component's own meshes in world space. */
export function componentBounds(handle: ComponentHandle, target = new THREE.Box3()): THREE.Box3 {
  target.makeEmpty();
  for (const mesh of handle.meshes) if (mesh.visible !== false) target.expandByObject(mesh, false);
  if (target.isEmpty()) target.setFromObject(handle.root);
  return target;
}

/** Statistics of a built machine for budgets and tests. */
export function machineStats(root: THREE.Object3D) {
  let meshes = 0;
  let triangles = 0;
  root.traverse((object) => {
    if (!(object instanceof THREE.Mesh)) return;
    // Only the nearest LOD level is counted, as the close-up camera shows it.
    const lod = object.parent?.parent;
    if (lod instanceof THREE.LOD && lod.levels[0]?.object !== object.parent) return;
    meshes += 1;
    const geometry = object.geometry;
    const count = geometry.index ? geometry.index.count : geometry.getAttribute("position").count;
    triangles += (count / 3) * (object instanceof THREE.InstancedMesh ? object.count : 1);
  });
  return { meshes, triangles: Math.round(triangles) };
}

export { Kit };
