import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  CalendarRange,
  Loader2,
  PlayCircle,
  CheckCircle2,
  AlertTriangle,
} from "lucide-react";
import { toast } from "sonner";
import CertUploader from "@/components/CertUploader";
import { useEnv } from "@/contexts/EnvContext";

// Lista plana de periodos AEAT con padding: "01".."12". Se define local porque
// `PERIODOS` en `@/lib/api` es un array de objetos {value,label} pensado para
// otros selectores (incluye 1T-4T) — aquí queremos sólo meses.
const PERIODOS = Array.from({ length: 12 }, (_, i) => String(i + 1).padStart(2, "0"));

/**
 * Sección "Consulta mensual SII".
 *
 * Encapsula todo el estado y la lógica de descarga directa desde AEAT
 * (síncrono y background con job). Se renderiza dentro de la pantalla
 * `Carga de datos`. Antes vivía dentro de `Comparativa.jsx`; se movió aquí
 * para que la pantalla de comparativa quede limpia y centrada en analítica.
 *
 * Cuando un job termina, llama a `onCompleted` para que la página padre
 * pueda refrescar contadores o KPIs si quiere.
 */
export default function CargaMensualSII({ onCompleted }) {
  const { entorno } = useEnv();
  const [mes, setMes] = useState({
    nif_titular: "A95000295",
    nombre_titular: "TotalEnergies Clientes S.A.U.",
    ejercicio: String(new Date().getFullYear()),
    periodo: "01",
  });
  const [maxPaginas, setMaxPaginas] = useState("1");
  const [loadingMes, setLoadingMes] = useState(false);
  const [runningJob, setRunningJob] = useState(null);
  const [cert, setCert] = useState({ enabled: false, file: null, password: "" });

  // Recupera job activo al montar — permite cerrar pestaña y volver.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { data } = await api.get("/jobs", { params: { limit: 10 } });
        if (cancelled) return;
        const activos = (data.items || []).filter((j) =>
          ["queued", "running"].includes(j.status),
        );
        if (activos.length > 0) {
          setRunningJob(activos[0]);
          toast.info("Recuperado job en curso", {
            description: `Job ${activos[0].id.slice(0, 8)}… (${activos[0].status})`,
            duration: 6000,
          });
        }
      } catch {
        /* no crítico */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Polling del job cada 1.5s mientras no esté en estado final.
  useEffect(() => {
    if (!runningJob || ["completed", "failed"].includes(runningJob.status)) {
      return undefined;
    }
    const interval = setInterval(async () => {
      try {
        const { data } = await api.get(`/jobs/${runningJob.id}`);
        setRunningJob(data);
        if (data.status === "completed") {
          toast.success("Consulta mensual completada", {
            description: `${data.result?.total ?? 0} facturas actualizadas`,
          });
          onCompleted?.();
        } else if (data.status === "failed") {
          toast.error("Consulta mensual fallida", {
            description: data.error_message || "Error desconocido",
            duration: 12000,
            className: "whitespace-pre-line",
          });
        }
      } catch {
        /* reintentamos en el siguiente tick */
      }
    }, 1500);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runningJob?.id, runningJob?.status]);

  const validate = () => {
    if (!mes.nif_titular || !mes.nombre_titular) {
      toast.error("Completa NIF y nombre titular");
      return false;
    }
    if (cert.enabled && !cert.file) {
      toast.error("Aporta el .pfx o desactiva el modo real");
      return false;
    }
    return true;
  };

  const buildFormData = () => {
    const fd = new FormData();
    Object.entries(mes).forEach(([k, v]) => fd.append(k, v));
    fd.append("entorno", entorno);
    if (maxPaginas !== "all") fd.append("max_paginas", maxPaginas);
    if (cert.enabled && cert.file) {
      fd.append("mode", "real");
      fd.append("certificate", cert.file);
      if (cert.password) fd.append("cert_password", cert.password);
    }
    return fd;
  };

  const lanzarMensual = async () => {
    if (!validate()) return;
    setLoadingMes(true);
    try {
      const { data } = await api.post("/sii/consulta-mensual", buildFormData(), {
        headers: { "Content-Type": "multipart/form-data" },
      });
      toast.success(`Consulta mensual · ${data.total} facturas actualizadas`);
      onCompleted?.();
    } catch (e) {
      const d = e.response?.data?.detail;
      const msg = typeof d === "string" ? d : "Error en consulta mensual";
      toast.error("Consulta mensual fallida", {
        description: msg,
        duration: 12000,
        className: "whitespace-pre-line",
      });
    } finally {
      setLoadingMes(false);
    }
  };

  const lanzarMensualAsync = async () => {
    if (!validate()) return;
    try {
      const { data } = await api.post("/sii/consulta-mensual-async", buildFormData(), {
        headers: { "Content-Type": "multipart/form-data" },
      });
      setRunningJob({
        id: data.job_id,
        status: "queued",
        progress: { page: 0, invoices: 0 },
      });
      toast.success("Consulta lanzada en background", {
        description: `Job ${data.job_id.slice(0, 8)}… en cola`,
      });
    } catch (e) {
      const d = e.response?.data?.detail;
      toast.error("No se pudo lanzar el job", {
        description: typeof d === "string" ? d : "Error en consulta mensual",
      });
    }
  };

  const cancelarJob = async (id) => {
    try {
      await api.post(`/jobs/${id}/cancel`);
      toast.info("Cancelación solicitada", {
        description:
          "El job se detendrá tras la página en curso. Las facturas descargadas hasta ahora se conservan.",
        duration: 8000,
      });
    } catch (e) {
      toast.error("No se pudo cancelar el job", {
        description: e.response?.data?.detail || "Error",
      });
    }
  };

  const limpiarJob = () => setRunningJob(null);

  return (
    <div data-testid="carga-mensual-sii">
      <div className="flex items-center gap-2 mb-4">
        <CalendarRange className="h-4 w-4 text-slate-500" />
        <h2 className="font-display text-lg font-bold tracking-tight">
          Consulta mensual SII
        </h2>
      </div>
      <p className="text-xs text-slate-500 mb-4">
        Trae todas las facturas del periodo desde el SII (SOAP / mTLS) y las
        actualiza en BD. Tarda según el volumen del mes y la latencia de
        AEAT — para periodos grandes, usa la versión en background.
      </p>
      <div className="grid grid-cols-2 gap-3">
        <Input
          placeholder="NIF Titular"
          value={mes.nif_titular}
          onChange={(e) => setMes({ ...mes, nif_titular: e.target.value.toUpperCase() })}
          className="rounded-none font-mono text-sm"
          data-testid="mes-nif"
        />
        <Input
          placeholder="Nombre Titular"
          value={mes.nombre_titular}
          onChange={(e) => setMes({ ...mes, nombre_titular: e.target.value })}
          className="rounded-none text-sm"
          data-testid="mes-nombre"
        />
        <Input
          placeholder="Ejercicio"
          value={mes.ejercicio}
          onChange={(e) => setMes({ ...mes, ejercicio: e.target.value })}
          className="rounded-none font-mono text-sm"
          data-testid="mes-ejercicio"
        />
        <Select
          value={mes.periodo}
          onValueChange={(v) => setMes({ ...mes, periodo: v })}
        >
          <SelectTrigger className="rounded-none text-sm" data-testid="mes-periodo">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {PERIODOS.map((p) => (
              <SelectItem key={p} value={p}>
                {p}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="mt-4">
        <CertUploader value={cert} onChange={setCert} testIdPrefix="mes-cert" />
      </div>

      <div className="mt-4 flex items-center gap-3">
        <Label className="text-xs uppercase tracking-wider text-slate-600 whitespace-nowrap">
          Máx. páginas
        </Label>
        <Select value={maxPaginas} onValueChange={setMaxPaginas}>
          <SelectTrigger
            className="rounded-none h-8 w-full text-sm"
            data-testid="mes-max-paginas"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {Array.from({ length: 10 }, (_, i) => String(i + 1)).map((n) => (
              <SelectItem key={n} value={n}>
                {n} {n === "1" ? "página" : "páginas"} (
                {(Number(n) * 10000).toLocaleString("es-ES")} máx.)
              </SelectItem>
            ))}
            <SelectItem value="all">Todas las páginas (sin límite)</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <Button
        onClick={lanzarMensual}
        disabled={loadingMes || !!runningJob}
        className="rounded-none bg-slate-900 hover:bg-slate-700 text-white mt-4 w-full"
        data-testid="lanzar-mensual"
      >
        {loadingMes ? (
          <Loader2 className="h-4 w-4 mr-2 animate-spin" />
        ) : (
          <CalendarRange className="h-4 w-4 mr-2" />
        )}
        Consultar mes online ({entorno})
      </Button>
      <Button
        onClick={lanzarMensualAsync}
        disabled={loadingMes || !!runningJob}
        variant="outline"
        className="rounded-none mt-2 w-full"
        data-testid="lanzar-mensual-async"
      >
        <PlayCircle className="h-4 w-4 mr-2" />
        Lanzar en background (con progreso)
      </Button>

      {runningJob && (
        <div
          className="mt-3 border border-slate-200 bg-slate-50 px-3 py-2 text-xs"
          data-testid="job-progress-card"
        >
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              {["queued", "running"].includes(runningJob.status) && (
                <Loader2 className="h-3.5 w-3.5 animate-spin text-slate-500" />
              )}
              {runningJob.status === "completed" && (
                <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />
              )}
              {runningJob.status === "failed" && (
                <AlertTriangle className="h-3.5 w-3.5 text-rose-600" />
              )}
              <span className="font-mono text-[11px] uppercase tracking-wider text-slate-600">
                Job {runningJob.id?.slice(0, 8)} · {runningJob.status}
              </span>
            </div>
            {["completed", "failed", "cancelled"].includes(runningJob.status) && (
              <button
                onClick={limpiarJob}
                className="text-slate-400 hover:text-slate-900 text-[11px]"
                data-testid="job-clear"
              >
                cerrar
              </button>
            )}
            {["queued", "running"].includes(runningJob.status) &&
              !runningJob.cancel_requested && (
                <button
                  onClick={() => cancelarJob(runningJob.id)}
                  className="text-rose-600 hover:text-rose-800 text-[11px] font-semibold"
                  data-testid="job-cancel"
                >
                  Cancelar
                </button>
              )}
            {runningJob.cancel_requested && (
              <span
                className="text-amber-600 text-[11px]"
                data-testid="job-cancel-pending"
              >
                cancelando…
              </span>
            )}
          </div>
          <div className="mt-1.5 font-mono text-[11px] text-slate-700 tabular-nums">
            Página {runningJob.progress?.page ?? 0} ·{" "}
            {(runningJob.progress?.invoices ?? 0).toLocaleString("es-ES")}{" "}
            facturas acumuladas
            {runningJob.status === "completed" && runningJob.result && (
              <span className="text-emerald-700 ml-2">
                ✓ total {runningJob.result.total?.toLocaleString("es-ES")}
              </span>
            )}
          </div>
          {runningJob.error_message && (
            <div
              className="mt-1.5 text-[11px] text-rose-700 whitespace-pre-line"
              data-testid="job-error"
            >
              {runningJob.error_message}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
