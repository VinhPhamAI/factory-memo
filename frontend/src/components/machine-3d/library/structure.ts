import * as THREE from "three";
import { at, placement, socket, turn, type Kit, type Params } from "../kit";
import type { BuiltComponent } from "./types";

/**
 * Frames, guards and housings. All stand on y = 0 with their front facing +z.
 */

/**
 * Machine bed with column (`vmc`), robot riser (`pedestal`), support post
 * (`post`) or anchor plate (`plate`).
 *
 * Sockets: vmc → `y-axis` (bed top), `z-axis` (column front), `atc` (column left);
 * pedestal, post and plate → `top`.
 */
export function baseFrame(p: Params, kit: Kit): BuiltComponent {
  const group = new THREE.Group();
  const style = p.str<"vmc" | "pedestal" | "post" | "plate">("style", "plate");
  if (style === "vmc") {
    const bedH = 0.7;
    group.add(at(kit.box(1.7, bedH - 0.08, 1.55, "castIron", 0.04), 0, 0.08 + (bedH - 0.08) / 2, 0));
    group.add(at(kit.box(1.78, 0.08, 1.62, "graphite", 0.02), 0, 0.04, 0));
    // Ribs on the bed sides read as a casting rather than a plain block.
    const ribs: THREE.Matrix4[] = [];
    for (const x of [-0.86, 0.86]) for (let z = -0.6; z <= 0.61; z += 0.3) ribs.push(placement(x, 0.38, z));
    group.add(kit.instances(kit.boxGeometry(0.04, 0.5, 0.06, 0.01), "castIron", ribs));
    // Column with front rails area and back ribs.
    const columnH = 1.5;
    group.add(at(kit.box(0.78, columnH, 0.55, "castIron", 0.05), 0, bedH + columnH / 2, -0.5));
    group.add(at(kit.box(0.84, 0.06, 0.6, "paintGrey", 0.02), 0, bedH + columnH, -0.5));
    for (const x of [-0.26, 0.26]) group.add(at(kit.box(0.08, columnH - 0.1, 0.12, "castIron", 0.02), x, bedH + columnH / 2, -0.82));
    // Chip trough at the bed front.
    group.add(at(kit.box(1.5, 0.08, 0.1, "steel", 0.02), 0, bedH - 0.04, 0.8));
    return {
      object: group,
      sockets: {
        "y-axis": socket(group, 0, bedH, 0.25),
        "z-axis": socket(group, 0, 1.8, -0.225),
        atc: socket(group, -0.39, 1.92, -0.45),
      },
    };
  }
  if (style === "pedestal") {
    const h = p.num("height", 0.45);
    group.add(at(kit.box(0.7, 0.04, 0.7, "graphite", 0.01), 0, 0.02, 0));
    group.add(at(kit.box(0.42, h - 0.08, 0.42, "paintDark", 0.03), 0, h / 2, 0));
    group.add(at(kit.box(0.56, 0.04, 0.56, "graphite", 0.01), 0, h - 0.02, 0));
    const bolts: THREE.Matrix4[] = [];
    for (const x of [-0.3, 0.3]) for (const z of [-0.3, 0.3]) bolts.push(placement(x, 0.05, z));
    group.add(kit.instances(kit.cylinderGeometry(0.018, 0.018, 0.03, 6), "steel", bolts));
    return { object: group, sockets: { top: socket(group, 0, h, 0) } };
  }
  if (style === "post") {
    const h = p.num("height", 0.8);
    group.add(at(kit.box(0.24, 0.015, 0.24, "graphite", 0.004), 0, 0.0075, 0));
    group.add(at(kit.box(0.07, h - 0.03, 0.07, "aluminum", 0.008), 0, (h - 0.03) / 2 + 0.015, 0));
    group.add(at(kit.box(0.16, 0.015, 0.16, "aluminum", 0.004), 0, h - 0.0075, 0));
    return { object: group, sockets: { top: socket(group, 0, h, 0) } };
  }
  const w = p.num("width", 4);
  const d = p.num("depth", 4);
  group.add(at(kit.box(w, 0.02, d, "floorPlate", 0.005), 0, 0.01, 0));
  return { object: group, sockets: { top: socket(group, 0, 0.02, 0) } };
}

