import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  RefreshCw,
  Eye,
  ChevronLeft,
  ChevronRight,
  Filter,
  X,
  CheckCircle2,
  AlertCircle,
  Loader2,
  Upload,
  Terminal,
  Download,
  FileSpreadsheet,
  FileSearch,
} from "lucide-react";

const PAGE_SIZE = 25;

const INITIAL_FILTERS = {
  date_from: "",
  date_to: "",
  origen: "all",
  fuente: "all",
  status: "all",
  user_email: "",
  nif_titular: "",
  file_name: "",
};

const FUENTE_LABELS = {
  ui_upload: { label: "UI · Upload", icon: Upload },
  conciliacion_newman: { label: "UI · Newman (sync)", icon: FileSpreadsheet },
  conciliacion_newman_async: { label: "UI · Newman (async)", icon: FileSpreadsheet },
  consulta_mensual_aeat: { label: "AEAT · Mensual", icon: Download },
  batch_csv: { label: "UI · Batch CSV", icon: FileSearch },
  cli_newman: { label: "CLI · Newman SII", icon: Terminal },
  cli_comercial: { label: "CLI · Comercial", icon: Terminal },
};

function StatusPill({ status }) {
  if (status === "done") {
    return (
      <span className="inline-flex items-center gap-1 text-[11px] font-mono uppercase tracking-wider px-1.5 py-0.5 bg-emerald-50 text-emerald-700 border border-emerald-300">
        <CheckCircle2 className="h-3 w-3" />
        Done
      </span>
    );
  }
  if (status === "running") {
    return (
      <span className="inline-flex items-center gap-1 text-[11px] font-mono uppercase tracking-wider px-1.5 py-0.5 bg-blue-50 text-blue-700 border border-blue-300">
        <Loader2 className="h-3 w-3 animate-spin" />
        Running
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-[11px] font-mono uppercase tracking-wider px-1.5 py-0.5 bg-rose-50 text-rose-700 border border-rose-300">
      <AlertCircle className="h-3 w-3" />
      Error
    </span>
  );
}

function FuentePill({ fuente }) {
  const meta = FUENTE_LABELS[fuente] || { label: fuente, icon: Upload };
  const Icon = meta.icon;
  return (
    <span className="inline-flex items-center gap-1 text-[11px] font-mono tracking-tight text-slate-700">
      <Icon className="h-3 w-3" />
      {meta.label}
    </span>
  );
}

function OrigenPill({ origen }) {
  const cls =
    origen === "sii"
      ? "bg-sky-50 text-sky-700 border-sky-300"
      : "bg-amber-50 text-amber-700 border-amber-300";
  return (
    <span
      className={`inline-flex items-center gap-1 text-[11px] font-mono uppercase tracking-wider px-1.5 py-0.5 border ${cls}`}
    >
      {origen}
    </span>
  );
}

function formatDuration(ms) {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)} s`;
  const m = s / 60;
  return `${m.toFixed(1)} min`;
}

function formatSize(bytes) {
  if (bytes == null) return "—";
  const KB = bytes / 1024;
  if (KB < 1024) return `${KB.toFixed(1)} KB`;
  return `${(KB / 1024).toFixed(1)} MB`;
}

export default function AdminImportsLog() {
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [filters, setFilters] = useState(INITIAL_FILTERS);
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    const params = { skip: page * PAGE_SIZE, limit: PAGE_SIZE };
    Object.entries(filters).forEach(([k, v]) => {
      if (v && v !== "all") params[k] = v;
    });
    try {
      const { data } = await api.get("/admin/imports-log", { params });
      setItems(data.items || []);
      setTotal(data.total || 0);
    } finally {
      setLoading(false);
    }
  }, [page, filters]);

  useEffect(() => {
    load();
    // Auto-refresh cada 10 s si hay algún job en curso visible en la tabla.
    const hasRunning = items.some((i) => i.status === "running");
    if (!hasRunning) return;
    const id = setInterval(load, 10000);
    return () => clearInterval(id);
  }, [load, items.length]);

  const openDetail = async (id) => {
    setDetailLoading(true);
    try {
      const { data } = await api.get(`/admin/imports-log/${id}`);
      setDetail(data);
    } finally {
      setDetailLoading(false);
    }
  };

  const resetFilters = () => {
    setFilters(INITIAL_FILTERS);
    setPage(0);
  };

  const updateFilter = (k, v) => {
    setFilters((f) => ({ ...f, [k]: v }));
    setPage(0);
  };

  return (
    <div className="px-8 py-8 max-w-[1400px]" data-testid="admin-imports-log-page">
      <div className="flex items-end justify-between mb-8">
        <div>
          <div className="text-xs uppercase tracking-[0.2em] text-slate-500 mb-2">
            Auditoría
          </div>
          <h1 className="font-display text-4xl font-bold tracking-tight text-slate-900">
            Historial de importaciones
          </h1>
          <p className="text-sm text-slate-600 mt-2">
            Cada carga de datos a la BD queda registrada: quién la lanzó, desde
            qué origen (UI/CLI/AEAT), qué fichero se procesó, cuántas facturas
            se afectaron y qué errores se encontraron.
          </p>
        </div>
        <Button
          variant="outline"
          onClick={load}
          className="rounded-none"
          data-testid="refresh-imports-log"
        >
          <RefreshCw className="h-4 w-4 mr-2" />
          Recargar
        </Button>
      </div>

      <div className="border border-slate-200 bg-slate-50/40 p-4 mb-4">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2 text-xs uppercase tracking-wider text-slate-600">
            <Filter className="h-3.5 w-3.5" />
            Filtros
          </div>
          <button
            onClick={resetFilters}
            className="text-[11px] text-slate-500 hover:text-slate-900 inline-flex items-center gap-1"
            data-testid="reset-filters"
          >
            <X className="h-3 w-3" /> Limpiar
          </button>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
          <div>
            <Label className="text-[11px] uppercase tracking-wider text-slate-500 mb-1">
              Desde
            </Label>
            <Input
              type="date"
              value={filters.date_from}
              onChange={(e) => updateFilter("date_from", e.target.value)}
              className="rounded-none font-mono text-xs h-9"
              data-testid="filter-date-from"
            />
          </div>
          <div>
            <Label className="text-[11px] uppercase tracking-wider text-slate-500 mb-1">
              Hasta
            </Label>
            <Input
              type="date"
              value={filters.date_to}
              onChange={(e) => updateFilter("date_to", e.target.value)}
              className="rounded-none font-mono text-xs h-9"
              data-testid="filter-date-to"
            />
          </div>
          <div>
            <Label className="text-[11px] uppercase tracking-wider text-slate-500 mb-1">
              Origen
            </Label>
            <Select
              value={filters.origen}
              onValueChange={(v) => updateFilter("origen", v)}
            >
              <SelectTrigger className="rounded-none h-9 text-xs" data-testid="filter-origen">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Todos</SelectItem>
                <SelectItem value="sii">SII</SelectItem>
                <SelectItem value="comercial">Comercial</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label className="text-[11px] uppercase tracking-wider text-slate-500 mb-1">
              Fuente
            </Label>
            <Select
              value={filters.fuente}
              onValueChange={(v) => updateFilter("fuente", v)}
            >
              <SelectTrigger className="rounded-none h-9 text-xs" data-testid="filter-fuente">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Todas</SelectItem>
                <SelectItem value="ui_upload">UI · Upload</SelectItem>
                <SelectItem value="conciliacion_newman">UI · Newman (sync)</SelectItem>
                <SelectItem value="conciliacion_newman_async">UI · Newman (async)</SelectItem>
                <SelectItem value="consulta_mensual_aeat">AEAT · Mensual</SelectItem>
                <SelectItem value="cli_newman">CLI · Newman SII</SelectItem>
                <SelectItem value="cli_comercial">CLI · Comercial</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label className="text-[11px] uppercase tracking-wider text-slate-500 mb-1">
              Estado
            </Label>
            <Select
              value={filters.status}
              onValueChange={(v) => updateFilter("status", v)}
            >
              <SelectTrigger className="rounded-none h-9 text-xs" data-testid="filter-status">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Todos</SelectItem>
                <SelectItem value="done">Done</SelectItem>
                <SelectItem value="running">Running</SelectItem>
                <SelectItem value="error">Error</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label className="text-[11px] uppercase tracking-wider text-slate-500 mb-1">
              Usuario
            </Label>
            <Input
              placeholder="email"
              value={filters.user_email}
              onChange={(e) => updateFilter("user_email", e.target.value)}
              className="rounded-none text-xs h-9"
              data-testid="filter-user-email"
            />
          </div>
          <div>
            <Label className="text-[11px] uppercase tracking-wider text-slate-500 mb-1">
              NIF titular
            </Label>
            <Input
              placeholder="A95000295"
              value={filters.nif_titular}
              onChange={(e) => updateFilter("nif_titular", e.target.value)}
              className="rounded-none text-xs h-9 font-mono"
              data-testid="filter-nif-titular"
            />
          </div>
          <div className="lg:col-span-2">
            <Label className="text-[11px] uppercase tracking-wider text-slate-500 mb-1">
              Nombre de archivo
            </Label>
            <Input
              placeholder="facturas_TEC_Junio.csv"
              value={filters.file_name}
              onChange={(e) => updateFilter("file_name", e.target.value)}
              className="rounded-none text-xs h-9 font-mono"
              data-testid="filter-file-name"
            />
          </div>
        </div>
      </div>

      <div className="border border-slate-200 bg-white overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow className="bg-slate-50 border-b border-slate-200">
              <TableHead className="text-[11px] uppercase tracking-wider text-slate-500 font-medium">
                Fecha
              </TableHead>
              <TableHead className="text-[11px] uppercase tracking-wider text-slate-500 font-medium">
                Origen
              </TableHead>
              <TableHead className="text-[11px] uppercase tracking-wider text-slate-500 font-medium">
                Fuente
              </TableHead>
              <TableHead className="text-[11px] uppercase tracking-wider text-slate-500 font-medium">
                Archivo
              </TableHead>
              <TableHead className="text-[11px] uppercase tracking-wider text-slate-500 font-medium">
                Usuario
              </TableHead>
              <TableHead className="text-[11px] uppercase tracking-wider text-slate-500 font-medium">
                Estado
              </TableHead>
              <TableHead className="text-[11px] uppercase tracking-wider text-slate-500 font-medium text-right">
                Insertados
              </TableHead>
              <TableHead className="text-[11px] uppercase tracking-wider text-slate-500 font-medium text-right">
                Errores
              </TableHead>
              <TableHead className="text-[11px] uppercase tracking-wider text-slate-500 font-medium text-right">
                Duración
              </TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow>
                <TableCell colSpan={10} className="text-center py-12 text-slate-500">
                  <Loader2 className="h-4 w-4 animate-spin inline-block mr-2" />
                  Cargando…
                </TableCell>
              </TableRow>
            ) : items.length === 0 ? (
              <TableRow>
                <TableCell colSpan={10} className="text-center py-12 text-slate-500">
                  Sin importaciones registradas con esos filtros
                </TableCell>
              </TableRow>
            ) : (
              items.map((it) => (
                <TableRow
                  key={it.id}
                  className="border-b border-slate-100 hover:bg-slate-50/60"
                  data-testid={`imports-log-row-${it.id}`}
                >
                  <TableCell className="font-mono text-[11px] text-slate-700 whitespace-nowrap">
                    {(it.timestamp_start || "").replace("T", " ").substring(0, 19)}
                  </TableCell>
                  <TableCell>
                    <OrigenPill origen={it.origen} />
                  </TableCell>
                  <TableCell>
                    <FuentePill fuente={it.fuente} />
                  </TableCell>
                  <TableCell className="font-mono text-[11px] text-slate-600 max-w-[240px] truncate">
                    {it.file_name || <span className="text-slate-400">—</span>}
                  </TableCell>
                  <TableCell className="text-xs text-slate-700">
                    {it.user_email || <span className="text-slate-400">—</span>}
                  </TableCell>
                  <TableCell>
                    <StatusPill status={it.status} />
                  </TableCell>
                  <TableCell className="text-right font-mono text-xs tabular-nums">
                    {it.insertados ?? 0}
                    {it.actualizados ? (
                      <span className="text-slate-400"> / {it.actualizados}</span>
                    ) : null}
                  </TableCell>
                  <TableCell className="text-right font-mono text-xs tabular-nums">
                    {it.errores_count ? (
                      <span className="text-rose-600">{it.errores_count}</span>
                    ) : (
                      <span className="text-slate-400">0</span>
                    )}
                  </TableCell>
                  <TableCell className="text-right font-mono text-xs tabular-nums">
                    {formatDuration(it.duration_ms)}
                  </TableCell>
                  <TableCell className="text-right">
                    <button
                      onClick={() => openDetail(it.id)}
                      className="text-slate-500 hover:text-slate-900"
                      data-testid={`view-imports-log-${it.id}`}
                      title="Ver detalle"
                    >
                      <Eye className="h-4 w-4" />
                    </button>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      <div className="flex items-center justify-between mt-4">
        <div className="text-xs text-slate-500">
          {total} resultado{total === 1 ? "" : "s"} · página {page + 1}
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={page === 0}
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            className="rounded-none"
            data-testid="page-prev"
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={(page + 1) * PAGE_SIZE >= total}
            onClick={() => setPage((p) => p + 1)}
            className="rounded-none"
            data-testid="page-next"
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      </div>

      <Sheet open={!!detail} onOpenChange={(o) => !o && setDetail(null)}>
        <SheetContent
          side="right"
          className="w-full sm:max-w-2xl overflow-y-auto"
          data-testid="imports-log-detail-sheet"
        >
          {detailLoading ? (
            <div className="flex items-center justify-center h-64 text-slate-500">
              <Loader2 className="h-4 w-4 animate-spin mr-2" />
              Cargando detalle…
            </div>
          ) : detail ? (
            <>
              <SheetHeader>
                <div className="flex items-center justify-between gap-3">
                  <SheetTitle className="font-display text-xl">
                    Importación #{detail.id.substring(0, 8)}
                  </SheetTitle>
                  <StatusPill status={detail.status} />
                </div>
                <SheetDescription className="font-mono text-[11px]">
                  {detail.timestamp_start}
                  {detail.timestamp_end
                    ? ` → ${detail.timestamp_end}`
                    : " · en curso"}
                </SheetDescription>
              </SheetHeader>

              <div className="mt-4 border border-slate-200 bg-slate-50/40 p-4 space-y-2 text-sm">
                <div className="grid grid-cols-[140px_1fr] gap-2">
                  <div className="text-xs uppercase text-slate-500">Origen</div>
                  <div><OrigenPill origen={detail.origen} /></div>
                </div>
                <div className="grid grid-cols-[140px_1fr] gap-2">
                  <div className="text-xs uppercase text-slate-500">Fuente</div>
                  <div><FuentePill fuente={detail.fuente} /></div>
                </div>
                <div className="grid grid-cols-[140px_1fr] gap-2">
                  <div className="text-xs uppercase text-slate-500">Usuario</div>
                  <div className="text-xs">{detail.user_email || "—"}</div>
                </div>
                {detail.file_name ? (
                  <div className="grid grid-cols-[140px_1fr] gap-2">
                    <div className="text-xs uppercase text-slate-500">Archivo</div>
                    <div className="font-mono text-xs break-all">
                      {detail.file_name}
                      {detail.file_size_bytes != null && (
                        <span className="text-slate-500 ml-2">
                          ({formatSize(detail.file_size_bytes)})
                        </span>
                      )}
                    </div>
                  </div>
                ) : null}
                {detail.nif_titular ? (
                  <div className="grid grid-cols-[140px_1fr] gap-2">
                    <div className="text-xs uppercase text-slate-500">NIF titular</div>
                    <div className="font-mono text-xs">{detail.nif_titular}</div>
                  </div>
                ) : null}
                {(detail.ejercicio || detail.periodo) ? (
                  <div className="grid grid-cols-[140px_1fr] gap-2">
                    <div className="text-xs uppercase text-slate-500">Periodo</div>
                    <div className="font-mono text-xs">
                      {detail.ejercicio || "—"} · {detail.periodo || "—"}
                    </div>
                  </div>
                ) : null}
                <div className="grid grid-cols-[140px_1fr] gap-2 pt-2 border-t border-slate-200">
                  <div className="text-xs uppercase text-slate-500">Procesados</div>
                  <div className="font-mono text-xs tabular-nums">
                    {detail.total_procesados ?? 0}
                  </div>
                </div>
                <div className="grid grid-cols-[140px_1fr] gap-2">
                  <div className="text-xs uppercase text-slate-500">Insertados</div>
                  <div className="font-mono text-xs tabular-nums text-emerald-700">
                    {detail.insertados ?? 0}
                  </div>
                </div>
                <div className="grid grid-cols-[140px_1fr] gap-2">
                  <div className="text-xs uppercase text-slate-500">Actualizados</div>
                  <div className="font-mono text-xs tabular-nums text-slate-700">
                    {detail.actualizados ?? 0}
                  </div>
                </div>
                <div className="grid grid-cols-[140px_1fr] gap-2">
                  <div className="text-xs uppercase text-slate-500">Errores</div>
                  <div className="font-mono text-xs tabular-nums text-rose-700">
                    {detail.errores_count ?? 0}
                  </div>
                </div>
                <div className="grid grid-cols-[140px_1fr] gap-2">
                  <div className="text-xs uppercase text-slate-500">Duración</div>
                  <div className="font-mono text-xs">
                    {formatDuration(detail.duration_ms)}
                  </div>
                </div>
                {detail.job_id ? (
                  <div className="grid grid-cols-[140px_1fr] gap-2">
                    <div className="text-xs uppercase text-slate-500">Job ID</div>
                    <div className="font-mono text-[11px] break-all">
                      {detail.job_id}
                    </div>
                  </div>
                ) : null}
                {detail.error_message ? (
                  <div className="grid grid-cols-[140px_1fr] gap-2 pt-2 border-t border-slate-200">
                    <div className="text-xs uppercase text-rose-600">Error</div>
                    <div className="text-xs text-rose-700 whitespace-pre-wrap break-all">
                      {detail.error_message}
                    </div>
                  </div>
                ) : null}
              </div>

              {detail.errores && detail.errores.length > 0 ? (
                <div className="mt-4">
                  <div className="text-xs uppercase tracking-wider text-slate-600 mb-2">
                    Errores por fila (mostrando {detail.errores.length} de {detail.errores_count ?? detail.errores.length})
                  </div>
                  <ScrollArea className="h-[360px] border border-slate-200 bg-slate-950">
                    <div className="p-3 space-y-2">
                      {detail.errores.map((e, idx) => (
                        <div
                          key={idx}
                          className="text-xs font-mono text-slate-100 border-b border-slate-800 pb-2 last:border-b-0"
                          data-testid={`import-error-${idx}`}
                        >
                          <div className="flex gap-2 items-baseline">
                            {e.fila != null && (
                              <span className="text-amber-400">
                                fila {e.fila}
                              </span>
                            )}
                            {e.num_serie_factura ? (
                              <span className="text-sky-400">
                                {e.num_serie_factura}
                              </span>
                            ) : null}
                          </div>
                          <div className="text-slate-200 whitespace-pre-wrap break-all mt-0.5">
                            {e.motivo}
                          </div>
                          {e.datos ? (
                            <div className="text-slate-500 text-[10px] mt-1">
                              {JSON.stringify(e.datos)}
                            </div>
                          ) : null}
                        </div>
                      ))}
                    </div>
                  </ScrollArea>
                </div>
              ) : null}

              {detail.extra && Object.keys(detail.extra).length > 0 ? (
                <div className="mt-4">
                  <div className="text-xs uppercase tracking-wider text-slate-600 mb-2">
                    Metadatos
                  </div>
                  <pre className="text-[11px] font-mono bg-slate-50 border border-slate-200 p-3 whitespace-pre-wrap break-all">
                    {JSON.stringify(detail.extra, null, 2)}
                  </pre>
                </div>
              ) : null}
            </>
          ) : null}
        </SheetContent>
      </Sheet>
    </div>
  );
}
