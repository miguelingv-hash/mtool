import { useEffect, useState, useCallback } from "react";
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
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
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
} from "lucide-react";

const PAGE_SIZE = 25;

const INITIAL_FILTERS = {
  date_from: "",
  date_to: "",
  endpoint: "",
  entorno: "all",
  status: "all",
  nif_titular: "",
  num_serie_factura: "",
};

const StatusPill = ({ status }) => {
  if (status === "ok") {
    return (
      <span className="pill pill-success">
        <CheckCircle2 className="h-3 w-3" />
        OK
      </span>
    );
  }
  return (
    <span className="pill pill-error">
      <AlertCircle className="h-3 w-3" />
      Error
    </span>
  );
};

export default function Logs() {
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [filters, setFilters] = useState(INITIAL_FILTERS);
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    const params = { skip: page * PAGE_SIZE, limit: PAGE_SIZE };
    Object.entries(filters).forEach(([k, v]) => {
      if (v && v !== "all") params[k] = v;
    });
    try {
      const { data } = await api.get("/wslogs", { params });
      setItems(data.items);
      setTotal(data.total);
    } finally {
      setLoading(false);
    }
  }, [page, filters]);

  useEffect(() => {
    load();
  }, [load]);

  const openDetail = async (id) => {
    const { data } = await api.get(`/wslogs/${id}`);
    setDetail(data);
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
    <div className="px-8 py-8 max-w-[1400px]">
      <div className="flex items-end justify-between mb-8">
        <div>
          <div className="text-xs uppercase tracking-[0.2em] text-slate-500 mb-2">
            Trazabilidad
          </div>
          <h1 className="font-display text-4xl font-bold tracking-tight text-slate-900">
            Log de invocaciones
          </h1>
          <p className="text-sm text-slate-600 mt-2">
            Cada llamada al SOAP del SII queda registrada con su request, su
            response y el tiempo de respuesta. Filtra por fecha, endpoint,
            modo o factura.
          </p>
        </div>
        <Button
          variant="outline"
          onClick={load}
          className="rounded-none"
          data-testid="refresh-logs"
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
              Endpoint
            </Label>
            <Input
              placeholder="www10 / SiiFactFEV1SOAP"
              value={filters.endpoint}
              onChange={(e) => updateFilter("endpoint", e.target.value)}
              className="rounded-none text-xs h-9"
              data-testid="filter-endpoint"
            />
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
                <SelectItem value="ok">OK</SelectItem>
                <SelectItem value="error">Error</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label className="text-[11px] uppercase tracking-wider text-slate-500 mb-1">
              Nº factura
            </Label>
            <Input
              placeholder="F2025-001"
              value={filters.num_serie_factura}
              onChange={(e) =>
                updateFilter("num_serie_factura", e.target.value)
              }
              className="rounded-none text-xs h-9 font-mono"
              data-testid="filter-num-factura"
            />
          </div>
        </div>
      </div>

      <div className="border border-slate-200">
        <Table>
          <TableHeader>
            <TableRow className="bg-slate-50 hover:bg-slate-50">
              <TableHead className="text-xs uppercase tracking-wider">
                Fecha · UTC
              </TableHead>
              <TableHead className="text-xs uppercase tracking-wider">
                Operación
              </TableHead>
              <TableHead className="text-xs uppercase tracking-wider">
                Endpoint
              </TableHead>
              <TableHead className="text-xs uppercase tracking-wider">
                NIF · Nº fact.
              </TableHead>
              <TableHead className="text-xs uppercase tracking-wider">
                Estado
              </TableHead>
              <TableHead className="text-xs uppercase tracking-wider text-right">
                ms
              </TableHead>
              <TableHead></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow>
                <TableCell colSpan={7} className="text-center py-12 text-slate-500">
                  Cargando…
                </TableCell>
              </TableRow>
            ) : items.length === 0 ? (
              <TableRow>
                <TableCell colSpan={7} className="text-center py-12 text-slate-500">
                  Sin logs registrados con esos filtros
                </TableCell>
              </TableRow>
            ) : (
              items.map((l) => (
                <TableRow
                  key={l.id}
                  className="data-row"
                  data-testid={`log-row-${l.id}`}
                >
                  <TableCell className="font-mono text-[11px] text-slate-700 whitespace-nowrap">
                    {l.timestamp.replace("T", " ").substring(0, 19)}
                  </TableCell>
                  <TableCell className="text-xs">{l.operation}</TableCell>
                  <TableCell className="font-mono text-[11px] text-slate-600 max-w-[260px] truncate">
                    {l.endpoint.replace("https://", "")}
                  </TableCell>
                  <TableCell className="font-mono text-[11px]">
                    <div>{l.nif_titular || "—"}</div>
                    <div className="text-slate-400">
                      {l.num_serie_factura || "—"}
                    </div>
                  </TableCell>
                  <TableCell>
                    <StatusPill status={l.status} />
                  </TableCell>
                  <TableCell className="text-right font-mono text-xs tabular-nums">
                    {l.duration_ms}
                  </TableCell>
                  <TableCell className="text-right">
                    <button
                      onClick={() => openDetail(l.id)}
                      className="text-slate-500 hover:text-slate-900"
                      data-testid={`view-log-${l.id}`}
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
          data-testid="log-detail-sheet"
        >
          {detail && (
            <>
              <SheetHeader>
                <div className="flex items-center justify-between gap-3">
                  <SheetTitle className="font-display text-xl">
                    Log #{detail.id.substring(0, 8)}
                  </SheetTitle>
                  <StatusPill status={detail.status} />
                </div>
                <SheetDescription className="font-mono text-[11px]">
                  {detail.timestamp}
                </SheetDescription>
              </SheetHeader>

              <div className="mt-4 border border-slate-200 bg-slate-50/40 p-4 space-y-2 text-sm">
                <div className="grid grid-cols-[140px_1fr] gap-2">
                  <div className="text-xs uppercase text-slate-500">
                    Operación
                  </div>
                  <div>{detail.operation}</div>
                </div>
                <div className="grid grid-cols-[140px_1fr] gap-2">
                  <div className="text-xs uppercase text-slate-500">
                    Endpoint
                  </div>
                  <div className="font-mono text-xs break-all">
                    {detail.endpoint}
                  </div>
                </div>
                <div className="grid grid-cols-[140px_1fr] gap-2">
                  <div className="text-xs uppercase text-slate-500">Entorno</div>
                  <div className="font-mono text-xs">{detail.entorno}</div>
                </div>
                <div className="grid grid-cols-[140px_1fr] gap-2">
                  <div className="text-xs uppercase text-slate-500">
                    Duración
                  </div>
                  <div className="font-mono text-xs">
                    {detail.duration_ms} ms
                  </div>
                </div>
                {detail.nif_titular && (
                  <div className="grid grid-cols-[140px_1fr] gap-2">
                    <div className="text-xs uppercase text-slate-500">
                      Factura
                    </div>
                    <div className="font-mono text-xs">
                      {detail.nif_titular} · {detail.num_serie_factura}
                    </div>
                  </div>
                )}
                {detail.error_message && (
                  <div className="grid grid-cols-[140px_1fr] gap-2 pt-2 border-t border-slate-200">
                    <div className="text-xs uppercase text-rose-600">
                      Error
                    </div>
                    <div className="text-xs text-rose-700 whitespace-pre-wrap">
                      {detail.error_message}
                    </div>
                  </div>
                )}
              </div>

              <div className="mt-4">
                <Tabs defaultValue="request">
                  <TabsList className="grid grid-cols-2 w-full rounded-none">
                    <TabsTrigger value="request" data-testid="log-tab-request">
                      Request
                    </TabsTrigger>
                    <TabsTrigger value="response" data-testid="log-tab-response">
                      Response
                    </TabsTrigger>
                  </TabsList>
                  <TabsContent value="request" className="mt-3">
                    <ScrollArea className="h-[480px] border border-slate-200 bg-slate-950">
                      <pre className="p-4 text-xs text-slate-100 font-mono whitespace-pre-wrap break-all">
                        {detail.request_xml || "(sin XML registrado)"}
                      </pre>
                    </ScrollArea>
                  </TabsContent>
                  <TabsContent value="response" className="mt-3">
                    <ScrollArea className="h-[480px] border border-slate-200 bg-slate-950">
                      <pre className="p-4 text-xs text-slate-100 font-mono whitespace-pre-wrap break-all">
                        {detail.response_xml || "(sin XML registrado)"}
                      </pre>
                    </ScrollArea>
                  </TabsContent>
                </Tabs>
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
}
