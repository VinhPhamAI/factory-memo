import * as THREE from "three";
import { at, placement, setLed, STATUS_COLORS, turn, type Kit, type Params } from "../kit";
import type { BuiltComponent, MachineState } from "./types";

/**
 * Controllers, operator devices, valves and sensors. Wall-mounted items have
 * their back at the origin and face +z.
 */

/**
 * PLC rack on a DIN rail: power supply, CPU with status LEDs, I/O modules,
 * terminal strip and cable ducts. Params: `modules`.
 */
export function plc(p: Params, kit: Kit): BuiltComponent {
  const modules = p.num("modules", 6);
  const moduleW = 0.035;
  const width = 0.07 + 0.06 + modules * moduleW + 0.04;
  const group = new THREE.Group();
  group.add(at(kit.box(width + 0.08, 0.035, 0.008, "steel", 0), 0, 0, 0.004));
  for (const y of [0.13, -0.15]) group.add(at(kit.box(width + 0.08, 0.06, 0.06, "paintGrey", 0.006), 0, y, 0.03));
  let x = -width / 2;
  group.add(at(kit.box(0.07, 0.13, 0.09, "paintGrey", 0.006), x + 0.035, 0, 0.045));
  x += 0.07;
  group.add(at(kit.box(0.06, 0.13, 0.1, "graphite", 0.006), x + 0.03, 0, 0.05));
  const run = kit.led(STATUS_COLORS.normal);
  const error = kit.led("#3b1d1d", 0.2);
  group.add(at(kit.box(0.012, 0.008, 0.004, run, 0), x + 0.02, 0.04, 0.101));
  group.add(at(kit.box(0.012, 0.008, 0.004, error, 0), x + 0.04, 0.04, 0.101));
  group.add(at(kit.box(0.044, 0.03, 0.002, "darkGlass", 0), x + 0.03, 0.0, 0.101));
  x += 0.06;
  const ios: THREE.Matrix4[] = [];
  const leds: THREE.Matrix4[] = [];
  for (let i = 0; i < modules; i += 1) {
    const cx = x + moduleW / 2 + i * moduleW;
    ios.push(placement(cx, 0, 0.045));
    for (let j = 0; j < 4; j += 1) leds.push(placement(cx - 0.008 + (j % 2) * 0.016, 0.045 - Math.floor(j / 2) * 0.012, 0.091));
  }
  group.add(kit.instances(kit.boxGeometry(moduleW - 0.002, 0.13, 0.09, 0.004), "paintDark", ios));
  group.add(kit.instances(kit.boxGeometry(0.006, 0.005, 0.003, 0), kit.led("#4ade80", 0.9), leds));
  const terminals: THREE.Matrix4[] = [];
  for (let i = 0; i < Math.floor(width / 0.012); i += 1) terminals.push(placement(-width / 2 + 0.006 + i * 0.012, -0.24, 0.03));
  group.add(at(kit.box(width + 0.04, 0.02, 0.008, "steel", 0), 0, -0.24, 0.004));
  group.add(kit.instances(kit.boxGeometry(0.01, 0.05, 0.05, 0.002), "paintGrey", terminals));
  return {
    object: group,
    setStatus(status) {
      setLed(error, status, "#3b1d1d");
      error.emissiveIntensity = status === "normal" ? 0.2 : 2.2;
      run.emissiveIntensity = status === "fault" ? 0.4 : 1.2;
    },
  };
}

/** Draw the machine HMI screen: axis readout normally, an alarm banner on a fault. */
function drawHmi(c: CanvasRenderingContext2D, machine: MachineState, compact: boolean) {
  const w = c.canvas.width;
  const h = c.canvas.height;
  c.fillStyle = "#0e1c2b";
  c.fillRect(0, 0, w, h);
  c.fillStyle = "#16324f";
  c.fillRect(0, 0, w, h * 0.16);
  c.fillStyle = "#cfe3ff";
  c.font = `bold ${Math.round(h * 0.09)}px Inter, system-ui, sans-serif`;
  c.fillText(compact ? "LINE" : "AUTO  ·  MEM", w * 0.04, h * 0.115);
  c.fillStyle = "#7dd3fc";
  c.font = `${Math.round(h * 0.1)}px ui-monospace, monospace`;
  const rows = compact ? ["RUN", "CNT 1284"] : ["X  -152.300", "Y    48.120", "Z   -12.000"];
  rows.forEach((row, i) => c.fillText(row, w * 0.06, h * (0.32 + i * 0.14)));
  if (!compact) {
    c.fillStyle = "#1e3a5f";
    c.fillRect(w * 0.6, h * 0.24, w * 0.34, h * 0.08);
    c.fillStyle = machine.fault ? "#f87171" : "#4ade80";
    c.fillRect(w * 0.6, h * 0.24, w * 0.34 * (machine.fault ? 0.92 : 0.46), h * 0.08);
  }
  if (machine.fault) {
    c.fillStyle = "#b91c1c";
    c.fillRect(0, h * 0.72, w, h * 0.28);
    c.fillStyle = "#fff";
    c.font = `bold ${Math.round(h * 0.1)}px Inter, system-ui, sans-serif`;
    c.fillText("ALARM", w * 0.05, h * 0.84);
    c.font = `${Math.round(h * 0.075)}px Inter, system-ui, sans-serif`;
    c.fillText((machine.issue ?? "Check machine").slice(0, 26), w * 0.05, h * 0.95);
  }
}

