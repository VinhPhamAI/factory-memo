import * as THREE from "three";
import { at, pivot, placement, socket, turn, type Kit, type Params } from "../kit";
import type { BuiltComponent } from "./types";

/**
 * Drives and moving mechanisms.
 */

const smooth = (x: number) => x * x * (3 - 2 * x);

/**
 * Servo or induction motor. The mounting flange is at the origin with the
 * shaft pointing +y into the driven part; the body extends along -y.
 *
 * Params: `type` (servo | induction), `radius`, `length`, `finish`.
 */
export function motor(p: Params, kit: Kit): BuiltComponent {
  const type = p.str<"servo" | "induction">("type", "servo");
  const r = p.num("radius", 0.07);
  const length = p.num("length", 0.22);
  const finish = p.str<"paintDark" | "blue" | "graphite" | "paint">("finish", type === "servo" ? "graphite" : "blue");
  const group = new THREE.Group();
  group.add(at(kit.cyl(0.012, 0.04, "chrome", 12), 0, 0.02, 0));
  if (type === "servo") {
    const side = r * 2;
    group.add(at(kit.box(side + 0.02, 0.025, side + 0.02, "aluminum", 0.006), 0, -0.0125, 0));
    group.add(at(kit.box(side, length * 0.78, side, finish, r * 0.35), 0, -0.025 - (length * 0.78) / 2, 0));
    group.add(at(kit.box(side * 0.86, length * 0.2, side * 0.86, "paintDark", r * 0.3), 0, -0.025 - length * 0.78 - length * 0.1, 0));
    // Power and encoder connectors with short cable stubs.
    for (const [z, size] of [[-0.25, 0.034], [0.25, 0.026]] as const) {
      group.add(at(kit.box(size, size, size * 1.2, "graphite", 0.004), side * z, -length * 0.35, side / 2 + size * 0.45));
      group.add(at(kit.axle(size * 0.3, 0.05, "cable", "z", 8), side * z, -length * 0.35, side / 2 + size + 0.02));
    }
    return { object: group };
  }
  // Induction motor: finned body (detail only when close), terminal box, fan cover.
  group.add(at(kit.cyl(r * 1.05, 0.03, "paintDark", 24), 0, -0.015, 0));
  const body = at(kit.cyl(r, length * 0.82, finish, 28), 0, -0.03 - (length * 0.82) / 2, 0);
  const fins: THREE.Matrix4[] = [];
  for (let i = 0; i < 16; i += 1) {
    const angle = (i / 16) * Math.PI * 2;
    fins.push(placement(Math.cos(angle) * r, body.position.y, Math.sin(angle) * r, [0, -angle, 0]));
  }
  const detailed = new THREE.Group();
  detailed.add(body.clone());
  detailed.add(kit.instances(kit.boxGeometry(0.018, length * 0.74, 0.006, 0), finish, fins));
  const simple = new THREE.Group();
  simple.add(kit.shape(`motor-low:${r}:${length}`, () => new THREE.CylinderGeometry(r * 1.06, r * 1.06, length * 0.82, 12), finish).translateY(body.position.y));
  group.add(kit.lod([[detailed, 0], [simple, 7]]));
  group.add(at(kit.box(r * 0.9, r * 0.6, r * 0.9, finish, 0.008), 0, -length * 0.35, r + r * 0.25));
  group.add(at(kit.cyl(r * 0.92, length * 0.16, "paintDark", 24), 0, -0.03 - length * 0.82 - length * 0.08, 0));
  return { object: group };
}

/**
 * Right-angle gearbox. Output flange at the origin (shaft +y); the input
 * socket on its side expects a motor whose shaft points into the housing.
 */
