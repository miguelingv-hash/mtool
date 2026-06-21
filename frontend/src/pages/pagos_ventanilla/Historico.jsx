import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, API } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { History, Search, X, FileText, Download } from "lucide-react";
import { toast } from "sonner";

const SOC_LABEL = { TTE: "TotalEnergies", BASER: "Baser" };

export default function PagosVentanillaHistorico() {
  const [search, setSearch] = useSearchParams();
  const [filters, setFilters] = useState({
    sociedad: "ALL",
    fecha_desde: "",
    fecha_hasta: "",
    importe_min: "",
    importe_max: "",
    cif_nif: "",
    numero_factura: "",
    referencia: "",
    estado: "ALL",
  });
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [limit] = useState(50);
  const [loading, setLoading] = useState(false);
  const [previewPago, setPreviewPago] = useState(null);
  const [downloadToken, setDownloadToken] = useState("");

  useEffect(() => {
    api.get("/pagos-ventanilla/jobs/auth/download-token").then((r) => setDownloadToken(r.data.token));
  }, []);

  const buildParams = () => {
    const p = { page, limit };
    if (filters.sociedad !== "ALL") p.sociedad = filters.sociedad;
    if (filters.estado !== "ALL") p.estado = filters.estado;
    if (filters.fecha_desde) p.fecha_desde = filters.fecha_desde;
    if (filters.fecha_hasta) p.fecha_hasta = filters.fecha_hasta;
    if (filters.importe_min) p.importe_min = filters.importe_min;
    if (filters.importe_max) p.importe_max = filters.importe_max;
    if (filters.cif_nif) p.cif_nif = filters.cif_nif;
    if (filters.numero_factura) p.numero_factura = filters.numero_factura;
    if (filters.referencia) p.referencia = filters.referencia;
    return p;
  };

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/pagos-ventanilla/pagos/search", { params: buildParams() });
      setItems(data.items);
      setTotal(data.total);
    } catch (e) {
      toast.error(e.response?.data?.detail || "Error cargando histórico");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  const onSearch = () => {
    setPage(1);
    load();
  };

  const clearFilters = () => {
    setFilters({
      sociedad: "ALL", fecha_desde: "", fecha_hasta: "", importe_min: "",
      importe_max: "", cif_nif: "", numero_factura: "", referencia: "", estado: "ALL",
    });
    setPage(1);
    setTimeout(load, 50);
  };

  const pdfUrl = (item) =>
    `${API}/pagos-ventanilla/jobs/${item.job_id}/files/${encodeURIComponent(item.pdf_filename)}?token=${downloadToken}`;

  return (
    <div className="space-y-6" data-testid="pv-historico-page">
      <div>
        <p className="text-xs uppercase tracking-[0.18em] text-slate-500 font-mono">Pagos Ventanilla</p>
        <h1 className="text-3xl font-bold text-slate-900 flex items-center gap-3 mt-1">
          <History className="h-7 w-7 text-emerald-700" />
          Histórico de pagos
        </h1>
        <p className="text-sm text-slate-600 mt-2">Búsqueda multi-campo. {total} resultados.</p>
      </div>

      <Card className="p-5 space-y-4" data-testid="pv-filters-card">
        <div className="grid grid-cols-1 md:grid-cols-3 lg:grid-cols-4 gap-4">
          <div>
            <Label className="text-xs">Sociedad</Label>
            <Select
              value={filters.sociedad}
              onValueChange={(v) => setFilters({ ...filters, sociedad: v })}
            >
              <SelectTrigger data-testid="pv-filter-sociedad"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="ALL">Todas</SelectItem>
                <SelectItem value="TTE">TTE — TotalEnergies</SelectItem>
                <SelectItem value="BASER">BASER — Baser</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label className="text-xs">Fecha desde</Label>
            <Input type="date" value={filters.fecha_desde}
              onChange={(e) => setFilters({ ...filters, fecha_desde: e.target.value })}
              data-testid="pv-filter-desde" />
          </div>
          <div>
            <Label className="text-xs">Fecha hasta</Label>
            <Input type="date" value={filters.fecha_hasta}
              onChange={(e) => setFilters({ ...filters, fecha_hasta: e.target.value })}
              data-testid="pv-filter-hasta" />
          </div>
          <div>
            <Label className="text-xs">Estado</Label>
            <Select value={filters.estado} onValueChange={(v) => setFilters({ ...filters, estado: v })}>
              <SelectTrigger data-testid="pv-filter-estado"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="ALL">Todos</SelectItem>
                <SelectItem value="OK">OK</SelectItem>
                <SelectItem value="ERROR">Error</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label className="text-xs">Importe mínimo (€)</Label>
            <Input type="number" step="0.01" value={filters.importe_min}
              onChange={(e) => setFilters({ ...filters, importe_min: e.target.value })}
              data-testid="pv-filter-min" />
          </div>
          <div>
            <Label className="text-xs">Importe máximo (€)</Label>
            <Input type="number" step="0.01" value={filters.importe_max}
              onChange={(e) => setFilters({ ...filters, importe_max: e.target.value })}
              data-testid="pv-filter-max" />
          </div>
          <div>
            <Label className="text-xs">CIF / NIF</Label>
            <Input value={filters.cif_nif}
              onChange={(e) => setFilters({ ...filters, cif_nif: e.target.value })}
              placeholder="A95000295" data-testid="pv-filter-cif" />
          </div>
          <div>
            <Label className="text-xs">Nº factura</Label>
            <Input value={filters.numero_factura}
              onChange={(e) => setFilters({ ...filters, numero_factura: e.target.value })}
              placeholder="2026A000…" data-testid="pv-filter-factura" />
          </div>
          <div className="md:col-span-2">
            <Label className="text-xs">Referencia (código de barras)</Label>
            <Input value={filters.referencia}
              onChange={(e) => setFilters({ ...filters, referencia: e.target.value })}
              placeholder="11+2 dígitos" data-testid="pv-filter-referencia" />
          </div>
        </div>
        <div className="flex gap-2">
          <Button onClick={onSearch} disabled={loading} className="bg-emerald-700 hover:bg-emerald-800" data-testid="pv-search-btn">
            <Search className="h-4 w-4 mr-2" /> Buscar
          </Button>
          <Button variant="outline" onClick={clearFilters} data-testid="pv-clear-btn">
            <X className="h-4 w-4 mr-2" /> Limpiar
          </Button>
        </div>
      </Card>

      <Card className="overflow-hidden" data-testid="pv-results-card">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="bg-slate-100">
              <tr className="text-left">
                <th className="px-3 py-2">Sociedad</th>
                <th className="px-3 py-2">Cliente</th>
                <th className="px-3 py-2">CIF/NIF</th>
                <th className="px-3 py-2">Nº factura</th>
                <th className="px-3 py-2">Referencia</th>
                <th className="px-3 py-2 text-right">Importe</th>
                <th className="px-3 py-2">Fecha emisión</th>
                <th className="px-3 py-2">Estado</th>
                <th className="px-3 py-2">Acciones</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr key={it.id} className="border-t border-slate-100 hover:bg-slate-50">
                  <td className="px-3 py-2">
                    <Badge variant="secondary" className="text-[10px]">{SOC_LABEL[it.sociedad] || it.sociedad}</Badge>
                  </td>
                  <td className="px-3 py-2">{it.nombre_cliente}</td>
                  <td className="px-3 py-2 font-mono">{it.cif_nif}</td>
                  <td className="px-3 py-2 font-mono">{it.numero_factura}</td>
                  <td className="px-3 py-2 font-mono text-slate-600">{it.referencia}</td>
                  <td className="px-3 py-2 text-right font-mono font-bold">{Number(it.importe).toFixed(2)} €</td>
                  <td className="px-3 py-2 font-mono">{it.fecha_emision_doc}</td>
                  <td className="px-3 py-2">
                    <Badge className={it.estado === "OK" ? "bg-emerald-100 text-emerald-800" : "bg-red-100 text-red-800"}>
                      {it.estado}
                    </Badge>
                  </td>
                  <td className="px-3 py-2 flex gap-2">
                    <Button size="sm" variant="ghost" onClick={() => setPreviewPago(it)} data-testid={`pv-view-${it.id}`}>
                      <FileText className="h-3.5 w-3.5" />
                    </Button>
                    <a href={pdfUrl(it)} target="_blank" rel="noopener noreferrer"
                       className="text-emerald-700 hover:text-emerald-900" data-testid={`pv-dl-${it.id}`}>
                      <Download className="h-3.5 w-3.5" />
                    </a>
                  </td>
                </tr>
              ))}
              {items.length === 0 && !loading && (
                <tr><td colSpan={9} className="px-3 py-12 text-center text-sm text-slate-500">Sin resultados</td></tr>
              )}
            </tbody>
          </table>
        </div>
        {total > limit && (
          <div className="px-4 py-3 border-t flex items-center justify-between bg-slate-50 text-xs">
            <span>Página {page} de {Math.ceil(total / limit)}</span>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage(page - 1)}>Anterior</Button>
              <Button size="sm" variant="outline" disabled={page * limit >= total} onClick={() => setPage(page + 1)}>Siguiente</Button>
            </div>
          </div>
        )}
      </Card>

      <Sheet open={!!previewPago} onOpenChange={(o) => !o && setPreviewPago(null)}>
        <SheetContent className="w-full sm:max-w-3xl p-0" data-testid="pv-preview-sheet">
          <SheetHeader className="px-5 py-4 border-b">
            <SheetTitle>
              {previewPago && (
                <span className="flex items-center gap-2">
                  <Badge variant="secondary">{SOC_LABEL[previewPago.sociedad]}</Badge>
                  {previewPago.numero_factura} · {Number(previewPago.importe).toFixed(2)} €
                </span>
              )}
            </SheetTitle>
          </SheetHeader>
          {previewPago && downloadToken && (
            <iframe
              src={pdfUrl(previewPago)}
              title="PDF preview"
              className="w-full h-[calc(100vh-65px)] border-0"
              data-testid="pv-pdf-iframe"
            />
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
}
