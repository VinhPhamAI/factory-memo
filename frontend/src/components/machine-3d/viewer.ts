import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DRenderer } from "three/addons/renderers/CSS2DRenderer.js";
import { componentStatus } from "@/services/machine-library/registry";
import type { ComponentStatus, FaultScenario, MachineDefinition } from "@/services/machine-library/types";
import { assembleMachine, componentBounds, type AssembledMachine, type ComponentHandle } from "./assemble";
import { Highlighter, markFor } from "./highlight";
import { canvasTexture, Kit } from "./kit";
import type { MachineState } from "./library/types";
import { createRenderer, type RendererBackend, type RendererHandle, type RendererPreference } from "./renderer";

export type ViewerStats = { fps: number; calls: number; triangles: number; backend: RendererBackend; pixelRatio: number };

export type ViewerEvents = {
  onSelect(id: string): void;
  onStats?(stats: ViewerStats): void;
};

/** Upper pixel-ratio cap; Retina screens render at 2× until frame time says otherwise. */
const MAX_PIXEL_RATIO = 2;
/** Frame rate the adaptive quality aims to keep. */
const TARGET_FPS = 55;

type Fly = { position: THREE.Vector3; target: THREE.Vector3 };

/** Build a marker element shown above a component in the 3D view. */
function markerElement(kind: "fault" | "related" | "selected" | "hover", label: string, detail?: string): HTMLElement {
  const element = document.createElement("div");
  element.className = `m3-marker ${kind}`;
  if (kind === "related") {
    element.title = label;
    element.innerHTML = '<span class="m3-pin"></span>';
    return element;
  }
  element.innerHTML = kind === "fault" ? '<span class="m3-pin"></span><span class="m3-tag"><strong></strong><small></small></span>' : '<span class="m3-tag"><strong></strong></span>';
  element.querySelector("strong")!.textContent = kind === "fault" ? "Nghi vấn" : label;
  const small = element.querySelector("small");
  if (small) small.textContent = detail ? `${label} · ${detail}` : label;
  return element;
}

/**
 * Interactive 3D view of one machine.
 *
 * Owns the renderer, scene, camera, picking and render loop. The React layer
 * creates one viewer per machine and passes selection, x-ray, motion and fault
 * state through setters; nothing here touches React.
 */
export class MachineViewer {
  private readonly scene = new THREE.Scene();
  private readonly camera = new THREE.PerspectiveCamera(35, 1, 0.05, 200);
  private readonly controls: OrbitControls;
  private readonly labels = new CSS2DRenderer();
  private readonly kit = new Kit();
  private readonly highlighter = new Highlighter();
  private readonly machineModel: AssembledMachine;
  private readonly raycaster = new THREE.Raycaster();
  private readonly pointer = new THREE.Vector2();
  private readonly ghosts = new Map<THREE.Material, THREE.Material>();
  private readonly cleanups: (() => void)[] = [];
  private readonly reduceMotion: boolean;
  private readonly clock = new THREE.Clock();
  private readonly sun: THREE.DirectionalLight;
  private pickables: THREE.Object3D[] = [];
  private fault: FaultScenario | undefined;
  private selected: string | null = null;
  private hidden = new Set<string>();
  private hovered: string | null = null;
  private pointerDirty = false;
  private xray = false;
  private motion: boolean;
  private time = 0;
  private fly: Fly | null = null;
  private visible = true;
  private pixelRatio: number;
  private frames: number[] = [];
  private lastQualityChange = 0;
  private lastStats = 0;
  private disposed = false;