/** Front frame bars and a glass pane of one window or door. */
function framedWindow(kit: Kit, w: number, h: number, frame: number, finish: "paint" | "paintGrey" | "yellow" = "paint") {
  const group = new THREE.Group();
  group.add(at(kit.box(w, frame, 0.04, finish, 0.01), 0, h / 2 - frame / 2, 0));
  group.add(at(kit.box(w, frame, 0.04, finish, 0.01), 0, -h / 2 + frame / 2, 0));
  group.add(at(kit.box(frame, h - 2 * frame, 0.04, finish, 0.01), -w / 2 + frame / 2, 0, 0));
  group.add(at(kit.box(frame, h - 2 * frame, 0.04, finish, 0.01), w / 2 - frame / 2, 0, 0));
  group.add(kit.box(w - 2 * frame + 0.01, h - 2 * frame + 0.01, 0.012, "glass", 0));
  return group;
}

/**
 * Sheet-metal machine guard with two sliding front doors and windows.
 *
 * Sockets: `panel` (right pillar front), `door-switch` (above the door gap),
 * `roof` (rear right roof corner).
 */
export function enclosure(p: Params, kit: Kit): BuiltComponent {
  const w = p.num("width", 2.5);
  const d = p.num("depth", 2.2);
  const h = p.num("height", 2.55);
  const group = new THREE.Group();
  const t = 0.04;
  const plinth = 0.12;
  group.add(at(kit.box(w + 0.02, plinth, d + 0.02, "graphite", 0.02), 0, plinth / 2, 0));
  // Side and back walls: grey lower skirt, white upper sheet.
  for (const x of [-w / 2 + t / 2, w / 2 - t / 2]) {
    group.add(at(kit.box(t, 0.78, d, "paintGrey", 0.015), x, plinth + 0.39, 0));
    group.add(at(kit.box(t, h - plinth - 0.78, d, "paint", 0.015), x, plinth + 0.78 + (h - plinth - 0.78) / 2, 0));
    group.add(at(kit.box(t + 0.012, 0.035, d - 0.1, "accent", 0.008), x, plinth + 0.8, 0));
  }
  group.add(at(kit.box(w - 2 * t, h - plinth, t, "paint", 0.015), 0, plinth + (h - plinth) / 2, -d / 2 + t / 2));
  // Roof with an opening skylight over the work zone.
  group.add(at(kit.box(w, 0.05, d * 0.45, "paint", 0.015), 0, h - 0.025, -d / 2 + (d * 0.45) / 2));
  group.add(turn(at(framedWindow(kit, w, d * 0.55, 0.06), 0, h - 0.02, d / 2 - (d * 0.55) / 2), -Math.PI / 2, 0, 0));
  // Front: pillars, header, apron and the door opening.
  const left = 0.32;
  const right = 0.58;
  const apron = 0.82;
  const header = 0.36;
  const z = d / 2 - t / 2;
  group.add(at(kit.box(left, h - plinth, t, "paint", 0.015), -w / 2 + left / 2, plinth + (h - plinth) / 2, z));
  group.add(at(kit.box(right, h - plinth, t, "paint", 0.015), w / 2 - right / 2, plinth + (h - plinth) / 2, z));
  group.add(at(kit.box(w - left - right, apron - plinth, t, "paintGrey", 0.015), (left - right) / 2, plinth + (apron - plinth) / 2, z));
  group.add(at(kit.box(w - left - right, header, t, "paint", 0.015), (left - right) / 2, h - header / 2, z));
  group.add(at(kit.box(w - left - right, 0.05, 0.03, "accent", 0.01), (left - right) / 2, h - header + 0.06, z + 0.03));
  const label = kit.screen(256, 48, (c) => {
    c.fillStyle = "#eef1f4";
    c.fillRect(0, 0, 256, 48);
    c.fillStyle = "#1d2f45";
    c.font = "bold 26px Inter, system-ui, sans-serif";
    c.fillText("VMC-850", 12, 34);
    c.fillStyle = "#c8102e";
    c.fillRect(170, 18, 74, 12);
  });
  group.add(at(new THREE.Mesh(new THREE.PlaneGeometry(0.5, 0.094), label.material), -w / 2 + left + 0.32, h - 0.17, z + 0.022));
  // Two sliding doors with windows and handles, slightly in front of the frame.
  const gap = w - left - right;
  const doorW = gap / 2 + 0.04;
  const doorH = h - header - apron + 0.04;
  for (const side of [-1, 1]) {
    const door = new THREE.Group();
    door.add(framedWindow(kit, doorW, doorH, 0.09));
    door.add(at(kit.box(0.03, 0.42, 0.04, "steel", 0.012), side * (-doorW / 2 + 0.06), 0, 0.04));
    group.add(at(door, (left - right) / 2 + side * (gap / 4), apron + doorH / 2 - 0.02, d / 2 + 0.03 + (side > 0 ? 0.045 : 0)));
  }
  return {
    object: group,
    sockets: {
      panel: socket(group, w / 2 - right / 2, 1.22, d / 2 + 0.005),
      "door-switch": socket(group, (left - right) / 2 + 0.08, h - header - 0.04, d / 2 + 0.02),
      roof: socket(group, w / 2 - 0.3, h, -d / 2 + 0.3),
    },
  };
}