export function gearbox(p: Params, kit: Kit): BuiltComponent {
  const s = p.num("size", 0.16);
  const group = new THREE.Group();
  group.add(at(kit.cyl(s * 0.42, 0.03, "paintDark", 24), 0, -0.015, 0));
  group.add(at(kit.box(s, s * 1.15, s * 0.9, p.str("finish", "blue") as "blue", s * 0.18), 0, -0.03 - (s * 1.15) / 2, 0));
  group.add(at(kit.box(s * 0.7, s * 0.5, s * 0.06, "paintDark", 0.01), 0, -0.03 - s * 0.55, s * 0.47));
  group.add(at(kit.cyl(0.012, 0.03, "brass", 8), 0, -0.03 + 0.005, s * 0.3));
  return { object: group, sockets: { input: socket(group, -s / 2, -0.03 - s * 0.6, 0, [0, 0, -Math.PI / 2]) } };
}

/**
 * Ball-screw linear axis along local x, centred on the origin, with two rails,
 * a screw, bellows covers and a carriage. Sockets: `carriage` (top of the
 * carriage), `motor` (+x end, shaft pointing -x).
 *
 * Params: `length`, `width`, `carriage` (position 0..1), `carriageLength`.
 */
export function linearAxis(p: Params, kit: Kit): BuiltComponent {
  const length = p.num("length", 1.2);
  const width = p.num("width", 0.6);
  const carriageLength = p.num("carriageLength", 0.4);
  const position = p.num("carriage", 0.5);
  const group = new THREE.Group();
  group.add(at(kit.box(length, 0.05, width, "castIron", 0.01), 0, 0.025, 0));
  for (const z of [-width / 2 + 0.06, width / 2 - 0.06]) group.add(at(kit.box(length - 0.04, 0.035, 0.035, "steel", 0.004), 0, 0.068, z));
  group.add(at(kit.axle(0.016, length - 0.06, "chrome", "x", 12), 0, 0.07, 0));
  group.add(at(kit.box(0.05, 0.08, 0.1, "castIron", 0.01), length / 2 - 0.03, 0.07, 0));
  const travel = length - carriageLength - 0.1;
  const x = (position - 0.5) * travel;
  const carriage = at(kit.box(carriageLength, 0.06, width - 0.04, "castIron", 0.012), x, 0.115, 0);
  group.add(carriage);
  // Bellows covers either side of the carriage.
  const left = x - carriageLength / 2 + length / 2;
  const right = length / 2 - (x + carriageLength / 2);
  const pleats = (from: number, span: number) => {
    const matrices: THREE.Matrix4[] = [];
    const count = Math.max(2, Math.floor(span / 0.035));
    for (let i = 0; i < count; i += 1) matrices.push(placement(from + (i + 0.5) * (span / count), 0.085, 0));
    return matrices;
  };
  const bellows = kit.boxGeometry(0.02, 0.05, width - 0.12, 0.008);
  if (left > 0.04) group.add(kit.instances(bellows, "graphite", pleats(-length / 2, left - 0.02)));
  if (right > 0.04) group.add(kit.instances(bellows, "graphite", pleats(x + carriageLength / 2 + 0.02, right - 0.04)));
  return {
    object: group,
    sockets: {
      carriage: socket(group, x, 0.145, 0),
      motor: socket(group, length / 2 + 0.005, 0.07, 0, [0, 0, Math.PI / 2]),
    },
  };
}

/**
 * Vertical spindle head. The origin is the mounting face on the Z-axis
 * carriage; the head extends +z and the tool points down.
 * Sockets: `motor` (top, shaft down), `sensor` (side, facing +x).
 */