  private constructor(
    private readonly container: HTMLElement,
    private readonly gpu: RendererHandle,
    private readonly machine: MachineDefinition,
    private readonly events: ViewerEvents,
  ) {
    this.reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    this.motion = !this.reduceMotion;
    this.pixelRatio = Math.min(window.devicePixelRatio || 1, MAX_PIXEL_RATIO);
    const canvas = gpu.renderer.domElement;
    canvas.className = "m3-canvas";
    container.appendChild(canvas);
    this.labels.domElement.className = "m3-labels";
    container.appendChild(this.labels.domElement);

    this.scene.environment = gpu.environment;
    this.scene.environmentIntensity = 0.7;
    this.machineModel = assembleMachine(machine, this.kit);
    this.scene.add(this.machineModel.root);
    this.sun = this.addStage();

    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.screenSpacePanning = true;
    this.controls.maxPolarAngle = Math.PI * 0.49;
    this.controls.autoRotateSpeed = 0.7;
    const radius = this.machineModel.bounds.getBoundingSphere(new THREE.Sphere()).radius;
    this.controls.minDistance = 0.6;
    this.controls.maxDistance = radius * 5;
    this.controls.addEventListener("start", () => {
      this.fly = null;
    });
    this.camera.position.set(...machine.camera.position);
    this.controls.target.set(...machine.camera.target);
    this.controls.update();

    this.refreshPickables();
    this.bindPointer(canvas);
    this.observe();
    this.applyStatus();
  }

  /**
   * Create a viewer inside a container.
   *
   * Args:
   *   container: Element that receives the canvas and label layer.
   *   machine: Machine to assemble.
   *   events: Selection and statistics callbacks.
   *   preference: Renderer backend preference.
   *
   * Returns:
   *   A running viewer.
   *
   * Raises:
   *   Error: No WebGPU or WebGL context is available.
   */
  static async create(container: HTMLElement, machine: MachineDefinition, events: ViewerEvents, preference: RendererPreference = "auto") {
    const gpu = await createRenderer(preference, Math.min(window.devicePixelRatio || 1, MAX_PIXEL_RATIO));
    const viewer = new MachineViewer(container, gpu, machine, events);
    viewer.start();
    return viewer;
  }

  get backend(): RendererBackend {
    return this.gpu.backend;
  }

  /** Floor, lights and a soft contact shadow sized to the machine. */
  private addStage(): THREE.DirectionalLight {
    const bounds = this.machineModel.bounds;
    const size = bounds.getSize(new THREE.Vector3());
    const center = bounds.getCenter(new THREE.Vector3());
    const span = Math.max(size.x, size.z);
    this.scene.fog = new THREE.Fog("#e8eef5", span * 3, span * 7);

    this.scene.add(new THREE.HemisphereLight("#ffffff", "#b7c4d4", 1.15));
    const sun = new THREE.DirectionalLight("#fffaf2", 2.4);
    sun.position.set(center.x + span * 0.8, span * 1.8, center.z + span * 1.1);
    sun.target.position.copy(center);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    const reach = span * 0.85;
    Object.assign(sun.shadow.camera, { left: -reach, right: reach, top: reach, bottom: -reach, near: 0.5, far: span * 5 });
    sun.shadow.bias = -0.0005;
    sun.shadow.normalBias = 0.02;
    this.scene.add(sun, sun.target);
    const fill = new THREE.DirectionalLight("#dbe7ff", 0.7);
    fill.position.set(center.x - span, span, center.z - span);
    this.scene.add(fill);

    const floor = new THREE.Mesh(new THREE.CircleGeometry(span * 4, 64), new THREE.MeshStandardMaterial({ color: "#e7ecf2", roughness: 0.92 }));
    floor.rotation.x = -Math.PI / 2;
    floor.receiveShadow = true;
    floor.userData.stage = true;
    this.scene.add(floor);
    // Epoxy work zone with a yellow walkway line around it.
    const zone = new THREE.Mesh(new THREE.PlaneGeometry(size.x + 1.2, size.z + 1.2), new THREE.MeshStandardMaterial({ color: "#d9e0e8", roughness: 0.75 }));
    zone.rotation.x = -Math.PI / 2;
    zone.position.set(center.x, 0.002, center.z);
    zone.receiveShadow = true;
    this.scene.add(zone);
    const line = new THREE.MeshBasicMaterial({ color: "#e6b000", toneMapped: false });
    const w = size.x + 1.2;
    const d = size.z + 1.2;
    for (const [sx, sz, x, z] of [
      [w, 0.06, center.x, center.z - d / 2],
      [w, 0.06, center.x, center.z + d / 2],
      [0.06, d, center.x - w / 2, center.z],
      [0.06, d, center.x + w / 2, center.z],
    ]) {
      const stripe = new THREE.Mesh(new THREE.PlaneGeometry(sx, sz), line);
      stripe.rotation.x = -Math.PI / 2;
      stripe.position.set(x, 0.004, z);
      this.scene.add(stripe);
    }
    // Soft ambient-occlusion blob keeps the machine grounded even without shadows.
    const blob = canvasTexture(128, 128, (c) => {
      const gradient = c.createRadialGradient(64, 64, 8, 64, 64, 64);
      gradient.addColorStop(0, "rgba(15,30,50,0.32)");
      gradient.addColorStop(1, "rgba(15,30,50,0)");
      c.fillStyle = gradient;
      c.fillRect(0, 0, 128, 128);
    });
    const contact = new THREE.Mesh(
      new THREE.PlaneGeometry(size.x * 1.25, size.z * 1.25),
      new THREE.MeshBasicMaterial({ map: blob, transparent: true, depthWrite: false, toneMapped: false }),
    );
    contact.rotation.x = -Math.PI / 2;
    contact.position.set(center.x, 0.006, center.z);
    this.scene.add(contact);
    for (const object of [zone, contact]) object.userData.stage = true;
    return sun;
  }