/**
 * Floor-standing electrical cabinet. `door: glass` shows the interior.
 *
 * Sockets: `din-rail` (inside back plate, facing +z), `top`, `side-right`, `side-left`.
 */
export function cabinet(p: Params, kit: Kit): BuiltComponent {
  const w = p.num("width", 0.7);
  const h = p.num("height", 1.8);
  const d = p.num("depth", 0.42);
  const glass = p.str<"solid" | "glass">("door", "solid") === "glass";
  const group = new THREE.Group();
  group.add(at(kit.box(w, 0.1, d, "graphite", 0.01), 0, 0.05, 0));
  const t = 0.025;
  group.add(at(kit.box(w, h - 0.1, t, "paintGrey", 0.01), 0, 0.1 + (h - 0.1) / 2, -d / 2 + t / 2));
  for (const x of [-w / 2 + t / 2, w / 2 - t / 2]) group.add(at(kit.box(t, h - 0.1, d, "paintGrey", 0.01), x, 0.1 + (h - 0.1) / 2, 0));
  group.add(at(kit.box(w, t, d, "paintGrey", 0.01), 0, h - t / 2, 0));
  group.add(at(kit.box(w - 0.06, h - 0.2, 0.01, "paint", 0), 0, 0.1 + (h - 0.1) / 2, -d / 2 + t + 0.006));
  // Vents on the sides.
  const vents: THREE.Matrix4[] = [];
  for (const x of [-w / 2 - 0.002, w / 2 + 0.002]) for (let i = 0; i < 6; i += 1) vents.push(placement(x, 0.4 + i * 0.05, d * 0.15));
  group.add(kit.instances(kit.boxGeometry(0.008, 0.018, d * 0.45, 0.004), "graphite", vents));
  // Door: full sheet or a frame around a window, with handle and nameplate.
  const doorZ = d / 2 - 0.01;
  if (glass) {
    const frame = new THREE.Group();
    const fw = 0.08;
    frame.add(at(kit.box(w, fw, 0.03, "paintGrey", 0.01), 0, h / 2 - 0.05 - fw / 2, 0));
    frame.add(at(kit.box(w, fw + 0.25, 0.03, "paintGrey", 0.01), 0, -h / 2 + 0.05 + (fw + 0.25) / 2, 0));
    frame.add(at(kit.box(fw, h - 0.1, 0.03, "paintGrey", 0.01), -w / 2 + fw / 2, 0, 0));
    frame.add(at(kit.box(fw, h - 0.1, 0.03, "paintGrey", 0.01), w / 2 - fw / 2, 0, 0));
    frame.add(at(kit.box(w - 2 * fw + 0.01, h - 0.43, 0.008, "glass", 0), 0, 0.1, 0));
    group.add(at(frame, 0, 0.05 + h / 2, doorZ));
  } else {
    group.add(at(kit.box(w - 0.01, h - 0.12, 0.03, "paintGrey", 0.012), 0, 0.1 + (h - 0.12) / 2, doorZ));
  }
  group.add(at(kit.box(0.03, 0.18, 0.03, "graphite", 0.01), w / 2 - 0.07, 1.0, doorZ + 0.03));
  group.add(at(kit.box(0.16, 0.05, 0.006, "accent", 0), -w / 2 + 0.16, h - 0.16, doorZ + 0.02));
  return {
    object: group,
    sockets: {
      "din-rail": socket(group, 0, h * 0.58, -d / 2 + t + 0.012),
      top: socket(group, w / 2 - 0.12, h, 0),
      "side-right": socket(group, w / 2, h * 0.45, 0, [0, Math.PI / 2, 0]),
      "side-left": socket(group, -w / 2, h * 0.45, 0, [0, -Math.PI / 2, 0]),
    },
  };
}

/**
 * Rectangular wire-mesh guard around a cell, centred on the origin.
 *
 * Params: `width` (x), `depth` (z), `height`, `panel` (target panel width),
 * `door` (front panel index for the hinged door), `opening` (left-side panel
 * index left open for a conveyor). Sockets: `door`, `opening`, `corner`.
 */
