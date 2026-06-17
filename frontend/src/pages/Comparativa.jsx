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
  PlayCircle,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
} from "lucide-react";
import { toast } from "sonner";
import CertUploader from "@/components/CertUploader";
import { useEnv } from "@/contexts/EnvContext";

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

function DetalleIvaTable({ label, lineas, testIdSuffix }) {
  const totalBase = lineas.reduce((a, li) => a + (li.base_imponible || 0), 0);
  const totalCuota = lineas.reduce((a, li) => a + (li.cuota_repercutida || 0), 0);
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
          {lineas.map((li, idx) => {
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
                  {li.origen || "—"}
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
          {lineas.length > 1 && (
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

export default function Comparativa() {
  const { entorno } = useEnv();
  const location = useLocation();
  const initialNumSerie = new URLSearchParams(location.search).get("num_serie") || "";
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [onlyDiffs, setOnlyDiffs] = useState(!initialNumSerie);
  const [onlyIvaErr, setOnlyIvaErr] = useState(false);
  const [filtroEjercicio, setFiltroEjercicio] = useState("__all__");
  const [filtroPeriodo, setFiltroPeriodo] = useState("__all__");
  const [filtroNumSerie, setFiltroNumSerie] = useState(initialNumSerie);
  const [filtroNumSerieDebounced, setFiltroNumSerieDebounced] = useState(initialNumSerie);
  const [periodosDisponibles, setPeriodosDisponibles] = useState({
    ejercicios: [],
    periodos: [],
  });
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [loading, setLoading] = useState(false);
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
        only_diffs: onlyDiffs,
        skip: (page - 1) * pageSize,
        limit: pageSize,
      };
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

  useEffect(() => {
    load();
    // eslint-disable-next-line
  }, [onlyDiffs, page, pageSize, filtroEjercicio, filtroPeriodo, filtroNumSerieDebounced]);

  // Reset paginación al cambiar filtros
  useEffect(() => {
    setPage(1);
  }, [onlyDiffs, filtroEjercicio, filtroPeriodo, pageSize, filtroNumSerieDebounced]);

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
    params.set("only_diffs", onlyDiffs);
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
        `Consulta mensual (${data.sii_mode}) · ${data.total} facturas actualizadas`,
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
    } catch (e) {
      const d = e.response?.data?.detail;
      toast.error(typeof d === "string" ? d : "Error al subir CSV");
    } finally {
      setLoadingCsv(false);
    }
  };

  const visibleItems = onlyIvaErr ? items.filter(tieneIvaIncorrecto) : items;

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
            Consultar mes online ({entorno} · {cert.enabled ? "real" : "mock"})
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
                {["completed", "failed"].includes(runningJob.status) && (
                  <button
                    onClick={limpiarJob}
                    className="text-slate-400 hover:text-slate-900 text-[11px]"
                    data-testid="job-clear"
                  >
                    cerrar
                  </button>
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
            del report SAP de informes fiscales (con cabeceras
            <span className="font-mono"> Soc.|Doc.causante|Nº doc.oficial|… </span>).
            La clave de comparación con el SII es <span className="font-mono">Nº doc.oficial</span>.
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

      {/* Tabla de comparativa */}
      <div className="border border-slate-200 bg-slate-50/40 p-4 mb-4 flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3 text-sm flex-wrap">
          <Label className="text-xs uppercase tracking-wider text-slate-600">
            Mostrar:
          </Label>
          <Select
            value={onlyDiffs ? "diffs" : "all"}
            onValueChange={(v) => setOnlyDiffs(v === "diffs")}
          >
            <SelectTrigger className="rounded-none h-8 w-[200px] text-xs" data-testid="filter-diffs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="diffs">Sólo con diferencias</SelectItem>
              <SelectItem value="all">Todas las facturas</SelectItem>
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

      <div className="border border-slate-200">
        <Table>
          <TableHeader>
            <TableRow className="bg-slate-50 hover:bg-slate-50">
              <TableHead className="text-xs uppercase tracking-wider">Nº factura</TableHead>
              <TableHead className="text-xs uppercase tracking-wider">Estado</TableHead>
              <TableHead className="text-xs uppercase tracking-wider text-right">
                Importe SII
              </TableHead>
              <TableHead className="text-xs uppercase tracking-wider text-right">
                Importe comercial
              </TableHead>
              <TableHead className="text-xs uppercase tracking-wider">
                Campos con diferencias
              </TableHead>
              <TableHead></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow>
                <TableCell colSpan={6} className="text-center py-10 text-slate-500">
                  Cargando…
                </TableCell>
              </TableRow>
            ) : visibleItems.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="text-center py-10 text-slate-500">
                  {onlyIvaErr
                    ? "Ninguna factura con redondeo IVA incorrecto"
                    : onlyDiffs
                      ? "Sin diferencias detectadas"
                      : "Sin datos"}
                </TableCell>
              </TableRow>
            ) : (
              visibleItems.map((r) => {
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
                    <TableCell className="font-mono text-xs tabular-nums text-right">
                      {r.sii?.importe_total != null
                        ? r.sii.importe_total.toFixed(2)
                        : "—"}
                    </TableCell>
                    <TableCell className="font-mono text-xs tabular-nums text-right">
                      {r.comercial?.importe_total != null
                        ? r.comercial.importe_total.toFixed(2)
                        : "—"}
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
                <div>
                  <span className={`pill ${ESTADO_PILL[detail.estado].cls}`}>
                    {ESTADO_PILL[detail.estado].label}
                  </span>
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
                        const isDiff = !!detail.diferencias[campo];
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
    </div>
  );
}
