import * as THREE from "three";
import { at, pivot, socket, turn, type Kit, type Params } from "../kit";
import type { BuiltComponent } from "./types";

/** Joint angles J1–J6 in radians. */
type Pose = [number, number, number, number, number, number];

const BASE = 0.2;
const SHOULDER_HEIGHT = 0.3;
const SHOULDER_OFFSET = 0.12;
const UPPER = 0.62;
const FORE = 0.6;
const FLANGE = 0.09;

/**
 * Solve the arm's joint angles so the tool tip reaches a point with the tool
 * pointing straight down.
 *
 * The method is planar two-link inverse kinematics: J1 turns the arm plane
 * toward the target, J2 and J3 place the wrist above it, and J5 keeps the tool
 * vertical. Unreachable targets are clamped to the arm's reach.
 *
 * Args:
 *   target: Tool-tip position in the robot base frame (metres).
 *   tool: Distance from the flange to the tool tip.
 *
 * Returns:
 *   Joint angles J1–J6.
 */
export function solvePose(target: THREE.Vector3, tool: number): Pose {
  const j1 = Math.atan2(-target.z, target.x);
  const radial = Math.hypot(target.x, target.z) - SHOULDER_OFFSET;
  const wristHeight = target.y + tool + FLANGE - (BASE + SHOULDER_HEIGHT);
  let distance = Math.hypot(radial, wristHeight);
  distance = Math.min(Math.max(distance, 0.15), UPPER + FORE - 0.01);
  const toWrist = Math.atan2(wristHeight, radial);
  const elbow = Math.acos(Math.min(1, Math.max(-1, (UPPER ** 2 + distance ** 2 - FORE ** 2) / (2 * UPPER * distance))));
  const upperAngle = toWrist + elbow;
  const ex = UPPER * Math.cos(upperAngle);
  const ey = UPPER * Math.sin(upperAngle);
  const wx = distance * Math.cos(toWrist);
  const wy = distance * Math.sin(toWrist);
  const foreAngle = Math.atan2(wy - ey, wx - ex);
  const j2 = upperAngle - Math.PI / 2;
  const j3 = foreAngle - j2;
  const j5 = -Math.PI / 2 - foreAngle;
  return [j1, j2, j3, 0, j5, 0];
}

const ease = (x: number) => (x < 0.5 ? 4 * x * x * x : 1 - (-2 * x + 2) ** 3 / 2);

/**
 * Six-axis articulated robot arm standing on its base at the origin. When J1
 * is zero the arm reaches along +x.
 *
 * Sockets: `j1-motor`, `j2-motor`, `j3-motor` (motor shafts point into the
 * joint), `flange` (tool points along the flange normal).
 * Params: `pick`, `place` ([x, y, z] tool-tip points in the base frame),
 * `tool` (tool length), `cycle` (seconds per pick-and-place).
 */