export function safetyFence(p: Params, kit: Kit): BuiltComponent {
  const w = p.num("width", 4);
  const d = p.num("depth", 3.6);
  const h = p.num("height", 1.9);
  const target = p.num("panel", 1);
  const doorIndex = p.num("door", 1);
  const openingIndex = p.num("opening", 1);
  const group = new THREE.Group();
  const posts: THREE.Matrix4[] = [];
  const feet: THREE.Matrix4[] = [];
  const panels: THREE.Matrix4[] = [];
  const bars: THREE.Matrix4[] = [];
  const panelBottom = 0.15;
  const panelH = h - 0.22;
  let doorCenter = new THREE.Vector3();
  let openingCenter = new THREE.Vector3();
  const sides = [
    { from: new THREE.Vector3(-w / 2, 0, d / 2), to: new THREE.Vector3(w / 2, 0, d / 2), name: "front" },
    { from: new THREE.Vector3(w / 2, 0, d / 2), to: new THREE.Vector3(w / 2, 0, -d / 2), name: "right" },
    { from: new THREE.Vector3(w / 2, 0, -d / 2), to: new THREE.Vector3(-w / 2, 0, -d / 2), name: "back" },
    { from: new THREE.Vector3(-w / 2, 0, -d / 2), to: new THREE.Vector3(-w / 2, 0, d / 2), name: "left" },
  ];
  for (const side of sides) {
    const length = side.from.distanceTo(side.to);
    const count = Math.max(1, Math.round(length / target));
    const span = length / count;
    const direction = side.to.clone().sub(side.from).normalize();
    const angle = Math.atan2(-direction.z, direction.x);
    for (let i = 0; i < count; i += 1) {
      const start = side.from.clone().addScaledVector(direction, i * span);
      posts.push(placement(start.x, h / 2, start.z));
      feet.push(placement(start.x, 0.01, start.z));
      const center = side.from.clone().addScaledVector(direction, (i + 0.5) * span);
      if (side.name === "left" && i === openingIndex) {
        openingCenter = center;
        bars.push(placement(center.x, h - 0.04, center.z, [0, angle, 0], [span, 1, 1]));
        continue;
      }
      const isDoor = side.name === "front" && i === doorIndex;
      if (isDoor) doorCenter = center;
      panels.push(placement(center.x, panelBottom + panelH / 2, center.z, [0, angle, 0], [(span - 0.08) / 0.92, 1, 1]));
      for (const y of [panelBottom, panelBottom + panelH]) bars.push(placement(center.x, y, center.z, [0, angle, 0], [span, 1, 1]));
    }
  }
  group.add(kit.instances(kit.boxGeometry(0.06, h, 0.06, 0.008), "yellow", posts));
  group.add(kit.instances(kit.boxGeometry(0.16, 0.02, 0.16, 0.004), "graphite", feet));
  group.add(kit.instances(kit.boxGeometry(0.92, panelH, 0.008, 0), "fence", panels));
  group.add(kit.instances(kit.boxGeometry(1, 0.035, 0.035, 0), "paintDark", bars));
  // Door: yellow frame, handle and hinge marks over the mesh panel.
  const door = new THREE.Group();
  door.add(at(kit.box(0.84, 0.04, 0.03, "yellow", 0.01), 0, panelH / 2 - 0.02, 0));
  door.add(at(kit.box(0.84, 0.04, 0.03, "yellow", 0.01), 0, -panelH / 2 + 0.02, 0));
  door.add(at(kit.box(0.04, panelH, 0.03, "yellow", 0.01), -0.4, 0, 0));
  door.add(at(kit.box(0.04, panelH, 0.03, "yellow", 0.01), 0.4, 0, 0));
  door.add(at(kit.box(0.025, 0.2, 0.05, "graphite", 0.01), 0.33, 0, 0.04));
  group.add(at(door, doorCenter.x, panelBottom + panelH / 2, doorCenter.z + 0.01));
  return {
    object: group,
    sockets: {
      door: socket(group, doorCenter.x + 0.46, 1.15, doorCenter.z + 0.04),
      opening: socket(group, openingCenter.x, 0, openingCenter.z, [0, Math.PI / 2, 0]),
      corner: socket(group, w / 2, h, -d / 2),
    },
  };
}

