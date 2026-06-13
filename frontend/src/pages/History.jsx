import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import EstadoBadge from "@/components/EstadoBadge";
import QueryDetailSheet from "@/components/QueryDetailSheet";
import { Eye, RefreshCw, ChevronLeft, ChevronRight } from "lucide-react";

const PAGE_SIZE = 20;

export default function History() {
  const [items, setItems] = useState([]);
  const [page, setPage] = useState(0);
  const [modo, setModo] = useState("all");
  const [estado, setEstado] = useState("all");
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState(null);

  const load = async () => {
    setLoading(true);
    const params = { skip: page * PAGE_SIZE, limit: PAGE_SIZE };
    if (modo !== "all") params.modo = modo;
    if (estado !== "all") params.estado = estado;
    try {
      const { data } = await api.get("/sii/consultas", { params });
      setItems(data);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, modo, estado]);

  return (
    <div className="px-8 py-8 max-w-[1400px]">
      <div className="flex items-end justify-between mb-8">
        <div>
          <div className="text-xs uppercase tracking-[0.2em] text-slate-500 mb-2">
            Auditoría
          </div>
          <h1 className="font-display text-4xl font-bold tracking-tight text-slate-900">
            Histórico de consultas
          </h1>
          <p className="text-sm text-slate-600 mt-2">
            Listado completo de invocaciones al servicio SOAP del SII con su
            entrada y respuesta XML.
          </p>
        </div>
        <Button
          variant="outline"
          onClick={load}
          className="rounded-none"
          data-testid="refresh-history"
        >
          <RefreshCw className="h-4 w-4 mr-2" />
          Recargar
        </Button>
      </div>

      <div className="flex flex-wrap items-end gap-3 mb-4 p-4 border border-slate-200 bg-slate-50/40">
        <div>
          <div className="text-[11px] uppercase tracking-wider text-slate-500 mb-1">
            Modo
          </div>
          <Select value={modo} onValueChange={(v) => { setModo(v); setPage(0); }}>
            <SelectTrigger
              className="w-[180px] rounded-none"
              data-testid="filter-modo"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Todos los modos</SelectItem>
              <SelectItem value="unitaria">Unitaria</SelectItem>
              <SelectItem value="batch">Batch</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-wider text-slate-500 mb-1">
            Estado factura
          </div>
          <Select value={estado} onValueChange={(v) => { setEstado(v); setPage(0); }}>
            <SelectTrigger
              className="w-[220px] rounded-none"
              data-testid="filter-estado"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Todos los estados</SelectItem>
              <SelectItem value="Correcta">Correcta</SelectItem>
              <SelectItem value="AceptadaConErrores">
                Aceptada con errores
              </SelectItem>
              <SelectItem value="Anulada">Anulada</SelectItem>
              <SelectItem value="NoRegistrada">No registrada</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      <div className="border border-slate-200">
        <Table>
          <TableHeader>
            <TableRow className="bg-slate-50 hover:bg-slate-50">
              <TableHead className="text-xs uppercase tracking-wider">
                Fecha
              </TableHead>
              <TableHead className="text-xs uppercase tracking-wider">
                Modo
              </TableHead>
              <TableHead className="text-xs uppercase tracking-wider">
                NIF emisor
              </TableHead>
              <TableHead className="text-xs uppercase tracking-wider">
                Nº factura
              </TableHead>
              <TableHead className="text-xs uppercase tracking-wider">
                Ejer/Per
              </TableHead>
              <TableHead className="text-xs uppercase tracking-wider">
                Estado
              </TableHead>
              <TableHead className="text-xs uppercase tracking-wider">
                Entorno
              </TableHead>
              <TableHead></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow>
                <TableCell colSpan={8} className="text-center py-12 text-slate-500">
                  Cargando…
                </TableCell>
              </TableRow>
            ) : items.length === 0 ? (
              <TableRow>
                <TableCell colSpan={8} className="text-center py-12 text-slate-500">
                  Sin consultas registradas
                </TableCell>
              </TableRow>
            ) : (
              items.map((r) => (
                <TableRow
                  key={r.id}
                  className="data-row"
                  data-testid={`history-row-${r.id}`}
                >
                  <TableCell className="font-mono text-[11px] text-slate-600 whitespace-nowrap">
                    {new Date(r.timestamp).toLocaleString("es-ES")}
                  </TableCell>
                  <TableCell>
                    <span className="pill pill-neutral capitalize">
                      {r.modo}
                    </span>
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {r.entrada.nif_emisor}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {r.entrada.num_serie_factura}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {r.entrada.ejercicio}/{r.entrada.periodo}
                  </TableCell>
                  <TableCell>
                    <EstadoBadge estado={r.respuesta.estado_factura} />
                  </TableCell>
                  <TableCell className="text-xs text-slate-600">
                    {r.entrada.entorno}
                  </TableCell>
                  <TableCell className="text-right">
                    <button
                      onClick={() => setDetail(r)}
                      className="text-slate-500 hover:text-slate-900"
                      data-testid={`view-history-${r.id}`}
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
          Página {page + 1} · {items.length} resultados
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
            disabled={items.length < PAGE_SIZE}
            onClick={() => setPage((p) => p + 1)}
            className="rounded-none"
            data-testid="page-next"
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      </div>

      <QueryDetailSheet
        open={!!detail}
        onOpenChange={(o) => !o && setDetail(null)}
        record={detail}
      />
    </div>
  );
}
