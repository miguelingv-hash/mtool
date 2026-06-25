import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { api, API } from "@/lib/api";
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import {
  Upload,
  Download,
  RefreshCw,
  CheckCircle2,
  AlertTriangle,
  CircleHelp,
  Eye,
  CalendarRange,
  Loader2,
  ListTodo,
  PlayCircle,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  ArrowUp,
  ArrowDown,
  ArrowUpDown,
} from "lucide-react";
import { toast } from "sonner";
import CertUploader from "@/components/CertUploader";
import { useEnv } from "@/contexts/EnvContext";
import { labelOrigenComercial } from "@/lib/origenes";
import ResumenTotales from "@/components/ResumenTotales";

const ESTADO_PILL = {
  coincide: { label: "Coincide", cls: "pill-success", Icon: CheckCircle2 },
  discrepancia: {
    label: "Discrepancia",
    cls: "pill-error",
    Icon: AlertTriangle,
  },
  solo_sii: { label: "Solo SII", cls: "pill-warning", Icon: CircleHelp },
  solo_comercial: {
    label: "Solo comercial",
    cls: "pill-warning",
    Icon: CircleHelp,
  },
};

const PERIODOS = Array.from({ length: 12 }, (_, i) => String(i + 1).padStart(2, "0"));

// Etiqueta visible del origen comercial: ver helper en @/lib/origenes.