  private start() {
    this.resize();
    this.gpu.renderer.setAnimationLoop(() => this.frame());
  }

  private observe() {
    const resize = new ResizeObserver(() => this.resize());
    resize.observe(this.container);
    const intersection = new IntersectionObserver(([entry]) => {
      this.visible = entry?.isIntersecting ?? true;
    });
    intersection.observe(this.container);
    const visibility = () => {
      // A hidden tab or an off-screen panel stops rendering entirely.
      if (document.hidden) this.gpu.renderer.setAnimationLoop(null);
      else if (!this.disposed) {
        this.clock.getDelta();
        this.gpu.renderer.setAnimationLoop(() => this.frame());
      }
    };
    document.addEventListener("visibilitychange", visibility);
    this.cleanups.push(
      () => resize.disconnect(),
      () => intersection.disconnect(),
      () => document.removeEventListener("visibilitychange", visibility),
    );
  }

  private resize() {
    const { width, height } = this.container.getBoundingClientRect();
    if (!width || !height) return;
    this.gpu.renderer.setSize(width, height, false);
    this.labels.setSize(width, height);
    const first = this.camera.aspect === 1;
    this.camera.aspect = width / height;
    // Narrow views step back so the whole machine stays in frame.
    this.camera.updateProjectionMatrix();
    if (first) this.camera.position.copy(this.homePosition());
  }

  private homePosition(): THREE.Vector3 {
    const target = new THREE.Vector3(...this.machine.camera.target);
    const position = new THREE.Vector3(...this.machine.camera.position);
    const scale = Math.min(Math.max(1.5 / this.camera.aspect, 1), 1.9);
    return target.clone().add(position.sub(target).multiplyScalar(scale));
  }

