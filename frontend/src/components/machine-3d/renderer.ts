import * as THREE from "three";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import type { WebGPURenderer } from "three/webgpu";

export type RendererBackend = "webgpu" | "webgl";
export type RendererPreference = RendererBackend | "auto";

/** The renderer methods the viewer uses; both backends provide them. */
export type AnyRenderer = THREE.WebGLRenderer | WebGPURenderer;

export type RendererHandle = {
  renderer: AnyRenderer;
  backend: RendererBackend;
  environment: THREE.Texture;
  /** Draw calls and triangles of the last frame. */
  frameInfo(): { calls: number; triangles: number };
  dispose(): void;
};

/** Renderer choice from `?renderer=webgl|webgpu` for checks; otherwise automatic. */
export function rendererPreference(search = typeof location === "undefined" ? "" : location.search): RendererPreference {
  const value = new URLSearchParams(search).get("renderer");
  return value === "webgl" || value === "webgpu" ? value : "auto";
}

async function webgpuAvailable(): Promise<boolean> {
  const gpu = (navigator as Navigator & { gpu?: { requestAdapter(): Promise<unknown> } }).gpu;
  if (!gpu) return false;
  try {
    return !!(await gpu.requestAdapter());
  } catch {
    return false;
  }
}

function configure(renderer: AnyRenderer, pixelRatio: number) {
  renderer.setPixelRatio(pixelRatio);
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = renderer instanceof THREE.WebGLRenderer ? THREE.PCFSoftShadowMap : THREE.PCFShadowMap;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.0;
}

/**
 * Create a WebGPU renderer when the browser has a WebGPU adapter, else WebGL.
 *
 * The WebGPU module is a separate chunk downloaded only when it will be used.
 * Both paths prefilter the same studio environment for reflections.
 *
 * Args:
 *   preference: `auto`, or a backend forced for comparison.
 *   pixelRatio: Initial device pixel ratio cap.
 *
 * Returns:
 *   The renderer, its backend name, the environment map and a disposer.
 *
 * Raises:
 *   Error: Neither WebGPU nor WebGL could create a context.
 */
export async function createRenderer(preference: RendererPreference, pixelRatio: number): Promise<RendererHandle> {
  if (preference !== "webgl" && (await webgpuAvailable())) {
    try {
      const gpu = await import("three/webgpu");
      const renderer = new gpu.WebGPURenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
      await renderer.init();
      if ((renderer.backend as { isWebGPUBackend?: boolean }).isWebGPUBackend) {
        configure(renderer, pixelRatio);
        const pmrem = new gpu.PMREMGenerator(renderer);
        const room = new RoomEnvironment();
        const target = pmrem.fromScene(room, 0.04);
        room.dispose();
        pmrem.dispose();
        return {
          renderer,
          backend: "webgpu",
          environment: target.texture,
          frameInfo: () => ({ calls: renderer.info.render.drawCalls, triangles: renderer.info.render.triangles }),
          dispose() {
            target.dispose();
            renderer.dispose();
          },
        };
      }
      renderer.dispose();
    } catch {
      // Fall through to WebGL: some adapters fail during device or pipeline setup.
    }
  }
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
  configure(renderer, pixelRatio);
  const pmrem = new THREE.PMREMGenerator(renderer);
  const room = new RoomEnvironment();
  const target = pmrem.fromScene(room, 0.04);
  room.dispose();
  pmrem.dispose();
  return {
    renderer,
    backend: "webgl",
    environment: target.texture,
    frameInfo: () => ({ calls: renderer.info.render.calls, triangles: renderer.info.render.triangles }),
    dispose() {
      target.dispose();
      renderer.dispose();
    },
  };
}
