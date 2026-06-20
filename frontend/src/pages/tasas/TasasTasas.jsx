import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, formatApiError  } from "@/lib/api";
import { motion, AnimatePresence } from "framer-motion";
import { Upload as UploadSimple, Zap as Lightning, ArrowRight, CheckSquare, Square, Search as MagnifyingGlass, Download as CloudArrowDown, Upload as CloudArrowUp, FileSpreadsheet as FileCsv } from "lucide-react";

export default function Tasas() {
  const navigate = useNavigate();
  const [source, setSource] = useState("upload"); // 'upload' | 'sharepoint'
  const [file, setFile] = useState(null);
  const [upload, setUpload] = useState(null);
  const [selected, setSelected] = useState(new Set());
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [filter, setFilter] = useState("");
  const [config, setConfig] = useState({ enabled_input: false, enabled_output: false, mock_mode: true });
  const [spFiles, setSpFiles] = useState([]);
  const [spLoading, setSpLoading] = useState(false);
  const [uploadToSp, setUploadToSp] = useState(false);

  useEffect(() => {
    api.get("/tasas-municipales/settings/public").then((r) => setConfig(r.data)).catch(() => {});
  }, []);

  useEffect(() => {
    if (source === "sharepoint" && config.enabled_input) {
      setSpLoading(true);
      api.get("/tasas-municipales/sharepoint/input-files")
        .then((r) => setSpFiles(r.data.files || []))
        .catch((e) => setError(formatApiError(e.response?.data?.detail)))
        .finally(() => setSpLoading(false));
    }
  }, [source, config.enabled_input]);

  const doUploadLocal = async () => {
    if (!file) return;
    setError(""); setUploading(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const { data } = await api.post("/tasas-municipales/upload", fd);
      setUpload(data);
      setSelected(new Set(data.municipios.map((m) => m.codigo)));
    } catch (e) {
      setError(formatApiError(e.response?.data?.detail));
    } finally { setUploading(false); }
  };

  const doImportSp = async (fileId) => {
    setError(""); setUploading(true);
    try {
      const { data } = await api.post("/tasas-municipales/upload-from-sharepoint", { file_id: fileId });
      setUpload(data);
      setSelected(new Set(data.municipios.map((m) => m.codigo)));
    } catch (e) {
      setError(formatApiError(e.response?.data?.detail));
    } finally { setUploading(false); }
  };

  const toggle = (codigo) => {
    const next = new Set(selected);
    if (next.has(codigo)) next.delete(codigo); else next.add(codigo);
    setSelected(next);
  };

  const filtered = upload ? upload.municipios.filter((m) => {
    if (!filter) return true;
    const f = filter.toLowerCase();
    return m.codigo.toLowerCase().includes(f) || (m.nombre || "").toLowerCase().includes(f);
  }) : [];

  const selectAll = () => setSelected(new Set(filtered.map((m) => m.codigo)));
  const selectNone = () => setSelected(new Set());

  const generate = async () => {
    if (selected.size === 0) return;
    setError(""); setGenerating(true);
    try {
      const { data } = await api.post("/tasas-municipales/generate", {
        upload_id: upload.id,
        codigos: [...selected],
        upload_to_sharepoint: uploadToSp && config.enabled_output,
      });
      navigate(`/tasas-municipales/jobs/${data.id}`);
    } catch (e) {
      setError(formatApiError(e.response?.data?.detail));
    } finally { setGenerating(false); }
  };

  const totalSelectedTasa = upload ?
    upload.municipios.filter((m) => selected.has(m.codigo)).reduce((a, m) => a + m.total_tasa, 0) : 0;

  return (
    <div className="space-y-10">
      <section className="pb-6 border-b border-zinc-200">
        <div className="flex items-center gap-3 mb-3">
          <Lightning size={24} className="text-finapp-primary" />
          <span className="label-track">Liquidación trimestral · Art. 24 LRHL</span>
        </div>
        <h1 className="font-heading font-black text-4xl sm:text-5xl tracking-tighter leading-none">
          Tasas eléctricas y gas.
        </h1>
        <p className="text-zinc-600 mt-3 max-w-3xl">
          Importa el CSV agregado de facturación y genera automáticamente un PDF por ayuntamiento
          con carta de remisión y tablas mensuales separadas para Electricidad y Gas.
        </p>
      </section>

      {/* Source selector */}
      <section>
        <div className="label-track mb-3">Paso 1 — origen del fichero</div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3" data-testid="source-selector">
          <button
            onClick={() => { setSource("upload"); setUpload(null); }}
            className={`border p-4 flex items-start gap-3 text-left transition-colors ${source === "upload" ? "border-finapp-primary bg-finapp-primary/5" : "border-zinc-200 hover:border-zinc-400"}`}
            data-testid="source-upload"
          >
            <UploadSimple size={22} className="text-finapp-primary flex-shrink-0 mt-0.5" />
            <div>
              <div className="font-heading font-bold">Subir archivo</div>
              <p className="text-xs text-zinc-600 mt-1">Arrastra o selecciona un CSV desde tu equipo.</p>
            </div>
          </button>
          <button
            onClick={() => { setSource("sharepoint"); setUpload(null); }}
            disabled={!config.enabled_input}
            className={`border p-4 flex items-start gap-3 text-left transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${source === "sharepoint" ? "border-finapp-primary bg-finapp-primary/5" : "border-zinc-200 hover:border-zinc-400"}`}
            data-testid="source-sharepoint"
          >
            <CloudArrowDown size={22} className="text-finapp-primary flex-shrink-0 mt-0.5" />
            <div>
              <div className="font-heading font-bold flex items-center gap-2">
                Importar desde SharePoint
                {!config.enabled_input && <span className="text-[10px] bg-zinc-200 text-zinc-700 px-2 py-0.5 uppercase tracking-widest font-bold">Desactivado</span>}
                {config.enabled_input && config.mock_mode && <span className="text-[10px] bg-[#FFD600] text-zinc-900 px-2 py-0.5 uppercase tracking-widest font-bold">Mock</span>}
              </div>
              <p className="text-xs text-zinc-600 mt-1">
                {config.enabled_input ? `Lee CSVs desde ${config.input_folder || "/Tasas/Entrada"}` : "Actívalo en Ajustes (solo admin)."}
              </p>
            </div>
          </button>
        </div>
      </section>

      <AnimatePresence mode="wait">
        {source === "upload" && !upload && (
          <motion.section
            key="upload"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="border border-zinc-200 p-6"
          >
            <label className="border border-dashed border-zinc-300 p-8 flex flex-col items-center justify-center cursor-pointer hover:border-finapp-primary transition-colors">
              <UploadSimple size={28} className="text-finapp-primary mb-2" />
              <span className="text-sm">{file ? file.name : "Selecciona el archivo .csv (separador ;)"}</span>
              <input type="file" accept=".csv,.txt" className="hidden" onChange={(e) => setFile(e.target.files[0])} data-testid="tasas-file-input" />
            </label>
            <button onClick={doUploadLocal} disabled={!file || uploading} className="btn-primary mt-4 w-full" data-testid="tasas-upload-btn">
              {uploading ? "Procesando…" : "Procesar CSV"}
            </button>
            {error && <div className="border border-[#FF2A00] text-[#FF2A00] text-sm px-3 py-2 mt-4">{error}</div>}
          </motion.section>
        )}

        {source === "sharepoint" && !upload && (
          <motion.section
            key="sp"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="border border-zinc-200"
            data-testid="sp-file-list"
          >
            <div className="px-5 py-3 border-b border-zinc-200 bg-zinc-50 flex items-center justify-between">
              <div>
                <div className="label-track">Archivos disponibles</div>
                <div className="font-mono text-xs text-zinc-600">{config.input_folder || "/Tasas/Entrada"}</div>
              </div>
              <span className="text-xs text-zinc-500">{spFiles.length} archivos</span>
            </div>
            {spLoading ? (
              <div className="p-8 text-center text-sm text-zinc-500">Cargando…</div>
            ) : spFiles.length === 0 ? (
              <div className="p-12 text-center text-sm text-zinc-500">Sin archivos en esa carpeta.</div>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-zinc-100">
                    <th className="text-left px-5 py-2 label-track">Nombre</th>
                    <th className="text-right px-5 py-2 label-track">Tamaño</th>
                    <th className="text-left px-5 py-2 label-track">Modificado</th>
                    <th className="px-5 py-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {spFiles.map((f) => (
                    <tr key={f.id} className="border-b border-zinc-100 hover:bg-zinc-50" data-testid={`sp-file-${f.id}`}>
                      <td className="px-5 py-3 font-mono flex items-center gap-2"><FileCsv size={18} className="text-finapp-primary" /> {f.name}</td>
                      <td className="px-5 py-3 text-right text-xs text-zinc-500">{(f.size / 1024).toFixed(1)} KB</td>
                      <td className="px-5 py-3 text-xs text-zinc-500">{new Date(f.modified).toLocaleString("es-ES")}</td>
                      <td className="px-5 py-3 text-right">
                        <button onClick={() => doImportSp(f.id)} disabled={uploading} className="text-finapp-primary text-xs uppercase tracking-wider font-bold hover:underline" data-testid={`sp-import-${f.id}`}>
                          {uploading ? "Importando…" : "Importar"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {error && <div className="border border-[#FF2A00] text-[#FF2A00] text-sm px-3 py-2 m-4">{error}</div>}
          </motion.section>
        )}
      </AnimatePresence>

      {/* Detected municipios */}
      {upload && (
        <motion.section initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}>
          <div className="flex flex-col lg:flex-row lg:items-end lg:justify-between mb-4 gap-4">
            <div>
              <div className="label-track mb-2">Paso 2 — municipios detectados</div>
              <h2 className="font-heading font-bold text-2xl">{upload.municipios_count} ayuntamientos</h2>
              <p className="text-zinc-600 text-sm mt-1">
                {upload.row_count} líneas procesadas · <span className="font-mono">{upload.filename}</span>
                {upload.source === "sharepoint" && <span className="ml-2 text-[10px] bg-finapp-primary text-white px-2 py-0.5 uppercase tracking-widest font-bold">SharePoint</span>}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button onClick={selectAll} className="btn-ghost text-xs">Todos</button>
              <button onClick={selectNone} className="btn-ghost text-xs">Ninguno</button>
              <button onClick={generate} disabled={selected.size === 0 || generating} className="btn-primary flex items-center gap-2" data-testid="tasas-generate-btn">
                {generating ? "Generando…" : `Generar ${selected.size} PDFs`} <ArrowRight size={16} />
              </button>
            </div>
          </div>

          {config.enabled_output && (
            <label className="flex items-center gap-3 border border-zinc-200 p-4 mb-4 cursor-pointer hover:border-finapp-primary">
              <input type="checkbox" checked={uploadToSp} onChange={(e) => setUploadToSp(e.target.checked)} data-testid="toggle-upload-sp" />
              <CloudArrowUp size={20} className="text-finapp-primary" />
              <div className="text-sm">
                <span className="font-heading font-bold">Subir PDFs a SharePoint al generar</span>
                <span className="text-zinc-600 ml-2">— se archivarán en <code className="font-mono text-xs">{config.output_folder || "/Tasas/Salida"}/{`{AYTO}/{YYYY-MM}/{AYTO}_{YYYY-MM-DD}.pdf`}</code></span>
                {config.mock_mode && <span className="ml-2 text-[10px] bg-[#FFD600] text-zinc-900 px-2 py-0.5 uppercase tracking-widest font-bold">Mock</span>}
              </div>
            </label>
          )}

          <div className="flex justify-between items-center mb-4 gap-4">
            <div className="relative max-w-md w-full">
              <MagnifyingGlass size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-400" />
              <input className="field-input pl-10" placeholder="Filtrar por código o nombre…" value={filter} onChange={(e) => setFilter(e.target.value)} data-testid="tasas-filter" />
            </div>
            <div className="text-sm">
              <span className="label-track block">Total seleccionado</span>
              <span className="font-heading font-bold text-xl">
                {totalSelectedTasa.toLocaleString("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €
              </span>
            </div>
          </div>

          <div className="border border-zinc-200 max-h-[60vh] overflow-auto" data-testid="tasas-municipios-list">
            <table className="w-full text-sm">
              <thead className="bg-zinc-50 sticky top-0 z-10">
                <tr>
                  <th className="px-3 py-3 w-10"></th>
                  <th className="text-left px-4 py-3 label-track">Código</th>
                  <th className="text-left px-4 py-3 label-track">Nombre</th>
                  <th className="text-left px-4 py-3 label-track">Filas</th>
                  <th className="text-left px-4 py-3 label-track">Periodo</th>
                  <th className="text-right px-4 py-3 label-track">Importe Tasa</th>
                  <th className="text-center px-4 py-3 label-track">Maestro</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((m) => {
                  const isSelected = selected.has(m.codigo);
                  return (
                    <tr key={m.codigo} className={`border-t border-zinc-100 ${isSelected ? "bg-finapp-primary/5" : ""}`} data-testid={`tasas-row-${m.codigo}`}>
                      <td className="px-3 py-2 text-center">
                        <button onClick={() => toggle(m.codigo)} className="text-finapp-primary" data-testid={`tasas-toggle-${m.codigo}`}>
                          {isSelected ? <CheckSquare size={20} /> : <Square size={20} />}
                        </button>
                      </td>
                      <td className="px-4 py-2 font-mono text-xs">{m.codigo}</td>
                      <td className="px-4 py-2 font-semibold">{m.nombre}</td>
                      <td className="px-4 py-2">{m.rows}</td>
                      <td className="px-4 py-2 font-mono text-xs">{m.min_period === m.max_period ? m.min_period : `${m.min_period} → ${m.max_period}`}</td>
                      <td className="px-4 py-2 text-right font-mono">
                        {m.total_tasa.toLocaleString("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €
                      </td>
                      <td className="px-4 py-2 text-center">
                        {m.exists_in_crud ? (
                          <span className="text-[10px] uppercase tracking-widest font-bold bg-[#008A27] text-white px-2 py-1">Sí</span>
                        ) : (
                          <span className="text-[10px] uppercase tracking-widest font-bold bg-[#FFD600] text-zinc-900 px-2 py-1">Auto</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {error && <div className="border border-[#FF2A00] text-[#FF2A00] text-sm px-3 py-2 mt-4">{error}</div>}
        </motion.section>
      )}
    </div>
  );
}
