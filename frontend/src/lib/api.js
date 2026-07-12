import axios from "axios";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
export const API = `${BACKEND_URL}/api`;

export const api = axios.create({
  baseURL: API,
  headers: { "Content-Type": "application/json" },
  withCredentials: true,  // imprescindible para las cookies HTTP-only de auth
  // Timeout generoso: los endpoints de la Comparativa sobre 485k+ facturas
  // pueden tardar 8-10s la 1ª vez (después el cache los sirve <200ms).
  timeout: 90_000,
});

// Retry automático para 502/504/timeout (transitorios del ingress).
// Con datasets grandes, la 1ª carga de la Comparativa dispara varias queries
// pesadas en paralelo y el ingress puede devolver 502 mientras el pod procesa.
// Reintentar 2 veces con backoff (0.5s / 1.5s) absorbe estos hipos sin que el
// usuario los vea. Sólo se aplica a GET idempotentes; POST/PUT/DELETE nunca
// se reintentan para no duplicar mutaciones.
const RETRIABLE_STATUS = new Set([502, 503, 504]);
const MAX_RETRIES = 2;

api.interceptors.response.use(
  (r) => r,
  async (error) => {
    const cfg = error?.config;
    if (!cfg || cfg.method !== "get") return Promise.reject(error);
    cfg.__retryCount = cfg.__retryCount || 0;
    const status = error?.response?.status;
    const isTimeout = error?.code === "ECONNABORTED" || error?.message?.includes("timeout");
    const isNetwork = !error?.response && error?.message === "Network Error";
    const retriable =
      RETRIABLE_STATUS.has(status) || isTimeout || isNetwork;
    if (!retriable || cfg.__retryCount >= MAX_RETRIES) {
      return Promise.reject(error);
    }
    cfg.__retryCount += 1;
    const backoffMs = 500 * Math.pow(3, cfg.__retryCount - 1); // 500, 1500
    await new Promise((res) => setTimeout(res, backoffMs));
    return api(cfg);
  },
);

/** Formatea `detail` de errores FastAPI (string, array de objetos, o {msg}). */
export const formatApiError = formatApiErrorDetail;
export function formatApiErrorDetail(detail) {
  if (detail == null) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail))
    return detail
      .map((e) => (e && typeof e.msg === "string" ? e.msg : JSON.stringify(e)))
      .filter(Boolean)
      .join(" · ");
  if (detail && typeof detail.msg === "string") return detail.msg;
  return String(detail);
}

export const ESTADO_META = {
  Correcta: {
    label: "Correcta",
    pill: "pill-success",
    color: "#059669",
    description: "Factura registrada correctamente en el SII.",
  },
  AceptadaConErrores: {
    label: "Aceptada con errores",
    pill: "pill-warning",
    color: "#d97706",
    description: "Registrada en el SII con incidencias.",
  },
  Anulada: {
    label: "Anulada",
    pill: "pill-neutral",
    color: "#475569",
    description: "Factura previamente anulada en el SII.",
  },
  NoRegistrada: {
    label: "No registrada",
    pill: "pill-error",
    color: "#dc2626",
    description: "La factura no consta en el SII.",
  },
};

export const PERIODOS = [
  { value: "01", label: "01 — Enero" },
  { value: "02", label: "02 — Febrero" },
  { value: "03", label: "03 — Marzo" },
  { value: "04", label: "04 — Abril" },
  { value: "05", label: "05 — Mayo" },
  { value: "06", label: "06 — Junio" },
  { value: "07", label: "07 — Julio" },
  { value: "08", label: "08 — Agosto" },
  { value: "09", label: "09 — Septiembre" },
  { value: "10", label: "10 — Octubre" },
  { value: "11", label: "11 — Noviembre" },
  { value: "12", label: "12 — Diciembre" },
  { value: "1T", label: "1T — Primer trimestre" },
  { value: "2T", label: "2T — Segundo trimestre" },
  { value: "3T", label: "3T — Tercer trimestre" },
  { value: "4T", label: "4T — Cuarto trimestre" },
];
