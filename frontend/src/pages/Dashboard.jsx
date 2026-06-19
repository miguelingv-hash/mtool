import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ESTADO_META } from "@/lib/api";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Cell,
} from "recharts";
import {
  CheckCircle2,
  AlertTriangle,
  Ban,
  XCircle,
  ArrowRight,
  FileSearch,
  FileSpreadsheet,
} from "lucide-react";
import EstadoBadge from "@/components/EstadoBadge";

const TILES = [
  {
    key: "correctas",
    label: "Correctas",
    icon: CheckCircle2,
    color: "#059669",
    estado: "Correcta",
  },
  {
    key: "aceptadas_con_errores",
    label: "Aceptadas con errores",
    icon: AlertTriangle,
    color: "#d97706",
    estado: "AceptadaConErrores",
  },
  {
    key: "anuladas",
    label: "Anuladas",
    icon: Ban,
    color: "#64748b",
    estado: "Anulada",
  },
  {
    key: "no_registradas",
    label: "No registradas",
    icon: XCircle,
    color: "#dc2626",
    estado: "NoRegistrada",
  },
];

export default function Dashboard() {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get("/sii/stats")
      .then((r) => setStats(r.data))
      .catch(() => setStats(null))
      .finally(() => setLoading(false));
  }, []);

  const chartData = stats
    ? TILES.map((t) => ({
        name: ESTADO_META[t.estado].label,
        value: stats[t.key],
        color: t.color,
      }))
    : [];

  return (
    <div className="px-8 py-8 max-w-[1400px]">
      <div className="flex items-start justify-between gap-6 mb-8">
        <div>
          <div className="text-xs uppercase tracking-[0.2em] text-slate-500 mb-2">
            Panel
          </div>
          <h1 className="font-display text-4xl font-bold tracking-tight text-slate-900">
            Estado del SII
          </h1>
          <p className="text-sm text-slate-600 mt-2 max-w-xl">
            Resumen de las consultas realizadas al servicio de la Agencia
            Tributaria sobre facturas emitidas. Los datos se generan en modo
            simulado a partir del WSDL oficial.
          </p>
        </div>
        <div className="flex gap-2">
          <Link
            to="/consulta"
            data-testid="cta-unit"
            className="inline-flex items-center gap-2 bg-slate-900 text-white px-4 py-2.5 text-sm hover:bg-slate-700 transition-colors"
          >
            <FileSearch className="h-4 w-4" />
            Consulta unitaria
          </Link>
          <Link
            to="/batch"
            data-testid="cta-batch"
            className="inline-flex items-center gap-2 border border-slate-300 px-4 py-2.5 text-sm hover:bg-slate-50 transition-colors"
          >
            <FileSpreadsheet className="h-4 w-4" />
            Subir CSV
          </Link>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-0 border border-slate-200 mb-8">
        <div className="stat-tile border-r border-slate-200" data-testid="stat-total">
          <div className="stat-accent bg-slate-900" />
          <div className="text-xs uppercase tracking-wider text-slate-500">
            Consultas totales
          </div>
          <div className="font-display text-4xl font-bold text-slate-900 mt-2">
            {loading ? "—" : stats.total}
          </div>
          <div className="text-xs text-slate-400 mt-1">acumulado histórico</div>
        </div>

        {TILES.map((t, idx) => {
          const Icon = t.icon;
          return (
            <div
              key={t.key}
              data-testid={`stat-${t.key}`}
              className={`stat-tile ${idx < TILES.length - 1 ? "border-r border-slate-200" : ""}`}
            >
              <div
                className="stat-accent"
                style={{ backgroundColor: t.color }}
              />
              <div className="flex items-center justify-between">
                <div className="text-xs uppercase tracking-wider text-slate-500">
                  {t.label}
                </div>
                <Icon
                  className="h-4 w-4"
                  style={{ color: t.color }}
                  strokeWidth={1.75}
                />
              </div>
              <div className="font-display text-4xl font-bold text-slate-900 mt-2">
                {loading ? "—" : stats[t.key]}
              </div>
              <div className="text-xs text-slate-400 mt-1">
                {!loading && stats.total > 0
                  ? `${((stats[t.key] / stats.total) * 100).toFixed(1)}%`
                  : "0%"}
              </div>
            </div>
          );
        })}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
        <div className="lg:col-span-3 border border-slate-200 p-6">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="font-display text-lg font-bold tracking-tight">
                Distribución por estado
              </h2>
              <p className="text-xs text-slate-500">
                Reparto de respuestas SOAP del SII
              </p>
            </div>
          </div>
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} margin={{ top: 8, right: 8, bottom: 0, left: -10 }}>
                <CartesianGrid stroke="#e2e8f0" vertical={false} />
                <XAxis
                  dataKey="name"
                  fontSize={11}
                  tick={{ fill: "#64748b" }}
                  axisLine={{ stroke: "#cbd5e1" }}
                  tickLine={false}
                />
                <YAxis
                  fontSize={11}
                  tick={{ fill: "#64748b" }}
                  axisLine={false}
                  tickLine={false}
                  allowDecimals={false}
                />
                <Tooltip
                  cursor={{ fill: "#f1f5f9" }}
                  contentStyle={{
                    border: "1px solid #e2e8f0",
                    borderRadius: 0,
                    fontSize: 12,
                  }}
                />
                <Bar dataKey="value" radius={0}>
                  {chartData.map((entry, idx) => (
                    <Cell key={idx} fill={entry.color} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="lg:col-span-2 border border-slate-200">
          <div className="flex items-center justify-between p-4 border-b border-slate-200">
            <h2 className="font-display text-lg font-bold tracking-tight">
              Últimas consultas
            </h2>
            <Link
              to="/historico"
              className="text-xs text-blue-600 hover:underline inline-flex items-center gap-1"
              data-testid="link-history"
            >
              Ver todas <ArrowRight className="h-3 w-3" />
            </Link>
          </div>
          <div className="divide-y divide-slate-100">
            {loading || !stats?.ultimas?.length ? (
              <div className="p-6 text-sm text-slate-400 text-center">
                Sin consultas registradas
              </div>
            ) : (
              stats.ultimas.map((r) => (
                <div key={r.id} className="data-row p-3 px-4 text-sm">
                  <div className="flex items-center justify-between gap-2">
                    <div className="font-mono text-xs text-slate-600 truncate">
                      {r.entrada.nif_emisor} · {r.entrada.num_serie_factura}
                    </div>
                    <EstadoBadge estado={r.respuesta.estado_factura} />
                  </div>
                  <div className="text-[11px] text-slate-400 mt-1">
                    {new Date(r.timestamp).toLocaleString("es-ES")}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