function DetalleIvaTable({ label, lineas, testIdSuffix }) {
  // Orden por tipo IVA descendente (21, 10, 4, ...) con exentas/sin tipo al final.
  // Así las dos tablas (SII y COMERCIAL) muestran las mismas categorías a la
  // misma altura cuando hay tramos comparables.
  const lineasOrdenadas = [...lineas].sort((a, b) => {
    const ta = a.tipo_impositivo;
    const tb = b.tipo_impositivo;
    if (ta != null && tb != null) return tb - ta; // descendente
    if (ta != null) return -1; // primero los que tienen tipo
    if (tb != null) return 1;
    // Ambos sin tipo: por causa_exencion ascendente (E1, E2, ...)
    return String(a.causa_exencion || "").localeCompare(String(b.causa_exencion || ""));
  });
  const totalBase = lineasOrdenadas.reduce((a, li) => a + (li.base_imponible || 0), 0);
  const totalCuota = lineasOrdenadas.reduce((a, li) => a + (li.cuota_repercutida || 0), 0);
  return (
    <div
      className="border border-slate-200"
      data-testid={`detalle-iva-${testIdSuffix}`}
    >
      <div className="px-4 py-2 bg-slate-50 border-b border-slate-200 flex items-center justify-between">
        <div className="text-xs uppercase tracking-wider text-slate-600 font-semibold">
          {label}
        </div>
        <div className="text-[11px] text-slate-500">
          {lineas.length} línea{lineas.length === 1 ? "" : "s"}
          <span className="ml-3 text-rose-600">●</span>{" "}
          <span className="text-slate-500">
            desvío de redondeo &gt; 0,01&nbsp;€
          </span>
        </div>
      </div>
      <Table>
        <TableHeader>
          <TableRow className="bg-white hover:bg-white">
            <TableHead className="text-xs uppercase tracking-wider">Origen</TableHead>
            <TableHead className="text-xs uppercase tracking-wider text-right">Tipo (%)</TableHead>
            <TableHead className="text-xs uppercase tracking-wider text-right">Base imponible</TableHead>
            <TableHead className="text-xs uppercase tracking-wider text-right">Cuota repercutida</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {lineasOrdenadas.map((li, idx) => {
            const esperada =
              li.base_imponible != null && li.tipo_impositivo != null
                ? (li.base_imponible * li.tipo_impositivo) / 100
                : null;
            const mismatch =
              esperada != null &&
              li.cuota_repercutida != null &&
              Math.abs(esperada - li.cuota_repercutida) > 0.01;
            return (
              <TableRow
                key={idx}
                data-testid={`detalle-iva-row-${testIdSuffix}-${idx}`}
                className={mismatch ? "bg-rose-50/60" : ""}
                title={
                  mismatch
                    ? `Redondeo: cuota esperada ${esperada.toFixed(2)} €, recibida ${li.cuota_repercutida.toFixed(2)} € (Δ ${(li.cuota_repercutida - esperada).toFixed(2)} €)`
                    : undefined
                }
              >
                <TableCell className="text-xs text-slate-600">
                  {labelOrigenComercial(li.origen) || "—"}
                  {li.causa_exencion && (
                    <span
                      className="ml-1 text-[10px] uppercase tracking-wider bg-amber-100 text-amber-800 px-1.5 py-0.5"
                      title="Operación exenta"
                    >
                      Exenta {li.causa_exencion}
                    </span>
                  )}
                </TableCell>
                <TableCell className="font-mono text-xs tabular-nums text-right">
                  {li.tipo_impositivo != null ? li.tipo_impositivo.toFixed(2) : "—"}
                </TableCell>
                <TableCell className="font-mono text-xs tabular-nums text-right">
                  {li.base_imponible != null ? li.base_imponible.toFixed(2) : "—"}
                </TableCell>
                <TableCell className="font-mono text-xs tabular-nums text-right">
                  {li.cuota_repercutida != null ? li.cuota_repercutida.toFixed(2) : "—"}
                  {mismatch && (
                    <span className="text-rose-600 ml-1" data-testid={`iva-mismatch-${testIdSuffix}-${idx}`}>●</span>
                  )}
                </TableCell>
              </TableRow>
            );
          })}
          {lineasOrdenadas.length > 1 && (
            <TableRow className="bg-slate-50 font-semibold">
              <TableCell className="text-xs uppercase tracking-wider text-slate-700">Totales</TableCell>
              <TableCell className="font-mono text-xs text-right">—</TableCell>
              <TableCell className="font-mono text-xs tabular-nums text-right">{totalBase.toFixed(2)}</TableCell>
              <TableCell className="font-mono text-xs tabular-nums text-right">{totalCuota.toFixed(2)}</TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </div>
  );
}

const tieneIvaIncorrecto = (row) => {
  const lineas = row?.sii?.detalle_iva;
  if (!Array.isArray(lineas) || lineas.length === 0) return false;
  return lineas.some((li) => {
    if (li.base_imponible == null || li.tipo_impositivo == null) return false;
    if (li.cuota_repercutida == null) return false;
    const esperada = (li.base_imponible * li.tipo_impositivo) / 100;
    return Math.abs(esperada - li.cuota_repercutida) > 0.01;
  });
};

/**
 * Comparación de líneas IVA emparejadas por tipo impositivo / causa de exención.
 * Recibe el array `diferencias.detalle_iva` que devuelve el backend.
 * Cada elemento tiene: {key:{tipo?,causa_exencion?}, sii:{base,cuota}, comercial:{base,cuota}, diff:bool}
 */
function LineasIvaCompare({ tramos }) {
  if (!Array.isArray(tramos) || tramos.length === 0) return null;
  const fmt = (v) => {
    if (v == null) return "—";
    // Normaliza -0 a 0 (IEEE-754 tras invertir signo del comercial)
    const n = Number(v) === 0 ? 0 : Number(v);
    return n.toFixed(2);
  };
  const labelKey = (k) => {
    if (!k) return "—";
    if (k.tipo != null) return `Tipo ${Number(k.tipo).toFixed(2)} %`;
    if (k.causa_exencion) return `Exenta · ${k.causa_exencion}`;
    return "Otro";
  };
  return (
    <div
      className="border border-slate-200 mt-6"
      data-testid="lineas-iva-compare"
    >
      <div className="px-4 py-2 bg-slate-900 text-white border-b border-slate-200 flex items-center justify-between">
        <div className="text-xs uppercase tracking-wider font-semibold">
          Comparación de líneas IVA
        </div>
        <div className="text-[11px] text-slate-300">
          Emparejadas por tipo / causa de exención
        </div>
      </div>
      <Table>
        <TableHeader>
          <TableRow className="bg-slate-50 hover:bg-slate-50">
            <TableHead className="text-xs uppercase tracking-wider">Tramo</TableHead>
            <TableHead className="text-xs uppercase tracking-wider text-right">SII · Base</TableHead>
            <TableHead className="text-xs uppercase tracking-wider text-right">SII · Cuota</TableHead>
            <TableHead className="text-xs uppercase tracking-wider text-right">Comercial · Base</TableHead>
            <TableHead className="text-xs uppercase tracking-wider text-right">Comercial · Cuota</TableHead>
            <TableHead className="text-xs uppercase tracking-wider w-24">Estado</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {tramos.map((t, idx) => {
            const sii = t.sii;
            const com = t.comercial;
            return (
              <TableRow
                key={idx}
                className={t.diff ? "bg-rose-50/60" : "bg-emerald-50/40"}
                data-testid={`tramo-row-${idx}`}
              >
                <TableCell className="text-xs text-slate-700 font-medium">
                  {labelKey(t.key)}
                </TableCell>
                <TableCell className="font-mono text-xs tabular-nums text-right">
                  {sii ? fmt(sii.base_imponible) : <span className="text-slate-400 italic">—</span>}
                </TableCell>
                <TableCell className="font-mono text-xs tabular-nums text-right">
                  {sii ? fmt(sii.cuota_repercutida) : <span className="text-slate-400 italic">—</span>}
                </TableCell>
                <TableCell className="font-mono text-xs tabular-nums text-right">
                  {com ? fmt(com.base_imponible) : <span className="text-slate-400 italic">solo SII</span>}
                </TableCell>
                <TableCell className="font-mono text-xs tabular-nums text-right">
                  {com ? fmt(com.cuota_repercutida) : <span className="text-slate-400 italic">—</span>}
                </TableCell>
                <TableCell className="text-xs">
                  {t.diff ? (
                    <span className="text-rose-700 font-semibold">Discrepancia</span>
                  ) : (
                    <span className="text-emerald-700 font-semibold">Coincide</span>
                  )}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

function SortableHead({ label, sortKey, sortBy, sortDir, onClick, align }) {
  const active = sortBy === sortKey;
  const Icon = !active ? ArrowUpDown : sortDir === "desc" ? ArrowDown : ArrowUp;
  return (
    <TableHead
      className={`text-xs uppercase tracking-wider cursor-pointer select-none hover:bg-slate-100 transition-colors ${
        align === "right" ? "text-right" : ""
      }`}
      onClick={() => onClick(sortKey)}
      data-testid={`sort-${sortKey}`}
    >
      <span
        className={`inline-flex items-center gap-1 ${
          align === "right" ? "justify-end" : ""
        } ${active ? "text-slate-900" : "text-slate-600"}`}
      >
        {label}
        <Icon className={`h-3 w-3 ${active ? "opacity-100" : "opacity-40"}`} />
      </span>
    </TableHead>
  );
}

export default function Comparativa() {
  const { entorno } = useEnv();
  const location = useLocation();
  const initialNumSerie = new URLSearchParams(location.search).get("num_serie") || "";
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [onlyIvaErr, setOnlyIvaErr] = useState(false);
  const [filtroEjercicio, setFiltroEjercicio] = useState("__all__");
  const [filtroPeriodo, setFiltroPeriodo] = useState("__all__");
  const [filtroNumSerie, setFiltroNumSerie] = useState(initialNumSerie);
  const [filtroNumSerieDebounced, setFiltroNumSerieDebounced] = useState(initialNumSerie);
  const [filtroEstado, setFiltroEstado] = useState(initialNumSerie ? "all" : "diffs"); // diffs|all|coincide|discrepancia|solo_sii|solo_comercial
  const [periodosDisponibles, setPeriodosDisponibles] = useState({
    ejercicios: [],
    periodos: [],
  });
  const [resumenOrigenes, setResumenOrigenes] = useState([]);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [loading, setLoading] = useState(false);
  const [sortBy, setSortBy] = useState(null);   // 'num_serie_factura' | 'estado' | 'importe_sii' | 'importe_comercial' | 'fecha_expedicion' | null
  const [sortDir, setSortDir] = useState("desc"); // 'asc' | 'desc'
  const [detail, setDetail] = useState(null);

  // Form consulta mensual
  const [mes, setMes] = useState({
    nif_titular: "A95000295",
    nombre_titular: "TotalEnergies Clientes S.A.U.",
    ejercicio: String(new Date().getFullYear()),
    periodo: "01",
  });
  const [maxPaginas, setMaxPaginas] = useState("1"); // "1"…"10" o "all"
  const [loadingMes, setLoadingMes] = useState(false);
  const [runningJob, setRunningJob] = useState(null);
  const [csvFile, setCsvFile] = useState(null);
  const [loadingCsv, setLoadingCsv] = useState(false);
  const [cert, setCert] = useState({ enabled: false, file: null, password: "" });

  const load = async () => {
    setLoading(true);
    try {
      const params = {
        skip: (page - 1) * pageSize,
        limit: pageSize,
      };
      // Mapeo del selector "Mostrar" a los parámetros del backend
      if (filtroEstado === "diffs") {
        params.only_diffs = true;
      } else if (filtroEstado === "all") {
        params.only_diffs = false;
      } else {
        // estado específico: coincide / discrepancia / solo_sii / solo_comercial
        params.only_diffs = false;
        params.estado = filtroEstado;
      }
      if (filtroEjercicio !== "__all__") params.ejercicio = filtroEjercicio;
      if (filtroPeriodo !== "__all__") params.periodo = filtroPeriodo;
      if (filtroNumSerieDebounced.trim()) params.num_serie = filtroNumSerieDebounced.trim();
      const { data } = await api.get("/comparativa", { params });
      setItems(data.items);
      setTotal(data.total);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    api
      .get("/comparativa/periodos")
      .then((r) => setPeriodosDisponibles(r.data))
      .catch(() => {});
  }, []);

  // Carga el resumen agregado por origen comercial (SAP / SIGLO / desconocido)
  // cuando cambian los filtros de ejercicio / periodo / num_serie.
  useEffect(() => {
    const params = {};
    if (filtroEjercicio !== "__all__") params.ejercicio = filtroEjercicio;
    if (filtroPeriodo !== "__all__") params.periodo = filtroPeriodo;
    if (filtroNumSerieDebounced.trim()) params.num_serie = filtroNumSerieDebounced.trim();
    api
      .get("/comparativa/resumen-origenes", { params })
      .then((r) => setResumenOrigenes(r.data.items || []))
      .catch(() => setResumenOrigenes([]));
  }, [filtroEjercicio, filtroPeriodo, filtroNumSerieDebounced]);

  useEffect(() => {
    load();
    // eslint-disable-next-line
  }, [filtroEstado, page, pageSize, filtroEjercicio, filtroPeriodo, filtroNumSerieDebounced]);

  // Reset paginación al cambiar filtros
  useEffect(() => {
    setPage(1);
  }, [filtroEstado, filtroEjercicio, filtroPeriodo, pageSize, filtroNumSerieDebounced]);

  // Debounce 300ms para el filtro de num_serie (evita request por keystroke)
  useEffect(() => {
    const t = setTimeout(() => setFiltroNumSerieDebounced(filtroNumSerie), 300);
    return () => clearTimeout(t);
  }, [filtroNumSerie]);

  // Al montar la página, comprobamos si hay un job de consulta mensual
  // activo (queued/running) o terminado recientemente. Esto permite que el
  // usuario cierre el navegador / pestaña / equipo durante horas y, al
  // reabrir, vea el estado actual sin perder seguimiento del proceso.
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
          // El más reciente
          setRunningJob(activos[0]);
          toast.info("Recuperado job en curso", {
            description: `Job ${activos[0].id.slice(0, 8)}… (${activos[0].status})`,
            duration: 6000,
          });
        }
      } catch (e) {
        // ignoramos silenciosamente — no es crítico
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const exportar = () => {
    const params = new URLSearchParams();
    if (filtroEstado === "diffs") {
      params.set("only_diffs", "true");
    } else if (filtroEstado === "all") {
      params.set("only_diffs", "false");
    } else {
      params.set("only_diffs", "false");
      params.set("estado", filtroEstado);
    }
    if (filtroEjercicio !== "__all__") params.set("ejercicio", filtroEjercicio);
    if (filtroPeriodo !== "__all__") params.set("periodo", filtroPeriodo);
    if (filtroNumSerieDebounced.trim()) params.set("num_serie", filtroNumSerieDebounced.trim());
    window.location.href = `${API}/comparativa/export?${params.toString()}`;
  };

  const lanzarMensual = async () => {
    if (!mes.nif_titular || !mes.nombre_titular) {
      toast.error("Completa NIF y nombre titular");
      return;
    }
    if (cert.enabled && !cert.file) {
      toast.error("Aporta el .pfx o desactiva el modo real");
      return;
    }
    setLoadingMes(true);
    try {
      const fd = new FormData();
      Object.entries(mes).forEach(([k, v]) => fd.append(k, v));
      fd.append("entorno", entorno);
      if (maxPaginas !== "all") fd.append("max_paginas", maxPaginas);
      if (cert.enabled && cert.file) {
        fd.append("mode", "real");
        fd.append("certificate", cert.file);
        if (cert.password) fd.append("cert_password", cert.password);
      }
      const { data } = await api.post("/sii/consulta-mensual", fd, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      toast.success(
        `Consulta mensual · ${data.total} facturas actualizadas`,
      );
      load();
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
    if (!mes.nif_titular || !mes.nombre_titular) {
      toast.error("Completa NIF y nombre titular");
      return;
    }
    if (cert.enabled && !cert.file) {
      toast.error("Aporta el .pfx o desactiva el modo real");
      return;
    }
    try {
      const fd = new FormData();
      Object.entries(mes).forEach(([k, v]) => fd.append(k, v));
      fd.append("entorno", entorno);
      if (maxPaginas !== "all") fd.append("max_paginas", maxPaginas);
      if (cert.enabled && cert.file) {
        fd.append("mode", "real");
        fd.append("certificate", cert.file);
        if (cert.password) fd.append("cert_password", cert.password);
      }
      const { data } = await api.post("/sii/consulta-mensual-async", fd, {
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

  // Polling del job en background cada 1.5s mientras no esté en estado final.
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
          load();
        } else if (data.status === "failed") {
          toast.error("Consulta mensual fallida", {
            description: data.error_message || "Error desconocido",
            duration: 12000,
            className: "whitespace-pre-line",
          });
        }
      } catch (err) {
        // Si falla el polling, no abortamos — reintentaremos en el siguiente tick.
      }
    }, 1500);
    return () => clearInterval(interval);
    // eslint-disable-next-line
  }, [runningJob?.id, runningJob?.status]);

  const limpiarJob = () => setRunningJob(null);

  const cancelarJob = async (id) => {
    try {
      await api.post(`/jobs/${id}/cancel`);
      toast.info("Cancelación solicitada", {
        description: "El job se detendrá tras la página en curso. Las facturas descargadas hasta ahora se conservan.",
        duration: 8000,
      });
    } catch (e) {
      toast.error("No se pudo cancelar el job", {
        description: e.response?.data?.detail || "Error",
      });
    }
  };

  const reanudarJob = async (id) => {
    try {
      const fd = new FormData();
      if (resumeForm.file) {
        fd.append("certificate", resumeForm.file);
        if (resumeForm.password) fd.append("cert_password", resumeForm.password);
      }
      const { data } = await api.post(`/jobs/${id}/resume`, fd, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      toast.success("Job reanudado", {
        description: `Continúa desde la página ${data.start_from_page + 1} (nuevo job ${data.job_id.slice(0, 8)}…)`,
        duration: 8000,
      });
      setResumeForm({ jobId: null, file: null, password: "" });
      cargarJobs();
      const { data: jdoc } = await api.get(`/jobs/${data.job_id}`);
      setRunningJob(jdoc);
    } catch (e) {
      toast.error("No se pudo reanudar el job", {
        description: e.response?.data?.detail || "Error",
        duration: 10000,
      });
    }
  };

  const [jobsOpen, setJobsOpen] = useState(false);
  const [jobsList, setJobsList] = useState([]);
  const [jobsLoading, setJobsLoading] = useState(false);
  const [resumeForm, setResumeForm] = useState({ jobId: null, file: null, password: "" });
  const cargarJobs = async () => {
    setJobsLoading(true);
    try {
      const { data } = await api.get("/jobs", { params: { limit: 20 } });
      setJobsList(data.items || []);
    } finally {
      setJobsLoading(false);
    }
  };
  useEffect(() => {
    if (jobsOpen) cargarJobs();
  }, [jobsOpen]);

  const subirCsv = async () => {
    if (!csvFile) {
      toast.error("Selecciona un CSV");
      return;
    }
    setLoadingCsv(true);
    try {
      const fd = new FormData();
      fd.append("file", csvFile);
      const { data } = await api.post("/comercial/csv", fd, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      const desc = [
        `${data.total.toLocaleString("es-ES")} facturas importadas`,
        data.origen && `formato ${data.origen}`,
        data.matches_sii != null &&
          `${data.matches_sii.toLocaleString("es-ES")} ya en SII · ${data.sin_match_sii.toLocaleString("es-ES")} sin match`,
        data.errores?.length && `${data.errores.length} errores`,
      ]
        .filter(Boolean)
        .join(" · ");
      toast.success("CSV comercial procesado", {
        description: desc,
        duration: 8000,
      });
      setCsvFile(null);
      load();
      // Recargar el resumen de orígenes (los counts pueden haber cambiado)
      const rsParams = {};
      if (filtroEjercicio !== "__all__") rsParams.ejercicio = filtroEjercicio;
      if (filtroPeriodo !== "__all__") rsParams.periodo = filtroPeriodo;
      if (filtroNumSerieDebounced.trim()) rsParams.num_serie = filtroNumSerieDebounced.trim();
      api
        .get("/comparativa/resumen-origenes", { params: rsParams })
        .then((r) => setResumenOrigenes(r.data.items || []))
        .catch(() => {});
    } catch (e) {
      const d = e.response?.data?.detail;
      toast.error(typeof d === "string" ? d : "Error al subir CSV");
    } finally {
      setLoadingCsv(false);
    }
  };

  const visibleItems = onlyIvaErr ? items.filter(tieneIvaIncorrecto) : items;

  // Sort de la tabla. `sortBy` es la clave de columna; `sortDir` 'asc'|'desc'.
  // 'Campos con diferencias' no es ordenable por petición expresa del usuario.
  const sortedItems = (() => {
    if (!sortBy) return visibleItems;
    const dir = sortDir === "asc" ? 1 : -1;
    const getVal = (r) => {
      switch (sortBy) {
        case "num_serie_factura":
          return r.num_serie_factura ?? "";
        case "estado":
          return r.estado ?? "";
        case "importe_sii":
          return r.sii?.importe_total ?? null;
        case "importe_comercial":
          return r.comercial?.importe_total ?? null;
        case "fecha_expedicion": {
          // Devuelve una tupla ordenable (Y, M, D) a partir de "DD-MM-YYYY".
          // Prioriza SII; si no hay, cae al comercial.
          const fe =
            r.sii?.fecha_expedicion || r.comercial?.fecha_expedicion;
          if (typeof fe !== "string") return null;
          const m = fe.match(/^(\d{2})-(\d{2})-(\d{4})$/);
          if (!m) return null;
          return Number(`${m[3]}${m[2]}${m[1]}`);
        }
        default:
          return null;
      }
    };
    // Stable sort
    return [...visibleItems].sort((a, b) => {
      const va = getVal(a);
      const vb = getVal(b);
      // null/undefined siempre al final independientemente de la dirección
      if (va === null || va === undefined)
        return vb === null || vb === undefined ? 0 : 1;
      if (vb === null || vb === undefined) return -1;
      if (typeof va === "number" && typeof vb === "number") {
        return (va - vb) * dir;
      }
      return String(va).localeCompare(String(vb), "es", { numeric: true }) * dir;
    });
  })();

  const toggleSort = (key) => {
    if (sortBy === key) {
      // mismo campo → toggle dir; tercer click → quita ordenación
      if (sortDir === "desc") setSortDir("asc");
      else {
        setSortBy(null);
        setSortDir("desc");
      }
    } else {
      setSortBy(key);
      setSortDir("desc"); // primer click siempre descendente (mayor a menor)
    }
  };

  return (
    <div className="px-8 py-8 max-w-[1500px]">
      <div className="mb-8">
        <div className="text-xs uppercase tracking-[0.2em] text-slate-500 mb-2">
          Conciliación
        </div>
        <h1 className="font-display text-4xl font-bold tracking-tight text-slate-900">
          Comparativa SII ↔ Comercial
        </h1>
        <p className="text-sm text-slate-600 mt-2 max-w-3xl">
          Compara las facturas reportadas al SII con las del sistema comercial.
          Identifica diferencias en importes, fechas, contrapartes o facturas
          que existen sólo en una de las dos fuentes.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
        {/* Consulta mensual SII */}
        <div className="border border-slate-200 p-5">
          <div className="flex items-center gap-2 mb-4">
            <CalendarRange className="h-4 w-4 text-slate-500" />
            <h2 className="font-display text-lg font-bold tracking-tight">
              Consulta mensual SII
            </h2>
          </div>
          <p className="text-xs text-slate-500 mb-4">
            Trae todas las facturas del periodo desde el SII y las actualiza en BD.
          </p>
          <div className="grid grid-cols-2 gap-3">
            <Input
              placeholder="NIF Titular"
              value={mes.nif_titular}
              onChange={(e) =>
                setMes({ ...mes, nif_titular: e.target.value.toUpperCase() })
              }
              className="rounded-none font-mono text-sm"
              data-testid="mes-nif"
            />
            <Input
              placeholder="Nombre Titular"
              value={mes.nombre_titular}
              onChange={(e) =>
                setMes({ ...mes, nombre_titular: e.target.value })
              }
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

          {/* Progreso del job background */}
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
                  <span className="text-amber-600 text-[11px]" data-testid="job-cancel-pending">
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

        {/* Subir CSV comercial */}
        <div className="border border-slate-200 p-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="font-display text-lg font-bold tracking-tight">
              Importar fichero comercial
            </h2>
            <a
              href={`${API}/comercial/csv-template`}
              className="text-xs text-blue-600 hover:underline inline-flex items-center gap-1"
              data-testid="download-template-comercial"
            >
              <Download className="h-3 w-3" /> plantilla CSV
            </a>
          </div>
          <p className="text-xs text-slate-500 mb-4">
            Acepta <span className="font-mono">.csv</span> con cabeceras
            estándar (descarga la plantilla) o <span className="font-mono">.txt</span>{" "}
            del report de informes fiscales en dos formatos:
            <br />
            <span className="font-mono">· SAP FI</span> — cabeceras{" "}
            <span className="font-mono">Soc.|Doc.causante|Nº doc.oficial|…</span>
            <br />
            <span className="font-mono">· SIGLO</span> — cabeceras{" "}
            <span className="font-mono">Soc.|Doc.caus.|Nº oficial|…</span>
            <br />
            La clave de comparación con el SII es el{" "}
            <span className="font-mono">Nº (doc.) oficial</span>. El origen
            (SAP / SIGLO) queda registrado en cada factura importada.
          </p>
          <label
            htmlFor="csv-com"
            className="block border-2 border-dashed border-slate-300 hover:border-slate-400 p-6 text-center cursor-pointer bg-slate-50/40"
            data-testid="csv-dropzone"
          >
            <Upload className="h-7 w-7 mx-auto text-slate-400" />
            <div className="text-sm mt-2 text-slate-700">
              {csvFile ? csvFile.name : "Selecciona el fichero comercial (.csv ó .txt)"}
            </div>
            <input
              id="csv-com"
              type="file"
              accept=".csv,.txt"
              className="hidden"
              onChange={(e) => setCsvFile(e.target.files?.[0])}
              data-testid="csv-input-comercial"
            />
          </label>
          <Button
            onClick={subirCsv}
            disabled={!csvFile || loadingCsv}
            className="rounded-none bg-slate-900 hover:bg-slate-700 text-white mt-4 w-full"
            data-testid="upload-csv-comercial"
          >
            {loadingCsv ? (
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
            ) : (
              <Upload className="h-4 w-4 mr-2" />
            )}
            Importar fichero
          </Button>
        </div>
      </div>

      {/* Dashboard resumen por origen comercial (SAP / SIGLO / desconocido) */}
      {resumenOrigenes.length > 0 && (
        <div
          className="mb-4 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3"
          data-testid="resumen-origenes"
        >
          {resumenOrigenes.map((o) => {
            const pctMatch = o.total_facturas
              ? Math.round((o.matches_sii / o.total_facturas) * 100)
              : 0;
            const pctCoincide = o.matches_sii
              ? Math.round((o.coincidencias / o.matches_sii) * 100)
              : 0;
            const accent =
              o.origen === "SAP"
                ? "border-l-blue-500"
                : o.origen === "SIGLO"
                  ? "border-l-amber-500"
                  : "border-l-slate-400";
            return (
              <div
                key={o.origen}
                className={`border border-slate-200 border-l-4 ${accent} bg-white p-4`}
                data-testid={`resumen-origen-${o.origen}`}
              >
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <span className="text-xs uppercase tracking-wider font-semibold text-slate-700 font-mono">
                      {labelOrigenComercial(o.origen)}
                    </span>
                    <span className="text-xs text-slate-400">comercial</span>
                  </div>
                  <span
                    className="font-mono text-2xl font-light text-slate-900 tabular-nums"
                    data-testid={`resumen-${o.origen}-total`}
                  >
                    {o.total_facturas.toLocaleString("es-ES")}
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                  <div className="text-slate-500">Base imp.</div>
                  <div className="font-mono tabular-nums text-right text-slate-800">
                    {o.base_total.toLocaleString("es-ES", {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 2,
                    })}{" "}
                    €
                  </div>
                  <div className="text-slate-500">Cuota IVA</div>
                  <div className="font-mono tabular-nums text-right text-slate-800">
                    {o.cuota_total.toLocaleString("es-ES", {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 2,
                    })}{" "}
                    €
                  </div>
                </div>
                <div className="mt-3 pt-3 border-t border-slate-100 text-xs space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="text-slate-500">Conciliación</span>
                    <span className="font-mono tabular-nums text-slate-700">
                      {pctMatch}%
                    </span>
                  </div>
                  <div className="h-1.5 bg-slate-100">
                    <div
                      className="h-full bg-slate-700"
                      style={{ width: `${pctMatch}%` }}
                    />
                  </div>
                  <div className="flex items-center justify-between pt-1 text-[11px]">
                    <span className="text-green-700">
                      {o.coincidencias.toLocaleString("es-ES")} match
                    </span>
                    <span className="text-red-600">
                      {o.discrepancias.toLocaleString("es-ES")} discrep.
                    </span>
                    <span className="text-amber-600">
                      {o.sin_match_sii.toLocaleString("es-ES")} sólo com.
                    </span>
                  </div>
                  {o.matches_sii > 0 && (
                    <div className="text-[11px] text-slate-500 pt-1">
                      De las {o.matches_sii.toLocaleString("es-ES")} con
                      contrapartida en SII, {pctCoincide}% coinciden
                      exactamente.
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Tabla de comparativa */}
      <div className="border border-slate-200 bg-slate-50/40 p-4 mb-4 flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3 text-sm flex-wrap">
          <Label className="text-xs uppercase tracking-wider text-slate-600">
            Mostrar:
          </Label>
          <Select
            value={filtroEstado}
            onValueChange={setFiltroEstado}
          >
            <SelectTrigger className="rounded-none h-8 w-[220px] text-xs" data-testid="filter-estado">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="diffs">Sólo con diferencias</SelectItem>
              <SelectItem value="all">Todas las facturas</SelectItem>
              <SelectItem value="coincide">Match (coinciden)</SelectItem>
              <SelectItem value="discrepancia">Con discrepancias</SelectItem>
              <SelectItem value="solo_sii">Sólo en SII</SelectItem>
              <SelectItem value="solo_comercial">Sólo en Comercial</SelectItem>
            </SelectContent>
          </Select>

          <Select value={filtroEjercicio} onValueChange={setFiltroEjercicio}>
            <SelectTrigger className="rounded-none h-8 w-[140px] text-xs" data-testid="filter-ejercicio">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">Todos los ejercicios</SelectItem>
              {periodosDisponibles.ejercicios.map((e) => (
                <SelectItem key={e} value={e}>
                  Ejercicio {e}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select value={filtroPeriodo} onValueChange={setFiltroPeriodo}>
            <SelectTrigger className="rounded-none h-8 w-[140px] text-xs" data-testid="filter-periodo">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">Todos los periodos</SelectItem>
              {periodosDisponibles.periodos.map((p) => (
                <SelectItem key={p} value={p}>
                  Periodo {p}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Input
            value={filtroNumSerie}
            onChange={(e) => setFiltroNumSerie(e.target.value)}
            placeholder="Nº factura (contiene)"
            className="rounded-none h-8 w-[200px] text-xs font-mono"
            data-testid="filter-num-serie"
          />
          {filtroNumSerie && (
            <button
              onClick={() => setFiltroNumSerie("")}
              className="text-[11px] text-slate-500 hover:text-slate-900 -ml-1"
              data-testid="filter-num-serie-clear"
              title="Limpiar"
            >
              ×
            </button>
          )}

          <label
            className="inline-flex items-center gap-2 text-xs text-slate-700 cursor-pointer select-none px-2 py-1 border border-slate-200 hover:bg-white"
            data-testid="filter-iva-mismatch-label"
          >
            <input
              type="checkbox"
              checked={onlyIvaErr}
              onChange={(e) => setOnlyIvaErr(e.target.checked)}
              className="accent-rose-600"
              data-testid="filter-iva-mismatch"
            />
            <span className="text-rose-600">●</span>
            Sólo redondeo IVA incorrecto
          </label>
          <span className="text-xs text-slate-500" data-testid="comp-total-count">
            · {total.toLocaleString("es-ES")} resultado{total === 1 ? "" : "s"}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            className="rounded-none"
            onClick={() => setJobsOpen(true)}
            data-testid="open-jobs-dialog"
          >
            <ListTodo className="h-4 w-4 mr-2" />
            Jobs
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="rounded-none"
            onClick={exportar}
            data-testid="export-comparativa"
            disabled={total === 0}
          >
            <Download className="h-4 w-4 mr-2" />
            Exportar
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="rounded-none"
            onClick={load}
            data-testid="refresh-comparativa"
          >
            <RefreshCw className="h-4 w-4 mr-2" />
            Recargar
          </Button>
        </div>
      </div>

      <ResumenTotales
        filtros={{
          ejercicio: filtroEjercicio !== "__all__" ? filtroEjercicio : undefined,
          periodo: filtroPeriodo !== "__all__" ? filtroPeriodo : undefined,
          num_serie: filtroNumSerieDebounced.trim() || undefined,
        }}
      />

      <div className="border border-slate-200">
        <Table>
          <TableHeader>
            <TableRow className="bg-slate-50 hover:bg-slate-50">
              <SortableHead
                label="Nº factura"
                sortKey="num_serie_factura"
                sortBy={sortBy}
                sortDir={sortDir}
                onClick={toggleSort}
              />
              <SortableHead
                label="Estado"
                sortKey="estado"
                sortBy={sortBy}
                sortDir={sortDir}
                onClick={toggleSort}
              />
              <SortableHead
                label="Fecha expedición"
                sortKey="fecha_expedicion"
                sortBy={sortBy}
                sortDir={sortDir}
                onClick={toggleSort}
              />
              <SortableHead
                label="Importe SII"
                sortKey="importe_sii"
                sortBy={sortBy}
                sortDir={sortDir}
                onClick={toggleSort}
                align="right"
              />
              <SortableHead
                label="Importe comercial"
                sortKey="importe_comercial"
                sortBy={sortBy}
                sortDir={sortDir}
                onClick={toggleSort}
                align="right"
              />
              <TableHead className="text-xs uppercase tracking-wider">
                Campos con diferencias
              </TableHead>
              <TableHead></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow>
                <TableCell colSpan={7} className="text-center py-10 text-slate-500">
                  Cargando…
                </TableCell>
              </TableRow>
            ) : sortedItems.length === 0 ? (
              <TableRow>
                <TableCell colSpan={7} className="text-center py-10 text-slate-500">
                  {onlyIvaErr
                    ? "Ninguna factura con redondeo IVA incorrecto"
                    : filtroEstado === "diffs"
                      ? "Sin diferencias detectadas"
                      : filtroEstado === "coincide"
                        ? "Ninguna factura coincide entre SII y Comercial"
                        : filtroEstado === "discrepancia"
                          ? "Sin discrepancias detectadas"
                          : filtroEstado === "solo_sii"
                            ? "Ninguna factura presente sólo en SII"
                            : filtroEstado === "solo_comercial"
                              ? "Ninguna factura presente sólo en Comercial"
                              : "Sin datos"}
                </TableCell>
              </TableRow>
            ) : (
              sortedItems.map((r) => {
                const meta = ESTADO_PILL[r.estado];
                const Icon = meta.Icon;
                return (
                  <TableRow
                    key={r.num_serie_factura}
                    className="data-row"
                    data-testid={`comp-row-${r.num_serie_factura}`}
                  >
                    <TableCell className="font-mono text-xs">
                      {r.num_serie_factura}
                    </TableCell>
                    <TableCell>
                      <span className={`pill ${meta.cls}`}>
                        <Icon className="h-3 w-3" /> {meta.label}
                      </span>
                    </TableCell>
                    <TableCell className="font-mono text-xs tabular-nums text-slate-700">
                      {r.sii?.fecha_expedicion ||
                        r.comercial?.fecha_expedicion ||
                        "—"}
                    </TableCell>
                    <TableCell className="font-mono text-xs tabular-nums text-right">
                      {r.sii?.importe_total != null
                        ? r.sii.importe_total.toFixed(2)
                        : "—"}
                    </TableCell>
                    <TableCell className="font-mono text-xs tabular-nums text-right">
                      <div className="flex items-center justify-end gap-2">
                        {r.comercial?.origen_comercial ? (
                          <span
                            className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 bg-slate-100 text-slate-600 font-sans"
                            data-testid={`origen-${r.num_serie_factura}`}
                          >
                            {labelOrigenComercial(r.comercial.origen_comercial)}
                          </span>
                        ) : null}
                        <span>
                          {r.comercial?.importe_total != null
                            ? r.comercial.importe_total.toFixed(2)
                            : "—"}
                        </span>
                      </div>
                    </TableCell>
                    <TableCell className="text-xs text-slate-700">
                      {Object.keys(r.diferencias).length
                        ? Object.keys(r.diferencias).join(", ")
                        : "—"}
                    </TableCell>
                    <TableCell className="text-right">
                      <button
                        onClick={() => setDetail(r)}
                        className="text-slate-500 hover:text-slate-900"
                        data-testid={`view-comp-${r.num_serie_factura}`}
                      >
                        <Eye className="h-4 w-4" />
                      </button>
                    </TableCell>
                  </TableRow>
                );
              })
            )}
          </TableBody>
        </Table>
      </div>

      {/* Paginación */}
      <div
        className="border border-t-0 border-slate-200 bg-slate-50/40 px-4 py-2 flex items-center justify-between text-xs"
        data-testid="comp-pagination"
      >
        <div className="flex items-center gap-2 text-slate-600">
          <span>Por página:</span>
          <Select
            value={String(pageSize)}
            onValueChange={(v) => setPageSize(Number(v))}
          >
            <SelectTrigger className="rounded-none h-7 w-[80px] text-xs" data-testid="page-size">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {[25, 50, 100, 200, 500].map((n) => (
                <SelectItem key={n} value={String(n)}>
                  {n}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <span className="text-slate-500 ml-2">
            {total === 0
              ? "0"
              : `${((page - 1) * pageSize + 1).toLocaleString("es-ES")}–${Math.min(
                  page * pageSize,
                  total,
                ).toLocaleString("es-ES")}`}{" "}
            de {total.toLocaleString("es-ES")}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="sm"
            className="rounded-none h-7 w-7 p-0"
            onClick={() => setPage(1)}
            disabled={page === 1}
            data-testid="page-first"
          >
            <ChevronsLeft className="h-4 w-4" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="rounded-none h-7 w-7 p-0"
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
            data-testid="page-prev"
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <span className="px-2 font-mono">
            {page} / {Math.max(1, Math.ceil(total / pageSize))}
          </span>
          <Button
            variant="ghost"
            size="sm"
            className="rounded-none h-7 w-7 p-0"
            onClick={() =>
              setPage((p) =>
                Math.min(Math.ceil(total / pageSize) || 1, p + 1),
              )
            }
            disabled={page >= Math.ceil(total / pageSize)}
            data-testid="page-next"
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="rounded-none h-7 w-7 p-0"
            onClick={() => setPage(Math.max(1, Math.ceil(total / pageSize)))}
            disabled={page >= Math.ceil(total / pageSize)}
            data-testid="page-last"
          >
            <ChevronsRight className="h-4 w-4" />
          </Button>
        </div>
      </div>

      <Sheet open={!!detail} onOpenChange={(o) => !o && setDetail(null)}>
        <SheetContent
          side="right"
          className="w-full sm:max-w-3xl overflow-y-auto"
          data-testid="comp-detail"
        >
          {detail && (
            <>
              <SheetHeader>
                <SheetTitle className="font-display text-xl">
                  {detail.num_serie_factura}
                </SheetTitle>
                <div className="flex items-center gap-2 flex-wrap">
                  <span className={`pill ${ESTADO_PILL[detail.estado].cls}`}>
                    {ESTADO_PILL[detail.estado].label}
                  </span>
                  {detail.comercial?.origen_comercial && (
                    <span
                      className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 bg-slate-100 text-slate-700"
                      data-testid="detail-origen-comercial"
                    >
                      Comercial · {labelOrigenComercial(detail.comercial.origen_comercial)}
                    </span>
                  )}
                </div>
              </SheetHeader>
              <div className="mt-4 border border-slate-200">
                <Table>
                  <TableHeader>
                    <TableRow className="bg-slate-50 hover:bg-slate-50">
                      <TableHead className="text-xs uppercase tracking-wider">Campo</TableHead>
                      <TableHead className="text-xs uppercase tracking-wider">SII</TableHead>
                      <TableHead className="text-xs uppercase tracking-wider">Comercial</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {Object.entries({
                      ...(detail.sii || {}),
                      ...(detail.comercial || {}),
                    })
                      .filter(([k]) =>
                        ![
                          "versiones",
                          "ultima_actualizacion",
                          "fuente_ultima",
                          "detalle_iva",
                          "_id",
                        ].includes(k),
                      )
                      .map(([campo]) => {
                        const hasTramoDiff = Array.isArray(
                          detail.diferencias?.detalle_iva,
                        );
                        // Cuando hay diff a nivel tramo, base/cuota/tipo a nivel
                        // cabecera no se incluyen en `diferencias` (los gestiona
                        // el desglose), pero visualmente queremos marcar la fila
                        // para que el usuario tenga el indicador en rojo aunque
                        // el detalle "real" esté en la tabla de tramos.
                        const isCampoDesglose = [
                          "base_imponible",
                          "tipo_impositivo",
                          "cuota_repercutida",
                        ].includes(campo);
                        const isDiff =
                          !!detail.diferencias[campo] ||
                          (hasTramoDiff && isCampoDesglose);
                        const vs = detail.sii?.[campo];
                        const vc = detail.comercial?.[campo];
                        return (
                          <TableRow
                            key={campo}
                            className={isDiff ? "bg-rose-50/40" : ""}
                          >
                            <TableCell className="font-mono text-xs text-slate-700">
                              {campo}
                              {isDiff && (
                                <span className="text-rose-600 ml-1">●</span>
                              )}
                            </TableCell>
                            <TableCell className="font-mono text-xs">
                              {vs == null || vs === "" ? "—" : String(vs)}
                            </TableCell>
                            <TableCell className="font-mono text-xs">
                              {vc == null || vc === "" ? "—" : String(vc)}
                            </TableCell>
                          </TableRow>
                        );
                      })}
                  </TableBody>
                </Table>
              </div>

              {(() => {
                const tramos = Array.isArray(detail?.diferencias?.detalle_iva)
                  ? detail.diferencias.detalle_iva
                  : null;
                if (!tramos) return null;
                return <LineasIvaCompare tramos={tramos} />;
              })()}

              {(() => {
                const sii = Array.isArray(detail.sii?.detalle_iva)
                  ? detail.sii.detalle_iva
                  : [];
                const com = Array.isArray(detail.comercial?.detalle_iva)
                  ? detail.comercial.detalle_iva
                  : [];
                if (sii.length === 0 && com.length === 0) return null;
                const bothSides = sii.length > 0 && com.length > 0;
                return (
                  <div
                    className={`mt-6 grid gap-4 ${
                      bothSides ? "grid-cols-1 lg:grid-cols-2" : "grid-cols-1"
                    }`}
                    data-testid="detalle-iva-block"
                  >
                    {sii.length > 0 && (
                      <DetalleIvaTable
                        label="Detalle IVA · SII"
                        lineas={sii}
                        testIdSuffix="sii"
                      />
                    )}
                    {com.length > 0 && (
                      <DetalleIvaTable
                        label="Detalle IVA · Comercial"
                        lineas={com}
                        testIdSuffix="comercial"
                      />
                    )}
                  </div>
                );
              })()}
            </>
          )}
        </SheetContent>
      </Sheet>

      {/* Dialog: Jobs en background ------------------------------------- */}
      <Sheet open={jobsOpen} onOpenChange={setJobsOpen}>
        <SheetContent
          side="right"
          className="w-full sm:max-w-[640px] overflow-y-auto"
          data-testid="jobs-sheet"
        >
          <SheetHeader>
            <SheetTitle className="font-display tracking-tight">
              Jobs en background
            </SheetTitle>
            <SheetDescription className="text-slate-500 text-xs">
              Histórico de consultas mensuales lanzadas en background. Los
              que están en cola o ejecutándose se pueden cancelar (la
              cancelación es cooperativa: se aplica tras la página en curso).
            </SheetDescription>
          </SheetHeader>
          <div className="mt-4 flex items-center justify-between">
            <span className="text-xs text-slate-500">
              {jobsLoading
                ? "Cargando…"
                : `${jobsList.length} job${jobsList.length === 1 ? "" : "s"}`}
            </span>
            <Button
              variant="outline"
              size="sm"
              className="rounded-none h-7 text-xs"
              onClick={cargarJobs}
              data-testid="jobs-refresh"
            >
              <RefreshCw className="h-3 w-3 mr-1" /> Recargar
            </Button>
          </div>
          <div className="mt-4 space-y-2" data-testid="jobs-list">
            {jobsList.map((j) => {
              const isActive = ["queued", "running"].includes(j.status);
              return (
                <div
                  key={j.id}
                  className="border border-slate-200 px-3 py-2 text-xs"
                  data-testid={`jobs-item-${j.id}`}
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      {j.status === "queued" && (
                        <span className="text-slate-500">⏳</span>
                      )}
                      {j.status === "running" && (
                        <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-600" />
                      )}
                      {j.status === "completed" && (
                        <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />
                      )}
                      {j.status === "failed" && (
                        <AlertTriangle className="h-3.5 w-3.5 text-rose-600" />
                      )}
                      {j.status === "cancelled" && (
                        <span className="text-amber-600">⏸</span>
                      )}
                      <span className="font-mono">{j.id.slice(0, 8)}…</span>
                      <span className="uppercase tracking-wider text-[10px] text-slate-600">
                        {j.status}
                      </span>
                      {j.cancel_requested && isActive && (
                        <span className="text-amber-600 text-[10px]">
                          cancelando…
                        </span>
                      )}
                    </div>
                    {isActive && !j.cancel_requested && (
                      <button
                        onClick={async () => {
                          await cancelarJob(j.id);
                          cargarJobs();
                        }}
                        className="text-rose-600 hover:text-rose-800 text-[11px] font-semibold"
                        data-testid={`jobs-cancel-${j.id}`}
                      >
                        Cancelar
                      </button>
                    )}
                    {["cancelled", "failed"].includes(j.status) &&
                      j.progress?.clave_paginacion && (
                        <button
                          onClick={() =>
                            setResumeForm({
                              jobId: resumeForm.jobId === j.id ? null : j.id,
                              file: null,
                              password: "",
                            })
                          }
                          className="text-emerald-700 hover:text-emerald-900 text-[11px] font-semibold"
                          data-testid={`jobs-resume-${j.id}`}
                          title={`Reanudar desde la página ${(j.progress?.page ?? 0) + 1}`}
                        >
                          {resumeForm.jobId === j.id ? "✕ cancelar" : "Reanudar"}
                        </button>
                      )}
                  </div>
                  <div className="mt-1 text-slate-600 font-mono text-[11px] tabular-nums">
                    {j.params?.ejercicio}/{j.params?.periodo} ·{" "}
                    {j.params?.entorno}
                    {j.params?.max_paginas != null
                      ? ` · máx ${j.params.max_paginas} pág`
                      : " · todas las pág"}
                  </div>
                  <div className="mt-0.5 text-slate-700 font-mono text-[11px] tabular-nums">
                    Página {j.progress?.page ?? 0} ·{" "}
                    {(j.progress?.invoices ?? 0).toLocaleString("es-ES")}{" "}
                    facturas
                    {j.result?.total != null && (
                      <span className="ml-2 text-emerald-700">
                        ✓ total {j.result.total.toLocaleString("es-ES")}
                      </span>
                    )}
                  </div>
                  <div className="mt-0.5 text-[10px] text-slate-400">
                    {j.created_at?.slice(0, 19).replace("T", " ")}
                  </div>
                  {j.error_message && (
                    <div className="mt-1 text-[11px] text-rose-700 whitespace-pre-line">
                      {j.error_message.slice(0, 240)}
                    </div>
                  )}
                  {resumeForm.jobId === j.id && (
                    <div
                      className="mt-2 border border-emerald-200 bg-emerald-50/40 px-3 py-2 space-y-2"
                      data-testid={`jobs-resume-form-${j.id}`}
                    >
                      <div className="text-[11px] text-emerald-900 font-semibold">
                        Reanudar desde página {(j.progress?.page ?? 0) + 1}
                      </div>
                      <>
                        <label className="block text-[11px] text-slate-600">
                          Certificado .pfx <span className="text-slate-400">(opcional si está configurado en el servidor)</span>
                          <input
                            type="file"
                            accept=".pfx,.p12"
                            className="block mt-1 text-[11px] w-full file:rounded-none file:border file:border-slate-300 file:bg-white file:px-2 file:py-0.5 file:mr-2 file:text-[11px]"
                            onChange={(e) =>
                              setResumeForm({ ...resumeForm, file: e.target.files?.[0] || null })
                            }
                            data-testid={`jobs-resume-cert-${j.id}`}
                          />
                        </label>
                        <label className="block text-[11px] text-slate-600">
                          Contraseña
                          <input
                            type="password"
                            value={resumeForm.password}
                            onChange={(e) =>
                              setResumeForm({ ...resumeForm, password: e.target.value })
                            }
                            placeholder="(opcional si el .pfx no la tiene)"
                            className="block mt-1 w-full rounded-none border border-slate-300 px-2 py-1 text-[11px] font-mono"
                            data-testid={`jobs-resume-pwd-${j.id}`}
                          />
                        </label>
                        {resumeForm.file && (
                          <div className="text-[10px] text-emerald-700 font-mono truncate">
                            ✓ {resumeForm.file.name} ({(resumeForm.file.size / 1024).toFixed(1)} KB)
                          </div>
                        )}
                      </>
                      <Button
                        size="sm"
                        className="rounded-none w-full h-7 text-[11px] bg-emerald-700 hover:bg-emerald-800 text-white"
                        onClick={() => reanudarJob(j.id)}
                        data-testid={`jobs-resume-confirm-${j.id}`}
                      >
                        Lanzar reanudación
                      </Button>
                    </div>
                  )}
                </div>
              );
            })}
            {!jobsLoading && jobsList.length === 0 && (
              <div className="text-center text-xs text-slate-500 py-8">
                No hay jobs registrados
              </div>
            )}
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