/** Machine table with T-slots, a vise and a workpiece. Origin: bottom centre. */
export function worktable(p: Params, kit: Kit): BuiltComponent {
  const w = p.num("width", 1.1);
  const d = p.num("depth", 0.5);
  const group = new THREE.Group();
  group.add(at(kit.box(w, 0.1, d, "castIron", 0.012), 0, 0.05, 0));
  const slots: THREE.Matrix4[] = [];
  for (let z = -d / 2 + 0.08; z < d / 2 - 0.05; z += 0.1) slots.push(placement(0, 0.1, z));
  group.add(kit.instances(kit.boxGeometry(w - 0.02, 0.006, 0.022, 0), "graphite", slots));
  // Vise: fixed jaw, moving jaw, screw and the workpiece.
  group.add(at(kit.box(0.42, 0.06, 0.2, "steel", 0.01), 0.05, 0.13, 0));
  group.add(at(kit.box(0.06, 0.1, 0.2, "steel", 0.01), -0.13, 0.21, 0));
  group.add(at(kit.box(0.06, 0.1, 0.2, "steel", 0.01), 0.17, 0.21, 0));
  group.add(at(kit.axle(0.012, 0.12, "chrome", "x", 12), 0.28, 0.17, 0));
  group.add(at(kit.box(0.24, 0.09, 0.16, "workpiece", 0.006), 0.02, 0.205, 0));
  return { object: group, sockets: { top: socket(group, 0, 0.1, 0) } };
}

/**
 * Assembly fixture: steel stand, aluminium plate and a blue locating jig.
 *
 * Sockets: `clamp-a`, `clamp-b` (horizontal, pushing toward the jig),
 * `side` (front face of the stand).
 */
export function fixture(p: Params, kit: Kit): BuiltComponent {
  const w = p.num("width", 0.9);
  const d = p.num("depth", 0.7);
  const h = p.num("height", 0.82);
  const group = new THREE.Group();
  const legs: THREE.Matrix4[] = [];
  for (const x of [-w / 2 + 0.04, w / 2 - 0.04]) for (const z of [-d / 2 + 0.04, d / 2 - 0.04]) legs.push(placement(x, (h - 0.03) / 2, z));
  group.add(kit.instances(kit.boxGeometry(0.06, h - 0.03, 0.06, 0.006), "paintDark", legs));
  group.add(at(kit.box(w - 0.06, 0.05, d - 0.06, "paintDark", 0.006), 0, 0.2, 0));
  group.add(at(kit.box(w, 0.03, d, "aluminum", 0.006), 0, h - 0.015, 0));
  group.add(at(kit.box(0.3, 0.07, 0.24, "blue", 0.01), 0, h + 0.035, 0));
  const pins: THREE.Matrix4[] = [placement(-0.1, h + 0.085, 0.07), placement(0.1, h + 0.085, -0.07)];
  group.add(kit.instances(kit.cylinderGeometry(0.008, 0.01, 0.03, 10), "chrome", pins));
  group.add(at(kit.box(0.16, 0.035, 0.12, "workpiece", 0.006), 0, h + 0.088, 0));
  return {
    object: group,
    sockets: {
      "clamp-a": socket(group, -0.36, h + 0.05, 0, [0, 0, -Math.PI / 2]),
      "clamp-b": socket(group, 0.36, h + 0.05, 0, [0, 0, Math.PI / 2]),
      side: socket(group, 0, h - 0.2, d / 2),
    },
  };
}

/** Coolant tank with a vertical pump and the delivery pipe into the guard. */
export function coolantUnit(p: Params, kit: Kit): BuiltComponent {
  const w = p.num("width", 0.55);
  const d = p.num("depth", 0.9);
  const h = p.num("height", 0.45);
  const pipe = p.num("pipe", 0.45);
  const group = new THREE.Group();
  group.add(at(kit.box(w, h, d, "paintGrey", 0.02), 0, h / 2, 0));
  group.add(at(kit.box(w + 0.02, 0.03, d + 0.02, "paintDark", 0.01), 0, h + 0.015, 0));
  // Level gauge window.
  group.add(at(kit.box(0.04, h * 0.6, 0.006, "glass", 0), w * 0.3, h * 0.5, d / 2 + 0.004));
  group.add(at(kit.box(0.03, h * 0.3, 0.004, "blue", 0), w * 0.3, h * 0.35, d / 2 + 0.006));
  // Pump: motor on a stool and a discharge pipe running to the guard.
  group.add(at(kit.cyl(0.085, 0.22, "blue", 24), 0, h + 0.14, -d * 0.2));
  group.add(at(kit.cyl(0.09, 0.03, "paintDark", 24), 0, h + 0.265, -d * 0.2));
  group.add(at(kit.cyl(0.05, 0.05, "paintDark", 16), 0, h + 0.05, -d * 0.2));
  group.add(at(kit.cyl(0.018, 0.6, "steel", 12), 0.08, h + 0.3, -d * 0.2));
  group.add(at(kit.axle(0.018, pipe, "steel", "x", 12), 0.08 + pipe / 2, h + 0.6, -d * 0.2));
  group.add(at(kit.cyl(0.03, 0.04, "brass", 12), 0.08, h + 0.6, -d * 0.2));
  return { object: group };
}
