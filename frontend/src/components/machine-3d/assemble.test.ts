import * as THREE from "three";
import { afterEach, describe, expect, it } from "vitest";
import { listMachines } from "@/services/machine-library/registry";
import { KIND_LABELS, type ComponentKind } from "@/services/machine-library/types";
import { assembleMachine, machineStats, mergeStaticMeshes } from "./assemble";
import { Kit, Params } from "./kit";
import { COMPONENT_BUILDERS } from "./library";
import { robotArm, solvePose } from "./library/robot";

/** Per-machine budget that keeps 60 FPS headroom on an M-series laptop GPU. */
const TRIANGLE_BUDGET = 250_000;
const MESH_BUDGET = 400;

const kits: Kit[] = [];
const newKit = () => {
  const kit = new Kit();
  kits.push(kit);
  return kit;
};
afterEach(() => kits.splice(0).forEach((kit) => kit.dispose()));

describe("component library", () => {
  it("has a builder and a label for every kind", () => {
    expect(Object.keys(COMPONENT_BUILDERS).sort()).toEqual(Object.keys(KIND_LABELS).sort());
  });

  it("builds every kind with default parameters", () => {
    for (const [kind, builder] of Object.entries(COMPONENT_BUILDERS) as [ComponentKind, (typeof COMPONENT_BUILDERS)[ComponentKind]][]) {
      const built = builder(new Params(), newKit());
      expect(built.object, kind).toBeInstanceOf(THREE.Object3D);
      built.setStatus?.("fault", { fault: true, issue: "Test" });
      built.update?.(1.5, 0.016, "fault");
    }
  });

  it("merges static meshes that share a material", () => {
    const kit = newKit();
    const group = new THREE.Group();
    for (let i = 0; i < 5; i += 1) group.add(kit.box(0.1, 0.1, 0.1, "steel").translateX(i * 0.2));
    const joint = new THREE.Group();
    joint.userData.rigid = true;
    joint.add(kit.box(0.1, 0.1, 0.1, "steel"), kit.box(0.1, 0.1, 0.1, "steel"));
    group.add(joint);
    expect(mergeStaticMeshes(group)).toBe(5);
    // One merged mesh at the root, one inside the moving joint.
    const meshes: THREE.Mesh[] = [];
    group.traverse((object) => object instanceof THREE.Mesh && meshes.push(object));
    expect(meshes).toHaveLength(2);
    expect(joint.children).toHaveLength(1);
  });
});

describe.each(listMachines().map((machine) => [machine.name, machine] as const))("%s", (_name, machine) => {
  it("assembles every component and mount within the render budget", () => {
    const assembled = assembleMachine(machine, newKit());
    expect([...assembled.components.keys()]).toEqual(machine.components.map((component) => component.id));
    for (const handle of assembled.components.values()) {
      if (handle.spec.mount) expect(handle.root.parent?.userData.socket, handle.spec.id).toBe(true);
      expect(handle.meshes.length, handle.spec.id).toBeGreaterThan(0);
    }
    const stats = machineStats(assembled.root);
    expect(stats.triangles).toBeLessThan(TRIANGLE_BUDGET);
    expect(stats.meshes).toBeLessThan(MESH_BUDGET);
    const size = assembled.bounds.getSize(new THREE.Vector3());
    // Real equipment scale in metres.
    expect(Math.max(size.x, size.z)).toBeGreaterThan(2);
    expect(Math.max(size.x, size.z)).toBeLessThan(8);
    expect(assembled.bounds.min.y).toBeGreaterThanOrEqual(-0.01);
  });
});

describe("robot inverse kinematics", () => {
  it.each([
    [0.9, 0.5, 0.4],
    [-0.85, 0.48, 0.65],
    [0.6, 0.9, -0.5],
  ])("places the tool tip at (%f, %f, %f)", (x, y, z) => {
    const kit = newKit();
    const tool = 0.16;
    const arm = robotArm(new Params({ cycle: 0 }), kit);
    const target = new THREE.Vector3(x, y, z);
    const pose = solvePose(target, tool);
    const joints: THREE.Object3D[] = [];
    arm.object.traverse((object) => object.userData.rigid && joints.push(object));
    const [j1, j2, j3, j4, j5, j6] = joints;
    j1.rotation.y = pose[0];
    j2.rotation.z = pose[1];
    j3.rotation.z = pose[2];
    j4.rotation.x = pose[3];
    j5.rotation.z = pose[4];
    j6.rotation.x = pose[5];
    arm.object.updateMatrixWorld(true);
    // The tool hangs `tool` metres below the flange along the flange socket's -y.
    const flange = arm.sockets!.flange;
    const tip = flange.localToWorld(new THREE.Vector3(0, -tool, 0));
    expect(tip.distanceTo(target)).toBeLessThan(0.01);
  });
});
