import * as THREE from "three";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";
import type { ComponentParams, ComponentStatus, ParamValue } from "@/services/machine-library/types";

/**
 * Shared materials and geometry for the component library.
 *
 * Components share one material per finish and one geometry per size, which
 * keeps GPU memory small and lets the assembler merge static meshes by material.
 */

const PALETTE = {
  paint: { color: "#eef1f4", roughness: 0.42, metalness: 0.08 },
  paintGrey: { color: "#c9d0d8", roughness: 0.5, metalness: 0.1 },
  paintDark: { color: "#3a434e", roughness: 0.55, metalness: 0.15 },
  graphite: { color: "#252b32", roughness: 0.6, metalness: 0.2 },
  castIron: { color: "#7d8792", roughness: 0.62, metalness: 0.35 },
  steel: { color: "#c3cbd3", roughness: 0.28, metalness: 0.88 },
  chrome: { color: "#eef2f6", roughness: 0.12, metalness: 1 },
  aluminum: { color: "#d4dae0", roughness: 0.34, metalness: 0.72 },
  rubber: { color: "#1f2327", roughness: 0.9, metalness: 0 },
  belt: { color: "#2b3036", roughness: 0.86, metalness: 0 },
  yellow: { color: "#f2b705", roughness: 0.45, metalness: 0.1 },
  accent: { color: "#c8102e", roughness: 0.4, metalness: 0.1 },
  blue: { color: "#2b5fae", roughness: 0.45, metalness: 0.15 },
  copper: { color: "#b87333", roughness: 0.35, metalness: 0.8 },
  brass: { color: "#c9a44c", roughness: 0.35, metalness: 0.85 },
  cable: { color: "#16191c", roughness: 0.7, metalness: 0 },
  cardboard: { color: "#c99a5b", roughness: 0.85, metalness: 0 },
  workpiece: { color: "#dfe5ec", roughness: 0.3, metalness: 0.65 },
  floorPlate: { color: "#d9dfe6", roughness: 0.8, metalness: 0.05 },
} satisfies Record<string, THREE.MeshStandardMaterialParameters>;

export type Finish = keyof typeof PALETTE | "glass" | "fence" | "darkGlass";

export const STATUS_COLORS: Record<ComponentStatus | "selected", string> = {
  normal: "#22c55e",
  fault: "#e0343f",
  related: "#f59e0b",
  selected: "#2b5fae",
};

