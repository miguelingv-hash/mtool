import { NavLink, Outlet } from "react-router-dom";
import {
  LayoutDashboard,
  FileSearch,
  FileSpreadsheet,
  History as HistoryIcon,
  ServerCog,
  ShieldCheck,
  Radio,
} from "lucide-react";
import { useEnv } from "@/contexts/EnvContext";
import { useSiiConfig } from "@/hooks/useSiiConfig";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { to: "/", label: "Resumen", icon: LayoutDashboard, end: true, testId: "nav-dashboard" },
  { to: "/consulta", label: "Consulta unitaria", icon: FileSearch, testId: "nav-unit" },
  { to: "/batch", label: "Consulta batch (CSV)", icon: FileSpreadsheet, testId: "nav-batch" },
  { to: "/historico", label: "Histórico", icon: HistoryIcon, testId: "nav-history" },
];

export default function Layout() {
  const { entorno, setEntorno } = useEnv();
  const config = useSiiConfig();
  const mode = config?.default_mode || "mock";
  const isMock = mode === "mock";
  return (
    <div className="min-h-screen flex flex-col bg-white text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="flex items-center justify-between px-6 py-3">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center bg-slate-900 text-white">
              <ShieldCheck className="h-5 w-5" strokeWidth={1.75} />
            </div>
            <div className="leading-tight">
              <div className="font-display text-base font-bold tracking-tight text-slate-900">
                SII Consulta
              </div>
              <div className="text-[11px] uppercase tracking-wider text-slate-500">
                Facturas Emitidas · AEAT
              </div>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <div
              className={`hidden md:inline-flex items-center gap-1.5 text-[11px] font-mono uppercase tracking-wider px-2 py-1 border ${
                isMock
                  ? "border-slate-300 text-slate-600 bg-slate-50"
                  : "border-emerald-300 text-emerald-700 bg-emerald-50"
              }`}
              data-testid="sii-mode-badge"
              title={`WSDL: ${config?.wsdl || ""}`}
            >
              {isMock ? (
                <ServerCog className="h-3.5 w-3.5" />
              ) : (
                <Radio className="h-3.5 w-3.5" />
              )}
              <span>
                {isMock ? "Modo Mock" : "Modo Real"} · WSDL v1.1
              </span>
            </div>
            <div className="h-6 w-px bg-slate-200 hidden md:block" />
            <div className="flex items-center gap-2">
              <span className="text-xs text-slate-500 hidden sm:block">
                Entorno
              </span>
              <Select value={entorno} onValueChange={setEntorno}>
                <SelectTrigger
                  className="h-8 w-[180px] rounded-none border-slate-300 text-sm"
                  data-testid="env-selector-trigger"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="preproduccion" data-testid="env-preprod">
                    Pre-producción
                  </SelectItem>
                  <SelectItem value="produccion" data-testid="env-prod">
                    Producción
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
        </div>
      </header>

      <div className="flex flex-1">
        <aside className="w-60 shrink-0 border-r border-slate-200 bg-slate-50/40">
          <nav className="p-3 space-y-0.5" data-testid="sidebar-nav">
            {NAV_ITEMS.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                data-testid={item.testId}
                className={({ isActive }) =>
                  cn(
                    "flex items-center gap-3 px-3 py-2 text-sm transition-colors border-l-2",
                    isActive
                      ? "bg-white border-blue-600 text-slate-900 font-medium"
                      : "border-transparent text-slate-600 hover:bg-white hover:text-slate-900",
                  )
                }
              >
                <item.icon className="h-4 w-4" strokeWidth={1.75} />
                {item.label}
              </NavLink>
            ))}
          </nav>

          <div className="mt-6 mx-3 p-3 border border-slate-200 bg-white text-[11px] leading-relaxed text-slate-500">
            <div className="font-semibold text-slate-700 mb-1 uppercase tracking-wider">
              WSDL
            </div>
            <div className="font-mono break-all">
              SuministroFactEmitidas.wsdl v1.1
            </div>
            <div className="mt-2 text-slate-400">
              ConsultaLRFactEmitidas · Agencia Tributaria Española
            </div>
          </div>
        </aside>

        <main className="flex-1 min-w-0 bg-white">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
