import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, API } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { Banknote, Download, FileSpreadsheet, Loader2, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";

export default function PagosVentanillaGeneracion() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const fileRef = useRef(null);
  const [file, setFile] = useState(null);
  const [upload, setUpload] = useState(null);
  const [busy, setBusy] = useState(false);
  const [generating, setGenerating] = useState(false);

  const onPick = (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    setFile(f);
    setUpload(null);
  };

  const onUpload = async () => {
    if (!file) return;
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const { data } = await api.post("/pagos-ventanilla/upload", fd, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      setUpload(data);
      toast.success(`CSV procesado: ${data.row_count} filas`);
    } catch (e) {
      toast.error(e.response?.data?.detail || "Error procesando el CSV");
    } finally {
      setBusy(false);
    }
  };

  const onGenerate = async () => {
    if (!upload) return;
    setGenerating(true);
    try {
      const { data } = await api.post("/pagos-ventanilla/generate", { upload_id: upload.id });
      toast.success(`${data.generated_count} PDFs generados`);
      navigate(`/pagos-ventanilla/historico?job=${data.id}`);
    } catch (e) {
      toast.error(e.response?.data?.detail || "Error generando PDFs");
    } finally {
      setGenerating(false);
    }
  };

  const downloadTemplate = () => {
    const a = document.createElement("a");
    a.href = `${API}/pagos-ventanilla/csv-template`;
    a.click();
  };

  return (
    <div className="space-y-6" data-testid="pv-generacion-page">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-[0.18em] text-slate-500 font-mono">Pagos Ventanilla</p>
          <h1 className="text-3xl font-bold text-slate-900 flex items-center gap-3 mt-1">
            <Banknote className="h-7 w-7 text-emerald-700" />
            Generación de documentos de pago
          </h1>
          <p className="text-sm text-slate-600 mt-2 max-w-2xl">
            Sube un CSV (separador <code>;</code>) con los datos de los pagos por ventanilla. La
            aplicación generará un PDF por fila según la sociedad emisora (TTE / Baser) con su
            código de barras Cuaderno 57 - 507 y código QR para la web pública.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={downloadTemplate} data-testid="pv-download-template">
          <Download className="h-4 w-4 mr-2" /> Plantilla CSV
        </Button>
      </div>

      <Card className="p-6 border-2 border-dashed border-slate-300 bg-slate-50">
        <div className="flex items-center gap-4">
          <input
            type="file"
            accept=".csv,.txt"
            ref={fileRef}
            onChange={onPick}
            className="hidden"
            data-testid="pv-file-input"
          />
          <Button
            variant="outline"
            onClick={() => fileRef.current?.click()}
            data-testid="pv-select-file"
          >
            <Upload className="h-4 w-4 mr-2" />
            {file ? file.name : "Seleccionar CSV"}
          </Button>
          <Button
            onClick={onUpload}
            disabled={!file || busy}
            className="bg-emerald-700 hover:bg-emerald-800"
            data-testid="pv-process-csv"
          >
            {busy ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <FileSpreadsheet className="h-4 w-4 mr-2" />}
            Procesar CSV
          </Button>
        </div>
      </Card>

      {upload && (
        <Card className="p-6 space-y-4" data-testid="pv-preview-card">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold">Resumen del CSV</h2>
              <p className="text-xs text-slate-500">
                {upload.filename} · {upload.row_count} filas
              </p>
            </div>
            <Button
              onClick={onGenerate}
              disabled={generating}
              className="bg-emerald-700 hover:bg-emerald-800"
              data-testid="pv-generate-pdfs"
            >
              {generating ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Banknote className="h-4 w-4 mr-2" />}
              Generar {upload.row_count} PDFs
            </Button>
          </div>

          <div className="flex flex-wrap gap-3">
            {upload.by_sociedad.map((s) => (
              <Badge
                key={s.sociedad}
                variant="secondary"
                className="text-sm py-1.5 px-3"
                data-testid={`pv-soc-${s.sociedad}`}
              >
                <span className="font-bold mr-2">{s.sociedad === "TTE" ? "TotalEnergies" : "Baser"}</span>
                {s.rows} {s.rows === 1 ? "pago" : "pagos"} · {s.importe_total.toFixed(2)} €
              </Badge>
            ))}
          </div>

          <div className="overflow-x-auto border border-slate-200">
            <table className="w-full text-xs">
              <thead className="bg-slate-100">
                <tr className="text-left">
                  <th className="px-3 py-2">#</th>
                  <th className="px-3 py-2">Sociedad</th>
                  <th className="px-3 py-2">Cliente</th>
                  <th className="px-3 py-2">CIF/NIF</th>
                  <th className="px-3 py-2">Nº factura</th>
                  <th className="px-3 py-2 text-right">Importe</th>
                  <th className="px-3 py-2">Fecha factura</th>
                  <th className="px-3 py-2">Fecha límite</th>
                </tr>
              </thead>
              <tbody>
                {upload.preview.map((p) => (
                  <tr key={p.idx} className="border-t border-slate-100">
                    <td className="px-3 py-2 font-mono text-slate-500">{p.idx}</td>
                    <td className="px-3 py-2 font-semibold">{p.sociedad}</td>
                    <td className="px-3 py-2">{p.nombre_cliente}</td>
                    <td className="px-3 py-2 font-mono">{p.cif_nif}</td>
                    <td className="px-3 py-2 font-mono">{p.numero_factura}</td>
                    <td className="px-3 py-2 text-right font-mono">{p.importe.toFixed(2)} €</td>
                    <td className="px-3 py-2 font-mono">{p.fecha_emision_factura}</td>
                    <td className="px-3 py-2 font-mono">{p.fecha_limite_pago}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {upload.row_count > upload.preview.length && (
              <p className="px-3 py-2 text-xs text-slate-500 bg-slate-50">
                Mostrando {upload.preview.length} de {upload.row_count} filas. Todas se generarán al pulsar el botón.
              </p>
            )}
          </div>
        </Card>
      )}
    </div>
  );
}
