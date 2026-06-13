import { useRef, useState } from "react";
import { api, API, ESTADO_META } from "@/lib/api";
import { useEnv } from "@/contexts/EnvContext";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import EstadoBadge from "@/components/EstadoBadge";
import QueryDetailSheet from "@/components/QueryDetailSheet";
import CertUploader from "@/components/CertUploader";
import {
  Upload,
  Download,
  Loader2,
  FileSpreadsheet,
  Eye,
  FileDown,
} from "lucide-react";
import { toast } from "sonner";

export default function BatchQuery() {
  const { entorno } = useEnv();
  const inputRef = useRef(null);
  const [file, setFile] = useState(null);
  const [cert, setCert] = useState({ enabled: false, file: null, password: "" });
  const [loading, setLoading] = useState(false);
  const [resumen, setResumen] = useState(null);
  const [detail, setDetail] = useState(null);

  const handleUpload = async () => {
    if (!file) {
      toast.error("Selecciona un archivo CSV");
      return;
    }
    if (cert.enabled && !cert.file) {
      toast.error("Aporta el certificado .pfx o desactiva el modo real");
      return;
    }
    setLoading(true);
    const fd = new FormData();
    fd.append("file", file);
    fd.append("entorno", entorno);
    if (cert.enabled && cert.file) {
      fd.append("mode", "real");
      fd.append("certificate", cert.file);
      if (cert.password) fd.append("cert_password", cert.password);
    }
    try {
      const { data } = await api.post("/sii/consulta-batch", fd, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      setResumen(data);
      toast.success(`Procesadas ${data.total} facturas`);
    } catch (e) {
      const detail = e.response?.data?.detail;
      toast.error(
        typeof detail === "string" ? detail : "Error procesando el archivo",
      );
    } finally {
      setLoading(false);
    }
  };

  const onPick = (e) => {
    const f = e.target.files?.[0];
    if (f) setFile(f);
  };

  return (
    <div className="px-8 py-8 max-w-[1400px]">
      <div className="mb-8">
        <div className="text-xs uppercase tracking-[0.2em] text-slate-500 mb-2">
          ConsultaLRFactEmitidas · Batch
        </div>
        <h1 className="font-display text-4xl font-bold tracking-tight text-slate-900">
          Consulta batch (CSV)
        </h1>
        <p className="text-sm text-slate-600 mt-2 max-w-2xl">
          Carga un CSV con múltiples facturas para invocar el servicio SOAP del
          SII en una sola operación. Cada fila se convertirá en una llamada
          independiente y obtendrás un resumen consolidado.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-1 border border-slate-200 p-6">
          <h2 className="font-display text-lg font-bold tracking-tight mb-1">
            Plantilla
          </h2>
          <p className="text-xs text-slate-500 mb-3">
            Descarga el modelo CSV con la cabecera requerida.
          </p>
          <a
            href={`${API}/sii/csv-template`}
            className="inline-flex items-center gap-2 border border-slate-300 px-3 py-2 text-sm hover:bg-slate-50"
            data-testid="download-template"
          >
            <Download className="h-4 w-4" />
            Descargar plantilla
          </a>

          <div className="mt-5 border border-slate-200 bg-slate-950 text-slate-100 p-3 font-mono text-[11px] leading-relaxed overflow-x-auto">
            <div className="text-slate-400">cabecera CSV (; o ,)</div>
            <div>nif_titular</div>
            <div>nombre_titular</div>
            <div>ejercicio</div>
            <div>periodo</div>
            <div>nif_emisor</div>
            <div>nombre_emisor</div>
            <div>num_serie_factura</div>
            <div>fecha_expedicion</div>
          </div>
        </div>

        <div className="lg:col-span-2 border border-slate-200 p-6">
          <h2 className="font-display text-lg font-bold tracking-tight mb-4">
            Subir CSV
          </h2>

          <label
            htmlFor="csv-input"
            className={`block border-2 border-dashed p-10 text-center cursor-pointer transition-colors ${
              file
                ? "border-blue-500 bg-blue-50/40"
                : "border-slate-300 hover:border-slate-400 bg-slate-50/30"
            }`}
            data-testid="dropzone"
          >
            <FileSpreadsheet
              className="h-10 w-10 mx-auto text-slate-400"
              strokeWidth={1.25}
            />
            <div className="mt-3 text-sm font-medium text-slate-700">
              {file ? file.name : "Selecciona un archivo CSV"}
            </div>
            <div className="text-xs text-slate-500 mt-1">
              {file
                ? `${(file.size / 1024).toFixed(1)} KB`
                : "Haz clic o arrastra aquí · separador ; o ,"}
            </div>
            <input
              id="csv-input"
              ref={inputRef}
              type="file"
              accept=".csv,text/csv"
              className="hidden"
              onChange={onPick}
              data-testid="csv-input"
            />
          </label>

          <div className="mt-5">
            <CertUploader value={cert} onChange={setCert} testIdPrefix="batch-cert" />
          </div>

          <div className="flex items-center gap-3 mt-5">
            <Button
              onClick={handleUpload}
              disabled={!file || loading}
              className="rounded-none bg-slate-900 hover:bg-slate-700 text-white"
              data-testid="submit-batch"
            >
              {loading ? (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              ) : (
                <Upload className="h-4 w-4 mr-2" />
              )}
              Procesar batch ({entorno} · {cert.enabled ? "real" : "mock"})
            </Button>
            {file && (
              <Button
                variant="ghost"
                onClick={() => {
                  setFile(null);
                  if (inputRef.current) inputRef.current.value = "";
                }}
                className="rounded-none"
                data-testid="clear-file"
              >
                Quitar archivo
              </Button>
            )}
          </div>
        </div>
      </div>

      {resumen && (
        <div className="mt-10" data-testid="batch-result">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="font-display text-2xl font-bold tracking-tight">
                Resultado del lote
              </h2>
              <div className="font-mono text-xs text-slate-500 mt-1">
                batch_id: {resumen.batch_id}
              </div>
            </div>
            <a
              href={`${API}/sii/batch/${resumen.batch_id}/export`}
              className="inline-flex items-center gap-2 border border-slate-300 px-3 py-2 text-sm hover:bg-slate-50"
              data-testid="export-batch"
            >
              <FileDown className="h-4 w-4" />
              Exportar resultados
            </a>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-5 border border-slate-200 mb-6">
            {[
              { label: "Total", value: resumen.total, color: "#0f172a" },
              {
                label: "Correctas",
                value: resumen.correctas,
                color: ESTADO_META.Correcta.color,
              },
              {
                label: "Con errores",
                value: resumen.aceptadas_con_errores,
                color: ESTADO_META.AceptadaConErrores.color,
              },
              {
                label: "Anuladas",
                value: resumen.anuladas,
                color: ESTADO_META.Anulada.color,
              },
              {
                label: "No registradas",
                value: resumen.no_registradas,
                color: ESTADO_META.NoRegistrada.color,
              },
            ].map((t, idx) => (
              <div
                key={t.label}
                className={`stat-tile ${idx < 4 ? "border-r border-slate-200" : ""}`}
              >
                <div
                  className="stat-accent"
                  style={{ backgroundColor: t.color }}
                />
                <div className="text-xs uppercase tracking-wider text-slate-500">
                  {t.label}
                </div>
                <div className="font-display text-3xl font-bold text-slate-900 mt-1">
                  {t.value}
                </div>
              </div>
            ))}
          </div>

          <div className="border border-slate-200">
            <Table>
              <TableHeader>
                <TableRow className="bg-slate-50 hover:bg-slate-50">
                  <TableHead className="text-xs uppercase tracking-wider">
                    NIF emisor
                  </TableHead>
                  <TableHead className="text-xs uppercase tracking-wider">
                    Nº factura
                  </TableHead>
                  <TableHead className="text-xs uppercase tracking-wider">
                    Fecha
                  </TableHead>
                  <TableHead className="text-xs uppercase tracking-wider">
                    Ejer. / Per.
                  </TableHead>
                  <TableHead className="text-xs uppercase tracking-wider">
                    Estado
                  </TableHead>
                  <TableHead className="text-xs uppercase tracking-wider">
                    Nº registro
                  </TableHead>
                  <TableHead></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {resumen.registros.map((r) => (
                  <TableRow
                    key={r.id}
                    className="data-row"
                    data-testid={`batch-row-${r.id}`}
                  >
                    <TableCell className="font-mono text-xs">
                      {r.entrada.nif_emisor}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {r.entrada.num_serie_factura}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {r.entrada.fecha_expedicion}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {r.entrada.ejercicio} / {r.entrada.periodo}
                    </TableCell>
                    <TableCell>
                      <EstadoBadge estado={r.respuesta.estado_factura} />
                    </TableCell>
                    <TableCell className="font-mono text-[11px] text-slate-600">
                      {r.respuesta.num_registro_presentacion || "—"}
                    </TableCell>
                    <TableCell className="text-right">
                      <button
                        className="text-slate-500 hover:text-slate-900"
                        onClick={() => setDetail(r)}
                        data-testid={`view-row-${r.id}`}
                      >
                        <Eye className="h-4 w-4" />
                      </button>
                    </TableCell>
                  </TableRow>
                ))}
                {!resumen.registros.length && (
                  <TableRow>
                    <TableCell
                      colSpan={7}
                      className="text-center py-8 text-slate-500"
                    >
                      No se procesó ninguna fila válida
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        </div>
      )}

      <QueryDetailSheet
        open={!!detail}
        onOpenChange={(o) => !o && setDetail(null)}
        record={detail}
      />
    </div>
  );
}
