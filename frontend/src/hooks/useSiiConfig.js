import { useEffect, useState } from "react";
import { api } from "@/lib/api";

/**
 * Hook para leer la configuración SII actual del backend (modo por defecto,
 * si hay certificado configurado en servidor, endpoints, WSDL).
 */
export function useSiiConfig() {
  const [config, setConfig] = useState(null);
  useEffect(() => {
    api
      .get("/sii/config")
      .then((r) => setConfig(r.data))
      .catch(() => setConfig(null));
  }, []);
  return config;
}
