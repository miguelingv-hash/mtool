import { useEffect, useState } from "react";
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
} from "lucide-react";
import { toast } from "sonner";
import CertUploader from "@/components/CertUploader";

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

export default function Comparativa() {
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [onlyDiffs, setOnlyDiffs] = useState(true);
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState(null);

  // Form consulta mensual
  const [mes, setMes] = useState({
    nif_titular: "A95000295",
    nombre_titular: "TotalEnergies Clientes S.A.U.",
    ejercicio: String(new Date().getFullYear()),
    periodo: "01",
  });
  const [loadingMes, setLoadingMes] = useState(false);
  const [csvFile, setCsvFile] = useState(null);
  const [loadingCsv, setLoadingCsv] = useState(false);
  const [cert, setCert] = useState({ enabled: false, file: null, password: "" });

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/comparativa", {
        params: { only_diffs: onlyDiffs, limit: 500 },
      });
      setItems(data.items);
      setTotal(data.total);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line
  }, [onlyDiffs]);

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
      fd.append("entorno", "preproduccion");
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
      toast.error(
        typeof d === "string" ? d : "Error en consulta mensual",
      );
    } finally {
      setLoadingMes(false);
    }
  };

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
      toast.success(
        `CSV comercial · ${data.total} facturas importadas${
          data.errores.length ? ` · ${data.errores.length} errores` : ""
        }`,
      );
      setCsvFile(null);
      load();
    } catch (e) {
      const d = e.response?.data?.detail;
      toast.error(typeof d === "string" ? d : "Error al subir CSV");
    } finally {
      setLoadingCsv(false);
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
          <Button
            onClick={lanzarMensual}
            disabled={loadingMes}
            className="rounded-none bg-slate-900 hover:bg-slate-700 text-white mt-4 w-full"
            data-testid="lanzar-mensual"
          >
            {loadingMes ? (
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
            ) : (
              <CalendarRange className="h-4 w-4 mr-2" />
            )}
            Consultar mes ({cert.enabled ? "real" : "mock"})
          </Button>
        </div>

        {/* Subir CSV comercial */}
        <div className="border border-slate-200 p-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="font-display text-lg font-bold tracking-tight">
              Importar CSV comercial
            </h2>
            <a
              href={`${API}/comercial/csv-template`}
              className="text-xs text-blue-600 hover:underline inline-flex items-center gap-1"
              data-testid="download-template-comercial"
            >
              <Download className="h-3 w-3" /> plantilla
            </a>
          </div>
          <p className="text-xs text-slate-500 mb-4">
            CSV con las mismas cabeceras que la BD. La columna
            <span className="font-mono"> num_serie_factura </span>es la clave de
            comparación.
          </p>
          <label
            htmlFor="csv-com"
            className="block border-2 border-dashed border-slate-300 hover:border-slate-400 p-6 text-center cursor-pointer bg-slate-50/40"
            data-testid="csv-dropzone"
          >
            <Upload className="h-7 w-7 mx-auto text-slate-400" />
            <div className="text-sm mt-2 text-slate-700">
              {csvFile ? csvFile.name : "Selecciona el CSV comercial"}
            </div>
            <input
              id="csv-com"
              type="file"
              accept=".csv"
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
            Importar CSV
          </Button>
        </div>
      </div>

      {/* Tabla de comparativa */}
      <div className="border border-slate-200 bg-slate-50/40 p-4 mb-4 flex items-center justify-between">
        <div className="flex items-center gap-3 text-sm">
          <Label className="text-xs uppercase tracking-wider text-slate-600">
            Mostrar:
          </Label>
          <Select
            value={onlyDiffs ? "diffs" : "all"}
            onValueChange={(v) => setOnlyDiffs(v === "diffs")}
          >
            <SelectTrigger className="rounded-none h-8 w-[220px] text-xs" data-testid="filter-diffs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="diffs">Sólo con diferencias</SelectItem>
              <SelectItem value="all">Todas las facturas</SelectItem>
            </SelectContent>
          </Select>
          <span className="text-xs text-slate-500">· {total} resultado{total === 1 ? "" : "s"}</span>
        </div>
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
            ) : items.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="text-center py-10 text-slate-500">
                  {onlyDiffs ? "Sin diferencias detectadas" : "Sin datos"}
                </TableCell>
              </TableRow>
            ) : (
              items.map((r) => {
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
            </>
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
}
