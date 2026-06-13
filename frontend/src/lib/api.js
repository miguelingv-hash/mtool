import axios from "axios";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
export const API = `${BACKEND_URL}/api`;

export const api = axios.create({
  baseURL: API,
  headers: { "Content-Type": "application/json" },
});

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
