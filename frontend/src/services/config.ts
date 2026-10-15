export const dataMode = import.meta.env.VITE_DATA_MODE || "api";
if (dataMode !== "api" && dataMode !== "mock")
  throw new Error("VITE_DATA_MODE phải là api hoặc mock.");

export const isLive = dataMode === "api";
// Empty means same origin (the Vite dev server proxies /v1 to FastAPI).
export const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL || "").replace(
  /\/+$/,
  "",
);
