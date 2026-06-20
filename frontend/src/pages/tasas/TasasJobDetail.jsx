import { useEffect, useState, useRef } from "react";
import { Link, useParams } from "react-router-dom";
import { api, API  } from "@/lib/api";
import { Download, ArrowLeft, FileText as FilePdf, AlertCircle as WarningCircle, CheckCircle2 as CheckCircle, Eye, X } from "lucide-react";
import { motion } from "framer-motion";
import PdfViewer from "../../components/PdfViewer";

const statusColor = {
  completado: "bg-[#008A27] text-white",
  parcial: "bg-[#FFD600] text-zinc-900",
  fallido: "bg-[#FF2A00] text-white",
};

export default function JobDetail() {
  const { id } = useParams();
  const [job, setJob] = useState(null);
  const [previewing, setPreviewing] = useState(null); // filename
  const [previewBlobUrl, setPreviewBlobUrl] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const blobUrlRef = useRef("");

  useEffect(() => {
    (async () => {
      const { data } = await api.get(`/tasas-municipales/jobs/${id}`);
      setJob(data);
      if (data.files && data.files.length > 0) setPreviewing(data.files[0]);
    })();
    return () => {
      if (blobUrlRef.current) URL.revokeObjectURL(blobUrlRef.current);
    };
  }, [id]);

  // Load PDF as blob to avoid ad-blockers blocking the URL pattern
  useEffect(() => {
    if (!previewing) return;
    setPreviewLoading(true);
    let cancelled = false;
    (async () => {
      try {
        const r = await api.get(`/tasas-municipales/jobs/${id}/files/${encodeURIComponent(previewing)}`, {
          responseType: "blob",
        });
        if (cancelled) return;
        const url = URL.createObjectURL(r.data);
        if (blobUrlRef.current) URL.revokeObjectURL(blobUrlRef.current);
        blobUrlRef.current = url;
        setPreviewBlobUrl(url);
      } catch (e) {
        console.error("preview load error", e);
      } finally {
        if (!cancelled) setPreviewLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [previewing, id]);

  const downloadFile = async (filename) => {
    try {
      const r = await api.get(`/tasas-municipales/jobs/${id}/files/${encodeURIComponent(filename)}`, { responseType: "blob" });
      const url = URL.createObjectURL(r.data);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      alert("Error al descargar el archivo");
    }
  };

  const downloadZip = async () => {
    try {
      const r = await api.get(`/tasas-municipales/jobs/${id}/download`, { responseType: "blob" });
      const url = URL.createObjectURL(r.data);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${job.template_name || "documentos"}.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      alert("Error al descargar el ZIP");
    }
  };

  const openInNewTab = () => {
    if (!previewBlobUrl) return;
    // Use anchor element to bypass strict popup-blockers and ad-blocker URL filters.
    const a = document.createElement("a");
    a.href = previewBlobUrl;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    document.body.appendChild(a);
    a.click();
    a.remove();
  };

  if (!job) return <div className="text-sm text-zinc-500">Cargando…</div>;

  return (
    <div className="space-y-10">
      <section className="pb-6 border-b border-zinc-200">
        <Link to="/tasas-municipales" className="text-xs uppercase tracking-[0.2em] text-zinc-500 hover:text-finapp-primary flex items-center gap-1 mb-4" data-testid="back-to-dashboard">
          <ArrowLeft size={14} /> Volver al panel
        </Link>
        <div className="flex flex-col lg:flex-row lg:items-end lg:justify-between gap-4">
          <div>
            <div className="label-track mb-2">Trabajo</div>
            <h1 className="font-heading font-black text-4xl sm:text-5xl tracking-tighter leading-none">
              {job.template_name}
            </h1>
            <div className="text-zinc-600 mt-2 font-mono text-xs">{job.excel_filename} · {new Date(job.created_at).toLocaleString("es-ES")}</div>
          </div>
          <div className="flex items-center gap-3">
            <span className={`text-xs uppercase tracking-widest font-bold px-3 py-2 ${statusColor[job.status] || "bg-zinc-200"}`} data-testid="job-status">
              {job.status}
            </span>
            {job.generated_count > 0 && (
              <button onClick={downloadZip} className="btn-primary flex items-center gap-2" data-testid="download-zip">
                <Download size={16} /> Descargar ZIP
              </button>
            )}
          </div>
        </div>
      </section>

      <section className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {[
          { label: job.template_name?.includes("Tasas") ? "Municipios" : "Filas Excel", value: job.row_count, icon: FilePdf, color: "text-finapp-primary" },
          { label: "Generados", value: job.generated_count, icon: CheckCircle, color: "text-[#008A27]" },
          { label: "Errores", value: job.error_count, icon: WarningCircle, color: "text-[#FF2A00]" },
          { label: "Éxito", value: job.row_count ? `${Math.round((job.generated_count / job.row_count) * 100)}%` : "—", icon: CheckCircle, color: "text-zinc-900" },
        ].map((s, i) => (
          <motion.div
            key={s.label}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.05 }}
            className="border border-zinc-200 p-5"
          >
            <div className="flex items-center justify-between">
              <div className="label-track">{s.label}</div>
              <s.icon size={18} className={s.color} />
            </div>
            <div className="font-heading font-black text-3xl tracking-tighter mt-2">{s.value}</div>
          </motion.div>
        ))}
      </section>

      {/* PDF Preview via blob */}
      {previewing && (
        <section data-testid="pdf-preview">
          <div className="flex items-center justify-between mb-3 gap-3 flex-wrap">
            <div>
              <div className="label-track">Vista previa</div>
              <h2 className="font-heading font-bold text-xl break-all">{previewing}</h2>
            </div>
            <div className="flex gap-2 flex-wrap">
              <button
                onClick={openInNewTab}
                disabled={!previewBlobUrl}
                className="btn-ghost text-xs flex items-center gap-2"
                data-testid="preview-open-tab"
              >
                <Eye size={14} /> Abrir a tamaño completo
              </button>
              <button onClick={() => downloadFile(previewing)} className="btn-ghost text-xs flex items-center gap-2" data-testid="preview-download">
                <Download size={14} /> Descargar
              </button>
              <button onClick={() => setPreviewing(null)} className="btn-ghost text-xs flex items-center gap-2" data-testid="preview-close">
                <X size={14} /> Cerrar
              </button>
            </div>
          </div>
          <div className="border border-zinc-300 bg-zinc-100 relative" style={{ height: "85vh" }}>
            {previewLoading && (
              <div className="absolute inset-0 flex items-center justify-center text-xs uppercase tracking-[0.2em] text-zinc-500 z-10">
                Cargando PDF…
              </div>
            )}
            {previewBlobUrl && <PdfViewer src={previewBlobUrl} />}
          </div>
        </section>
      )}

      <section>
        <h2 className="font-heading font-bold text-2xl mb-4">Archivos generados</h2>
        {job.files.length === 0 ? (
          <div className="border border-dashed border-zinc-300 p-12 text-center text-zinc-500">
            No se generaron archivos.
          </div>
        ) : (
          <div className="border border-zinc-200" data-testid="generated-files">
            <table className="w-full text-sm">
              <thead className="bg-zinc-50">
                <tr>
                  <th className="text-left px-5 py-3 label-track">#</th>
                  <th className="text-left px-5 py-3 label-track">Archivo</th>
                  <th className="text-right px-5 py-3 label-track">Acción</th>
                </tr>
              </thead>
              <tbody>
                {job.files.map((f, i) => (
                  <tr key={f} className={`border-t border-zinc-100 ${previewing === f ? "bg-finapp-primary/5" : ""}`} data-testid={`file-row-${i}`}>
                    <td className="px-5 py-3 font-mono text-zinc-500">{String(i + 1).padStart(3, "0")}</td>
                    <td className="px-5 py-3 font-mono break-all">{f}</td>
                    <td className="px-5 py-3 text-right">
                      <div className="flex items-center justify-end gap-3">
                        <button
                          onClick={() => setPreviewing(f)}
                          className="text-finapp-primary font-semibold text-xs uppercase tracking-wider inline-flex items-center gap-1 hover:underline"
                          data-testid={`preview-${i}`}
                        >
                          <Eye size={14} /> Ver
                        </button>
                        <button
                          onClick={() => downloadFile(f)}
                          className="text-zinc-700 font-semibold text-xs uppercase tracking-wider inline-flex items-center gap-1 hover:underline"
                          data-testid={`download-${i}`}
                        >
                          <Download size={14} /> Descargar
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {job.sharepoint_uploads && job.sharepoint_uploads.length > 0 && (
        <section data-testid="sp-uploads">
          <h2 className="font-heading font-bold text-2xl mb-4 flex items-center gap-2">
            Subidos a SharePoint
            <span className="text-[10px] uppercase tracking-widest font-bold bg-finapp-primary text-white px-2 py-1">{job.sharepoint_uploads.length}</span>
          </h2>
          <div className="border border-zinc-200">
            <table className="w-full text-sm">
              <thead className="bg-zinc-50">
                <tr>
                  <th className="text-left px-5 py-3 label-track">Municipio</th>
                  <th className="text-left px-5 py-3 label-track">Ruta SharePoint</th>
                </tr>
              </thead>
              <tbody>
                {job.sharepoint_uploads.map((u, i) => (
                  <tr key={i} className="border-t border-zinc-100">
                    <td className="px-5 py-3 font-semibold">{u.municipio}</td>
                    <td className="px-5 py-3 font-mono text-xs break-all text-zinc-700">{u.path}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {job.errors && job.errors.length > 0 && (
        <section>
          <h2 className="font-heading font-bold text-2xl mb-4 text-[#FF2A00]">Errores</h2>
          <div className="border border-[#FF2A00] divide-y divide-[#FF2A00]/30">
            {job.errors.map((e, i) => (
              <div key={i} className="p-4 text-sm font-mono">
                <span className="font-bold">{e.codigo ? `Código ${e.codigo}` : `Fila ${e.row}`}:</span> {e.error}
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