/**
 * Operator panel. `style: cnc` is a pillar-mounted panel with screen, keys,
 * feed buttons and an emergency stop; `style: station` is a pushbutton box on
 * a floor post with a small screen.
 */
export function controlPanel(p: Params, kit: Kit): BuiltComponent {
  const style = p.str<"cnc" | "station">("style", "cnc");
  const group = new THREE.Group();
  const compact = style === "station";
  const screen = kit.screen(compact ? 160 : 256, compact ? 100 : 180, (c) => drawHmi(c, { fault: false }, compact));
  const redraw = (machine: MachineState) => {
    const context = (screen.texture.image as HTMLCanvasElement).getContext?.("2d");
    if (!context) return;
    drawHmi(context, machine, compact);
    screen.texture.needsUpdate = true;
  };
  const buttons: [string, number, number][] = [];
  let face: THREE.Object3D = group;
  if (style === "cnc") {
    group.add(at(kit.box(0.5, 0.78, 0.1, "paintDark", 0.025), 0, 0, 0.05));
    group.add(at(kit.box(0.46, 0.33, 0.01, "graphite", 0.008), 0, 0.17, 0.103));
    group.add(at(new THREE.Mesh(new THREE.PlaneGeometry(0.42, 0.295), screen.material), 0, 0.17, 0.109));
    const keys: THREE.Matrix4[] = [];
    for (let row = 0; row < 4; row += 1) for (let col = 0; col < 7; col += 1) keys.push(placement(-0.18 + col * 0.04, -0.05 - row * 0.035, 0.104));
    group.add(kit.instances(kit.boxGeometry(0.03, 0.025, 0.01, 0.004), "paintGrey", keys));
    buttons.push(["#16a34a", -0.16, -0.29], ["#f59e0b", -0.08, -0.29]);
    group.add(turn(at(kit.cyl(0.045, 0.025, "graphite", 24), 0.12, -0.29, 0.11), Math.PI / 2, 0, 0));
    group.add(turn(at(kit.cyl(0.032, 0.02, "chrome", 20), 0.12, -0.29, 0.13), Math.PI / 2, 0, 0));
    group.add(turn(at(kit.cyl(0.035, 0.03, "accent", 24), 0.2, -0.29, 0.115), Math.PI / 2, 0, 0));
  } else {
    group.add(at(kit.cyl(0.03, 1.0, "paintGrey", 16), 0, 0.5, 0));
    group.add(at(kit.box(0.24, 0.02, 0.24, "graphite", 0.004), 0, 0.01, 0));
    face = turn(at(new THREE.Group(), 0, 1.08, 0), -0.5, 0, 0);
    group.add(face);
    face.add(at(kit.box(0.28, 0.2, 0.12, "paintGrey", 0.02), 0, 0, 0));
    face.add(at(new THREE.Mesh(new THREE.PlaneGeometry(0.12, 0.075), screen.material), -0.06, 0.03, 0.061));
    buttons.push(["#16a34a", 0.05, 0.045], ["#f8fafc", 0.1, 0.045]);
    face.add(turn(at(kit.cyl(0.03, 0.03, "accent", 20), 0.07, -0.05, 0.07), Math.PI / 2, 0, 0));
    face.add(turn(at(kit.cyl(0.035, 0.006, "yellow", 20), 0.07, -0.05, 0.061), Math.PI / 2, 0, 0));
  }
  for (const [color, x, y] of buttons) {
    const button = kit.cyl(compact ? 0.016 : 0.022, 0.02, kit.led(color, 0.5), 16);
    face.add(turn(at(button, x, y, compact ? 0.065 : 0.11), Math.PI / 2, 0, 0));
  }
  return {
    object: group,
    setStatus(_status, machine) {
      redraw(machine);
    },
  };
}

