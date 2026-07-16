import { useEffect, useState, useMemo } from "react";
import { api, formatApiErrorDetail } from "@/lib/api";
import { labelOrigenComercial } from "@/lib/origenes";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { Loader2, RefreshCw, ArrowDownUp, AlertTriangle, CalendarClock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";

/**
 * Tarjeta-resumen de totales agregados de la Comparativa.
 *
 * Llama a `GET /api/comparativa/totales` con los mismos filtros (ejercicio,
 * periodo, num_serie) que la tabla principal, EXCEPTO `only_diffs` (los
 * totales se calculan sobre toda la masa fiscal por consistencia).
 *
 * Estructura visual:
 *   [ Banner % conciliado ]
 *   [ SII | SAP FI | SIGLO | Σ Comercial ]
 *     - Base imponible / Cuota IVA en cada columna
 *     - Diferencia destacada en la columna Σ Comercial
 */
function formatEUR(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("es-ES", {
    style: "currency",
    currency: "EUR",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

function formatPct(value) {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("es-ES", {
    style: "percent",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

function ColumnaTotales({ titulo, sub, base, cuota, n, ultimaFecha, diff, testId }) {
  const isDiff = diff && (Math.abs(diff.base) > 0.005 || Math.abs(diff.cuota) > 0.005);
  return (
    <div
      className={cn(
        "px-4 py-3 border-r border-slate-200 last:border-r-0 flex flex-col gap-2",
        isDiff && "bg-rose-50/60",
      )}
      data-testid={testId}
    >
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-700">
          {titulo}
        </h3>
        {n !== undefined && (
          <span className="text-[10px] text-slate-500 tabular-nums">
            {(n ?? 0).toLocaleString("es-ES")} fact.
          </span>
        )}
      </div>
      {sub && <p className="text-[10px] text-slate-500 -mt-1">{sub}</p>}
      <div className="space-y-1.5 mt-1">
        <div>
          <div className="text-[10px] uppercase text-slate-500 tracking-wider">
            Base imponible
          </div>
          <div className="font-mono text-sm font-semibold text-slate-900 tabular-nums">
            {formatEUR(base)}
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase text-slate-500 tracking-wider">
            Cuota IVA
          </div>
          <div className="font-mono text-sm font-semibold text-slate-900 tabular-nums">
            {formatEUR(cuota)}
          </div>
        </div>
      </div>
      {ultimaFecha !== undefined && (
        <div className="mt-1 pt-1.5 border-t border-slate-100 flex items-center gap-1.5">
          <CalendarClock className="h-3 w-3 text-slate-400" />
          <span className="text-[10px] uppercase text-slate-500 tracking-wider">
            Última factura
          </span>
          <span className="text-[11px] font-mono text-slate-700 tabular-nums ml-auto">
            {ultimaFecha || "—"}
          </span>
        </div>
      )}
      {isDiff && (
        <div className="mt-1 pt-2 border-t border-rose-200 space-y-0.5">
          <div className="text-[10px] uppercase text-rose-700 tracking-wider flex items-center gap-1">
            <AlertTriangle className="h-2.5 w-2.5" />
            Diferencia vs SII
          </div>
          <div className="font-mono text-xs text-rose-700 tabular-nums">
            base {formatEUR(diff.base)}
          </div>
          <div className="font-mono text-xs text-rose-700 tabular-nums">
            cuota {formatEUR(diff.cuota)}
          </div>
        </div>
      )}
    </div>
  );
}

export default function ResumenTotales({ filtros, refreshKey, enabled = true, initialData = null }) {
  const [data, setData] = useState(initialData);
  const [loading, setLoading] = useState(false);

  // Compacto los filtros relevantes (excluye only_diffs y estado por diseño)
  const params = useMemo(() => {
    const q = new URLSearchParams();
    if (filtros?.ejercicio) q.set("ejercicio", filtros.ejercicio);
    if (filtros?.periodo) q.set("periodo", filtros.periodo);
    if (filtros?.num_serie) q.set("num_serie", filtros.num_serie);
    if (filtros?.nif_titular) q.set("nif_titular", filtros.nif_titular);
    return q.toString();
  }, [filtros?.ejercicio, filtros?.periodo, filtros?.num_serie, filtros?.nif_titular]);

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await api.get(
        `/comparativa/totales${params ? `?${params}` : ""}`,
      );
      setData(data);
    } catch (e) {
      toast.error(
        formatApiErrorDetail(e?.response?.data?.detail) ||
          "Error cargando totales",
      );
    } finally {
      setLoading(false);
    }
  };

  // Cuando el padre nos pasa `initialData` (viene del bundle), NO hacemos
  // fetch: nos limitamos a reflejar los datos que ya vienen precargados.
  // Sin esto duplicaríamos peticiones al backend cada vez que cambia un
  // filtro y contribuiríamos a la saturación que provocaba 502.
  useEffect(() => {
    if (initialData !== null) {
      setData(initialData);
      return;
    }
    if (!enabled) return;
    load();
  }, [params, refreshKey, enabled, initialData]);

  const sii = data?.sii;
  const total = data?.comercial_total;
  const porOrigen = data?.comercial_por_origen || {};
  const diff = data?.diferencias;

  // Construye el orden de columnas dinámico: siempre SAP primero si existe,
  // luego SIGLO, luego el resto alfabético.
  const origenesOrden = useMemo(() => {
    const keys = Object.keys(porOrigen);
    const orden = ["SAP", "SIGLO"];
    return [
      ...orden.filter((k) => keys.includes(k)),
      ...keys.filter((k) => !orden.includes(k)).sort(),
    ];
  }, [porOrigen]);

  // Banner % conciliado
  // Diferencia importante: hay 2 métricas de conciliación:
  //   - `pctImporteMin`: mínimo entre pct_base y pct_cuota → % de IMPORTE (€)
  //   - `pctFacturas`  : matches / universo union por num_serie → % de Nº facturas
  // Ambas son válidas pero distintas. El banner las muestra separadas para
  // que el usuario no confunda "93 % conciliado en €" con "93 % en nº facturas".
  const pctBase = diff?.pct_conciliado_base;
  const pctCuota = diff?.pct_conciliado_cuota;
  const pctImporteMin = [pctBase, pctCuota]
    .filter((v) => v !== null && v !== undefined)
    .reduce((acc, v) => (acc === null ? v : Math.min(acc, v)), null);
  const pctFacturas = diff?.pct_conciliado_facturas;
  const matchesN = diff?.matches_num_serie;
  const universoN = diff?.universo_num_serie;

  const conciliado100 =
    pctBase === 1 && pctCuota === 1 &&
    pctFacturas === 1 &&
    Math.abs(diff?.base ?? 0) < 0.005 &&
    Math.abs(diff?.cuota ?? 0) < 0.005;

  return (
    <Card className="overflow-hidden" data-testid="resumen-totales">
      <CardHeader className="bg-gradient-to-r from-slate-50 to-white pb-3">
        <div className="flex items-center justify-between gap-3">
          <CardTitle className="text-base flex items-center gap-2">
            <ArrowDownUp className="h-4 w-4 text-slate-500" />
            Resumen de conciliación
          </CardTitle>
          <Button
            variant="ghost"
            size="sm"
            onClick={load}
            disabled={loading}
            data-testid="btn-totales-recargar"
          >
            {loading ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        {/* Banner KPI */}
        <div
          className={cn(
            "px-4 py-3 border-b border-slate-200 flex items-center justify-between gap-4",
            conciliado100 ? "bg-emerald-50/60" : "bg-amber-50/60",
          )}
          data-testid="totales-kpi-banner"
        >
          <div className="flex items-center gap-3 flex-wrap">
            {conciliado100 ? (
              <Badge className="bg-emerald-600 hover:bg-emerald-600 text-white">
                100% conciliado
              </Badge>
            ) : (
              <>
                <Badge
                  className="bg-amber-600 hover:bg-amber-600 text-white"
                  data-testid="badge-conciliado-importe"
                  title="Porcentaje de conciliación en IMPORTE (€) — min(base, cuota)"
                >
                  {pctImporteMin !== null ? formatPct(pctImporteMin) : "—"} en €
                </Badge>
                <Badge
                  variant="outline"
                  className="border-amber-300 text-amber-800 bg-amber-50"
                  data-testid="badge-conciliado-facturas"
                  title="Porcentaje de facturas conciliadas (matches / unión de num_serie)"
                >
                  {pctFacturas !== null && pctFacturas !== undefined
                    ? formatPct(pctFacturas)
                    : "—"}{" "}
                  en nº facturas
                </Badge>
              </>
            )}
            <div className="text-xs text-slate-600">
              {sii?.n_facturas?.toLocaleString("es-ES") || 0} facturas SII ·{" "}
              {total?.n_facturas?.toLocaleString("es-ES") || 0} comerciales
              {matchesN !== null && matchesN !== undefined && universoN ? (
                <>
                  {" · "}
                  <span className="text-slate-500">
                    {matchesN.toLocaleString("es-ES")} con contraparte de{" "}
                    {universoN.toLocaleString("es-ES")}
                  </span>
                </>
              ) : null}
            </div>
          </div>
          {!conciliado100 && diff && (
            <div className="flex items-center gap-4 text-xs">
              <span className="text-slate-500">Δ Base</span>
              <span className="font-mono font-semibold text-rose-700 tabular-nums">
                {formatEUR(diff.base)}
              </span>
              <span className="text-slate-300">·</span>
              <span className="text-slate-500">Δ Cuota</span>
              <span className="font-mono font-semibold text-rose-700 tabular-nums">
                {formatEUR(diff.cuota)}
              </span>
            </div>
          )}
        </div>

        {/* Grid de columnas */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4">
          <ColumnaTotales
            titulo="SII"
            sub="Datos AEAT"
            base={sii?.base}
            cuota={sii?.cuota}
            n={sii?.n_facturas}
            ultimaFecha={sii?.ultima_fecha_expedicion}
            testId="totales-sii"
          />
          {origenesOrden.map((origen) => {
            const o = porOrigen[origen];
            return (
              <ColumnaTotales
                key={origen}
                titulo={labelOrigenComercial(origen)}
                sub={o.invertido ? "Signo invertido" : "Signo directo"}
                base={o.base}
                cuota={o.cuota}
                n={o.n_facturas}
                ultimaFecha={o.ultima_fecha_expedicion}
                testId={`totales-origen-${origen.toLowerCase()}`}
              />
            );
          })}
          {/* Si no hay orígenes, mantenemos la columna Σ Comercial sola */}
          <ColumnaTotales
            titulo="Σ Comercial"
            sub="Suma orígenes"
            base={total?.base}
            cuota={total?.cuota}
            n={total?.n_facturas}
            diff={diff}
            testId="totales-comercial-suma"
          />
        </div>
      </CardContent>
    </Card>
  );
}