/** A small canvas drawn into a colour texture, e.g. for a screen. */
export function canvasTexture(width: number, height: number, draw: (context: CanvasRenderingContext2D) => void) {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  let context: CanvasRenderingContext2D | null = null;
  try {
    context = canvas.getContext("2d");
  } catch {
    // jsdom and some locked-down browsers have no 2D canvas; the texture stays blank.
  }
  if (context) draw(context);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

/** Read typed component parameters with defaults. */
export class Params {
  constructor(private readonly values: ComponentParams = {}) {}
  num(key: string, fallback: number): number {
    const value = this.values[key];
    return typeof value === "number" && Number.isFinite(value) ? value : fallback;
  }
  str<T extends string>(key: string, fallback: T): T {
    const value = this.values[key];
    return (typeof value === "string" ? value : fallback) as T;
  }
  bool(key: string, fallback: boolean): boolean {
    const value = this.values[key];
    return typeof value === "boolean" ? value : fallback;
  }
  list(key: string, fallback: readonly number[]): readonly number[] {
    const value: ParamValue | undefined = this.values[key];
    return Array.isArray(value) ? value : fallback;
  }
}

const round = (value: number) => Math.round(value * 1000) / 1000;

/**
 * Factory for shared materials, cached geometries and small mesh helpers.
 *
 * One kit belongs to one viewer; `dispose` frees everything it created.
 */
export class Kit {
  private readonly materials = new Map<string, THREE.Material>();
  private readonly geometries = new Map<string, THREE.BufferGeometry>();
  private readonly textures: THREE.Texture[] = [];
  private readonly owned = new Set<THREE.Material>();

  /** Shared material for a finish. */
  mat(finish: Finish): THREE.Material {
    let material = this.materials.get(finish);
    if (material) return material;
    if (finish === "glass") {
      material = new THREE.MeshStandardMaterial({
        color: "#cfe6ff",
        transparent: true,
        opacity: 0.2,
        roughness: 0.05,
        metalness: 0.1,
        depthWrite: false,
        side: THREE.DoubleSide,
      });
      material.userData.noShadow = true;
      material.userData.noPick = true;
    } else if (finish === "darkGlass") {
      material = new THREE.MeshStandardMaterial({ color: "#0d141c", roughness: 0.08, metalness: 0.4 });
    } else if (finish === "fence") {
      const alpha = this.canvas(64, 64, (c) => {
        c.fillStyle = "#000";
        c.fillRect(0, 0, 64, 64);
        c.fillStyle = "#fff";
        c.fillRect(0, 0, 64, 4);
        c.fillRect(0, 0, 4, 64);
      });
      alpha.colorSpace = THREE.NoColorSpace;
      alpha.wrapS = alpha.wrapT = THREE.RepeatWrapping;
      alpha.repeat.set(12, 20);
      material = new THREE.MeshStandardMaterial({
        color: "#4a5561",
        roughness: 0.5,
        metalness: 0.6,
        alphaMap: alpha,
        alphaTest: 0.5,
        side: THREE.DoubleSide,
      });
      material.userData.noPick = true;
    } else {
      material = new THREE.MeshStandardMaterial(PALETTE[finish]);
    }
    this.materials.set(finish, material);
    return material;
  }

  /** A material owned by one component, e.g. a status LED it recolours. */
  own<T extends THREE.Material>(material: T): T {
    this.owned.add(material);
    return material;
  }

  /** Emissive indicator material; `setLed` recolours it for a status. */
  led(color = STATUS_COLORS.normal, intensity = 1.2): THREE.MeshStandardMaterial {
    const material = this.own(
      new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: intensity, roughness: 0.25 }),
    );
    material.userData.noShadow = true;
    return material;
  }

  /** A canvas texture freed with the kit. */
  canvas(width: number, height: number, draw: (context: CanvasRenderingContext2D) => void) {
    const texture = canvasTexture(width, height, draw);
    this.textures.push(texture);
    return texture;
  }

  /** A screen or label material drawn on a canvas. */
  screen(width: number, height: number, draw: (context: CanvasRenderingContext2D) => void) {
    const texture = this.canvas(width, height, draw);
    const material = this.own(new THREE.MeshBasicMaterial({ map: texture, toneMapped: false }));
    material.userData.noShadow = true;
    return { material, texture };
  }

  private cached(key: string, create: () => THREE.BufferGeometry) {
    let geometry = this.geometries.get(key);
    if (!geometry) {
      geometry = create();
      geometry.userData.shared = true;
      this.geometries.set(key, geometry);
    }
    return geometry;
  }

  boxGeometry(w: number, h: number, d: number, radius = 0) {
    const r = round(Math.min(radius, w / 2, h / 2, d / 2));
    const key = `box:${round(w)}:${round(h)}:${round(d)}:${r}`;
    return this.cached(key, () => (r > 0.001 ? new RoundedBoxGeometry(w, h, d, 2, r) : new THREE.BoxGeometry(w, h, d)));
  }

  cylinderGeometry(top: number, bottom: number, height: number, segments = 24, open = false) {
    const key = `cyl:${round(top)}:${round(bottom)}:${round(height)}:${segments}:${open}`;
    return this.cached(key, () => new THREE.CylinderGeometry(top, bottom, height, segments, 1, open));
  }

  /** Rounded or square box mesh, centred on its position. */
  box(w: number, h: number, d: number, finish: Finish | THREE.Material, radius = 0.02): THREE.Mesh {
    return new THREE.Mesh(this.boxGeometry(w, h, d, radius), this.resolve(finish));
  }

  /** Cylinder along Y, centred on its position. */
  cyl(radius: number, height: number, finish: Finish | THREE.Material, segments = 24, bottomRadius = radius): THREE.Mesh {
    return new THREE.Mesh(this.cylinderGeometry(radius, bottomRadius, height, segments), this.resolve(finish));
  }

  /** Cylinder whose axis lies along X or Z instead of Y. */
  axle(radius: number, length: number, finish: Finish | THREE.Material, axis: "x" | "z", segments = 24): THREE.Mesh {
    const mesh = this.cyl(radius, length, finish, segments);
    if (axis === "x") mesh.rotation.z = Math.PI / 2;
    else mesh.rotation.x = Math.PI / 2;
    return mesh;
  }

  /** Any custom geometry built once per key. */
  shape(key: string, create: () => THREE.BufferGeometry, finish: Finish | THREE.Material): THREE.Mesh {
    return new THREE.Mesh(this.cached(key, create), this.resolve(finish));
  }

  /**
   * Draw many copies of one shape with a single draw call.
   *
   * Args:
   *   geometry: Shared geometry for every copy.
   *   finish: Shared finish or material.
   *   transforms: One matrix per copy in the parent's space.
   */
  instances(geometry: THREE.BufferGeometry, finish: Finish | THREE.Material, transforms: THREE.Matrix4[]): THREE.InstancedMesh {
    const mesh = new THREE.InstancedMesh(geometry, this.resolve(finish), transforms.length);
    transforms.forEach((matrix, index) => mesh.setMatrixAt(index, matrix));
    mesh.instanceMatrix.needsUpdate = true;
    mesh.computeBoundingSphere();
    mesh.computeBoundingBox();
    return mesh;
  }

  /**
   * Swap a detailed object for a simpler one when the camera is far away.
   *
   * Args:
   *   levels: [object, minimum camera distance] pairs, nearest first.
   */
  lod(levels: [THREE.Object3D, number][]): THREE.LOD {
    const lod = new THREE.LOD();
    for (const [object, distance] of levels) {
      object.userData.rigid = true;
      lod.addLevel(object, distance, distance > 0 ? 0.05 : 0);
    }
    return lod;
  }

  private resolve(finish: Finish | THREE.Material) {
    return typeof finish === "string" ? this.mat(finish) : finish;
  }

  /** Free every material, geometry and texture this kit created. */
  dispose() {
    for (const material of this.materials.values()) material.dispose();
    for (const material of this.owned) material.dispose();
    for (const geometry of this.geometries.values()) geometry.dispose();
    for (const texture of this.textures) texture.dispose();
    this.materials.clear();
    this.owned.clear();
    this.geometries.clear();
    this.textures.length = 0;
  }
}

