import { useEffect, useRef, type RefObject } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/addons/renderers/CSS2DRenderer.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";
import { STATIONS, type DiagnosisScenario, type StationId } from "@/services/machine-diagnosis";

/** Camera commands the panel toolbar sends to the scene. */
export type SceneApi = { resetView(): void; focus(station: StationId): void };

type Props = {
  scenario: DiagnosisScenario;
  selected: StationId;
  autoRotate: boolean;
  onSelect(station: StationId): void;
  onError(message: string): void;
  apiRef?: RefObject<SceneApi | null>;
};

const STATION_X: Record<StationId, number> = { feeder: -7, robot: -3.6, processing: 0, inspection: 3.6, packing: 7 };
const BELT_Y = 0.9;
const HOME_TARGET = new THREE.Vector3(0.6, 1, 0);
const HOME_DIRECTION = new THREE.Vector3(0.48, 0.5, 0.72).normalize();

/** Camera position that frames the whole line for the viewer's aspect ratio. */
function homePosition(aspect: number): THREE.Vector3 {
  const distance = 15 * Math.min(Math.max(1.9 / aspect, 1), 2.2);
  return HOME_TARGET.clone().addScaledVector(HOME_DIRECTION, distance);
}
const FAULT = new THREE.Color("#e0343f");
const OK = new THREE.Color("#22c55e");
const SELECT = new THREE.Color("#2563eb");

type StationParts = { group: THREE.Group; hit: THREE.Mesh; ring: THREE.Mesh; outline: THREE.LineSegments; lamp: THREE.Mesh; label: HTMLDivElement; top: number };

function standard(color: string, options: THREE.MeshStandardMaterialParameters = {}) {
  return new THREE.MeshStandardMaterial({ color, roughness: 0.5, metalness: 0.15, ...options });
}

function shadowed<T extends THREE.Object3D>(object: T): T {
  object.traverse((child) => {
    if (child instanceof THREE.Mesh) { child.castShadow = true; child.receiveShadow = true; }
  });
  return object;
}

function box(w: number, h: number, d: number, material: THREE.Material, radius = 0.04) {
  return shadowed(new THREE.Mesh(new RoundedBoxGeometry(w, h, d, 3, Math.min(radius, w / 2, h / 2, d / 2)), material));
}

function cylinder(top: number, bottom: number, height: number, material: THREE.Material, segments = 32, open = false) {
  return shadowed(new THREE.Mesh(new THREE.CylinderGeometry(top, bottom, height, segments, 1, open), material));
}

function at<T extends THREE.Object3D>(object: T, x: number, y: number, z: number): T {
  object.position.set(x, y, z);
  return object;
}

