import { useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { toast } from "sonner";
import { Loader2, Upload, FileSearch, Database, AlertTriangle, CheckCircle2, Import } from "lucide-react";
import { PERIODOS } from "@/lib/api";

const EJERCICIOS = ["2026", "2025", "2024", "2023", "2022"];

function StatBox({ label, value, tone, icon: Icon, testId }) {
  const colors = {
    danger: "border-red-200 bg-red-50 text-red-900",
    warn:   "border-amber-200 bg-amber-50 text-amber-900",
    ok:     "border-emerald-200 bg-emerald-50 text-emerald-900",
    neutral:"border-slate-200 bg-slate-50 text-slate-900",
  }[tone || "neutral"];
  return (
    <div className={`rounded-lg border p-4 ${colors}`} data-testid={testId}>
      <div className="flex items-center gap-2 text-xs uppercase tracking-wider opacity-70">
        {Icon ? <Icon className="h-3.5 w-3.5" /> : null}
        <span>{label}</span>
      </div>
      <div className="mt-1 text-3xl font-semibold tabular-nums">{value?.toLocaleString("es-ES") ?? "—"}</div>
    </div>
  );
}

export default function ConciliacionNewman() {
  const [file, setFile] = useState(null);
  const [nifTitular, setNifTitular] = useState("");
  const [nombreTitular, setNombreTitular] = useState("");
  const [ejercicio, setEjercicio] = useState("");
  const [periodo, setPeriodo] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [importing, setImporting] = useState(false);
  const [reporte, setReporte] = useState(null);

  const reset = () => { setReporte(null); };

  const onFile = (e) => {
    setFile(e.target.files?.[0] || null);
    reset();
  };

  const buildForm = (includeNombre = false) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("nif_titular", nifTitular.trim());
    if (includeNombre && nombreTitular.trim()) fd.append("nombre_titular", nombreTitular.trim());
    if (ejercicio) fd.append("ejercicio", ejercicio);
    if (periodo) fd.append("periodo", periodo);
    return fd;
  };

  const validar = () => {
    if (!file) { toast.error("Selecciona el CSV generado por Newman + extraer_csv.py"); return false; }
    if (!nifTitular.trim()) { toast.error("Indica el NIF titular"); return false; }
    return true;
  };

  const analizar = async () => {
    if (!validar()) return;
    setAnalyzing(true);
    setReporte(null);
    try {
      const { data } = await api.post("/sii/conciliar-newman", buildForm(false), {
        headers: { "Content-Type": "multipart/form-data" },
      });
      setReporte(data);
      if (data.faltantes_en_bd === 0) {
        toast.success("BD y CSV coinciden: no hay facturas perdidas");
      } else {
        toast.warning(`${data.faltantes_en_bd} facturas en el CSV no están en BD`);
      }
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Error al conciliar");
    } finally {
      setAnalyzing(false);
    }
  };

  const importar = async () => {
    if (!reporte || reporte.faltantes_en_bd === 0) {
      toast.info("No hay faltantes que importar");
      return;
    }
    if (!nifTitular.trim()) {
      toast.error("Indica el NIF titular");
      return;
    }
    const lote = reporte.faltantes_completas || [];
    if (lote.length === 0) {
      toast.error("El reporte no trae el detalle de faltantes. Pulsa Analizar de nuevo.");
      return;
    }
    if (!window.confirm(
      `Vas a insertar ${lote.length.toLocaleString("es-ES")} facturas en BD (de ${reporte.faltantes_en_bd.toLocaleString("es-ES")} faltantes${reporte.faltantes_truncado ? ", el resto requiere otra pasada por límite de payload" : ""}). ¿Continuar?`
    )) return;
    setImporting(true);
    try {
      const { data } = await api.post(
        "/sii/conciliar-newman/importar-lote",
        {
          nif_titular: nifTitular.trim(),
          nombre_titular: nombreTitular.trim() || undefined,
          ejercicio: ejercicio || undefined,
          periodo: periodo || undefined,
          facturas: lote,
        },
        {
          // El JSON de 100K facturas puede pesar ~10-15MB; axios por defecto
          // se queda corto. Subimos los límites.
          maxBodyLength: 256 * 1024 * 1024,
          maxContentLength: 256 * 1024 * 1024,
          timeout: 300_000,  // 5 min por si Mongo va a ritmo lento
        },
      );
      toast.success(`Importadas ${data.insertadas.toLocaleString("es-ES")} facturas`);
      // Re-analizar para refrescar contadores. Aquí SÍ vuelve a subir el CSV
      // pero ya sabemos que la operación importadora fue exitosa.
      await analizar();
    } catch (e) {
      toast.error(e?.response?.data?.detail || e?.message || "Error al importar");
    } finally {
      setImporting(false);
    }
  };

  return (
    <div className="space-y-6" data-testid="page-conciliacion">
      <header className="space-y-1">
        <h1 className="text-3xl font-semibold tracking-tight">Conciliación con CSV de Newman</h1>
        <p className="text-muted-foreground max-w-3xl">
          Sube el CSV generado en local con Newman + <code>extraer_csv.py</code> y compáralo con
          la BD para detectar facturas perdidas en jobs anteriores. Las faltantes
          pueden insertarse con un clic, manteniendo idempotencia (clave única <code>num_serie_factura</code>).
        </p>
      </header>

      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Upload className="h-4 w-4" /> Entrada
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="rec-file">CSV (Newman → extraer_csv.py)</Label>
              <Input
                id="rec-file"
                type="file"
                accept=".csv,text/csv"
                onChange={onFile}
                data-testid="rec-file-input"
              />
              {file ? <p className="text-xs text-muted-foreground" data-testid="rec-file-name">{file.name} · {(file.size/1024).toFixed(1)} KB</p> : null}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="rec-nif">NIF titular</Label>
              <Input
                id="rec-nif"
                placeholder="B12345678"
                value={nifTitular}
                onChange={(e) => { setNifTitular(e.target.value); reset(); }}
                data-testid="rec-nif-input"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="rec-nombre">Razón social (opcional, sólo para importar)</Label>
              <Input
                id="rec-nombre"
                placeholder="MI EMPRESA S.L."
                value={nombreTitular}
                onChange={(e) => setNombreTitular(e.target.value)}
                data-testid="rec-nombre-input"
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1.5">
                <Label>Ejercicio</Label>
                <Select value={ejercicio || "_"} onValueChange={(v) => { setEjercicio(v === "_" ? "" : v); reset(); }}>
                  <SelectTrigger data-testid="rec-ejercicio-select"><SelectValue placeholder="Todos" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="_">Todos</SelectItem>
                    {EJERCICIOS.map((e) => <SelectItem key={e} value={e}>{e}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>Periodo</Label>
                <Select value={periodo || "_"} onValueChange={(v) => { setPeriodo(v === "_" ? "" : v); reset(); }}>
                  <SelectTrigger data-testid="rec-periodo-select"><SelectValue placeholder="Todos" /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="_">Todos</SelectItem>
                    {PERIODOS.map((p) => <SelectItem key={p.value} value={p.value}>{p.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>
          </div>

          <div className="flex flex-wrap gap-2 pt-2">
            <Button
              onClick={analizar}
              disabled={analyzing || importing}
              data-testid="rec-analizar-btn"
            >
              {analyzing ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <FileSearch className="h-4 w-4 mr-2" />}
              Analizar
            </Button>
            {reporte && reporte.faltantes_en_bd > 0 ? (
              <Button
                onClick={importar}
                disabled={importing || analyzing}
                variant="default"
                data-testid="rec-importar-btn"
              >
                {importing ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Import className="h-4 w-4 mr-2" />}
                Importar {reporte.faltantes_en_bd.toLocaleString("es-ES")} faltantes
              </Button>
            ) : null}
          </div>
        </CardContent>
      </Card>

      {reporte ? (
        <Card data-testid="rec-reporte">
          <CardHeader>
            <CardTitle className="text-base">Resultado de la conciliación</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              <StatBox label="Total CSV" value={reporte.total_csv} tone="neutral" icon={Upload} testId="rec-stat-csv" />
              <StatBox label="Total BD" value={reporte.total_bd} tone="neutral" icon={Database} testId="rec-stat-bd" />
              <StatBox label="Coinciden" value={reporte.coinciden} tone="ok" icon={CheckCircle2} testId="rec-stat-coinciden" />
              <StatBox label="Faltantes en BD" value={reporte.faltantes_en_bd} tone="danger" icon={AlertTriangle} testId="rec-stat-faltantes" />
              <StatBox label="Sólo en BD" value={reporte.extra_en_bd} tone="warn" icon={Database} testId="rec-stat-extras" />
            </div>

            {reporte.errores_csv?.length ? (
              <Alert variant="destructive" data-testid="rec-errores">
                <AlertTriangle className="h-4 w-4" />
                <AlertDescription>
                  <div className="font-medium mb-1">Errores en el CSV ({reporte.errores_csv.length}):</div>
                  <ul className="list-disc pl-5 space-y-0.5 text-xs">
                    {reporte.errores_csv.slice(0, 10).map((e, i) => <li key={i}>{e}</li>)}
                    {reporte.errores_csv.length > 10 ? <li>… y {reporte.errores_csv.length - 10} más</li> : null}
                  </ul>
                </AlertDescription>
              </Alert>
            ) : null}

            {reporte.total_csv === 0 && reporte.debug ? (
              <Alert variant="destructive" data-testid="rec-debug-zero">
                <AlertTriangle className="h-4 w-4" />
                <AlertDescription>
                  <div className="font-medium mb-1">El CSV no produjo filas válidas tras aplicar los filtros. Diagnóstico:</div>
                  <div className="text-xs space-y-1 font-mono">
                    <div>Delimitador detectado: <strong>{reporte.debug.delimitador}</strong></div>
                    <div>Filas brutas leídas: <strong>{reporte.debug.total_filas_brutas?.toLocaleString("es-ES")}</strong></div>
                    <div>Headers detectadas: <strong>{(reporte.debug.headers_detectadas || []).join(" | ")}</strong></div>
                    {reporte.debug.primera_fila_bruta ? (
                      <div>Primera fila bruta (10 col.): <strong>{JSON.stringify(reporte.debug.primera_fila_bruta)}</strong></div>
                    ) : null}
                    {reporte.debug.primera_fila_mapeada ? (
                      <div>Primera fila mapeada: <strong>{JSON.stringify(reporte.debug.primera_fila_mapeada)}</strong></div>
                    ) : null}
                  </div>
                  <div className="text-xs mt-2 opacity-80">
                    Causas habituales: (1) el filtro de Ejercicio/Periodo descarta todas las filas (revisa qué <code>ejercicio</code>/<code>periodo</code> tiene la primera fila mapeada); (2) las cabeceras no son las del XSD AEAT (la colección Postman emite <code>NumSerieFacturaEmisor</code>, <code>PeriodoPeriodo</code>, ...).
                  </div>
                </AlertDescription>
              </Alert>
            ) : null}

            {reporte.extra_preview?.length && reporte.faltantes_en_bd === 0 ? (
              <div className="rounded-lg border bg-amber-50/30 px-4 py-3 text-xs" data-testid="rec-extra-preview">
                <div className="font-medium mb-1 text-amber-900">Muestra de "Sólo en BD" (en BD pero no en CSV, hasta 20):</div>
                <div className="font-mono text-amber-900/80 break-all">{reporte.extra_preview.join(" · ")}</div>
              </div>
            ) : null}

            {reporte.faltantes_preview?.length ? (
              <div className="rounded-lg border overflow-hidden" data-testid="rec-faltantes-tabla">
                <div className="px-4 py-2 bg-slate-50 border-b text-sm font-medium">
                  Faltantes detectadas (mostrando {reporte.faltantes_preview.length} de {reporte.faltantes_en_bd.toLocaleString("es-ES")})
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="bg-slate-50/50 text-xs uppercase tracking-wider text-slate-500">
                      <tr>
                        <th className="text-left px-4 py-2">Nº serie</th>
                        <th className="text-left px-4 py-2">Fecha</th>
                        <th className="text-right px-4 py-2">Base</th>
                        <th className="text-right px-4 py-2">Total</th>
                        <th className="text-left px-4 py-2">Estado</th>
                      </tr>
                    </thead>
                    <tbody>
                      {reporte.faltantes_preview.map((f) => (
                        <tr key={f.num_serie_factura} className="border-t hover:bg-slate-50/60">
                          <td className="px-4 py-1.5 font-mono text-xs">{f.num_serie_factura}</td>
                          <td className="px-4 py-1.5">{f.fecha_expedicion || "—"}</td>
                          <td className="px-4 py-1.5 text-right tabular-nums">{f.base_imponible?.toLocaleString("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) ?? "—"}</td>
                          <td className="px-4 py-1.5 text-right tabular-nums">{f.importe_total?.toLocaleString("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) ?? "—"}</td>
                          <td className="px-4 py-1.5">{f.estado_factura || "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : (
              reporte.faltantes_en_bd === 0 ? (
                <Alert>
                  <CheckCircle2 className="h-4 w-4" />
                  <AlertDescription>
                    Todas las facturas del CSV están presentes en BD. No hay nada que importar.
                  </AlertDescription>
                </Alert>
              ) : null
            )}
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