  private bindPointer(canvas: HTMLCanvasElement) {
    let down: { x: number; y: number } | null = null;
    const locate = (event: PointerEvent | MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      this.pointer.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
    };
    const move = (event: PointerEvent) => {
      locate(event);
      this.pointerDirty = true;
    };
    const press = (event: PointerEvent) => {
      down = { x: event.clientX, y: event.clientY };
    };
    const release = (event: PointerEvent) => {
      // A drag orbits the camera; only a near-stationary press selects.
      if (down && Math.hypot(event.clientX - down.x, event.clientY - down.y) < 5) {
        locate(event);
        const id = this.pick();
        if (id) this.events.onSelect(id);
      }
      down = null;
    };
    const leave = () => {
      this.setHovered(null);
    };
    const doubleClick = (event: MouseEvent) => {
      locate(event);
      const id = this.pick();
      if (id) this.focus(id);
    };
    canvas.addEventListener("pointermove", move);
    canvas.addEventListener("pointerdown", press);
    canvas.addEventListener("pointerup", release);
    canvas.addEventListener("pointerleave", leave);
    canvas.addEventListener("dblclick", doubleClick);
    this.cleanups.push(() => {
      canvas.removeEventListener("pointermove", move);
      canvas.removeEventListener("pointerdown", press);
      canvas.removeEventListener("pointerup", release);
      canvas.removeEventListener("pointerleave", leave);
      canvas.removeEventListener("dblclick", doubleClick);
    });
  }

  /** Meshes the pointer can hit; x-ray removes shells so interior parts are reachable. */
  private refreshPickables() {
    this.pickables = [];
    for (const handle of this.machineModel.components.values()) {
      if (handle.spec.selectable === false || this.hidden.has(handle.spec.id) || (this.xray && handle.spec.shell)) continue;
      for (const mesh of handle.meshes) if (!mesh.userData.noPick && !(mesh.material as THREE.Material).userData.noPick) this.pickables.push(mesh);
    }
  }

  private pick(): string | null {
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const hit = this.raycaster.intersectObjects(this.pickables, false)[0];
    let object: THREE.Object3D | null = hit?.object ?? null;
    while (object && !object.userData.componentId) object = object.parent;
    return (object?.userData.componentId as string | undefined) ?? null;
  }

  private setHovered(id: string | null) {
    if (id === this.hovered) return;
    const previous = this.hovered;
    this.hovered = id;
    this.gpu.renderer.domElement.style.cursor = id ? "pointer" : "";
    for (const changed of [previous, id]) if (changed) this.refreshComponent(changed);
  }

  private statusOf(id: string): ComponentStatus {
    return componentStatus(this.fault, id);
  }

  private machineState(): MachineState {
    return { fault: !!this.fault, issue: this.fault?.issueShort };
  }

  /** Update tint, bracket and marker of one component. */
  private refreshComponent(id: string) {
    const handle = this.machineModel.components.get(id);
    if (!handle || handle.spec.selectable === false) return;
    if (this.hidden.has(id)) {
      this.highlighter.mark(handle, "none");
      this.highlighter.marker(handle, null);
      return;
    }
    const status = this.statusOf(id);
    const selected = this.selected === id;
    const hovered = this.hovered === id;
    this.highlighter.mark(handle, markFor(status, selected, hovered));
    const kind = status === "fault" ? "fault" : selected ? "selected" : status === "related" ? "related" : hovered ? "hover" : null;
    const element = kind ? markerElement(kind, handle.spec.label, status === "fault" ? this.fault?.issue : undefined) : null;
    if (element) {
      element.classList.toggle("is-selected", selected);
      element.addEventListener("click", () => this.events.onSelect(id));
    }
    this.highlighter.marker(handle, element);
  }

  private applyStatus() {
    const machine = this.machineState();
    for (const handle of this.machineModel.components.values()) {
      handle.built.setStatus?.(this.statusOf(handle.spec.id), machine);
      handle.root.position.copy(handle.rest);
      this.refreshComponent(handle.spec.id);
    }
  }

  /** Show a fault scenario on this machine, or none. */
  setFault(fault: FaultScenario | undefined) {
    this.fault = fault?.machine === this.machine.id ? fault : undefined;
    this.applyStatus();
  }

  setSelected(id: string | null) {
    if (id === this.selected) return;
    const previous = this.selected;
    this.selected = id;
    for (const changed of [previous, id]) if (changed) this.refreshComponent(changed);
  }

