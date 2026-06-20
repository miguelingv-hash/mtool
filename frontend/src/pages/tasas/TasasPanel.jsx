import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, API  } from "@/lib/api";
import { Plus, FileText, Download, ArrowRight, CheckCircle2 as CheckCircle, AlertCircle as WarningCircle } from "lucide-react";
import { motion } from "framer-motion";

const statusStyles = {
  completado: "bg-[#008A27] text-white",
  parcial: "bg-[#FFD600] text-zinc-900",
  fallido: "bg-[#FF2A00] text-white",
};

export default function Dashboard() {
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [token, setToken] = useState("");

  useEffect(() => {
    (async () => {
      try {
        const [jr, tk] = await Promise.all([
          api.get("/tasas-municipales/jobs"),
          api.get("/tasas-municipales/jobs/auth/download-token"),
        ]);
        setJobs(jr.data);
        setToken(tk.data.token);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const stats = [
    { label: "Trabajos totales", value: jobs.length, icon: FileText },
    { label: "PDFs generados", value: jobs.reduce((a, j) => a + (j.generated_count || 0), 0), icon: CheckCircle },
    { label: "Con errores", value: jobs.filter((j) => j.error_count > 0).length, icon: WarningCircle },
  ];

  return (
    <div className="space-y-12">
      {/* Hero */}
      <motion.section
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
        className="flex flex-col lg:flex-row lg:items-end lg:justify-between gap-6 pb-8 border-b border-zinc-200"
      >
        <div>
          <div className="label-track mb-3">Panel principal</div>
          <h1 className="font-heading font-black text-4xl sm:text-5xl lg:text-6xl tracking-tighter leading-none">
            Historial<br/>de trabajos.
          </h1>
        </div>
        <div className="flex flex-wrap gap-3">
          <Link to="/tasas-municipales/municipios" className="btn-ghost" data-testid="goto-municipios-btn">Municipios</Link>
          <Link to="/tasas-municipales/tasas" className="btn-primary flex items-center gap-2" data-testid="new-job-cta">
            <Plus size={16} /> Generar Tasas
          </Link>
        </div>
      </motion.section>

      {/* Stats */}
      <section className="grid grid-cols-1 md:grid-cols-3 gap-4" data-testid="stats-grid">
        {stats.map((s, idx) => (
          <motion.div
            key={s.label}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4, delay: idx * 0.07 }}
            className="border border-zinc-200 p-6 hover:border-finapp-primary transition-colors"
          >
            <div className="flex items-start justify-between">
              <div>
                <div className="label-track mb-3">{s.label}</div>
                <div className="font-heading font-black text-5xl tracking-tighter">{s.value}</div>
              </div>
              <s.icon size={28} className="text-finapp-primary" />
            </div>
          </motion.div>
        ))}
      </section>

      {/* Jobs Table */}
      <section>
        <div className="flex items-center justify-between mb-6">
          <h2 className="font-heading font-bold text-2xl tracking-tight">Trabajos recientes</h2>
          <span className="label-track">{jobs.length} registros</span>
        </div>

        {loading ? (
          <div className="text-zinc-500 text-sm">Cargando…</div>
        ) : jobs.length === 0 ? (
          <div className="border border-dashed border-zinc-300 p-16 text-center">
            <WarningCircle size={36} className="text-zinc-400 mx-auto mb-3" />
            <div className="font-heading font-bold text-xl mb-2">Sin trabajos todavía</div>
            <p className="text-zinc-600 mb-6 max-w-sm mx-auto">Crea tu primer trabajo subiendo una plantilla PDF y un Excel.</p>
            <Link to="/tasas-municipales/tasas" className="btn-primary inline-flex items-center gap-2" data-testid="empty-new-job-btn">
              Generar Tasas <ArrowRight size={16} />
            </Link>
          </div>
        ) : (
          <div className="border border-zinc-200" data-testid="jobs-table">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-zinc-200 bg-zinc-50">
                  <th className="text-left px-5 py-3 label-track">Plantilla</th>
                  <th className="text-left px-5 py-3 label-track">Excel</th>
                  <th className="text-left px-5 py-3 label-track">Filas</th>
                  <th className="text-left px-5 py-3 label-track">Generados</th>
                  <th className="text-left px-5 py-3 label-track">Estado</th>
                  <th className="text-left px-5 py-3 label-track">Fecha</th>
                  <th className="text-right px-5 py-3 label-track">Acciones</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((j) => (
                  <tr key={j.id} className="border-b border-zinc-100 hover:bg-zinc-50/50" data-testid={`job-row-${j.id}`}>
                    <td className="px-5 py-4 font-medium">{j.template_name}</td>
                    <td className="px-5 py-4 text-zinc-600 font-mono text-xs">{j.excel_filename}</td>
                    <td className="px-5 py-4">{j.row_count}</td>
                    <td className="px-5 py-4">
                      <span className="font-mono">{j.generated_count}</span>
                      {j.error_count > 0 && <span className="text-[#FF2A00] ml-2 text-xs">({j.error_count} errores)</span>}
                    </td>
                    <td className="px-5 py-4">
                      <span className={`text-[10px] uppercase tracking-widest font-bold px-2 py-1 ${statusStyles[j.status] || "bg-zinc-200"}`}>
                        {j.status}
                      </span>
                    </td>
                    <td className="px-5 py-4 text-zinc-600 text-xs font-mono">
                      {new Date(j.created_at).toLocaleString("es-ES")}
                    </td>
                    <td className="px-5 py-4 text-right">
                      <div className="flex items-center justify-end gap-2">
                        <Link to={`/trabajo/${j.id}`} className="text-finapp-primary font-semibold text-xs uppercase tracking-wider underline-offset-4 hover:underline" data-testid={`view-job-${j.id}`}>Ver</Link>
                        {j.generated_count > 0 && token && (
                          <a
                            href={`${API}/tasas-municipales/jobs/${j.id}/download?token=${token}`}
                            className="ml-3 inline-flex items-center gap-1 text-zinc-700 hover:text-finapp-primary text-xs uppercase tracking-wider font-semibold"
                            data-testid={`download-zip-${j.id}`}
                          >
                            <Download size={14} /> ZIP
                          </a>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
