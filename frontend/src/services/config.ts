export const dataMode = import.meta.env.VITE_DATA_MODE || "api";
if (dataMode !== "api" && dataMode !== "mock")
  throw new Error("VITE_DATA_MODE phải là api hoặc mock.");

export const isLive = dataMode === "api";

/**
 * Whether chat opens the mock 3D machine-diagnosis panel. Independent of
 * VITE_DATA_MODE: enabled, it appears with the fixture demo and the live agent.
 */
export const diagnosisVizEnabled = /^(1|true|yes|on)$/i.test(
  (import.meta.env.VITE_MACHINE_DIAGNOSIS_VIZ || "").trim(),
);
// Empty means same origin (the Vite dev server proxies /v1 to FastAPI).
export const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL || "").replace(
  /\/+$/,
  "",
);