/**
 * Stack light on a pole: red, amber and green tiers. Red blinks while the
 * machine has a suspected fault. Params: `pole`.
 */
export function signalTower(p: Params, kit: Kit): BuiltComponent {
  const pole = p.num("pole", 0.25);
  const group = new THREE.Group();
  group.add(at(kit.cyl(0.035, 0.03, "graphite", 16), 0, 0.015, 0));
  group.add(at(kit.cyl(0.012, pole, "steel", 10), 0, 0.03 + pole / 2, 0));
  const tiers = [
    { color: "#ef4444", y: 0 },
    { color: "#f59e0b", y: 1 },
    { color: "#22c55e", y: 2 },
  ].map(({ color, y }) => {
    const material = kit.led(color, 0.15);
    group.add(at(kit.cyl(0.04, 0.07, material, 20), 0, 0.03 + pole + 0.215 - y * 0.072, 0));
    return material;
  });
  group.add(at(kit.cyl(0.042, 0.02, "graphite", 20), 0, 0.03 + pole + 0.26, 0));
  group.add(at(kit.cyl(0.042, 0.015, "graphite", 20), 0, 0.03 + pole, 0));
  const [red, amber, green] = tiers;
  let fault = false;
  const apply = (blinkOn: boolean) => {
    red.emissiveIntensity = fault && blinkOn ? 3 : 0.12;
    amber.emissiveIntensity = 0.12;
    green.emissiveIntensity = fault ? 0.12 : 2;
  };
  apply(true);
  return {
    object: group,
    update(time) {
      apply(Math.sin(time * 6) > 0);
    },
    setStatus(_status, machine) {
      fault = machine.fault;
      apply(true);
    },
  };
}

/**
 * Solenoid valve manifold: base block with valves along x, coils facing +z,
 * silencers and push-in fittings. Params: `stations`.
 */
export function valveManifold(p: Params, kit: Kit): BuiltComponent {
  const stations = p.num("stations", 4);
  const pitch = 0.032;
  const length = stations * pitch + 0.06;
  const group = new THREE.Group();
  group.add(at(kit.box(length, 0.035, 0.07, "aluminum", 0.004), 0, 0.0175, 0.035));
  for (const side of [-1, 1]) group.add(at(kit.box(0.025, 0.05, 0.075, "graphite", 0.004), side * (length / 2 - 0.0125), 0.025, 0.0375));
  const bodies: THREE.Matrix4[] = [];
  const coils: THREE.Matrix4[] = [];
  const fittings: THREE.Matrix4[] = [];
  for (let i = 0; i < stations; i += 1) {
    const x = -length / 2 + 0.03 + pitch / 2 + i * pitch;
    bodies.push(placement(x, 0.06, 0.035));
    coils.push(placement(x, 0.085, 0.085));
    for (const dx of [-0.007, 0.007]) fittings.push(placement(x + dx, 0.05, 0.085, [Math.PI / 2, 0, 0]));
  }
  group.add(kit.instances(kit.boxGeometry(pitch - 0.002, 0.05, 0.07, 0.003), "graphite", bodies));
  group.add(kit.instances(kit.boxGeometry(pitch - 0.004, 0.035, 0.035, 0.004), "paintDark", coils));
  group.add(kit.instances(kit.cylinderGeometry(0.004, 0.004, 0.03, 8), "blue", fittings));
  const led = kit.led(STATUS_COLORS.related, 0.6);
  const leds = Array.from({ length: stations }, (_, i) => placement(-length / 2 + 0.03 + pitch / 2 + i * pitch, 0.104, 0.1));
  group.add(kit.instances(kit.boxGeometry(0.006, 0.004, 0.004, 0), led, leds));
  for (const side of [-1, 1]) group.add(at(kit.axle(0.007, 0.03, "brass", "x", 10), side * (length / 2 + 0.012), 0.025, 0.04));
  return {
    object: group,
    setStatus(status) {
      setLed(led, status, STATUS_COLORS.related);
    },
  };
}

