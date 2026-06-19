import { NavLink, Outlet, useNavigate } from "react-router-dom";
import {
  LayoutDashboard,
  FileSearch,
  FileSpreadsheet,
  GitCompareArrows,
  History as HistoryIcon,
  ScrollText,
  ServerCog,
  Settings,
  Stamp,
  Radio,
  ShieldCheck,
  Users,
  UserCog,
  LogOut,
} from "lucide-react";
import { useEnv } from "@/contexts/EnvContext";
import { useAuth } from "@/contexts/AuthContext";
import { useSiiConfig } from "@/hooks/useSiiConfig";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { to: "/", label: "Resumen", icon: LayoutDashboard, end: true, testId: "nav-dashboard" },
  { to: "/comparativa", label: "Comparativa SII↔CSV", icon: GitCompareArrows, testId: "nav-comparativa", perm: "comparativa.view" },
  { to: "/consulta", label: "Consulta unitaria", icon: FileSearch, testId: "nav-unit", perm: "consultas.unitaria" },
  { to: "/batch", label: "Consulta batch (CSV)", icon: FileSpreadsheet, testId: "nav-batch", perm: "consultas.batch" },
  { to: "/historico", label: "Histórico", icon: HistoryIcon, testId: "nav-history" },
  { to: "/logs", label: "Log de WS", icon: ScrollText, testId: "nav-logs", perm: "logs.view" },
  { to: "/conciliacion", label: "Conciliación Newman", icon: ShieldCheck, testId: "nav-conciliacion", perm: "conciliacion.view" },
  { to: "/configuracion", label: "Configuración", icon: Settings, testId: "nav-config", perm: "comparativa.edit_config" },
  { to: "/admin/usuarios", label: "Usuarios", icon: Users, testId: "nav-admin-users", perm: "users.manage" },
  { to: "/admin/roles", label: "Roles", icon: UserCog, testId: "nav-admin-roles", perm: "roles.manage" },
];

export default function Layout() {
  const { entorno, setEntorno } = useEnv();
  const config = useSiiConfig();
  const { user, logout, hasPermission } = useAuth();
  const navigate = useNavigate();
  const mode = config?.default_mode || "mock";
  const isMock = mode === "mock";
  const isProd = entorno.startsWith("produccion");
  const envTriggerCls = isProd
    ? "h-8 w-[260px] rounded-none border-rose-500 bg-rose-50 text-rose-800 font-semibold text-sm focus:ring-rose-400"
    : "h-8 w-[260px] rounded-none border-amber-400 bg-amber-50 text-amber-900 font-semibold text-sm focus:ring-amber-400";

  const items = NAV_ITEMS.filter((it) => !it.perm || hasPermission(it.perm));

  const onLogout = async () => {
    await logout();
    navigate("/login", { replace: true });
  };
  return (
    <div className="min-h-screen flex flex-col bg-white text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="flex items-center justify-between px-6 py-3">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center bg-slate-900 text-white">
              <Stamp className="h-5 w-5" strokeWidth={1.75} />
            </div>
            <div className="leading-tight">
              <div className="font-display text-base font-bold tracking-tight text-slate-900">
                Monitor SII
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
                  className={envTriggerCls}
                  data-testid="env-selector-trigger"
                >
                  <span
                    className={`inline-block h-2 w-2 rounded-full mr-2 ${
                      isProd ? "bg-rose-500" : "bg-amber-500"
                    }`}
                    aria-hidden
                  />
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="preproduccion" data-testid="env-preprod">
                    Pre-producción · cert. normal
                  </SelectItem>
                  <SelectItem
                    value="preproduccion_sello"
                    data-testid="env-preprod-sello"
                  >
                    Pre-producción · cert. de sello
                  </SelectItem>
                  <SelectItem value="produccion" data-testid="env-prod">
                    Producción · cert. normal
                  </SelectItem>
                  <SelectItem
                    value="produccion_sello"
                    data-testid="env-prod-sello"
                  >
                    Producción · cert. de sello
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
            {user ? (
              <>
                <div className="h-6 w-px bg-slate-200 hidden md:block" />
                <div className="hidden md:flex flex-col items-end leading-tight text-right">
                  <div className="text-xs font-medium text-slate-900">{user.name || user.email}</div>
                  <div className="text-[10px] uppercase tracking-wider text-slate-500">{user.role}</div>
                </div>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={onLogout}
                  data-testid="logout-btn"
                  title="Cerrar sesión"
                >
                  <LogOut className="h-4 w-4" />
                </Button>
              </>
            ) : null}
          </div>
        </div>
      </header>

      <div className="flex flex-1">
        <aside className="w-60 shrink-0 border-r border-slate-200 bg-slate-50/40">
          <nav className="p-3 space-y-0.5" data-testid="sidebar-nav">
            {items.map((item) => (
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