/** Draw a small canvas into a texture for screens and stickers. */
function canvasTexture(width: number, height: number, draw: (context: CanvasRenderingContext2D) => void) {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (context) draw(context);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

/** Build the procedural line; materials are shared and disposed by the caller's traversal. */
function buildLine(scene: THREE.Scene, scenario: DiagnosisScenario) {
  const m = {
    body: standard("#eef1f4", { roughness: 0.42 }),
    panel: standard("#d9dee4", { roughness: 0.5 }),
    dark: standard("#2a323d", { roughness: 0.6 }),
    belt: standard("#2b2f36", { roughness: 0.92, metalness: 0 }),
    steel: standard("#b9c2cb", { roughness: 0.28, metalness: 0.85 }),
    yellow: standard("#f2b705", { roughness: 0.45 }),
    robot: standard("#f6c21a", { roughness: 0.38, metalness: 0.2 }),
    glass: new THREE.MeshStandardMaterial({ color: "#d6ebff", transparent: true, opacity: 0.16, roughness: 0.05, metalness: 0.1, depthWrite: false, side: THREE.DoubleSide }),
    frame: standard("#a3202a", { roughness: 0.4 }),
    cardboard: standard("#c99a5b", { roughness: 0.85, metalness: 0 }),
    part: standard("#dfe5ec", { roughness: 0.3, metalness: 0.6 }),
  };
  const line = new THREE.Group();
  scene.add(line);

  // Conveyor: belt, rails with yellow guards and legs.
  const start = -6.2;
  const end = 6.0;
  const length = end - start;
  const center = (start + end) / 2;
  line.add(at(box(length, 0.08, 1.12, m.belt, 0.02), center, BELT_Y, 0));
  for (const z of [-0.62, 0.62]) {
    line.add(at(box(length, 0.2, 0.1, m.panel, 0.03), center, BELT_Y - 0.04, z));
    line.add(at(box(length, 0.04, 0.11, m.yellow, 0.01), center, BELT_Y + 0.08, z));
    for (let x = start + 0.3; x <= end; x += 1.5) {
      line.add(at(cylinder(0.05, 0.05, BELT_Y - 0.1, m.steel, 12), x, (BELT_Y - 0.1) / 2, z * 0.9));
      line.add(at(cylinder(0.11, 0.11, 0.05, m.dark, 16), x, 0.03, z * 0.9));
    }
  }
  // Roller exit after packing.
  for (let x = 6.2; x <= 8.8; x += 0.24) {
    const roller = cylinder(0.05, 0.05, 1.0, m.steel, 12);
    roller.rotation.x = Math.PI / 2;
    line.add(at(roller, x, BELT_Y - 0.02, 0));
  }
  for (const z of [-0.55, 0.55]) line.add(at(box(2.8, 0.12, 0.08, m.panel, 0.02), 7.5, BELT_Y - 0.08, z));

  // Moving parts on the belt.
  const products: THREE.Mesh[] = [];
  for (let i = 0; i < 16; i += 1) {
    const product = box(0.34, 0.12, 0.34, m.part, 0.03);
    product.position.set(start + (i * length) / 16, BELT_Y + 0.1, 0);
    products.push(product);
    line.add(product);
  }

  const stations = new Map<StationId, StationParts>();
  const lampFor = (group: THREE.Group, x: number, y: number, z: number) => {
    group.add(at(cylinder(0.025, 0.025, 0.4, m.steel, 8), x, y + 0.2, z));
    const lamp = at(cylinder(0.09, 0.09, 0.2, standard("#22c55e", { emissive: "#22c55e", emissiveIntensity: 1.4, roughness: 0.2 }), 20), x, y + 0.5, z);
    group.add(lamp);
    group.add(at(cylinder(0.1, 0.1, 0.04, m.dark, 20), x, y + 0.62, z));
    return lamp;
  };

  const builders: Record<StationId, (group: THREE.Group) => { top: number; lamp: THREE.Mesh; size: THREE.Vector3 }> = {
    feeder(group) {
      group.add(at(box(1.6, 1.05, 1.4, m.body, 0.08), 0, 0.53, 0));
      group.add(at(box(1.62, 0.12, 1.42, m.panel, 0.04), 0, 0.06, 0));
      group.add(at(box(0.5, 0.3, 0.02, m.dark, 0.02), -0.3, 0.62, 0.71));
      group.add(at(cylinder(0.78, 0.55, 0.42, standard("#b9c2cb", { roughness: 0.28, metalness: 0.85, side: THREE.DoubleSide }), 40, true), 0, 1.3, 0));
      group.add(at(cylinder(0.56, 0.56, 0.04, m.steel, 40), 0, 1.1, 0));
      for (let i = 0; i < 12; i += 1) {
        const angle = (i / 12) * Math.PI * 2;
        group.add(at(box(0.16, 0.06, 0.16, m.part, 0.02), Math.cos(angle) * 0.38, 1.15, Math.sin(angle) * 0.38));
      }
      const chute = box(1.0, 0.06, 0.32, m.steel, 0.02);
      chute.rotation.z = -0.35;
      group.add(at(chute, 0.95, 1.05, 0));
      // Signal tower: red, amber and the shared status lamp.
      group.add(at(cylinder(0.03, 0.03, 1.4, m.steel, 8), -0.68, 1.75, -0.56));
      group.add(at(cylinder(0.09, 0.09, 0.16, standard("#ef4444", { emissive: "#7f1d1d", emissiveIntensity: 0.2 }), 20), -0.68, 2.55, -0.56));
      group.add(at(cylinder(0.09, 0.09, 0.16, standard("#f59e0b", { emissive: "#78350f", emissiveIntensity: 0.2 }), 20), -0.68, 2.38, -0.56));
      const lamp = at(cylinder(0.09, 0.09, 0.16, standard("#22c55e", { emissive: "#22c55e", emissiveIntensity: 1.4 }), 20), -0.68, 2.21, -0.56);
      group.add(lamp);
      return { top: 2.75, lamp, size: new THREE.Vector3(2.2, 2.8, 1.6) };
    },
    robot(group) {
      group.add(at(box(2.4, 0.05, 2.6, standard("#cfd6de", { roughness: 0.7 }), 0.02), 0, 0.03, 0));
      for (const [x, z] of [[-1.15, -1.25], [1.15, -1.25], [-1.15, 1.25], [1.15, 1.25]]) group.add(at(box(0.09, 1.9, 0.09, m.yellow, 0.02), x, 0.95, z));
      for (const z of [-1.25, 1.25]) group.add(at(box(2.3, 1.4, 0.02, m.glass, 0.005), 0, 1.15, z));
      group.add(at(box(0.02, 1.4, 2.5, m.glass, 0.005), -1.15, 1.15, 0));
      group.add(at(box(2.36, 0.07, 0.09, m.yellow, 0.02), 0, 1.88, -1.25));
      group.add(at(box(2.36, 0.07, 0.09, m.yellow, 0.02), 0, 1.88, 1.25));
      // Arm: base → turret → shoulder → elbow → wrist.
      const base = at(new THREE.Group(), 0, 0, -0.9);
      group.add(base);
      base.add(at(box(0.7, 0.75, 0.7, m.dark, 0.06), 0, 0.38, 0));
      const turret = at(new THREE.Group(), 0, 0.78, 0);
      base.add(turret);
      turret.add(at(cylinder(0.3, 0.34, 0.3, m.robot, 32), 0, 0.15, 0));
      const shoulder = at(new THREE.Group(), 0, 0.35, 0);
      turret.add(shoulder);
      shoulder.add(at(box(0.24, 0.95, 0.24, m.robot, 0.08), 0, 0.45, 0));
      const elbowJoint = cylinder(0.15, 0.15, 0.3, m.dark, 24);
      elbowJoint.rotation.x = Math.PI / 2;
      shoulder.add(at(elbowJoint, 0, 0.92, 0));
      const elbow = at(new THREE.Group(), 0, 0.92, 0);
      shoulder.add(elbow);
      elbow.add(at(box(0.2, 0.2, 0.95, m.robot, 0.08), 0, 0, 0.45));
      const wrist = at(new THREE.Group(), 0, 0, 0.92);
      elbow.add(wrist);
      wrist.add(at(cylinder(0.08, 0.08, 0.32, m.dark, 16), 0, -0.14, 0));
      wrist.add(at(box(0.05, 0.16, 0.04, m.steel, 0.01), -0.06, -0.34, 0));
      wrist.add(at(box(0.05, 0.16, 0.04, m.steel, 0.01), 0.06, -0.34, 0));
      // Cable loop on the arm.
      const cable = shadowed(new THREE.Mesh(new THREE.TorusGeometry(0.32, 0.03, 8, 32, Math.PI * 1.3), m.dark));
      cable.rotation.y = Math.PI / 2;
      shoulder.add(at(cable, -0.18, 0.7, 0.12));
      group.userData.arm = { turret, shoulder, elbow };
      const lamp = lampFor(group, 1.1, 1.9, -1.25);
      return { top: 2.6, lamp, size: new THREE.Vector3(2.5, 2.6, 2.7) };
    },
    processing(group) {
      group.add(at(box(2.1, 2.1, 1.8, m.body, 0.1), 0, 1.4, -0.05));
      group.add(at(box(2.14, 0.32, 1.84, m.panel, 0.06), 0, 0.2, -0.05));
      // Door frame, dark chamber, spindle and heat glow behind the glass.
      group.add(at(box(1.22, 1.08, 0.08, m.frame, 0.04), -0.25, 1.45, 0.86));
      const chamberMaterial = standard("#1d2127", { emissive: "#ff5a1f", emissiveIntensity: 0, roughness: 0.8 });
      group.add(at(box(1.04, 0.9, 0.04, chamberMaterial, 0.01), -0.25, 1.45, 0.89));
      group.add(at(cylinder(0.09, 0.09, 0.42, m.steel, 20), -0.25, 1.62, 0.95));
      group.add(at(cylinder(0.02, 0.05, 0.16, standard("#f59e0b", { emissive: "#f59e0b", emissiveIntensity: 0.6 }), 12), -0.25, 1.33, 0.95));
      group.add(at(box(1.04, 0.9, 0.02, m.glass, 0.005), -0.25, 1.45, 0.94));
      // Control panel with screen and buttons.
      group.add(at(box(0.42, 0.85, 0.08, m.panel, 0.03), 0.72, 1.45, 0.86));
      const screen = new THREE.Mesh(new THREE.PlaneGeometry(0.3, 0.22), new THREE.MeshBasicMaterial({
        map: canvasTexture(128, 96, (c) => {
          c.fillStyle = "#0f2a44"; c.fillRect(0, 0, 128, 96);
          c.fillStyle = "#38bdf8"; c.fillRect(10, 14, 70, 8); c.fillRect(10, 30, 50, 8);
          c.fillStyle = "#f87171"; c.fillRect(10, 52, 96, 12);
          c.fillStyle = "#fff"; c.font = "bold 11px sans-serif"; c.fillText("TEMP HIGH", 18, 62);
        }),
      }));
      group.add(at(screen, 0.72, 1.68, 0.905));
      ["#22c55e", "#f59e0b", "#ef4444"].forEach((color, i) => {
        const button = cylinder(0.035, 0.035, 0.03, standard(color, { emissive: color, emissiveIntensity: 0.4 }), 16);
        button.rotation.x = Math.PI / 2;
        group.add(at(button, 0.62 + i * 0.1, 1.38, 0.91));
      });
      // Warning sticker and vents.
      const sticker = new THREE.Mesh(new THREE.PlaneGeometry(0.26, 0.24), new THREE.MeshBasicMaterial({
        transparent: true,
        map: canvasTexture(128, 116, (c) => {
          c.fillStyle = "#facc15"; c.strokeStyle = "#111"; c.lineWidth = 7;
          c.beginPath(); c.moveTo(64, 6); c.lineTo(122, 110); c.lineTo(6, 110); c.closePath(); c.fill(); c.stroke();
          c.fillStyle = "#111"; c.font = "bold 58px sans-serif"; c.textAlign = "center"; c.fillText("!", 64, 96);
        }),
      }));
      sticker.rotation.y = Math.PI / 2;
      group.add(at(sticker, 1.06, 1.25, 0.3));
      for (let i = 0; i < 5; i += 1) group.add(at(box(0.7, 0.03, 0.05, m.dark, 0.01), -0.4, 2.2 - i * 0.07, 0.86));
      const lamp = lampFor(group, 0.55, 2.45, -0.2);
      group.userData.chamber = chamberMaterial;
      return { top: 3.1, lamp, size: new THREE.Vector3(2.4, 3.0, 2.1) };
    },
    inspection(group) {
      for (const z of [-0.85, 0.85]) group.add(at(box(0.14, 2.2, 0.14, m.panel, 0.03), 0, 1.1, z));
      group.add(at(box(0.2, 0.16, 1.9, m.panel, 0.04), 0, 2.2, 0));
      group.add(at(box(0.36, 0.3, 0.3, m.dark, 0.05), 0, 1.95, 0));
      const lens = cylinder(0.08, 0.1, 0.16, m.dark, 20);
      group.add(at(lens, 0, 1.75, 0));
      const cone = new THREE.Mesh(new THREE.ConeGeometry(0.42, 0.8, 32, 1, true), new THREE.MeshBasicMaterial({ color: "#bfe3ff", transparent: true, opacity: 0.22, depthWrite: false, side: THREE.DoubleSide }));
      group.add(at(cone, 0, 1.3, 0));
      // Monitor on a stand in front of the belt.
      group.add(at(cylinder(0.04, 0.04, 1.2, m.steel, 12), 0.65, 0.6, 1.25));
      group.add(at(box(0.85, 0.62, 0.08, m.dark, 0.04), 0.65, 1.45, 1.25));
      const monitor = new THREE.Mesh(new THREE.PlaneGeometry(0.75, 0.52), new THREE.MeshBasicMaterial({
        map: canvasTexture(192, 132, (c) => {
          c.fillStyle = "#0b2a1f"; c.fillRect(0, 0, 192, 132);
          c.fillStyle = "#16a34a"; c.beginPath(); c.arc(96, 66, 40, 0, Math.PI * 2); c.fill();
          c.fillStyle = "#fff"; c.font = "bold 30px sans-serif"; c.textAlign = "center"; c.fillText("OK", 96, 77);
        }),
      }));
      group.add(at(monitor, 0.65, 1.45, 1.295));
      group.userData.cone = cone;
      group.userData.monitor = monitor;
      const lamp = lampFor(group, 0, 2.28, -0.85);
      return { top: 2.95, lamp, size: new THREE.Vector3(1.6, 2.9, 2.2) };
    },
    packing(group) {
      for (const [x, z] of [[-0.95, -0.95], [0.95, -0.95], [-0.95, 0.95], [0.95, 0.95]]) group.add(at(box(0.1, 2.0, 0.1, m.panel, 0.03), x, 1.0, z));
      for (const [x, z] of [[0, -0.95], [0, 0.95]]) group.add(at(box(1.98, 0.08, 0.1, m.panel, 0.03), x, 2.0, z));
      for (const x of [-0.95, 0.95]) group.add(at(box(0.1, 0.08, 1.98, m.panel, 0.03), x, 2.0, 0));
      group.add(at(box(1.8, 1.2, 0.02, m.glass, 0.005), 0, 1.35, -0.95));
      group.add(at(box(0.02, 1.2, 1.8, m.glass, 0.005), -0.95, 1.35, 0));
      for (const [x, z] of [[-0.95, 0.95], [0.95, 0.95]]) group.add(at(box(0.11, 0.5, 0.11, m.yellow, 0.02), x, 0.25, z));
      group.add(at(box(0.9, 0.62, 0.78, m.cardboard, 0.03), 0.15, 1.25, 0.05));
      group.add(at(box(0.92, 0.04, 0.4, standard("#b3854b", { roughness: 0.9 }), 0.01), 0.15, 1.58, -0.17));
      for (let i = 0; i < 6; i += 1) group.add(at(box(0.18, 0.08, 0.18, m.part, 0.02), -0.05 + (i % 3) * 0.2, 1.6, 0.1 + Math.floor(i / 3) * 0.2));
      group.add(at(box(0.7, 0.55, 0.6, m.cardboard, 0.03), 1.6, BELT_Y + 0.3, 0.05));
      const lamp = lampFor(group, 0.95, 2.04, -0.95);
      return { top: 2.75, lamp, size: new THREE.Vector3(2.2, 2.7, 2.2) };
    },
  };

  for (const station of STATIONS) {
    const group = at(new THREE.Group(), STATION_X[station.id], 0, 0);
    group.userData.stationId = station.id;
    const { top, lamp, size } = builders[station.id](group);
    // Invisible pick volume, floor ring and outline used for hover/selection/fault.
    const hit = new THREE.Mesh(new THREE.BoxGeometry(size.x, size.y, size.z), new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false }));
    hit.position.y = size.y / 2;
    hit.userData.stationId = station.id;
    group.add(hit);
    const ring = new THREE.Mesh(new THREE.RingGeometry(Math.max(size.x, size.z) * 0.62, Math.max(size.x, size.z) * 0.7, 64), new THREE.MeshBasicMaterial({ color: FAULT, transparent: true, opacity: 0, depthWrite: false }));
    ring.rotation.x = -Math.PI / 2;
    ring.position.y = 0.012;
    group.add(ring);
    const outline = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(size.x + 0.12, size.y + 0.08, size.z + 0.12)), new THREE.LineBasicMaterial({ color: FAULT, transparent: true, opacity: 0 }));
    outline.position.y = (size.y + 0.08) / 2;
    group.add(outline);
    const label = document.createElement("div");
    label.className = "dx-label";
    label.dataset.station = station.id;
    label.innerHTML = `<span class="dx-label-index">${station.index}</span><span class="dx-label-name"></span>`;
    (label.querySelector(".dx-label-name") as HTMLElement).textContent = station.name;
    const labelObject = new CSS2DObject(label);
    labelObject.position.set(0, top + 0.35, 0);
    group.add(labelObject);
    if (station.id === scenario.station) {
      const callout = document.createElement("div");
      callout.className = "dx-callout";
      callout.innerHTML = `<span class="dx-callout-icon">!</span><span><strong>Nghi vấn</strong><small></small></span>`;
      (callout.querySelector("small") as HTMLElement).textContent = scenario.issue;
      // Leader line from the beacon to the callout, as on a plant overview board.
      const anchor = new THREE.Vector3(0.4, top - 0.1, 0);
      const tip = new THREE.Vector3(2.1, top + 1.5, 0.2);
      group.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([anchor, tip]), new THREE.LineBasicMaterial({ color: FAULT })));
      const dot = new THREE.Mesh(new THREE.SphereGeometry(0.06, 16, 12), new THREE.MeshBasicMaterial({ color: FAULT }));
      group.add(at(dot, anchor.x, anchor.y, anchor.z));
      const calloutObject = new CSS2DObject(callout);
      calloutObject.position.copy(tip);
      calloutObject.center.set(0, 1);
      group.add(calloutObject);
    }
    line.add(group);
    stations.set(station.id, { group, hit, ring, outline, lamp, label, top });
  }

  return { products, stations, start, end };
}