export function spindle(p: Params, kit: Kit): BuiltComponent {
  const reach = p.num("reach", 0.42);
  const group = new THREE.Group();
  group.add(at(kit.box(0.34, 0.42, 0.1, "castIron", 0.02), 0, 0, 0.05));
  group.add(at(kit.box(0.3, 0.4, reach - 0.02, "paint", 0.05), 0, 0, 0.08 + (reach - 0.1) / 2));
  group.add(at(kit.cyl(0.13, 0.4, "paint", 32), 0, 0, reach));
  group.add(at(kit.cyl(0.135, 0.02, "accent", 32), 0, 0.12, reach));
  group.add(at(kit.cyl(0.09, 0.12, "steel", 32), 0, -0.26, reach));
  group.add(at(kit.cyl(0.07, 0.02, "graphite", 32), 0, -0.33, reach));
  // Rotating tool holder and end mill.
  const rotor = pivot(group, 0, -0.34, reach);
  rotor.add(at(kit.cyl(0.03, 0.07, "steel", 20, 0.045), 0, -0.035, 0));
  rotor.add(at(kit.cyl(0.032, 0.02, "graphite", 20), 0, -0.08, 0));
  rotor.add(at(kit.cyl(0.013, 0.13, "chrome", 12), 0, -0.155, 0));
  rotor.add(at(kit.box(0.004, 0.1, 0.03, "graphite", 0), 0, -0.165, 0));
  // Coolant nozzles beside the spindle nose.
  for (const side of [-1, 1]) {
    const nozzle = turn(at(kit.cyl(0.008, 0.14, "accent", 8), side * 0.11, -0.3, reach + 0.05), 0, 0, side * 0.45);
    group.add(nozzle);
  }
  return {
    object: group,
    sockets: {
      motor: socket(group, 0, 0.2, reach, [Math.PI, 0, 0]),
      sensor: socket(group, 0.15, 0.04, reach - 0.12, [0, Math.PI / 2, 0]),
    },
    update(_time, dt, status) {
      if (status === "fault") return;
      rotor.rotation.y += dt * 9;
    },
  };
}

/**
 * Carousel tool magazine on a bracket extending -x from the mount.
 * Params: `pockets`, `radius`.
 */
export function toolChanger(p: Params, kit: Kit): BuiltComponent {
  const pockets = p.num("pockets", 20);
  const radius = p.num("radius", 0.3);
  const group = new THREE.Group();
  group.add(at(kit.box(0.24, 0.1, 0.16, "castIron", 0.02), -0.1, 0, 0));
  const cx = -0.22 - radius;
  group.add(at(kit.cyl(radius + 0.02, 0.05, "paint", 40), cx, 0.07, 0));
  group.add(at(kit.cyl(radius * 0.4, 0.12, "paintGrey", 24), cx, 0.14, 0));
  group.add(at(kit.cyl(radius * 0.25, 0.08, "graphite", 20), cx, 0.24, 0));
  const disc = pivot(group, cx, 0, 0);
  disc.add(kit.cyl(radius, 0.04, "paintGrey", 40));
  const holders: THREE.Matrix4[] = [];
  const tools: THREE.Matrix4[] = [];
  for (let i = 0; i < pockets; i += 1) {
    const angle = (i / pockets) * Math.PI * 2;
    const x = Math.cos(angle) * (radius - 0.04);
    const z = Math.sin(angle) * (radius - 0.04);
    holders.push(placement(x, -0.05, z));
    if (i % 5 !== 3) tools.push(placement(x, -0.13, z));
  }
  disc.add(kit.instances(kit.cylinderGeometry(0.022, 0.03, 0.06, 12), "steel", holders));
  disc.add(kit.instances(kit.cylinderGeometry(0.009, 0.009, 0.1, 8), "chrome", tools));
  // Change arm reaching toward the spindle.
  group.add(at(kit.box(0.4, 0.035, 0.06, "steel", 0.01), cx + radius * 0.5, -0.2, 0.25));
  group.add(at(kit.cyl(0.03, 0.08, "graphite", 16), cx + radius * 0.5, -0.17, 0.25));
  return {
    object: group,
    update(time, _dt, status) {
      if (status === "fault") return;
      // Index one pocket every 4 s with an eased step.
      const step = Math.floor(time / 4);
      const phase = Math.min(1, (time % 4) / 0.8);
      disc.rotation.y = ((step + smooth(phase)) * Math.PI * 2) / pockets;
    },
  };
}

/**
 * Belt conveyor along local x, centred on the origin. `height` is the belt top.
 *
 * Sockets: `drive` (head end, shaft +y toward the belt), `side-left` / `side-right`
 * (rail tops at mid-length), `top` (belt surface).
 * Params: `length`, `width`, `height`, `legSpacing`, `speed` (m/s).
 */