/**
 * Sensor variants. Sensors face +z (the sensing direction or the outward
 * normal of the surface they sit on) and carry a status LED.
 *
 * Params: `variant` (photoelectric | proximity | reed | interlock | temperature |
 * camera | pressure), `beam` (photoelectric beam length), `mast` and `reach`
 * (camera bracket).
 */
export function sensor(p: Params, kit: Kit): BuiltComponent {
  const variant = p.str<"photoelectric" | "proximity" | "reed" | "interlock" | "temperature" | "camera" | "pressure">("variant", "photoelectric");
  const group = new THREE.Group();
  const led = kit.led(STATUS_COLORS.normal, 1);
  let beam: THREE.Mesh | null = null;
  if (variant === "photoelectric") {
    group.add(at(kit.box(0.004, 0.08, 0.04, "steel", 0), 0, -0.02, -0.02));
    group.add(at(kit.box(0.032, 0.052, 0.022, "graphite", 0.004), 0, 0.03, 0));
    group.add(at(kit.box(0.032, 0.012, 0.022, "yellow", 0.003), 0, 0.058, 0));
    group.add(turn(at(kit.cyl(0.008, 0.004, "darkGlass", 16), 0, 0.03, 0.012), Math.PI / 2, 0, 0));
    group.add(at(kit.box(0.008, 0.004, 0.006, led, 0), 0, 0.066, 0.004));
    group.add(at(kit.axle(0.0035, 0.06, "cable", "z", 6), 0, 0.0, -0.035));
    const length = p.num("beam", 0);
    if (length > 0) {
      const material = kit.own(new THREE.MeshBasicMaterial({ color: "#ef4444", transparent: true, opacity: 0.35, depthWrite: false }));
      material.userData.noShadow = true;
      material.userData.noPick = true;
      beam = turn(at(new THREE.Mesh(kit.cylinderGeometry(0.002, 0.002, length, 6), material), 0, 0.03, 0.014 + length / 2), Math.PI / 2, 0, 0);
      group.add(beam);
      group.add(at(kit.box(0.04, 0.05, 0.006, "accent", 0.002), 0, 0.03, 0.014 + length));
    }
  } else if (variant === "proximity") {
    group.add(turn(at(kit.cyl(0.009, 0.06, "brass", 16), 0, 0, 0.03), Math.PI / 2, 0, 0));
    group.add(turn(at(kit.cyl(0.012, 0.006, "steel", 6), 0, 0, 0.012), Math.PI / 2, 0, 0));
    group.add(turn(at(kit.cyl(0.0095, 0.006, led, 16), 0, 0, -0.003), Math.PI / 2, 0, 0));
    group.add(at(kit.axle(0.003, 0.05, "cable", "z", 6), 0, 0, -0.03));
  } else if (variant === "reed") {
    group.add(at(kit.box(0.008, 0.026, 0.006, "graphite", 0.002), 0, 0, 0.003));
    group.add(at(kit.box(0.006, 0.006, 0.003, led, 0), 0, 0.008, 0.007));
    group.add(at(kit.cyl(0.0022, 0.05, "cable", 6), 0, -0.035, 0.003));
  } else if (variant === "interlock") {
    group.add(at(kit.box(0.04, 0.1, 0.035, "yellow", 0.005), 0, 0, 0.0175));
    group.add(at(kit.box(0.03, 0.03, 0.006, "graphite", 0.002), 0, 0.03, 0.036));
    group.add(at(kit.box(0.012, 0.006, 0.004, led, 0), 0, -0.02, 0.036));
    group.add(at(kit.box(0.014, 0.03, 0.03, "steel", 0.003), -0.03, 0.03, 0.02));
  } else if (variant === "temperature") {
    group.add(turn(at(kit.cyl(0.018, 0.02, "steel", 6), 0, 0, 0.01), Math.PI / 2, 0, 0));
    group.add(turn(at(kit.cyl(0.014, 0.025, "graphite", 16), 0, 0, 0.032), Math.PI / 2, 0, 0));
    group.add(turn(at(kit.cyl(0.006, 0.004, led, 12), 0, 0, 0.046), Math.PI / 2, 0, 0));
    group.add(at(kit.cyl(0.003, 0.12, "cable", 6), 0, -0.06, 0.032));
  } else if (variant === "pressure") {
    group.add(turn(at(kit.cyl(0.03, 0.02, "chrome", 24), 0, 0, 0.01), Math.PI / 2, 0, 0));
    const dial = kit.screen(64, 64, (c) => {
      c.fillStyle = "#f8fafc";
      c.beginPath();
      c.arc(32, 32, 31, 0, Math.PI * 2);
      c.fill();
      c.strokeStyle = "#16a34a";
      c.lineWidth = 6;
      c.beginPath();
      c.arc(32, 32, 24, Math.PI * 0.8, Math.PI * 1.6);
      c.stroke();
      c.strokeStyle = "#111";
      c.lineWidth = 3;
      c.beginPath();
      c.moveTo(32, 32);
      c.lineTo(14, 22);
      c.stroke();
    });
    group.add(at(new THREE.Mesh(new THREE.CircleGeometry(0.026, 24), dial.material), 0, 0, 0.0205));
    group.add(at(kit.box(0.008, 0.006, 0.004, led, 0), 0, -0.036, 0.01));
  } else {
    // Vision camera on an L-bracket: mast up from the origin, arm along +z, looking down.
    const mast = p.num("mast", 0.5);
    const reach = p.num("reach", 0.35);
    group.add(at(kit.box(0.04, mast, 0.04, "aluminum", 0.006), 0, mast / 2, 0));
    group.add(at(kit.box(0.04, 0.04, reach + 0.04, "aluminum", 0.006), 0, mast + 0.02, reach / 2));
    group.add(at(kit.box(0.07, 0.06, 0.1, "aluminum", 0.01), 0, mast - 0.03, reach));
    group.add(at(kit.cyl(0.022, 0.05, "graphite", 20), 0, mast - 0.085, reach));
    group.add(at(kit.cyl(0.04, 0.012, kit.led("#e0f2fe", 1.4), 24), 0, mast - 0.115, reach));
    group.add(at(kit.box(0.01, 0.006, 0.01, led, 0), 0.03, mast, reach + 0.03));
    const cone = new THREE.Mesh(
      kit.cylinderGeometry(0.02, 0.16, mast - 0.25, 24, true),
      kit.own(new THREE.MeshBasicMaterial({ color: "#bfe3ff", transparent: true, opacity: 0.12, depthWrite: false, side: THREE.DoubleSide })),
    );
    (cone.material as THREE.Material).userData.noShadow = true;
    (cone.material as THREE.Material).userData.noPick = true;
    group.add(at(cone, 0, (mast - 0.12) / 2 + 0.06, reach));
  }
  return {
    object: group,
    setStatus(status) {
      setLed(led, status);
      if (beam) (beam.material as THREE.MeshBasicMaterial).opacity = status === "normal" ? 0.3 : 0.75;
    },
  };
}

