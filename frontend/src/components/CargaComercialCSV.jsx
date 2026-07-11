import { useEffect, useState } from "react";
import { api, API } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Upload, Download, Loader2 } from "lucide-react";
import { toast } from "sonner";

/**
 * Sección "Importar fichero comercial (SAP FI / SIGLO)".
 *
 * Antes vivía en `Comparativa.jsx`; extraída a componente independiente y
 * renderizada dentro de `Carga de datos`. El parser autodetecta el formato
 * (SAP FI vs SIGLO) por las cabeceras y mapea `Soc.` → `nif_titular` con el
 * catálogo de sociedades.
 *
 * Selector "Forzar sociedad": para reports SIGLO variante HC30 (extracto de
 * balance) donde la columna `Soc.` contiene la clase de asiento en lugar del
 * código de sociedad, permite forzar el NIF titular para todas las filas.
 *
 * Tras una carga correcta llama a `onCompleted` por si la página padre quiere
 * refrescar contadores.
 */
export default function CargaComercialCSV({ onCompleted }) {
  const [csvFile, setCsvFile] = useState(null);
  const [loadingCsv, setLoadingCsv] = useState(false);
  const [sociedades, setSociedades] = useState([]);
  const [nifOverride, setNifOverride] = useState("__auto__");

  // Carga las sociedades disponibles al montar
  useEffect(() => {
    api
      .get("/comparativa/nifs-titulares")
      .then((r) => setSociedades(r.data?.sociedades || []))
      .catch(() => setSociedades([]));
  }, []);

  const subirCsv = async () => {
    if (!csvFile) {
      toast.error("Selecciona un CSV");
      return;
    }
    setLoadingCsv(true);
    try {
      const fd = new FormData();
      fd.append("file", csvFile);
      if (nifOverride !== "__auto__") {
        fd.append("nif_titular_override", nifOverride);
      }
      const { data } = await api.post("/comercial/csv", fd, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      const desc = [
        `${data.total.toLocaleString("es-ES")} facturas importadas`,
        data.origen && `formato ${data.origen}`,
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
      onCompleted?.(data);
    } catch (e) {
      const d = e.response?.data?.detail;
      toast.error(typeof d === "string" ? d : "Error al subir CSV");
    } finally {
      setLoadingCsv(false);
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
            ? csvFile.name
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