export function conveyor(p: Params, kit: Kit): BuiltComponent {
  const length = p.num("length", 3);
  const width = p.num("width", 0.5);
  const h = p.num("height", 0.85);
  const speed = p.num("speed", 0.25);
  const group = new THREE.Group();
  // The belt has a running pattern so motion reads without moving geometry.
  const texture = kit.canvas(64, 64, (c) => {
    c.fillStyle = "#2b3036";
    c.fillRect(0, 0, 64, 64);
    c.strokeStyle = "#3a4048";
    c.lineWidth = 6;
    c.beginPath();
    c.moveTo(0, 8);
    c.lineTo(32, 32);
    c.lineTo(0, 56);
    c.stroke();
  });
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(length / 0.25, 1);
  const beltMaterial = kit.own(new THREE.MeshStandardMaterial({ map: texture, roughness: 0.85, metalness: 0 }));
  group.add(at(kit.box(length, 0.012, width, beltMaterial, 0), 0, h - 0.006, 0));
  group.add(at(kit.box(length, 0.01, width, "belt", 0), 0, h - 0.11, 0));
  for (const x of [-length / 2, length / 2]) group.add(at(kit.axle(0.058, width, "belt", "z", 24), x, h - 0.058, 0));
  // Side frames, guide rails and their brackets.
  for (const side of [-1, 1]) {
    const z = side * (width / 2 + 0.03);
    group.add(at(kit.box(length + 0.08, 0.12, 0.04, "aluminum", 0.008), 0, h - 0.07, z));
    group.add(at(kit.box(length, 0.03, 0.015, "steel", 0.004), 0, h + 0.06, side * (width / 2 + 0.005)));
  }
  const brackets: THREE.Matrix4[] = [];
  for (let x = -length / 2 + 0.25; x <= length / 2 - 0.2; x += 0.5) for (const side of [-1, 1]) brackets.push(placement(x, h + 0.02, side * (width / 2 + 0.02)));
  group.add(kit.instances(kit.boxGeometry(0.02, 0.07, 0.03, 0.004), "steel", brackets));
  // Legs, feet and cross braces.
  const spacing = p.num("legSpacing", 1.4);
  const legs: THREE.Matrix4[] = [];
  const feet: THREE.Matrix4[] = [];
  const braces: THREE.Matrix4[] = [];
  const count = Math.max(2, Math.round((length - 0.3) / spacing) + 1);
  for (let i = 0; i < count; i += 1) {
    const x = -length / 2 + 0.15 + (i * (length - 0.3)) / (count - 1);
    for (const side of [-1, 1]) {
      legs.push(placement(x, (h - 0.13) / 2, side * (width / 2 + 0.03)));
      feet.push(placement(x, 0.015, side * (width / 2 + 0.03)));
    }
    braces.push(placement(x, 0.25, 0));
  }
  group.add(kit.instances(kit.boxGeometry(0.045, h - 0.13, 0.045, 0.006), "aluminum", legs));
  group.add(kit.instances(kit.cylinderGeometry(0.035, 0.04, 0.03, 12), "graphite", feet));
  group.add(kit.instances(kit.boxGeometry(0.04, 0.04, width + 0.06, 0.006), "aluminum", braces));
  let slip = 1;
  return {
    object: group,
    sockets: {
      drive: socket(group, length / 2, h - 0.058, -(width / 2 + 0.05), [Math.PI / 2, 0, 0]),
      tail: socket(group, -length / 2, h - 0.058, width / 2 + 0.05, [-Math.PI / 2, 0, 0]),
      "side-left": socket(group, 0, h + 0.02, -(width / 2 + 0.06)),
      "side-right": socket(group, 0, h + 0.02, width / 2 + 0.06, [0, Math.PI, 0]),
      top: socket(group, 0, h, 0),
    },
    update(_time, dt) {
      texture.offset.x -= ((speed * slip) / 0.25) * dt;
    },
    setStatus(status) {
      // A suspected belt shows reduced, uneven travel.
      slip = status === "normal" ? 1 : 0.35;
    },
  };
}