/** Free GPU resources for every mesh, line and texture in the scene. */
function dispose(scene: THREE.Scene) {
  const materials = new Set<THREE.Material>();
  scene.traverse((object) => {
    if (object instanceof THREE.Mesh || object instanceof THREE.Line) {
      object.geometry.dispose();
      for (const material of Array.isArray(object.material) ? object.material : [object.material]) materials.add(material);
    }
    if (object instanceof CSS2DObject) object.element.remove();
  });
  for (const material of materials) {
    for (const value of Object.values(material)) if (value instanceof THREE.Texture) value.dispose();
    material.dispose();
  }
}

/**
 * Interactive WebGL view of a five-station production line.
 *
 * Builds the line once per scenario, highlights the suspected station and lets
 * the user orbit, zoom, pan and click stations. Calls `onError` when WebGL is
 * unavailable instead of throwing.
 */
export default function ProductionLineScene({ scenario, selected, autoRotate, onSelect, onError, apiRef }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const state = useRef({ selected, autoRotate, onSelect, onError });
  state.current = { selected, autoRotate, onSelect, onError };

  useEffect(() => {
    const container = host.current;
    if (!container) return;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
    } catch {
      state.current.onError("Trình duyệt không hỗ trợ WebGL nên không hiển thị được mô hình 3D.");
      return;
    }
    const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;
    renderer.domElement.className = "dx-canvas";
    container.appendChild(renderer.domElement);
    const labels = new CSS2DRenderer();
    labels.domElement.className = "dx-labels";
    container.appendChild(labels.domElement);

    const scene = new THREE.Scene();
    const pmrem = new THREE.PMREMGenerator(renderer);
    const environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    scene.environment = environment;
    scene.environmentIntensity = 0.55;
    scene.fog = new THREE.Fog("#eef2f7", 26, 48);

    const camera = new THREE.PerspectiveCamera(34, 1, 0.1, 120);
    camera.position.copy(homePosition(1.6));
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.copy(HOME_TARGET);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.minDistance = 5;
    controls.maxDistance = 28;
    controls.maxPolarAngle = Math.PI * 0.46;
    controls.autoRotateSpeed = 0.6;
    controls.update();

    scene.add(new THREE.HemisphereLight("#ffffff", "#c7d2e0", 1.1));
    const sun = new THREE.DirectionalLight("#ffffff", 2.2);
    sun.position.set(6, 12, 8);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    sun.shadow.camera.left = -12;
    sun.shadow.camera.right = 12;
    sun.shadow.camera.top = 8;
    sun.shadow.camera.bottom = -8;
    sun.shadow.bias = -0.0004;
    sun.shadow.radius = 4;
    scene.add(sun);
    const fill = new THREE.DirectionalLight("#dbe7ff", 0.6);
    fill.position.set(-8, 6, -6);
    scene.add(fill);

    const floor = new THREE.Mesh(new THREE.CircleGeometry(22, 64), new THREE.MeshStandardMaterial({ color: "#f1f4f8", roughness: 0.95 }));
    floor.rotation.x = -Math.PI / 2;
    floor.receiveShadow = true;
    scene.add(floor);
    const grid = new THREE.GridHelper(40, 40, "#d3dbe6", "#e3e8ef");
    grid.position.y = 0.005;
    (grid.material as THREE.Material).transparent = true;
    (grid.material as THREE.Material).opacity = 0.55;
    scene.add(grid);

    const { products, stations, start, end } = buildLine(scene, scenario);
    const faultLight = new THREE.PointLight("#ff3b30", 0, 4.5, 2);
    const faultStation = stations.get(scenario.station);
    if (faultStation) {
      faultLight.position.set(STATION_X[scenario.station], faultStation.top - 0.1, 0.6);
      scene.add(faultLight);
    }

    let hovered: StationId | null = null;
    let fly: { position: THREE.Vector3; target: THREE.Vector3 } | null = null;
    const flyTo = (station: StationId | null) => {
      const target = station ? new THREE.Vector3(STATION_X[station], 1.2, 0) : HOME_TARGET.clone();
      const position = station ? target.clone().add(new THREE.Vector3(3.4, 3.6, 6.2)) : homePosition(camera.aspect);
      if (reduceMotion) {
        controls.target.copy(target);
        camera.position.copy(position);
      } else fly = { position, target };
    };
    controls.addEventListener("start", () => { fly = null; });
    if (apiRef) apiRef.current = { resetView: () => flyTo(null), focus: (station) => flyTo(station) };

    // Pointer picking against the invisible station volumes.
    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    const hits = [...stations.values()].map((parts) => parts.hit);
    const pick = (event: PointerEvent): StationId | null => {
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
      raycaster.setFromCamera(pointer, camera);
      return (raycaster.intersectObjects(hits, false)[0]?.object.userData.stationId as StationId | undefined) ?? null;
    };
    let down: { x: number; y: number } | null = null;
    const onMove = (event: PointerEvent) => {
      hovered = pick(event);
      renderer.domElement.style.cursor = hovered ? "pointer" : "grab";
    };
    const onDown = (event: PointerEvent) => { down = { x: event.clientX, y: event.clientY }; };
    const onUp = (event: PointerEvent) => {
      // A drag rotates the camera; only a near-stationary press selects.
      if (down && Math.hypot(event.clientX - down.x, event.clientY - down.y) < 5) {
        const station = pick(event);
        if (station) state.current.onSelect(station);
      }
      down = null;
    };
    const onLeave = () => { hovered = null; };
    renderer.domElement.addEventListener("pointermove", onMove);
    renderer.domElement.addEventListener("pointerdown", onDown);
    renderer.domElement.addEventListener("pointerup", onUp);
    renderer.domElement.addEventListener("pointerleave", onLeave);
    const labelClicks = [...stations.entries()].map(([id, parts]) => {
      const handler = () => state.current.onSelect(id);
      parts.label.addEventListener("click", handler);
      return () => parts.label.removeEventListener("click", handler);
    });

    const resize = () => {
      const { width, height } = container.getBoundingClientRect();
      if (!width || !height) return;
      renderer.setSize(width, height, false);
      labels.setSize(width, height);
      const first = camera.aspect === 1;
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      if (first) camera.position.copy(homePosition(camera.aspect));
    };
    const observer = new ResizeObserver(resize);
    observer.observe(container);
    resize();

    const clock = new THREE.Clock();
    renderer.setAnimationLoop(() => {
      const dt = Math.min(clock.getDelta(), 0.05);
      const t = clock.elapsedTime;
      const pulse = reduceMotion ? 0.7 : 0.5 + 0.5 * Math.sin(t * 4);
      const { selected: current, autoRotate } = state.current;

      if (!reduceMotion) {
        const speed = scenario.station === "feeder" ? 0.25 : 0.6;
        for (const product of products) {
          product.position.x += speed * dt;
          if (product.position.x > end) product.position.x = start;
        }
        const arm = stations.get("robot")?.group.userData.arm as { turret: THREE.Group; shoulder: THREE.Group; elbow: THREE.Group } | undefined;
        if (arm) {
          const shake = scenario.station === "robot" ? Math.sin(t * 38) * 0.02 : 0;
          arm.turret.rotation.y = Math.sin(t * 0.9) * 0.9 + shake;
          arm.shoulder.rotation.x = 0.35 + Math.sin(t * 1.8) * 0.15 + shake;
          arm.elbow.rotation.x = 0.9 + Math.sin(t * 1.8 + 1) * 0.2;
        }
        const inspection = stations.get("inspection")?.group.userData.cone as THREE.Mesh | undefined;
        if (inspection) (inspection.material as THREE.MeshBasicMaterial).opacity = 0.14 + 0.1 * (0.5 + 0.5 * Math.sin(t * 6));
      }
      const chamber = stations.get("processing")?.group.userData.chamber as THREE.MeshStandardMaterial | undefined;
      if (chamber) chamber.emissiveIntensity = scenario.station === "processing" ? 0.25 + 0.45 * pulse : 0;
      faultLight.intensity = 6 + 10 * pulse;

      for (const [id, parts] of stations) {
        const fault = id === scenario.station;
        const isSelected = id === current;
        const lamp = parts.lamp.material as THREE.MeshStandardMaterial;
        lamp.color.copy(fault ? FAULT : OK);
        lamp.emissive.copy(fault ? FAULT : OK);
        lamp.emissiveIntensity = fault ? 1 + 2.5 * pulse : 1.2;
        const ring = parts.ring.material as THREE.MeshBasicMaterial;
        const outline = parts.outline.material as THREE.LineBasicMaterial;
        if (fault) {
          ring.color.copy(FAULT);
          ring.opacity = 0.25 + 0.45 * pulse;
          outline.color.copy(FAULT);
          outline.opacity = 0.45 + 0.5 * pulse;
        } else {
          ring.color.copy(SELECT);
          ring.opacity = isSelected ? 0.45 : hovered === id ? 0.22 : 0;
          outline.color.copy(SELECT);
          outline.opacity = isSelected ? 0.8 : hovered === id ? 0.35 : 0;
        }
        parts.label.classList.toggle("fault", fault);
        parts.label.classList.toggle("selected", isSelected);
        parts.label.classList.toggle("hovered", hovered === id);
      }

      if (fly) {
        const k = 1 - Math.exp(-dt * 4);
        controls.target.lerp(fly.target, k);
        camera.position.lerp(fly.position, k);
        if (camera.position.distanceTo(fly.position) < 0.02) fly = null;
      }
      controls.autoRotate = autoRotate && !reduceMotion && !fly;
      controls.update();
      renderer.render(scene, camera);
      labels.render(scene, camera);
    });

    return () => {
      renderer.setAnimationLoop(null);
      observer.disconnect();
      labelClicks.forEach((remove) => remove());
      renderer.domElement.removeEventListener("pointermove", onMove);
      renderer.domElement.removeEventListener("pointerdown", onDown);
      renderer.domElement.removeEventListener("pointerup", onUp);
      renderer.domElement.removeEventListener("pointerleave", onLeave);
      if (apiRef) apiRef.current = null;
      controls.dispose();
      dispose(scene);
      environment.dispose();
      pmrem.dispose();
      renderer.dispose();
      renderer.domElement.remove();
      labels.domElement.remove();
    };
  }, [scenario, apiRef]);

  return <div ref={host} className="dx-scene" data-testid="diagnosis-scene" />;
}
