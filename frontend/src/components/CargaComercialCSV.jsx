import { useEffect, useState } from "react";
import { api, API } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Upload, Download, Loader2 } from "lucide-react";
import { toast } from "sonner";

// Umbral (bytes) por encima del cual se usa el endpoint async con job + polling
// en vez del sync. Cloudflare corta conexiones HTTP idle a ~100s → un fichero
// grande procesado sync (12k+ filas, parseo tabular + bulk write) puede
// superarlo. El async devuelve job_id al instante y libera el HTTP.
const ASYNC_THRESHOLD_BYTES = 5 * 1024 * 1024; // 5 MB

/**
 * Sección "Importar fichero comercial (SAP FI / SIGLO)".
 *
 * Modo dual:
 *   - **Sync** (`POST /api/comercial/csv`) para ficheros <5 MB — respuesta
 *     inmediata con el resumen (total, matches SII, errores).
 *   - **Async** (`POST /api/comercial/csv-async`) para >5 MB — devuelve
 *     `job_id` y `import_id`; polling cada 2s a `/api/jobs/{job_id}` con barra
 *     de progreso hasta ver `status=done`. Evita el timeout 100s de Cloudflare
 *     que devolvía 502 al subir ficheros grandes HC30.
 */
export default function CargaComercialCSV({ onCompleted }) {
  const [csvFile, setCsvFile] = useState(null);
  const [loadingCsv, setLoadingCsv] = useState(false);
  const [sociedades, setSociedades] = useState([]);
  const [nifOverride, setNifOverride] = useState("__auto__");
  const [progress, setProgress] = useState(null);

  useEffect(() => {
    api
      .get("/comparativa/nifs-titulares")
      .then((r) => setSociedades(r.data?.sociedades || []))
      .catch(() => setSociedades([]));
  }, []);

  const subirCsvAsync = async (fd) => {
    const t0 = performance.now();
    setProgress({ fase: "upload", pctUpload: 0 });
    const { data: enqueue } = await api.post("/comercial/csv-async", fd, {
      headers: { "Content-Type": "multipart/form-data" },
      maxBodyLength: 1024 * 1024 * 1024,
      maxContentLength: 1024 * 1024 * 1024,
      timeout: 600_000,
      onUploadProgress: (ev) => {
        if (ev.total) {
          const pct = Math.round((ev.loaded / ev.total) * 100);
          setProgress((p) => ({
            ...(p || {}),
            fase: pct < 100 ? "upload" : "procesando",
            pctUpload: pct,
          }));
        }
      },
    });
    const jobId = enqueue?.job_id;
    if (!jobId) throw new Error("Backend no devolvió job_id");

    // Polling cada 2 s
    while (true) {
      await new Promise((r) => setTimeout(r, 2000));
      const { data: job } = await api.get(`/jobs/${jobId}`, { timeout: 30_000 });
      const p = job?.progress || {};
      setProgress({
        fase: p.phase || "procesando",
        procesadas: p.processed || 0,
        total: p.total || 0,
        pctUpload: 100,
      });
      if (job.status === "done") {
        const ms = Math.round(performance.now() - t0);
        const r = job.result || {};
        toast.success("Fichero comercial procesado", {
          description: [
            `${(r.total || 0).toLocaleString("es-ES")} facturas en ${(ms / 1000).toFixed(1)} s`,
            r.origen && `formato ${r.origen}`,
            r.matches_sii != null &&
              `${r.matches_sii.toLocaleString("es-ES")} ya en SII · ${(r.sin_match_sii || 0).toLocaleString("es-ES")} sin match`,
            r.errores?.length && `${r.errores.length} errores`,
          ]
            .filter(Boolean)
            .join(" · "),
          duration: 8000,
        });
        return r;
      }
      if (job.status === "error") {
        throw new Error(job.error_message || "Job terminó con error");
      }
    }
  };

  const subirCsvSync = async (fd) => {
    const { data } = await api.post("/comercial/csv", fd, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: 120_000,
    });
    const desc = [
      `${(data.total || 0).toLocaleString("es-ES")} facturas importadas`,
      data.origen && `formato ${data.origen}`,
      data.matches_sii != null &&
        `${data.matches_sii.toLocaleString("es-ES")} ya en SII · ${(data.sin_match_sii || 0).toLocaleString("es-ES")} sin match`,
      data.errores?.length && `${data.errores.length} errores`,
    ]
      .filter(Boolean)
      .join(" · ");
    toast.success("CSV comercial procesado", { description: desc, duration: 8000 });
    return data;
  };

  const subirCsv = async () => {
    if (!csvFile) {
      toast.error("Selecciona un CSV");
      return;
    }
    setLoadingCsv(true);
    setProgress(null);
    try {
      const fd = new FormData();
      fd.append("file", csvFile);
      if (nifOverride !== "__auto__") {
        fd.append("nif_titular_override", nifOverride);
      }
      const usarAsync = csvFile.size > ASYNC_THRESHOLD_BYTES;
      const data = usarAsync ? await subirCsvAsync(fd) : await subirCsvSync(fd);
      setCsvFile(null);
      onCompleted?.(data);
    } catch (e) {
      const d = e?.response?.data?.detail;
      const msg =
        typeof d === "string"
          ? d
          : e?.message || "Error al subir CSV";
      toast.error(msg, { duration: 10000 });
    } finally {
      setLoadingCsv(false);
      setProgress(null);
    }
  };

  return (
    <div data-testid="carga-comercial-csv">
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
        Acepta <span className="font-mono">.csv</span> con cabeceras estándar
        o <span className="font-mono">.txt</span> del report tabular en dos formatos:
        <br />
        <span className="font-mono">· SAP FI</span> — cabecera{" "}
        <span className="font-mono">Soc.|Doc.causante|Nº doc.oficial|…</span>
        <br />
        <span className="font-mono">· SIGLO</span> — cabecera{" "}
        <span className="font-mono">Soc.|Doc.caus.|Nº oficial|…</span>{" "}
        (incluye variante HC30 con columnas extra).
        <br />
        La columna <span className="font-mono">Soc.</span> se mapea
        automáticamente a NIF + nombre de sociedad usando el catálogo
        (<span className="font-mono">/admin/sociedades</span>). Si el report
        no trae el código en esa columna (p.ej. HC30 muestra la clase de
        asiento), usa el selector <strong>&quot;Forzar sociedad&quot;</strong>.
        <br />
        <span className="text-slate-400">
          Ficheros &gt; 5 MB se procesan en background (job + progreso).
        </span>
      </p>

      <div className="mb-4">
        <Label className="text-[11px] uppercase tracking-wider text-slate-600 mb-1.5 block">
          Forzar sociedad (opcional)
        </Label>
        <Select value={nifOverride} onValueChange={setNifOverride}>
          <SelectTrigger
            className="rounded-none text-sm"
            data-testid="csv-nif-override"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__auto__">
              Auto-detectar por columna Soc. (recomendado)
            </SelectItem>
            {sociedades.map((s) => (
              <SelectItem key={s.nif_titular} value={s.nif_titular}>
                {s.nombre_titular
                  ? `${s.nombre_titular} · ${s.nif_titular}`
                  : s.nif_titular}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <label
        htmlFor="csv-com"
        className="block border-2 border-dashed border-slate-300 hover:border-slate-400 p-6 text-center cursor-pointer bg-slate-50/40"
        data-testid="csv-dropzone"
      >
        <Upload className="h-7 w-7 mx-auto text-slate-400" />
        <div className="text-sm mt-2 text-slate-700">
          {csvFile
            ? `${csvFile.name} · ${(csvFile.size / 1024 / 1024).toFixed(2)} MB${csvFile.size > ASYNC_THRESHOLD_BYTES ? " · modo async" : ""}`
            : "Selecciona el fichero comercial (.csv ó .txt)"}
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

      {progress ? (
        <div className="mt-3 border border-slate-200 bg-slate-50 p-3 space-y-2" data-testid="upload-progress">
          <div className="flex items-center justify-between text-xs">
            <span className="font-mono uppercase tracking-wider text-slate-600">
              {progress.fase === "upload"
                ? `Subiendo · ${progress.pctUpload || 0}%`
                : progress.fase === "reading"
                ? "Leyendo fichero…"
                : progress.fase === "parsing"
                ? "Parseando cabeceras y filas…"
                : progress.fase === "inserting"
                ? `Insertando en BD · ${(progress.procesadas || 0).toLocaleString("es-ES")} / ${(progress.total || 0).toLocaleString("es-ES")}`
                : progress.fase === "done"
                ? "Finalizando…"
                : "Procesando…"}
            </span>
          </div>
          {progress.fase === "upload" ? (
            <Progress value={progress.pctUpload || 0} className="h-1.5" />
          ) : progress.total > 0 ? (
            <Progress
              value={Math.min(100, ((progress.procesadas || 0) / progress.total) * 100)}
              className="h-1.5"
            />
          ) : (
            <Progress value={0} className="h-1.5" />
          )}
        </div>
      ) : null}

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
  );
}