export function robotArm(p: Params, kit: Kit): BuiltComponent {
  const tool = p.num("tool", 0.16);
  const cycle = p.num("cycle", 7);
  const pickPoint = new THREE.Vector3(...(p.list("pick", [0.8, 0.2, -0.3]) as [number, number, number]));
  const placePoint = new THREE.Vector3(...(p.list("place", [0.8, 0.2, 0.3]) as [number, number, number]));
  const lift = new THREE.Vector3(0, 0.18, 0);
  const group = new THREE.Group();

  // Base and J1 turret.
  group.add(at(kit.cyl(0.19, 0.06, "graphite", 32), 0, 0.03, 0));
  group.add(at(kit.cyl(0.16, BASE - 0.06, "paint", 32), 0, 0.06 + (BASE - 0.06) / 2, 0));
  group.add(at(kit.cyl(0.165, 0.012, "accent", 32), 0, BASE - 0.02, 0));
  const j1 = pivot(group, 0, BASE, 0);
  j1.add(at(kit.cyl(0.15, 0.12, "paint", 32), 0, 0.06, 0));
  j1.add(at(kit.box(0.3, SHOULDER_HEIGHT - 0.1, 0.26, "paint", 0.08), SHOULDER_OFFSET * 0.6, 0.1 + (SHOULDER_HEIGHT - 0.1) / 2, 0));
  j1.add(at(kit.axle(0.13, 0.3, "paintGrey", "z", 32), SHOULDER_OFFSET, SHOULDER_HEIGHT, 0));
  const j1Motor = socket(group, -0.16, 0.12, 0, [0, 0, -Math.PI / 2]);

  // J2 shoulder and upper arm (vertical when J2 = 0).
  const j2 = pivot(j1, SHOULDER_OFFSET, SHOULDER_HEIGHT, 0);
  j2.add(at(kit.box(0.17, UPPER, 0.15, "paint", 0.07), 0, UPPER / 2, 0.04));
  j2.add(at(kit.axle(0.11, 0.2, "paintGrey", "z", 32), 0, UPPER, 0.03));
  const harness = new THREE.Mesh(new THREE.TorusGeometry(0.2, 0.014, 8, 24, Math.PI * 0.9), kit.mat("cable"));
  j2.add(turn(at(harness, -0.08, UPPER * 0.5, 0.13), 0, 0, Math.PI * 0.55));
  const j2Motor = socket(j2, 0, 0, -0.075, [Math.PI / 2, 0, 0]);

  // J3 elbow and forearm (horizontal when J3 = 0), J4 roll inside it.
  const j3 = pivot(j2, 0, UPPER, 0);
  j3.add(at(kit.box(0.22, 0.17, 0.17, "paint", 0.07), 0.02, 0, 0));
  const j3Motor = socket(j3, -0.12, 0.02, 0, [0, 0, -Math.PI / 2]);
  const j4 = pivot(j3, 0.12, 0, 0);
  j4.add(at(kit.axle(0.065, FORE - 0.2, "paint", "x", 28), (FORE - 0.2) / 2, 0, 0));
  j4.add(at(kit.axle(0.072, 0.02, "accent", "x", 28), 0.04, 0, 0));
  j4.add(at(kit.box(0.12, 0.1, 0.15, "paint", 0.04), FORE - 0.17, 0, 0));

  // J5 wrist pitch and J6 flange.
  const j5 = pivot(j4, FORE - 0.12, 0, 0);
  j5.add(at(kit.axle(0.05, 0.13, "paintGrey", "z", 24), 0, 0, 0));
  j5.add(at(kit.axle(0.045, 0.06, "paint", "x", 24), 0.04, 0, 0));
  const j6 = pivot(j5, FLANGE, 0, 0);
  j6.add(at(kit.axle(0.04, 0.015, "chrome", "x", 24), -0.008, 0, 0));
  const flange = socket(j6, 0, 0, 0, [0, 0, Math.PI / 2]);

  const joints = [j1, j2, j3, j4, j5, j6];
  const home: Pose = [0, -0.3, 0.2, 0, -1.4, 0];
  const above = (point: THREE.Vector3) => solvePose(point.clone().add(lift), tool);
  // Key poses of one pick-and-place cycle with their start times (fraction of cycle).
  const keys: [number, Pose][] = [
    [0, above(pickPoint)],
    [0.12, solvePose(pickPoint, tool)],
    [0.22, solvePose(pickPoint, tool)],
    [0.32, above(pickPoint)],
    [0.55, above(placePoint)],
    [0.67, solvePose(placePoint, tool)],
    [0.77, solvePose(placePoint, tool)],
    [0.87, above(placePoint)],
    [1, above(pickPoint)],
  ];
  const apply = (pose: Pose) => {
    joints[0].rotation.y = pose[0];
    joints[1].rotation.z = pose[1];
    joints[2].rotation.z = pose[2];
    joints[3].rotation.x = pose[3];
    joints[4].rotation.z = pose[4];
    joints[5].rotation.x = pose[5];
  };
  apply(cycle > 0 ? keys[0][1] : home);

  return {
    object: group,
    sockets: { "j1-motor": j1Motor, "j2-motor": j2Motor, "j3-motor": j3Motor, flange },
    update(time, _dt, status) {
      if (cycle <= 0) return;
      const phase = (time % cycle) / cycle;
      let index = 0;
      while (index < keys.length - 2 && phase >= keys[index + 1][0]) index += 1;
      const [t0, a] = keys[index];
      const [t1, b] = keys[index + 1];
      const k = ease(Math.min(1, (phase - t0) / (t1 - t0)));
      const pose = a.map((value, i) => value + (b[i] - value) * k) as Pose;
      // A suspected arm shakes at the shoulder while it moves.
      if (status !== "normal") pose[1] += Math.sin(time * 47) * 0.012;
      apply(pose);
    },
  };
}