/**
 * Safety light curtain: emitter and receiver posts at ±width/2 along x with a
 * faint sensing field between them. Params: `width`, `height`.
 */
export function lightCurtain(p: Params, kit: Kit): BuiltComponent {
  const width = p.num("width", 0.8);
  const height = p.num("height", 1.2);
  const base = p.num("base", 0.3);
  const group = new THREE.Group();
  const strip = kit.led(STATUS_COLORS.normal, 0.9);
  for (const side of [-1, 1]) {
    const x = side * (width / 2);
    group.add(at(kit.box(0.08, 0.02, 0.08, "graphite", 0.004), x, 0.01, 0));
    group.add(at(kit.box(0.035, base + height + 0.05, 0.035, "paintDark", 0.006), x, (base + height + 0.05) / 2, -0.03));
    group.add(at(kit.box(0.032, height, 0.03, "yellow", 0.006), x, base + height / 2, 0));
    group.add(at(kit.box(0.006, height - 0.04, 0.012, "darkGlass", 0), x - side * 0.017, base + height / 2, 0));
    group.add(at(kit.box(0.02, 0.012, 0.006, strip, 0), x, base + height + 0.012, 0.012));
  }
  const fieldMaterial = kit.own(
    new THREE.MeshBasicMaterial({ color: "#86efac", transparent: true, opacity: 0.08, depthWrite: false, side: THREE.DoubleSide }),
  );
  fieldMaterial.userData.noShadow = true;
  fieldMaterial.userData.noPick = true;
  group.add(at(new THREE.Mesh(new THREE.PlaneGeometry(width - 0.04, height), fieldMaterial), 0, base + height / 2, 0));
  return {
    object: group,
    setStatus(status) {
      setLed(strip, status);
      fieldMaterial.color.set(status === "normal" ? "#86efac" : STATUS_COLORS[status]);
      fieldMaterial.opacity = status === "normal" ? 0.08 : 0.18;
    },
  };
}