/** Gravity roller conveyor along local x. Params: `length`, `width`, `height`, `pitch`. */
export function rollerConveyor(p: Params, kit: Kit): BuiltComponent {
  const length = p.num("length", 1.2);
  const width = p.num("width", 0.5);
  const h = p.num("height", 0.8);
  const pitch = p.num("pitch", 0.09);
  const group = new THREE.Group();
  const rollers: THREE.Matrix4[] = [];
  for (let x = -length / 2 + pitch / 2; x < length / 2; x += pitch) rollers.push(placement(x, h - 0.025, 0, [Math.PI / 2, 0, 0]));
  group.add(kit.instances(kit.cylinderGeometry(0.024, 0.024, width, 14), "steel", rollers));
  for (const side of [-1, 1]) group.add(at(kit.box(length, 0.09, 0.03, "aluminum", 0.006), 0, h - 0.04, side * (width / 2 + 0.02)));
  const legs: THREE.Matrix4[] = [];
  for (const x of [-length / 2 + 0.08, length / 2 - 0.08]) for (const side of [-1, 1]) legs.push(placement(x, (h - 0.08) / 2, side * (width / 2 + 0.02)));
  group.add(kit.instances(kit.boxGeometry(0.04, h - 0.08, 0.04, 0.006), "aluminum", legs));
  group.add(at(kit.box(0.04, 0.12, width + 0.08, "yellow", 0.01), length / 2 + 0.02, h + 0.02, 0));
  return { object: group, sockets: { top: socket(group, 0, h, 0) } };
}

/**
 * Pneumatic cylinder along +y: rear cap at the origin, rod extending +y.
 *
 * Params: `bore` (radius), `stroke`, `tool` (plate | clamp | none), `cycle`
 * (seconds per extend/retract, 0 = static), `offset` (phase in seconds).
 * Sockets: `reed-retracted`, `reed-extended` (on the barrel, facing +z), `rod-end`.
 */
export function pneumaticCylinder(p: Params, kit: Kit): BuiltComponent {
  const r = p.num("bore", 0.025);
  const stroke = p.num("stroke", 0.2);
  const cycle = p.num("cycle", 0);
  const offset = p.num("offset", 0);
  const tool = p.str<"plate" | "clamp" | "none">("tool", "none");
  const barrel = stroke + 0.06;
  const cap = r * 2.5;
  const group = new THREE.Group();
  group.add(at(kit.box(cap, 0.03, cap, "aluminum", 0.004), 0, 0.015, 0));
  group.add(at(kit.box(cap * 0.92, barrel, cap * 0.92, "aluminum", r * 0.5), 0, 0.03 + barrel / 2, 0));
  group.add(at(kit.box(cap, 0.03, cap, "aluminum", 0.004), 0, 0.045 + barrel, 0));
  // Profile grooves for reed switches, and the two air fittings.
  for (const x of [-1, 1]) group.add(at(kit.box(0.004, barrel - 0.01, 0.006, "graphite", 0), x * cap * 0.25, 0.03 + barrel / 2, cap * 0.46));
  for (const y of [0.015, 0.045 + barrel]) group.add(at(kit.axle(0.006, 0.03, "brass", "x", 8), cap / 2 + 0.012, y, 0));
  const rod = pivot(group, 0, 0.06 + barrel, 0);
  rod.add(at(kit.cyl(r * 0.4, stroke + 0.02, "chrome", 16), 0, -stroke / 2 + 0.01, 0));
  if (tool === "plate") rod.add(at(kit.box(0.3, 0.02, 0.12, "paintDark", 0.005), 0, 0.03, 0));
  if (tool === "clamp") {
    rod.add(at(kit.box(cap * 0.8, 0.03, cap * 0.8, "steel", 0.006), 0, 0.035, 0));
    rod.add(at(kit.box(cap * 0.6, 0.02, cap * 0.6, "rubber", 0.004), 0, 0.06, 0));
  }
  const rodEnd = socket(rod, 0, 0.04, 0);
  let stalled = false;
  const extend = (amount: number) => {
    rod.position.y = 0.06 + barrel + amount * stroke;
  };
  extend(cycle > 0 ? 0 : 1);
  return {
    object: group,
    sockets: {
      "reed-retracted": socket(group, cap * 0.25, 0.06, cap * 0.46),
      "reed-extended": socket(group, cap * 0.25, 0.01 + barrel, cap * 0.46),
      "rod-end": rodEnd,
    },
    update(time) {
      if (cycle <= 0) return;
      if (stalled) {
        extend(0.3);
        return;
      }
      const phase = ((time + offset) % cycle) / cycle;
      // Extend, hold, retract, rest.
      const amount = phase < 0.2 ? smooth(phase / 0.2) : phase < 0.5 ? 1 : phase < 0.7 ? 1 - smooth((phase - 0.5) / 0.2) : 0;
      extend(amount);
    },
    setStatus(status) {
      stalled = status === "fault";
    },
  };
}