  /**
   * Stop drawing some components. Only each component's own meshes are hidden;
   * parts mounted on it stay visible, so hiding a guard reveals what it covers.
   */
  setHidden(ids: Iterable<string>) {
    const next = new Set(ids);
    const changed = new Set([...this.hidden, ...next].filter((id) => this.hidden.has(id) !== next.has(id)));
    if (!changed.size) return;
    this.hidden = next;
    for (const id of changed) {
      const handle = this.machineModel.components.get(id);
      if (!handle) continue;
      for (const mesh of handle.meshes) mesh.visible = !next.has(id);
      if (this.hovered === id) this.hovered = null;
      this.refreshComponent(id);
    }
    this.refreshPickables();
  }

  /** Draw shells (guards, cabinets) see-through so interior parts show. */
  setXray(on: boolean) {
    if (on === this.xray) return;
    this.xray = on;
    for (const handle of this.machineModel.components.values()) {
      if (!handle.spec.shell) continue;
      for (const mesh of handle.meshes) {
        const solid = (mesh.userData.solid as THREE.Material | undefined) ?? (mesh.material as THREE.Material);
        mesh.userData.solid = solid;
        mesh.material = on ? this.ghost(solid) : solid;
        mesh.castShadow = !on && !(solid.transparent || solid.userData.noShadow);
      }
    }
    this.refreshPickables();
  }

  private ghost(material: THREE.Material): THREE.Material {
    let ghost = this.ghosts.get(material);
    if (!ghost) {
      ghost = material.clone();
      ghost.transparent = true;
      ghost.opacity = material.transparent ? material.opacity * 0.5 : 0.2;
      ghost.depthWrite = false;
      ghost.userData = { ...material.userData, noPick: true };
      this.ghosts.set(material, ghost);
    }
    return ghost;
  }

  setMotion(on: boolean) {
    this.motion = on;
    if (!on) for (const handle of this.machineModel.components.values()) handle.root.position.copy(handle.rest);
  }

  setAutoRotate(on: boolean) {
    this.controls.autoRotate = on && !this.reduceMotion;
  }

