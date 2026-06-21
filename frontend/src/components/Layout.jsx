import { useMemo, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  Building2,
  ChevronDown,
  ChevronRight,
  FileSearch,
  FileSpreadsheet,
  GitCompareArrows,
  History as HistoryIcon,
  LayoutDashboard,
  LogOut,
  Radio,
  ScrollText,
  Settings,
  ShieldCheck,
  UserCog,
  Users,
  Activity,
  Coins,
  LayoutGrid,
  Building,
  Sliders,
  Banknote,
  FileText,
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

/**
 * Árbol del menú. Cada nodo puede ser:
 *   - item: { to, label, icon, end?, perm?, testId }
 *   - grupo: { id, label, icon, perm?, testId, children: [items|grupos] }
 *
 * El grupo se considera visible si tiene al menos un hijo accesible.
 */
const NAV_TREE = [
  {
    id: "monitor-sii",
    label: "Monitor SII",
    icon: Activity,
    testId: "nav-group-monitor-sii",
    children: [
      { to: "/", label: "Resumen", icon: LayoutDashboard, end: true, testId: "nav-dashboard" },
      { to: "/comparativa", label: "Comparativa SII↔CSV", icon: GitCompareArrows, testId: "nav-comparativa", perm: "comparativa.view" },
      { to: "/consulta", label: "Consulta unitaria", icon: FileSearch, testId: "nav-unit", perm: "consultas.unitaria" },
      { to: "/batch", label: "Consulta batch (CSV)", icon: FileSpreadsheet, testId: "nav-batch", perm: "consultas.batch" },
      { to: "/historico", label: "Histórico", icon: HistoryIcon, testId: "nav-history" },
      { to: "/logs", label: "Log de WS", icon: ScrollText, testId: "nav-logs", perm: "logs.view" },
      { to: "/conciliacion", label: "Conciliación Newman", icon: ShieldCheck, testId: "nav-conciliacion", perm: "conciliacion.view" },
      { to: "/configuracion", label: "Configuración", icon: Settings, testId: "nav-config", perm: "comparativa.edit_config" },
    ],
  },
  {
    id: "tasas-municipales",
    label: "Tasas Municipales",
    icon: Coins,
    testId: "nav-group-tasas",
    children: [
      { to: "/tasas-municipales", label: "Panel", icon: LayoutGrid, end: true, testId: "nav-tasas-panel", perm: "tasas.view" },
      { to: "/tasas-municipales/tasas", label: "Tasas", icon: FileSpreadsheet, testId: "nav-tasas-tasas", perm: "tasas.manage" },
      { to: "/tasas-municipales/municipios", label: "Municipios", icon: Building, testId: "nav-tasas-municipios", perm: "tasas.view" },
      { to: "/tasas-municipales/ajustes", label: "Ajustes", icon: Sliders, testId: "nav-tasas-ajustes", perm: "tasas.admin" },
    ],
  },
  {
    id: "pagos-ventanilla",
    label: "Pagos Ventanilla",
    icon: Banknote,
    testId: "nav-group-pv",
    children: [
      { to: "/pagos-ventanilla/generacion", label: "Generación", icon: FileText, testId: "nav-pv-generar", perm: "pagos_ventanilla.manage" },
      { to: "/pagos-ventanilla/historico", label: "Histórico", icon: HistoryIcon, testId: "nav-pv-historico", perm: "pagos_ventanilla.view" },
    ],
  },
  { to: "/admin/usuarios", label: "Usuarios", icon: Users, testId: "nav-admin-users", perm: "users.manage" },
  { to: "/admin/roles", label: "Roles", icon: UserCog, testId: "nav-admin-roles", perm: "roles.manage" },
];

function isItemAccessible(node, hasPermission) {
  if (node.children) {
    return node.children.some((c) => isItemAccessible(c, hasPermission));
  }
  return !node.perm || hasPermission(node.perm);
}

function filterTree(tree, hasPermission) {
  return tree
    .filter((n) => isItemAccessible(n, hasPermission))
    .map((n) => (n.children ? { ...n, children: filterTree(n.children, hasPermission) } : n));
}

function NavItem({ item }) {
  return (
    <NavLink
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
      <span className="truncate">{item.label}</span>
    </NavLink>
  );
}

function NavGroup({ group, defaultOpen }) {
  const [open, setOpen] = useState(defaultOpen);
  const Icon = group.icon;
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        data-testid={group.testId}
        className={cn(
          "w-full flex items-center gap-3 px-3 py-2 text-sm transition-colors border-l-2 border-transparent text-slate-700 hover:bg-white hover:text-slate-900",
          open && "text-slate-900 font-medium",
        )}
        aria-expanded={open}
      >
        <Icon className="h-4 w-4" strokeWidth={1.75} />
        <span className="flex-1 text-left truncate">{group.label}</span>
        <Chevron className="h-3.5 w-3.5 opacity-70" />
      </button>
      {open ? (
        <div className="ml-3 border-l border-slate-200 pl-2 py-0.5 space-y-0.5">
          {group.children.map((child) => (
            <NavItem key={child.to} item={child} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

export default function Layout() {
  const { entorno, setEntorno } = useEnv();
  const config = useSiiConfig();
  const { user, logout, hasPermission } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const isProd = entorno.startsWith("produccion");
  const envTriggerCls = isProd
    ? "h-8 w-[260px] rounded-none border-rose-500 bg-rose-50 text-rose-800 font-semibold text-sm focus:ring-rose-400"
    : "h-8 w-[260px] rounded-none border-amber-400 bg-amber-50 text-amber-900 font-semibold text-sm focus:ring-amber-400";

  const tree = useMemo(() => filterTree(NAV_TREE, hasPermission), [hasPermission]);

  const onLogout = async () => {
    await logout();
    navigate("/login", { replace: true });
  };

  // Auto-expandir el grupo cuya ruta actual coincida con algún hijo
  const isPathInside = (group) =>
    group.children?.some((c) => location.pathname === c.to || location.pathname.startsWith(c.to + "/"));

  return (
    <div className="min-h-screen flex flex-col bg-white text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="flex items-center justify-between px-6 py-3">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center bg-slate-900 text-white">
              <Building2 className="h-5 w-5" strokeWidth={1.75} />
            </div>
            <div className="leading-tight">
              <div className="font-display text-base font-bold tracking-tight text-slate-900">
                Corporate App
              </div>
              <div className="text-[11px] uppercase tracking-wider text-slate-500">
                Plataforma corporativa
              </div>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <div
              className="hidden md:inline-flex items-center gap-1.5 text-[11px] font-mono uppercase tracking-wider px-2 py-1 border border-emerald-300 text-emerald-700 bg-emerald-50"
              data-testid="sii-mode-badge"
              title={`WSDL: ${config?.wsdl || ""}`}
            >
              <Radio className="h-3.5 w-3.5" />
              <span>SII · WSDL v1.1 · mTLS</span>
            </div>
            <div className="h-6 w-px bg-slate-200 hidden md:block" />
            <div className="flex items-center gap-2">
              <span className="text-xs text-slate-500 hidden lg:inline">
                Entorno
              </span>
              <Select value={entorno} onValueChange={setEntorno}>
                <SelectTrigger className={envTriggerCls} data-testid="env-select">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="preproduccion" data-testid="env-preprod">
                    Pre-producción · cert. normal
                  </SelectItem>
                  <SelectItem value="preproduccion_sello" data-testid="env-preprod-sello">
                    Pre-producción · cert. de sello
                  </SelectItem>
                  <SelectItem value="produccion" data-testid="env-prod">
                    Producción · cert. normal
                  </SelectItem>
                  <SelectItem value="produccion_sello" data-testid="env-prod-sello">
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
        <aside className="w-64 shrink-0 border-r border-slate-200 bg-slate-50/40">
          <nav className="p-3 space-y-0.5" data-testid="sidebar-nav">
            {tree.map((node) =>
              node.children ? (
                <NavGroup key={node.id} group={node} defaultOpen={isPathInside(node)} />
              ) : (
                <NavItem key={node.to} item={node} />
              ),
            )}
          </nav>
        </aside>

        <main className="flex-1 min-w-0 bg-white">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