/** Set an object's position and return it, for compact scene code. */
export function at<T extends THREE.Object3D>(object: T, x: number, y: number, z: number): T {
  object.position.set(x, y, z);
  return object;
}

/** Set Euler rotation and return the object. */
export function turn<T extends THREE.Object3D>(object: T, x: number, y: number, z: number): T {
  object.rotation.set(x, y, z);
  return object;
}

/** An empty attachment point another component can mount on. */
export function socket(parent: THREE.Object3D, x: number, y: number, z: number, rotation: [number, number, number] = [0, 0, 0]) {
  const node = new THREE.Object3D();
  node.position.set(x, y, z);
  node.rotation.set(...rotation);
  node.userData.socket = true;
  parent.add(node);
  return node;
}

/** Mark a group whose transform changes at run time, so its meshes are not merged into the parent. */
export function pivot(parent: THREE.Object3D, x = 0, y = 0, z = 0): THREE.Group {
  const group = new THREE.Group();
  group.position.set(x, y, z);
  group.userData.rigid = true;
  parent.add(group);
  return group;
}

/** Matrix for an instance at a position with an optional Euler rotation and scale. */
export function placement(x: number, y: number, z: number, rotation: [number, number, number] = [0, 0, 0], scale: [number, number, number] = [1, 1, 1]) {
  return new THREE.Matrix4().compose(
    new THREE.Vector3(x, y, z),
    new THREE.Quaternion().setFromEuler(new THREE.Euler(...rotation)),
    new THREE.Vector3(...scale),
  );
}

/** Recolour an LED material for a component status. */
export function setLed(material: THREE.MeshStandardMaterial, status: ComponentStatus, normal = STATUS_COLORS.normal) {
  const color = status === "normal" ? normal : STATUS_COLORS[status];
  material.color.set(color);
  material.emissive.set(color);
  material.emissiveIntensity = status === "normal" ? 1.1 : 2.2;
}