  /** Fly the camera to a component, or back to the machine overview. */
  focus(id: string | null) {
    const handle = id ? this.machineModel.components.get(id) : undefined;
    let fly: Fly;
    if (!handle) {
      fly = { position: this.homePosition(), target: new THREE.Vector3(...this.machine.camera.target) };
    } else {
      const box = componentBounds(handle);
      const target = box.getCenter(new THREE.Vector3());
      const size = box.getSize(new THREE.Vector3()).length();
      const distance = Math.min(Math.max((size * 1.1) / Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2)), 1.7), 7);
      const direction = this.camera.position.clone().sub(this.controls.target).normalize();
      // Keep the current side but look from 20–55° above the horizon.
      const elevation = THREE.MathUtils.clamp(Math.asin(direction.y), 0.35, 0.95);
      const flat = new THREE.Vector3(direction.x, 0, direction.z).normalize();
      if (!Number.isFinite(flat.x)) flat.set(0.6, 0, 0.8);
      const look = flat.multiplyScalar(Math.cos(elevation)).setY(Math.sin(elevation));
      fly = { position: target.clone().addScaledVector(look, distance), target };
    }
    if (this.reduceMotion) {
      this.camera.position.copy(fly.position);
      this.controls.target.copy(fly.target);
      this.fly = null;
    } else this.fly = fly;
  }

  resetView() {
    this.focus(null);
  }

  /** Lower the pixel ratio when frames run slow, raise it again when there is headroom. */
  private adaptQuality(now: number) {
    if (this.frames.length < 90 || now - this.lastQualityChange < 2500) return;
    const span = (this.frames[this.frames.length - 1] - this.frames[0]) / 1000;
    const fps = (this.frames.length - 1) / span;
    const cap = Math.min(window.devicePixelRatio || 1, MAX_PIXEL_RATIO);
    let next = this.pixelRatio;
    if (fps < TARGET_FPS - 10 && this.pixelRatio > 1) next = Math.max(1, this.pixelRatio - 0.25);
    else if (fps < 30 && this.sun.castShadow) {
      this.sun.castShadow = false;
      this.lastQualityChange = now;
    } else if (fps > TARGET_FPS + 2 && this.pixelRatio < cap) next = Math.min(cap, this.pixelRatio + 0.25);
    if (next !== this.pixelRatio) {
      this.pixelRatio = next;
      this.gpu.renderer.setPixelRatio(next);
      this.resize();
      this.lastQualityChange = now;
      this.frames = [];
    }
  }

  private frame() {
    if (this.disposed) return;
    const dt = Math.min(this.clock.getDelta(), 0.05);
    if (!this.visible) return;
    const now = performance.now();
    this.frames.push(now);
    if (this.frames.length > 120) this.frames.shift();

    if (this.motion) {
      this.time += dt;
      for (const handle of this.machineModel.components.values()) handle.built.update?.(this.time, dt, this.statusOf(handle.spec.id));
      const suspect = this.fault ? this.machineModel.components.get(this.fault.component) : undefined;
      if (suspect && this.fault?.effect === "vibration") {
        suspect.root.position.set(
          suspect.rest.x + (Math.random() - 0.5) * 0.006,
          suspect.rest.y + (Math.random() - 0.5) * 0.006,
          suspect.rest.z + (Math.random() - 0.5) * 0.006,
        );
      }
    }
    this.highlighter.pulse(now / 1000, this.fault?.effect === "heat", this.reduceMotion);

    if (this.fly) {
      const k = 1 - Math.exp(-dt * 4.5);
      this.controls.target.lerp(this.fly.target, k);
      this.camera.position.lerp(this.fly.position, k);
      if (this.camera.position.distanceTo(this.fly.position) < 0.01) this.fly = null;
    }
    this.controls.update(dt);
    if (this.pointerDirty) {
      this.pointerDirty = false;
      this.setHovered(this.pick());
    }
    this.gpu.renderer.render(this.scene, this.camera);
    this.labels.render(this.scene, this.camera);

    this.adaptQuality(now);
    if (this.events.onStats && now - this.lastStats > 500 && this.frames.length > 10) {
      this.lastStats = now;
      const span = (this.frames[this.frames.length - 1] - this.frames[0]) / 1000;
      const info = this.gpu.frameInfo();
      this.events.onStats({
        fps: Math.round((this.frames.length - 1) / span),
        calls: info.calls,
        triangles: info.triangles,
        backend: this.gpu.backend,
        pixelRatio: this.pixelRatio,
      });
    }
  }

  /** Stop rendering and free GPU memory, DOM nodes and listeners. */
  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    this.gpu.renderer.setAnimationLoop(null);
    for (const cleanup of this.cleanups) cleanup();
    this.controls.dispose();
    this.highlighter.dispose();
    const materials = new Set<THREE.Material>();
    this.scene.traverse((object) => {
      if (object instanceof THREE.Mesh || object instanceof THREE.Line) {
        object.geometry.dispose();
        for (const material of Array.isArray(object.material) ? object.material : [object.material]) materials.add(material);
      }
    });
    for (const material of materials) {
      for (const value of Object.values(material)) if (value instanceof THREE.Texture) value.dispose();
      material.dispose();
    }
    for (const ghost of this.ghosts.values()) ghost.dispose();
    this.kit.dispose();
    this.gpu.dispose();
    this.gpu.renderer.domElement.remove();
    this.labels.domElement.remove();
  }

  /** Component handles for inspection in tests and tools. */
  get components(): ReadonlyMap<string, ComponentHandle> {
    return this.machineModel.components;
  }
}
