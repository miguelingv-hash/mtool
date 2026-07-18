import { Fragment, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  ChevronDown,
  ChevronRight,
  Download,
  RefreshCw,
  Loader2,
  ExternalLink,
  CheckCircle2,
  AlertTriangle,
  Grid3X3,
} from "lucide-react";
import { toast } from "sonner";
import { labelOrigenComercial } from "@/lib/origenes";

const MONTH_LABELS = {
  "01": "Enero",
  "02": "Febrero",
  "03": "Marzo",
  "04": "Abril",
  "05": "Mayo",
  "06": "Junio",
  "07": "Julio",
  "08": "Agosto",
  "09": "Septiembre",
  10: "Octubre",
  11: "Noviembre",
  12: "Diciembre",
};

const TIPO_LABEL = {
  F1: "Factura normal",
  F2: "Simplificada / tique",
  F3: "Reemplaza simplificada",
  F4: "Resumen de facturas",
  R1: "Rectificativa · 80.1/80.2/80.6",
  R2: "Rectificativa · 80.3",
  R3: "Rectificativa · 80.4",
  R4: "Rectificativa · otro motivo",
  R5: "Rectificativa · simplificada",
  _sin_clasificar: "Sólo en Comercial (sin tipo)",
};

function fmtEur(v) {
  if (v == null || Number.isNaN(v)) return "—";
  const n = Number(v);
  return n.toLocaleString("es-ES", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function fmtInt(v) {
  if (v == null) return "—";
  return Number(v).toLocaleString("es-ES");
}

function fmtPct(v) {
  if (v == null) return "—";
  return `${(v * 100).toFixed(2)}%`;
}

function DeltaCell({ value, mode = "eur" }) {
  if (value == null || value === 0 || value === 0.0) {
    return (
      <span className="inline-flex items-center gap-1 text-emerald-700 font-mono tabular-nums">
        <CheckCircle2 className="h-3 w-3" />
        {mode === "eur" ? "0,00" : "0"}
      </span>
    );
  }
  const abs = Math.abs(value);
  const isAmber = mode === "eur" ? abs < 1 : abs < 1;
  const cls = isAmber ? "text-amber-700" : "text-rose-700 font-semibold";
  return (
    <span
      className={`inline-flex items-center gap-1 font-mono tabular-nums ${cls}`}
    >
      <AlertTriangle className="h-3 w-3" />
      {mode === "eur" ? fmtEur(value) : fmtInt(value)}
    </span>
  );
}

function PctCell({ value }) {
  if (value == null) {
    return <span className="text-slate-400 text-xs italic">n/a</span>;
  }
  const pct = value * 100;
  let cls = "text-emerald-700";
  if (pct < 99.5) cls = "text-amber-700";
  if (pct < 95) cls = "text-rose-700 font-semibold";
  return (
    <span className={`font-mono tabular-nums text-xs ${cls}`}>
      {pct.toFixed(2)}%
    </span>
  );
}

/**
 * Fila expandida: lista de facturas del tipo + periodo + sociedad seleccionados.
 * Reusa el endpoint /comparativa/bundle con filtros pre-fijados.
 */
function DetalleFacturas({ nifTitular, ejercicio, periodo, tipoFactura }) {
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      try {
        const params = {
          skip: 0,
          limit: 20,
          only_diffs: false,
          nif_titular: nifTitular,
          ejercicio,
          periodo,
          tipos_factura: tipoFactura,
        };
        // Usamos /comparativa (sólo lista) en lugar de /comparativa/bundle
        // para evitar recalcular totales + resumen_origenes (30-40s extra).
        const r = await api.get("/comparativa", { params });
        if (cancelled) return;
        const list = r.data?.items || [];
        setItems(list);
        setTotal(r.data?.total || 0);
      } catch (err) {
        if (!cancelled) {
          toast.error("No se pudo cargar el detalle", {
            description: err?.message || "",
          });
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [nifTitular, ejercicio, periodo, tipoFactura]);

  const deepLink = useMemo(() => {
    const p = new URLSearchParams();
    if (nifTitular) p.set("nif_titular", nifTitular);
    if (ejercicio) p.set("ejercicio", ejercicio);
    if (periodo) p.set("periodo", periodo);
    if (tipoFactura) p.set("tipos_factura", tipoFactura);
    return `/comparativa?${p.toString()}`;
  }, [nifTitular, ejercicio, periodo, tipoFactura]);

  if (loading) {
    return (
      <div className="px-6 py-4 flex items-center gap-2 text-sm text-slate-500">
        <Loader2 className="h-4 w-4 animate-spin" />
        Cargando facturas del tramo…
        <span className="text-[11px] text-slate-400 italic ml-1">
          (puede tardar hasta 60 s en frío; luego se cachea 5 min)
        </span>
      </div>
    );
  }

  return (
    <div className="px-6 py-4 bg-slate-50/60 border-t border-slate-200">
      <div className="flex items-center justify-between mb-3">
        <div className="text-xs uppercase tracking-wider text-slate-600">
          {fmtInt(total)} facturas · Periodo {periodo} · Tipo{" "}
          <span className="font-mono">
            {tipoFactura === "_sin_clasificar" ? "—" : tipoFactura}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <Link to={deepLink} data-testid="cuadro-detalle-open-comparativa">
            <Button
              variant="outline"
              size="sm"
              className="rounded-none h-7 text-xs"
            >
              <ExternalLink className="h-3 w-3 mr-1" />
              Abrir en Comparativa
            </Button>
          </Link>
        </div>
      </div>
      {items.length === 0 ? (
        <div className="text-xs text-slate-500 italic">
          No hay facturas para este tramo.
        </div>
      ) : (
        <div className="border border-slate-200 bg-white">
          <Table>
            <TableHeader>
              <TableRow className="bg-slate-100 hover:bg-slate-100">
                <TableHead className="text-xs uppercase tracking-wider">
                  Nº Factura
                </TableHead>
                <TableHead className="text-xs uppercase tracking-wider">
                  Estado
                </TableHead>
                <TableHead className="text-xs uppercase tracking-wider text-right">
                  Base SII
                </TableHead>
                <TableHead className="text-xs uppercase tracking-wider text-right">
                  Cuota SII
                </TableHead>
                <TableHead className="text-xs uppercase tracking-wider text-right">
                  Base Comercial
                </TableHead>
                <TableHead className="text-xs uppercase tracking-wider text-right">
                  Cuota Comercial
                </TableHead>
                <TableHead className="text-xs uppercase tracking-wider">
                  Origen
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((it) => (
                <TableRow key={it.num_serie_factura}>
                  <TableCell className="font-mono text-xs">
                    {it.num_serie_factura}
                  </TableCell>
                  <TableCell className="text-xs">{it.estado}</TableCell>
                  <TableCell className="font-mono text-xs tabular-nums text-right">
                    {fmtEur(it?.sii?.base_imponible)}
                  </TableCell>
                  <TableCell className="font-mono text-xs tabular-nums text-right">
                    {fmtEur(it?.sii?.cuota_repercutida)}
                  </TableCell>
                  <TableCell className="font-mono text-xs tabular-nums text-right">
                    {fmtEur(it?.comercial?.base_imponible)}
                  </TableCell>
                  <TableCell className="font-mono text-xs tabular-nums text-right">
                    {fmtEur(it?.comercial?.cuota_repercutida)}
                  </TableCell>
                  <TableCell className="text-xs">
                    {labelOrigenComercial(it?.comercial?.origen_comercial) || "—"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {total > items.length && (
            <div className="px-3 py-2 text-[11px] text-slate-500 italic border-t border-slate-200">
              Mostrando {items.length} de {fmtInt(total)}. Abre en Comparativa
              para ver todas y exportar.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function CuadroMensual() {
  const [sociedades, setSociedades] = useState([]); // [{nif_titular, nombre_titular}]
  const [nifSel, setNifSel] = useState(null); // nif seleccionado
  const [ejercicios, setEjercicios] = useState([]);
  const [ejercicioSel, setEjercicioSel] = useState("");
  const [periodos, setPeriodos] = useState([]); // meses disponibles
  const [periodoSel, setPeriodoSel] = useState("__all__");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState({}); // key = periodo|tipo

  // Cargar sociedades
  useEffect(() => {
    async function load() {
      try {
        const r = await api.get("/comparativa/nifs-titulares");
        const socs = r.data?.sociedades || [];
        setSociedades(socs);
        if (socs.length > 0 && !nifSel) {
          // Preselecciona la de menor volumen (arranque rápido)
          const sorted = [...socs].sort(
            (a, b) => (a.n_sii || 0) - (b.n_sii || 0),
          );
          setNifSel(sorted[0].nif_titular);
        }
      } catch (err) {
        toast.error("No se pudieron cargar las sociedades");
      }
    }
    load();
  }, []);

  // Cargar periodos disponibles para la sociedad seleccionada
  useEffect(() => {
    if (!nifSel) return;
    async function load() {
      try {
        const r = await api.get("/comparativa/periodos", {
          params: { nif_titular: nifSel },
        });
        const ej = r.data?.ejercicios || [];
        const per = r.data?.periodos || [];
        setEjercicios(ej);
        setPeriodos(per);
        // Auto-select último ejercicio
        if (ej.length > 0) {
          setEjercicioSel((prev) => (ej.includes(prev) ? prev : ej[ej.length - 1]));
        } else {
          setEjercicioSel("");
        }
        setPeriodoSel("__all__");
      } catch (err) {
        toast.error("No se pudieron cargar los periodos");
      }
    }
    load();
  }, [nifSel]);

  // Cargar cuadro mensual
  const load = async () => {
    if (!nifSel || !ejercicioSel) return;
    setLoading(true);
    setExpanded({});
    try {
      const params = { nif_titular: nifSel, ejercicio: ejercicioSel };
      if (periodoSel !== "__all__") params.periodo = periodoSel;
      const r = await api.get("/comparativa/cuadro-mensual", { params });
      setData(r.data);
    } catch (err) {
      toast.error("Error cargando el cuadro mensual", {
        description: err?.response?.data?.detail || err?.message || "",
      });
      setData(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (nifSel && ejercicioSel) load();
  }, [nifSel, ejercicioSel, periodoSel]);

  // CSV export del cuadro (front-side, sin round-trip al backend)
  const exportCsv = () => {
    if (!data) return;
    const orig = data.origenes || [];
    const header = [
      "Periodo",
      "Tipo",
      "SII_Base",
      "SII_Cuota",
      "SII_N",
      ...orig.flatMap((o) => [
        `${o}_Base`,
        `${o}_Cuota`,
        `${o}_N`,
        `Delta_${o}_Base`,
        `Delta_${o}_Cuota`,
        `Delta_${o}_N`,
        `Pct_${o}_Base`,
        `Pct_${o}_Cuota`,
        `Pct_${o}_N`,
      ]),
    ];
    const rows = [header];
    for (const r of data.rows || []) {
      const line = [
        r.periodo,
        r.tipo_factura,
        r.sii?.base ?? 0,
        r.sii?.cuota ?? 0,
        r.sii?.n ?? 0,
      ];
      for (const o of orig) {
        const c = r.comercial_por_origen?.[o] || {};
        const d = r.delta_por_origen?.[o] || {};
        const p = r.pct_conciliacion_por_origen?.[o] || {};
        line.push(
          c.base ?? 0,
          c.cuota ?? 0,
          c.n ?? 0,
          d.base ?? 0,
          d.cuota ?? 0,
          d.n ?? 0,
          p.base ?? "",
          p.cuota ?? "",
          p.facturas ?? "",
        );
      }
      rows.push(line);
    }
    // TOTAL
    const t = data.totales || {};
    const tRow = [
      "TOTAL",
      "—",
      t.sii?.base ?? 0,
      t.sii?.cuota ?? 0,
      t.sii?.n ?? 0,
    ];
    for (const o of orig) {
      const c = t.comercial_por_origen?.[o] || {};
      const d = t.delta_por_origen?.[o] || {};
      const p = t.pct_conciliacion_por_origen?.[o] || {};
      tRow.push(
        c.base ?? 0,
        c.cuota ?? 0,
        c.n ?? 0,
        d.base ?? 0,
        d.cuota ?? 0,
        d.n ?? 0,
        p.base ?? "",
        p.cuota ?? "",
        p.facturas ?? "",
      );
    }
    rows.push(tRow);

    const csv = rows
      .map((r) =>
        r
          .map((v) => {
            if (v == null) return "";
            const s = String(v).replace(/"/g, '""');
            return /[";,\n]/.test(s) ? `"${s}"` : s;
          })
          .join(";"),
      )
      .join("\n");
    const blob = new Blob(["\uFEFF" + csv], {
      type: "text/csv;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `cuadro_mensual_${nifSel}_${ejercicioSel}${periodoSel !== "__all__" ? `_${periodoSel}` : ""}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  const toggle = (key) => {
    setExpanded((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const origenes = data?.origenes || [];
  const rows = data?.rows || [];
  const totales = data?.totales;

  return (
    <div className="max-w-[1600px] mx-auto px-6 py-6" data-testid="cuadro-mensual-page">
      <div className="mb-6">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center bg-slate-900 text-white">
            <Grid3X3 className="h-5 w-5" strokeWidth={1.75} />
          </div>
          <div>
            <h1 className="text-2xl font-display font-bold tracking-tight text-slate-900">
              Cuadro de Conciliación Mensual
            </h1>
            <p className="text-sm text-slate-500 mt-0.5">
              Base + Cuota + Nº de facturas por sociedad y tipo de factura ·
              SII vs SIGLO vs SAP FI
            </p>
          </div>
        </div>
      </div>

      {/* Sociedad tabs */}
      <div className="mb-4">
        <Tabs value={nifSel || ""} onValueChange={setNifSel}>
          <TabsList className="rounded-none bg-slate-100 p-1">
            {sociedades.map((s) => (
              <TabsTrigger
                key={s.nif_titular}
                value={s.nif_titular}
                className="rounded-none data-[state=active]:bg-slate-900 data-[state=active]:text-white text-xs px-4"
                data-testid={`cuadro-tab-${s.nif_titular}`}
              >
                {s.nombre_titular || s.nif_titular}
                <span className="ml-2 text-[10px] opacity-70">
                  ({s.nif_titular})
                </span>
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>

      {/* Filtros */}
      <div className="border border-slate-200 bg-slate-50/40 p-4 mb-4 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <span className="text-xs uppercase tracking-wider text-slate-600">
            Ejercicio
          </span>
          <Select value={ejercicioSel} onValueChange={setEjercicioSel}>
            <SelectTrigger
              className="rounded-none h-8 w-[140px] text-xs"
              data-testid="cuadro-filter-ejercicio"
            >
              <SelectValue placeholder="Selecciona" />
            </SelectTrigger>
            <SelectContent>
              {ejercicios.map((e) => (
                <SelectItem key={e} value={e}>
                  {e}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs uppercase tracking-wider text-slate-600">
            Mes
          </span>
          <Select value={periodoSel} onValueChange={setPeriodoSel}>
            <SelectTrigger
              className="rounded-none h-8 w-[180px] text-xs"
              data-testid="cuadro-filter-periodo"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">Todo el año</SelectItem>
              {periodos.map((p) => (
                <SelectItem key={p} value={p}>
                  {p} — {MONTH_LABELS[p] || p}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex-1" />
        <Button
          variant="outline"
          size="sm"
          className="rounded-none"
          onClick={load}
          disabled={loading}
          data-testid="cuadro-refresh"
        >
          {loading ? (
            <Loader2 className="h-4 w-4 mr-2 animate-spin" />
          ) : (
            <RefreshCw className="h-4 w-4 mr-2" />
          )}
          Recargar
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="rounded-none"
          onClick={exportCsv}
          disabled={!data || loading}
          data-testid="cuadro-export"
        >
          <Download className="h-4 w-4 mr-2" />
          Exportar cuadro
        </Button>
      </div>

      {/* Tabla */}
      {loading && !data ? (
        <div className="border border-slate-200 p-8 flex flex-col items-center gap-3 text-sm text-slate-500">
          <Loader2 className="h-6 w-6 animate-spin" />
          Calculando cuadro mensual (puede tardar hasta 60s en frío)…
        </div>
      ) : !data || rows.length === 0 ? (
        <div className="border border-slate-200 p-8 text-center text-sm text-slate-500">
          {ejercicioSel
            ? "No hay datos para la sociedad / ejercicio seleccionados."
            : "Selecciona una sociedad y un ejercicio."}
        </div>
      ) : (
        <div
          className="border border-slate-200 overflow-x-auto"
          data-testid="cuadro-table-wrapper"
        >
          <Table>
            <TableHeader>
              <TableRow className="bg-slate-900 hover:bg-slate-900">
                <TableHead className="text-[11px] uppercase tracking-wider text-white w-8" />
                <TableHead className="text-[11px] uppercase tracking-wider text-white">
                  Periodo
                </TableHead>
                <TableHead className="text-[11px] uppercase tracking-wider text-white">
                  Tipo
                </TableHead>
                <TableHead className="text-[11px] uppercase tracking-wider text-white text-right bg-slate-800">
                  SII · Base
                </TableHead>
                <TableHead className="text-[11px] uppercase tracking-wider text-white text-right bg-slate-800">
                  SII · Cuota
                </TableHead>
                <TableHead className="text-[11px] uppercase tracking-wider text-white text-right bg-slate-800">
                  SII · Nº
                </TableHead>
                {origenes.map((o) => (
                  <Fragment key={`hdr-${o}`}>
                    <TableHead
                      className="text-[11px] uppercase tracking-wider text-white text-right"
                    >
                      {labelOrigenComercial(o)} · Base
                    </TableHead>
                    <TableHead
                      className="text-[11px] uppercase tracking-wider text-white text-right"
                    >
                      {labelOrigenComercial(o)} · Cuota
                    </TableHead>
                    <TableHead
                      className="text-[11px] uppercase tracking-wider text-white text-right"
                    >
                      {labelOrigenComercial(o)} · Nº
                    </TableHead>
                    <TableHead
                      className="text-[11px] uppercase tracking-wider text-white text-right bg-slate-700"
                    >
                      Δ Base
                    </TableHead>
                    <TableHead
                      className="text-[11px] uppercase tracking-wider text-white text-right bg-slate-700"
                    >
                      Δ Cuota
                    </TableHead>
                    <TableHead
                      className="text-[11px] uppercase tracking-wider text-white text-right bg-slate-700"
                    >
                      Δ Nº
                    </TableHead>
                    <TableHead
                      className="text-[11px] uppercase tracking-wider text-white text-right bg-emerald-900/40"
                    >
                      % Base
                    </TableHead>
                    <TableHead
                      className="text-[11px] uppercase tracking-wider text-white text-right bg-emerald-900/40"
                    >
                      % Cuota
                    </TableHead>
                    <TableHead
                      className="text-[11px] uppercase tracking-wider text-white text-right bg-emerald-900/40"
                    >
                      % Nº
                    </TableHead>
                  </Fragment>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r) => {
                const key = `${r.periodo}|${r.tipo_factura}`;
                const isOpen = !!expanded[key];
                return (
                  <Fragment key={key}>
                    <TableRow
                      className="cursor-pointer hover:bg-slate-50"
                      onClick={() => toggle(key)}
                      data-testid={`cuadro-row-${r.periodo}-${r.tipo_factura}`}
                    >
                      <TableCell className="w-8 text-slate-500">
                        {isOpen ? (
                          <ChevronDown className="h-4 w-4" />
                        ) : (
                          <ChevronRight className="h-4 w-4" />
                        )}
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {r.periodo}
                      </TableCell>
                      <TableCell className="text-xs">
                        <div className="font-mono font-semibold">
                          {r.tipo_factura === "_sin_clasificar"
                            ? "—"
                            : r.tipo_factura}
                        </div>
                        <div className="text-[10px] text-slate-500">
                          {TIPO_LABEL[r.tipo_factura] || ""}
                        </div>
                      </TableCell>
                      <TableCell className="font-mono text-xs tabular-nums text-right bg-slate-50">
                        {fmtEur(r.sii?.base)}
                      </TableCell>
                      <TableCell className="font-mono text-xs tabular-nums text-right bg-slate-50">
                        {fmtEur(r.sii?.cuota)}
                      </TableCell>
                      <TableCell className="font-mono text-xs tabular-nums text-right bg-slate-50">
                        {fmtInt(r.sii?.n)}
                      </TableCell>
                      {origenes.map((o) => {
                        const c = r.comercial_por_origen?.[o] || {};
                        const d = r.delta_por_origen?.[o] || {};
                        const p = r.pct_conciliacion_por_origen?.[o] || {};
                        return (
                          <Fragment key={`${key}-${o}`}>
                            <TableCell
                              className="font-mono text-xs tabular-nums text-right"
                            >
                              {fmtEur(c.base)}
                            </TableCell>
                            <TableCell
                              className="font-mono text-xs tabular-nums text-right"
                            >
                              {fmtEur(c.cuota)}
                            </TableCell>
                            <TableCell
                              className="font-mono text-xs tabular-nums text-right"
                            >
                              {fmtInt(c.n)}
                            </TableCell>
                            <TableCell
                              className="font-mono text-xs tabular-nums text-right bg-slate-50"
                            >
                              <DeltaCell value={d.base} mode="eur" />
                            </TableCell>
                            <TableCell
                              className="font-mono text-xs tabular-nums text-right bg-slate-50"
                            >
                              <DeltaCell value={d.cuota} mode="eur" />
                            </TableCell>
                            <TableCell
                              className="font-mono text-xs tabular-nums text-right bg-slate-50"
                            >
                              <DeltaCell value={d.n} mode="int" />
                            </TableCell>
                            <TableCell
                              className="text-right bg-emerald-50/40"
                            >
                              <PctCell value={p.base} />
                            </TableCell>
                            <TableCell
                              className="text-right bg-emerald-50/40"
                            >
                              <PctCell value={p.cuota} />
                            </TableCell>
                            <TableCell
                              className="text-right bg-emerald-50/40"
                            >
                              <PctCell value={p.facturas} />
                            </TableCell>
                          </Fragment>
                        );
                      })}
                    </TableRow>
                    {isOpen && (
                      <TableRow className="hover:bg-transparent">
                        <TableCell
                          colSpan={6 + origenes.length * 9}
                          className="p-0"
                        >
                          <DetalleFacturas
                            nifTitular={nifSel}
                            ejercicio={ejercicioSel}
                            periodo={r.periodo}
                            tipoFactura={r.tipo_factura}
                          />
                        </TableCell>
                      </TableRow>
                    )}
                  </Fragment>
                );
              })}
              {totales && (
                <TableRow
                  className="bg-slate-900 hover:bg-slate-900 text-white font-semibold"
                  data-testid="cuadro-row-total"
                >
                  <TableCell />
                  <TableCell className="text-xs uppercase tracking-wider">
                    Total
                  </TableCell>
                  <TableCell />
                  <TableCell className="font-mono text-xs tabular-nums text-right">
                    {fmtEur(totales.sii?.base)}
                  </TableCell>
                  <TableCell className="font-mono text-xs tabular-nums text-right">
                    {fmtEur(totales.sii?.cuota)}
                  </TableCell>
                  <TableCell className="font-mono text-xs tabular-nums text-right">
                    {fmtInt(totales.sii?.n)}
                  </TableCell>
                  {origenes.map((o) => {
                    const c = totales.comercial_por_origen?.[o] || {};
                    const d = totales.delta_por_origen?.[o] || {};
                    const p = totales.pct_conciliacion_por_origen?.[o] || {};
                    return (
                      <Fragment key={`total-${o}`}>
                        <TableCell
                          className="font-mono text-xs tabular-nums text-right"
                        >
                          {fmtEur(c.base)}
                        </TableCell>
                        <TableCell
                          className="font-mono text-xs tabular-nums text-right"
                        >
                          {fmtEur(c.cuota)}
                        </TableCell>
                        <TableCell
                          className="font-mono text-xs tabular-nums text-right"
                        >
                          {fmtInt(c.n)}
                        </TableCell>
                        <TableCell
                          className="font-mono text-xs tabular-nums text-right"
                        >
                          {fmtEur(d.base)}
                        </TableCell>
                        <TableCell
                          className="font-mono text-xs tabular-nums text-right"
                        >
                          {fmtEur(d.cuota)}
                        </TableCell>
                        <TableCell
                          className="font-mono text-xs tabular-nums text-right"
                        >
                          {fmtInt(d.n)}
                        </TableCell>
                        <TableCell
                          className="text-right font-mono text-xs tabular-nums"
                        >
                          {fmtPct(p.base)}
                        </TableCell>
                        <TableCell
                          className="text-right font-mono text-xs tabular-nums"
                        >
                          {fmtPct(p.cuota)}
                        </TableCell>
                        <TableCell
                          className="text-right font-mono text-xs tabular-nums"
                        >
                          {fmtPct(p.facturas)}
                        </TableCell>
                      </Fragment>
                    );
                  })}
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
