// Every simulated stream in the demo uses fixed intervals divided by one speed
// factor. `?speed=2` halves every delay; `?speed=1` is the natural recording pace.
const KEY = "voice-agent-speed";
let current = readSpeed();

function parse(value: string | null) {
  const speed = Number(value);
  return Number.isFinite(speed) && speed > 0
    ? Math.min(speed, 1000)
    : undefined;
}

function readSpeed(): number {
  if (typeof window === "undefined") return 1;
  const fromUrl = parse(
    new URLSearchParams(window.location.search).get("speed"),
  );
  try {
    if (fromUrl) {
      window.sessionStorage.setItem(KEY, String(fromUrl));
      return fromUrl;
    }
    return parse(window.sessionStorage.getItem(KEY)) ?? 1;
  } catch {
    return fromUrl ?? 1;
  }
}

export function getSpeed() {
  return current;
}
export function setSpeed(speed: number) {
  current = parse(String(speed)) ?? 1;
}
/** Scales a base delay (milliseconds at speed 1) by the current speed. */
export function scaled(ms: number) {
  return Math.max(1, Math.round(ms / current));
}

export function sleep(ms: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Stopped", "AbortError"));
      return;
    }
    const abort = () => {
      clearTimeout(timer);
      reject(new DOMException("Stopped", "AbortError"));
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", abort);
      resolve();
    }, scaled(ms));
    signal?.addEventListener("abort", abort, { once: true });
  });
}