/**
 * Parallel pneumatic gripper hanging -y from the mount, fingers along x.
 * Params: `cycle`, `closeAt`, `openAt` (fractions of the cycle). Socket: `sensor`.
 */
export function gripper(p: Params, kit: Kit): BuiltComponent {
  const cycle = p.num("cycle", 0);
  const closeAt = p.num("closeAt", 0.3);
  const openAt = p.num("openAt", 0.75);
  const group = new THREE.Group();
  group.add(at(kit.cyl(0.04, 0.02, "aluminum", 24), 0, -0.01, 0));
  group.add(at(kit.box(0.11, 0.06, 0.06, "aluminum", 0.008), 0, -0.05, 0));
  group.add(at(kit.box(0.112, 0.012, 0.062, "blue", 0.004), 0, -0.03, 0));
  const fingers = [-1, 1].map((side) => {
    const finger = pivot(group, side * 0.035, -0.08, 0);
    finger.add(at(kit.box(0.014, 0.09, 0.03, "steel", 0.004), 0, -0.045, 0));
    finger.add(at(kit.box(0.006, 0.035, 0.026, "rubber", 0.002), -side * 0.009, -0.07, 0));
    return { finger, side };
  });
  const set = (closed: number) => {
    for (const { finger, side } of fingers) finger.position.x = side * (0.035 - closed * 0.02);
  };
  set(0);
  return {
    object: group,
    sockets: { sensor: socket(group, 0, -0.05, 0.03) },
    update(time, _dt, status) {
      if (cycle <= 0) return;
      const phase = (time % cycle) / cycle;
      // A suspected gripper closes only part way.
      const closed = phase > closeAt && phase < openAt ? 1 : 0;
      set(closed * (status === "fault" ? 0.35 : 1));
    },
  };
}

/**
 * Parts travelling along local x on a conveyor, drawn with one instanced mesh.
 * Params: `length`, `count`, `speed`, `size`, `y`, `finish` (cardboard | workpiece).
 */
export function workpieceFlow(p: Params, kit: Kit): BuiltComponent {
  const length = p.num("length", 3);
  const count = p.num("count", 6);
  const speed = p.num("speed", 0.25);
  const size = p.num("size", 0.2);
  const finish = p.str<"cardboard" | "workpiece">("finish", "cardboard");
  const y = p.num("y", 0);
  const group = new THREE.Group();
  const geometry = kit.boxGeometry(size, size * 0.7, size * 0.8, 0.01);
  const items = kit.instances(
    geometry,
    finish,
    Array.from({ length: count }, (_, i) => placement(-length / 2 + (i * length) / count, y + size * 0.35, 0)),
  );
  items.frustumCulled = false;
  group.add(items);
  const matrix = new THREE.Matrix4();
  let factor = 1;
  let travelled = 0;
  return {
    object: group,
    update(_time, dt) {
      travelled += speed * factor * dt;
      for (let i = 0; i < count; i += 1) {
        const x = (((travelled + (i * length) / count) % length) + length) % length;
        matrix.makeTranslation(-length / 2 + x, y + size * 0.35, 0);
        items.setMatrixAt(i, matrix);
      }
      items.instanceMatrix.needsUpdate = true;
    },
    setStatus(_status, machine) {
      factor = machine.fault ? 0.4 : 1;
    },
  };
}
