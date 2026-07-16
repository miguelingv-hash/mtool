import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { api, API } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  Download,
  RefreshCw,
  CheckCircle2,
  AlertTriangle,
  CircleHelp,
  Eye,
  Loader2,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  ArrowUp,
  ArrowDown,
  ArrowUpDown,
} from "lucide-react";
import { toast } from "sonner";
import { labelOrigenComercial } from "@/lib/origenes";
import ResumenTotales from "@/components/ResumenTotales";

const ESTADO_PILL = {
  coincide: { label: "Coincide", cls: "pill-success", Icon: CheckCircle2 },
  discrepancia: {
    label: "Discrepancia",
    cls: "pill-error",
    Icon: AlertTriangle,
  },
  solo_sii: { label: "Solo SII", cls: "pill-warning", Icon: CircleHelp },
  solo_comercial: {
    label: "Solo comercial",
    cls: "pill-warning",
    Icon: CircleHelp,
  },
};

const PERIODOS = Array.from({ length: 12 }, (_, i) => String(i + 1).padStart(2, "0"));

// Mapa de Quarter → meses (formato AEAT con padding "01".."12").
// Se usa para expandir la selección de trimestres a una lista de periodos
// que el backend entiende vía `$in`.
const QUARTERS_TO_MONTHS = {
  Q1: ["01", "02", "03"],
  Q2: ["04", "05", "06"],
  Q3: ["07", "08", "09"],
  Q4: ["10", "11", "12"],
};
const QUARTERS = ["Q1", "Q2", "Q3", "Q4"];
const MONTH_LABELS = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"];

// Etiqueta visible del origen comercial: ver helper en @/lib/origenes.

function DetalleIvaTable({ label, lineas, testIdSuffix }) {
  // Orden por tipo IVA descendente (21, 10, 4, ...) con exentas/sin tipo al final.
  // Así las dos tablas (SII y COMERCIAL) muestran las mismas categorías a la
  // misma altura cuando hay tramos comparables.
  const lineasOrdenadas = [...lineas].sort((a, b) => {
    const ta = a.tipo_impositivo;
    const tb = b.tipo_impositivo;
    if (ta != null && tb != null) return tb - ta; // descendente
    if (ta != null) return -1; // primero los que tienen tipo
    if (tb != null) return 1;
    // Ambos sin tipo: por causa_exencion ascendente (E1, E2, ...)
    return String(a.causa_exencion || "").localeCompare(String(b.causa_exencion || ""));
  });
  const totalBase = lineasOrdenadas.reduce((a, li) => a + (li.base_imponible || 0), 0);
  const totalCuota = lineasOrdenadas.reduce((a, li) => a + (li.cuota_repercutida || 0), 0);
  return (
    <div
      className="border border-slate-200"
      data-testid={`detalle-iva-${testIdSuffix}`}
    >
      <div className="px-4 py-2 bg-slate-50 border-b border-slate-200 flex items-center justify-between">
        <div className="text-xs uppercase tracking-wider text-slate-600 font-semibold">
          {label}
        </div>
        <div className="text-[11px] text-slate-500">
          {lineas.length} línea{lineas.length === 1 ? "" : "s"}
          <span className="ml-3 text-rose-600">●</span>{" "}
          <span className="text-slate-500">
            desvío de redondeo &gt; 0,01&nbsp;€
          </span>
        </div>
      </div>
      <Table>
        <TableHeader>
          <TableRow className="bg-white hover:bg-white">
            <TableHead className="text-xs uppercase tracking-wider">Origen</TableHead>
            <TableHead className="text-xs uppercase tracking-wider text-right">Tipo (%)</TableHead>
            <TableHead className="text-xs uppercase tracking-wider text-right">Base imponible</TableHead>
            <TableHead className="text-xs uppercase tracking-wider text-right">Cuota repercutida</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {lineasOrdenadas.map((li, idx) => {
            const esperada =
              li.base_imponible != null && li.tipo_impositivo != null
                ? (li.base_imponible * li.tipo_impositivo) / 100
                : null;
            const mismatch =
              esperada != null &&
              li.cuota_repercutida != null &&
              Math.abs(esperada - li.cuota_repercutida) > 0.01;
            return (
              <TableRow
                key={idx}
                data-testid={`detalle-iva-row-${testIdSuffix}-${idx}`}
                className={mismatch ? "bg-rose-50/60" : ""}
                title={
                  mismatch
                    ? `Redondeo: cuota esperada ${esperada.toFixed(2)} €, recibida ${li.cuota_repercutida.toFixed(2)} € (Δ ${(li.cuota_repercutida - esperada).toFixed(2)} €)`
                    : undefined
                }
              >
                <TableCell className="text-xs text-slate-600">
                  {labelOrigenComercial(li.origen) || "—"}
                  {li.causa_exencion && (
                    <span
                      className="ml-1 text-[10px] uppercase tracking-wider bg-amber-100 text-amber-800 px-1.5 py-0.5"
                      title="Operación exenta"
                    >
                      Exenta {li.causa_exencion}
                    </span>
                  )}
                </TableCell>
                <TableCell className="font-mono text-xs tabular-nums text-right">
                  {li.tipo_impositivo != null ? li.tipo_impositivo.toFixed(2) : "—"}
                </TableCell>
                <TableCell className="font-mono text-xs tabular-nums text-right">
                  {li.base_imponible != null ? li.base_imponible.toFixed(2) : "—"}
                </TableCell>
                <TableCell className="font-mono text-xs tabular-nums text-right">
                  {li.cuota_repercutida != null ? li.cuota_repercutida.toFixed(2) : "—"}
                  {mismatch && (
                    <span className="text-rose-600 ml-1" data-testid={`iva-mismatch-${testIdSuffix}-${idx}`}>●</span>
                  )}
                </TableCell>
              </TableRow>
            );
          })}
          {lineasOrdenadas.length > 1 && (
            <TableRow className="bg-slate-50 font-semibold">
              <TableCell className="text-xs uppercase tracking-wider text-slate-700">Totales</TableCell>
              <TableCell className="font-mono text-xs text-right">—</TableCell>
              <TableCell className="font-mono text-xs tabular-nums text-right">{totalBase.toFixed(2)}</TableCell>
              <TableCell className="font-mono text-xs tabular-nums text-right">{totalCuota.toFixed(2)}</TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </div>
  );
}

const tieneIvaIncorrecto = (row) => {
  const lineas = row?.sii?.detalle_iva;
  if (!Array.isArray(lineas) || lineas.length === 0) return false;
  return lineas.some((li) => {
    if (li.base_imponible == null || li.tipo_impositivo == null) return false;
    if (li.cuota_repercutida == null) return false;
    const esperada = (li.base_imponible * li.tipo_impositivo) / 100;
    return Math.abs(esperada - li.cuota_repercutida) > 0.01;
  });
};

/**
 * Comparación de líneas IVA emparejadas por tipo impositivo / causa de exención.
 * Recibe el array `diferencias.detalle_iva` que devuelve el backend.
 * Cada elemento tiene: {key:{tipo?,causa_exencion?}, sii:{base,cuota}, comercial:{base,cuota}, diff:bool}
 */
function LineasIvaCompare({ tramos }) {
  if (!Array.isArray(tramos) || tramos.length === 0) return null;
  const fmt = (v) => {
    if (v == null) return "—";
    // Normaliza -0 a 0 (IEEE-754 tras invertir signo del comercial)
    const n = Number(v) === 0 ? 0 : Number(v);
    return n.toFixed(2);
  };
  const labelKey = (k) => {
    if (!k) return "—";
    if (k.tipo != null) return `Tipo ${Number(k.tipo).toFixed(2)} %`;
    if (k.causa_exencion) return `Exenta · ${k.causa_exencion}`;
    return "Otro";
  };
  return (
    <div
      className="border border-slate-200 mt-6"
      data-testid="lineas-iva-compare"
    >
      <div className="px-4 py-2 bg-slate-900 text-white border-b border-slate-200 flex items-center justify-between">
        <div className="text-xs uppercase tracking-wider font-semibold">
          Comparación de líneas IVA
        </div>
        <div className="text-[11px] text-slate-300">
          Emparejadas por tipo / causa de exención
        </div>
      </div>
      <Table>
        <TableHeader>
          <TableRow className="bg-slate-50 hover:bg-slate-50">
            <TableHead className="text-xs uppercase tracking-wider">Tramo</TableHead>
            <TableHead className="text-xs uppercase tracking-wider text-right">SII · Base</TableHead>
            <TableHead className="text-xs uppercase tracking-wider text-right">SII · Cuota</TableHead>
            <TableHead className="text-xs uppercase tracking-wider text-right">Comercial · Base</TableHead>
            <TableHead className="text-xs uppercase tracking-wider text-right">Comercial · Cuota</TableHead>
            <TableHead className="text-xs uppercase tracking-wider w-24">Estado</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {tramos.map((t, idx) => {
            const sii = t.sii;
            const com = t.comercial;
            return (
              <TableRow
                key={idx}
                className={t.diff ? "bg-rose-50/60" : "bg-emerald-50/40"}
                data-testid={`tramo-row-${idx}`}
              >
                <TableCell className="text-xs text-slate-700 font-medium">
                  {labelKey(t.key)}
                </TableCell>
                <TableCell className="font-mono text-xs tabular-nums text-right">
                  {sii ? fmt(sii.base_imponible) : <span className="text-slate-400 italic">—</span>}
                </TableCell>
                <TableCell className="font-mono text-xs tabular-nums text-right">
                  {sii ? fmt(sii.cuota_repercutida) : <span className="text-slate-400 italic">—</span>}
                </TableCell>
                <TableCell className="font-mono text-xs tabular-nums text-right">
                  {com ? fmt(com.base_imponible) : <span className="text-slate-400 italic">solo SII</span>}
                </TableCell>
                <TableCell className="font-mono text-xs tabular-nums text-right">
                  {com ? fmt(com.cuota_repercutida) : <span className="text-slate-400 italic">—</span>}
                </TableCell>
                <TableCell className="text-xs">
                  {t.diff ? (
                    <span className="text-rose-700 font-semibold">Discrepancia</span>
                  ) : (
                    <span className="text-emerald-700 font-semibold">Coincide</span>
                  )}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

function SortableHead({ label, sortKey, sortBy, sortDir, onClick, align }) {
  const active = sortBy === sortKey;
  const Icon = !active ? ArrowUpDown : sortDir === "desc" ? ArrowDown : ArrowUp;
  return (
    <TableHead
      className={`text-xs uppercase tracking-wider cursor-pointer select-none hover:bg-slate-100 transition-colors ${
        align === "right" ? "text-right" : ""
      }`}
      onClick={() => onClick(sortKey)}
      data-testid={`sort-${sortKey}`}
    >
      <span
        className={`inline-flex items-center gap-1 ${
          align === "right" ? "justify-end" : ""
        } ${active ? "text-slate-900" : "text-slate-600"}`}
      >
        {label}
        <Icon className={`h-3 w-3 ${active ? "opacity-100" : "opacity-40"}`} />
      </span>
    </TableHead>
  );
}

export default function Comparativa() {
  const location = useLocation();
  const initialNumSerie = new URLSearchParams(location.search).get("num_serie") || "";
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [onlyIvaErr, setOnlyIvaErr] = useState(false);
  const [filtroEjercicio, setFiltroEjercicio] = useState("__all__");
  // Filtro de periodo en dos líneas mutuamente excluyentes:
  //   `quartersSel`: subset de {"Q1","Q2","Q3","Q4"} (multi)
  //   `monthsSel`  : subset de {"01"…"12"}            (multi)
  // Si una está poblada, la otra se borra al togglear. El valor que se envía
  // al backend en el parámetro `periodo` es siempre una lista CSV de meses
  // (expandiendo los trimestres a sus tres meses correspondientes).
  const [quartersSel, setQuartersSel] = useState([]);
  const [monthsSel, setMonthsSel] = useState([]);
  const [filtroNumSerie, setFiltroNumSerie] = useState(initialNumSerie);
  const [filtroNumSerieDebounced, setFiltroNumSerieDebounced] = useState(initialNumSerie);
  const [filtroEstado, setFiltroEstado] = useState(initialNumSerie ? "all" : "diffs"); // diffs|all|coincide|discrepancia|solo_sii|solo_comercial
  // Toggle de "Sociedad" (NIF titular). Se rellena dinámicamente al montar:
  // si sólo hay 1 NIF en BD se autoselecciona; si hay 2+ el usuario alterna.
  // Filtro por NIF titular (sociedad). Default `null` = "aún no decidido";
  // el useEffect de /nifs-titulares decidirá al montar si autoselecciona una
  // sociedad (cuando sólo hay una en BD) o pasa a `__all__` explícito. Los
  // otros useEffects que disparan queries pesadas SE SALTAN mientras esté a
  // null para no lanzar la query monstruo "sin filtro" y a los 100ms hacer
  // otra idéntica con filtro — ese doble disparo saturaba el ingress y
  // provocaba 502 en la 1ª carga de la Comparativa.
  const [filtroNif, setFiltroNif] = useState(null);
  const [nifsDisponibles, setNifsDisponibles] = useState([]);
  const [sociedadesMap, setSociedadesMap] = useState({}); // {nif: nombre}
  const [comercialSinNif, setComercialSinNif] = useState(0);
  const [exporting, setExporting] = useState(false);
  const [periodosDisponibles, setPeriodosDisponibles] = useState({
    ejercicios: [],
    periodos: [],
  });
  const [resumenOrigenes, setResumenOrigenes] = useState([]);
  // Totales del bundle — poblado por `load()` en cada refresco. Se pasa al
  // componente ResumenTotales como prop para evitar que él haga su propio
  // fetch (que multiplicaba las peticiones y provocaba 502 antes).
  const [bundleTotales, setBundleTotales] = useState(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [loading, setLoading] = useState(false);
  const [sortBy, setSortBy] = useState(null);   // 'num_serie_factura' | 'estado' | 'importe_sii' | 'importe_comercial' | 'fecha_expedicion' | null
  const [sortDir, setSortDir] = useState("desc"); // 'asc' | 'desc'
  const [detail, setDetail] = useState(null);
  // Contador que se incrementa cada vez que se recargan los items de la
  // tabla. Lo usamos como prop refreshKey en ResumenTotales para que el
  // resumen se vuelva a calcular tras imports/recargas.
  const [refreshTick, setRefreshTick] = useState(0);

  // NOTA: el estado y los handlers de "Consulta mensual SII" y de
  // "Importar fichero comercial" se movieron a /carga-datos (componentes
  // CargaMensualSII y CargaComercialCSV). Esta pantalla queda enfocada
  // únicamente en mostrar la comparativa.

  // Valor efectivo del filtro de periodo enviado al backend (CSV de meses):
  //   - si hay meses seleccionados → ["01","03"] → "01,03"
  //   - si hay quarters seleccionados → se expanden y se ordenan
  //   - si no hay nada → "" (no se filtra por periodo)
  const effectivePeriodo = (() => {
    if (monthsSel.length > 0) {
      return [...monthsSel].sort().join(",");
    }
    if (quartersSel.length > 0) {
      const months = new Set();
      quartersSel.forEach((q) => QUARTERS_TO_MONTHS[q].forEach((m) => months.add(m)));
      return [...months].sort().join(",");
    }
    return "";
  })();

  // Toggle handlers: marcar un quarter limpia meses, marcar un mes limpia
  // quarters. Cumplen la regla de exclusividad mutua entre las dos líneas.
  const toggleQuarter = (q) => {
    setMonthsSel([]);
    setQuartersSel((prev) =>
      prev.includes(q) ? prev.filter((x) => x !== q) : [...prev, q],
    );
  };
  const toggleMonth = (m) => {
    setQuartersSel([]);
    setMonthsSel((prev) =>
      prev.includes(m) ? prev.filter((x) => x !== m) : [...prev, m],
    );
  };
  const limpiarPeriodos = () => {
    setQuartersSel([]);
    setMonthsSel([]);
  };

  // Ref para cancelar requests obsoletos cuando cambia el filtro.
  // Sin esto, un load() de la sociedad A que tarda 60s puede sobreescribir
  // el resultado de la sociedad B que el usuario acaba de seleccionar
  // (race condition). El AbortController garantiza que sólo el último
  // request en vuelo llega a modificar el estado.
  const abortRef = useRef(null);

  const load = async () => {
    // Cancela request en vuelo si lo hay (evita race condition al
    // cambiar de sociedad rápidamente).
    if (abortRef.current) {
      abortRef.current.abort();
    }
    const controller = new AbortController();
    abortRef.current = controller;

    setLoading(true);
    try {
      const params = {
        skip: (page - 1) * pageSize,
        limit: pageSize,
      };
      // Mapeo del selector "Mostrar" a los parámetros del backend
      if (filtroEstado === "diffs") {
        params.only_diffs = true;
      } else if (filtroEstado === "all") {
        params.only_diffs = false;
      } else {
        // estado específico: coincide / discrepancia / solo_sii / solo_comercial
        params.only_diffs = false;
        params.estado = filtroEstado;
      }
      if (filtroEjercicio !== "__all__") params.ejercicio = filtroEjercicio;
      if (effectivePeriodo) params.periodo = effectivePeriodo;
      if (filtroNumSerieDebounced.trim()) params.num_serie = filtroNumSerieDebounced.trim();
      if (filtroNif !== "__all__") params.nif_titular = filtroNif;
      if (sortBy) {
        params.sort_by = sortBy;
        params.sort_dir = sortDir;
      }
      // Endpoint agregado: 1 sola petición HTTP devuelve list + totales +
      // resumen_origenes. Sustituye a 3 requests paralelas que saturaban el
      // ingress (502) al filtrar por mes con dataset grande (1M+ docs).
      let bundle;
      try {
        const resp = await api.get("/comparativa/bundle", {
          params,
          signal: controller.signal,
        });
        bundle = resp.data;
      } catch (err) {
        // Si el request fue cancelado por AbortController, salimos en
        // silencio: hay otro load() en curso que actualizará el estado.
        if (
          err?.name === "CanceledError" ||
          err?.name === "AbortError" ||
          err?.code === "ERR_CANCELED"
        ) {
          return;
        }
        const status = err?.response?.status;
        const detail = err?.response?.data?.detail || "";
        const isTimeout =
          err?.code === "ECONNABORTED" ||
          (typeof err?.message === "string" && err.message.includes("timeout"));
        // Backend explícito de "dataset masivo": informamos y sugerimos.
        // NO cambiamos automáticamente ningún filtro — respeta la elección
        // del usuario. Sólo cambiamos si SU filtro es `__all__` (implícito).
        if (
          status === 400 &&
          typeof detail === "string" &&
          detail.includes("Dataset demasiado grande")
        ) {
          toast.error("El dataset es demasiado grande para esta consulta", {
            description:
              "Selecciona una sociedad concreta o filtra por ejercicio/período para acotar el ámbito.",
            duration: 10000,
          });
          return;
        }
        // Timeouts / 502 / 504: mensaje claro. NO auto-cambiamos el
        // filtro del usuario (era un bug: podía sobreescribir la sociedad
        // que el usuario había seleccionado con la primera alfabética).
        if (status === 502 || status === 504 || isTimeout) {
          const isGlobal = filtroNif === "__all__";
          toast.error(
            isGlobal
              ? "La consulta global es demasiado pesada"
              : "La consulta está tardando demasiado (>60s)",
            {
              description: isGlobal
                ? "Selecciona una sociedad en el desplegable de arriba para acotar la consulta."
                : "La 1ª carga precalienta la caché (30-60s). Reintenta la operación en unos segundos.",
              duration: 12000,
            },
          );
          return;
        }
        throw err;
      }
      // Nos aseguramos de que este resultado corresponda al filtro actual
      // (si un load posterior canceló este por otra ruta, controller.signal
      // ya estará aborted). Sanity check por si acaso.
      if (controller.signal.aborted) return;
      const data = bundle.list || {};
      setItems(data.items);
      setTotal(data.total);
      // Publicamos también los totales y el resumen para que los componentes
      // hijos (ResumenTotales, ResumenPorOrigen) los consuman sin hacer sus
      // propias peticiones.
      setBundleTotales(bundle.totales || null);
      setResumenOrigenes(bundle.resumen_origenes?.items || []);
      setRefreshTick((t) => t + 1);
    } finally {
      // Sólo apagamos el spinner si el controller sigue activo (no
      // cancelado por otro load posterior).
      if (!controller.signal.aborted) {
        setLoading(false);
      }
    }
  };

  useEffect(() => {
    // Aún no sabemos qué sociedad usar: esperamos al fetch de /nifs-titulares.
    if (filtroNif === null) return;
    api
      .get("/comparativa/periodos", {
        params: filtroNif !== "__all__" ? { nif_titular: filtroNif } : {},
      })
      .then((r) => setPeriodosDisponibles(r.data))
      .catch(() => {});
  }, [filtroNif]);

  // Carga la lista de NIFs titulares disponibles (sociedades) para construir
  // el selector de primer nivel. Se llama una vez al montar.
  useEffect(() => {
    api
      .get("/comparativa/nifs-titulares")
      .then((r) => {
        const lst = r.data?.nifs_titulares || [];
        const sociedades = r.data?.sociedades || [];
        setNifsDisponibles(lst);
        const mp = {};
        sociedades.forEach((s) => {
          if (s.nif_titular) mp[s.nif_titular] = s.nombre_titular || "";
        });
        setSociedadesMap(mp);
        setComercialSinNif(r.data?.comercial_sin_nif || 0);
        // Autoselección: elegimos la sociedad con MENOR volumen para que
        // la primera carga sea la más rápida (cache-miss ~5-10s en vez
        // de ~50s). El usuario puede cambiar a otra al momento.
        // Volumen se estima como max(n_comercial, n_sii) para cubrir el
        // peor caso del $lookup.
        //   - 1 NIF → ese
        //   - varios → menor volumen
        //   - ninguno → "__all__" (fallback benigno; muestra pantalla vacía)
        if (lst.length === 1) {
          setFiltroNif(lst[0]);
        } else if (lst.length > 1) {
          const enriched = sociedades
            .filter((s) => s.nif_titular)
            .map((s) => ({
              nif: s.nif_titular,
              size: Math.max(s.n_comercial || 0, s.n_sii || 0),
            }));
          if (enriched.length > 0) {
            enriched.sort((a, b) => a.size - b.size);
            setFiltroNif(enriched[0].nif);
          } else {
            setFiltroNif([...lst].sort()[0]);
          }
        } else {
          setFiltroNif("__all__");
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (filtroNif === null) return;  // esperamos a que se decida el NIF
    load();
  }, [filtroEstado, page, pageSize, filtroEjercicio, effectivePeriodo, filtroNumSerieDebounced, sortBy, sortDir, filtroNif]);

  // Reset paginación al cambiar filtros
  useEffect(() => {
    setPage(1);
  }, [filtroEstado, filtroEjercicio, effectivePeriodo, pageSize, filtroNumSerieDebounced, filtroNif]);

  // Debounce 300ms para el filtro de num_serie (evita request por keystroke)
  useEffect(() => {
    const t = setTimeout(() => setFiltroNumSerieDebounced(filtroNumSerie), 300);
    return () => clearTimeout(t);
  }, [filtroNumSerie]);

  // El recovery de jobs activos al montar y el polling de /jobs/{id} se han
  // movido al componente CargaMensualSII (vive en /carga-datos).

  const exportar = async () => {
    const params = {};
    if (filtroEstado === "diffs") {
      params.only_diffs = true;
    } else if (filtroEstado === "all") {
      params.only_diffs = false;
    } else {
      params.only_diffs = false;
      params.estado = filtroEstado;
    }
    if (filtroEjercicio !== "__all__") params.ejercicio = filtroEjercicio;
    if (effectivePeriodo) params.periodo = effectivePeriodo;
    if (filtroNumSerieDebounced.trim()) params.num_serie = filtroNumSerieDebounced.trim();
    if (filtroNif !== "__all__") params.nif_titular = filtroNif;

    setExporting(true);
    const toastId = toast.loading("Preparando exportación CSV…", {
      description: "Streaming desde el servidor; puede tardar unos segundos.",
    });
    try {
      const resp = await api.get("/comparativa/export", {
        params,
        responseType: "blob",
        timeout: 1000 * 60 * 10, // 10 minutos por si el dataset es grande
      });
      // Construye filename desde Content-Disposition (si está) o uno genérico
      const cd = resp.headers?.["content-disposition"] || "";
      const m = cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
      const filename = m ? decodeURIComponent(m[1]) : "comparativa.csv";

      const blob = new Blob([resp.data], { type: "text/csv;charset=utf-8" });
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);

      toast.success("Exportación lista", {
        id: toastId,
        description: `${filename} descargado.`,
      });
    } catch (e) {
      toast.error("No se pudo exportar la comparativa", {
        id: toastId,
        description:
          e?.response?.status === 401
            ? "Sesión expirada — vuelve a iniciar sesión."
            : (e?.message || "Error desconocido"),
      });
    } finally {
      setExporting(false);
    }
  };


  const visibleItems = onlyIvaErr ? items.filter(tieneIvaIncorrecto) : items;
  // La ordenación se hace ahora server-side (params sort_by + sort_dir),
  // por lo que `sortedItems` es simplemente la lista visible sin re-ordenar.
  // Mantenemos el alias para no romper el render de la tabla.
  const sortedItems = visibleItems;

  const toggleSort = (key) => {
    if (sortBy === key) {
      // mismo campo → toggle dir; tercer click → quita ordenación
      if (sortDir === "desc") setSortDir("asc");
      else {
        setSortBy(null);
        setSortDir("desc");
      }
    } else {
      setSortBy(key);
      setSortDir("desc"); // primer click siempre descendente (mayor a menor)
    }
  };

  return (
    <div className="px-8 py-8 max-w-[1500px]">
      <div className="mb-8">
        <div className="text-xs uppercase tracking-[0.2em] text-slate-500 mb-2">
          Conciliación
        </div>
        <h1 className="font-display text-4xl font-bold tracking-tight text-slate-900">
          Comparativa SII
        </h1>
        <p className="text-sm text-slate-600 mt-2 max-w-3xl">
          Compara las facturas reportadas al SII con las del sistema comercial.
          Identifica diferencias en importes, fechas, contrapartes o facturas
          que existen sólo en una de las dos fuentes.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
        {/* Las secciones "Consulta mensual SII" y "Importar fichero comercial"
            se han movido a /carga-datos para mantener esta pantalla centrada
            en analítica (Comparativa SII pura). Los componentes
            CargaMensualSII y CargaComercialCSV viven ahora en
            /app/frontend/src/components y se montan dentro de tabs. */}
      </div>

      {/* Dashboard resumen por origen comercial (SAP / SIGLO / desconocido) */}
      {resumenOrigenes.length > 0 && (
        <div
          className="mb-4 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3"
          data-testid="resumen-origenes"
        >
          {resumenOrigenes.map((o) => {
            // Helper: nunca redondear hacia arriba a 100% si no es exacto.
            // Si `num === den` devolvemos 100 limpio; en cualquier otro caso
            // usamos Math.floor con 1 decimal para que 99,96% no aparezca
            // como 100% (lo que era engañoso cuando había 2 discrepancias).
            const pct = (num, den) => {
              if (!den) return 0;
              if (num === den) return 100;
              return Math.floor((num / den) * 1000) / 10;
            };
            // Métrica PRINCIPAL (la que se muestra grande): coincidencia
            // EXACTA sobre el universo comercial completo. Esto refleja la
            // realidad de conciliación: incluye al numerador SÓLO las
            // facturas que (a) tienen contrapartida en SII y (b) cuadran
            // campo a campo. Las discrepancias y las "sólo comercial"
            // bajan el porcentaje — como debe ser.
            const pctConciliacion = pct(o.coincidencias, o.total_facturas);
            const pctConciliacionExact =
              o.coincidencias === o.total_facturas;
            // Métricas secundarias para el desglose:
            const pctMatch = pct(o.matches_sii, o.total_facturas);
            const pctCoincide = pct(o.coincidencias, o.matches_sii);
            const accent =
              o.origen === "SAP"
                ? "border-l-blue-500"
                : o.origen === "SIGLO"
                  ? "border-l-amber-500"
                  : "border-l-slate-400";
            return (
              <div
                key={o.origen}
                className={`border border-slate-200 border-l-4 ${accent} bg-white p-4`}
                data-testid={`resumen-origen-${o.origen}`}
              >
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <span className="text-xs uppercase tracking-wider font-semibold text-slate-700 font-mono">
                      {labelOrigenComercial(o.origen)}
                    </span>
                    <span className="text-xs text-slate-400">comercial</span>
                  </div>
                  <span
                    className="font-mono text-2xl font-light text-slate-900 tabular-nums"
                    data-testid={`resumen-${o.origen}-total`}
                  >
                    {o.total_facturas.toLocaleString("es-ES")}
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                  <div className="text-slate-500">Base imp.</div>
                  <div className="font-mono tabular-nums text-right text-slate-800">
                    {o.base_total.toLocaleString("es-ES", {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 2,
                    })}{" "}
                    €
                  </div>
                  <div className="text-slate-500">Cuota IVA</div>
                  <div className="font-mono tabular-nums text-right text-slate-800">
                    {o.cuota_total.toLocaleString("es-ES", {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 2,
                    })}{" "}
                    €
                  </div>
                </div>
                <div className="mt-3 pt-3 border-t border-slate-100 text-xs space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="text-slate-500">
                      Conciliación exacta
                    </span>
                    <span
                      className={`font-mono tabular-nums ${
                        pctConciliacionExact
                          ? "text-emerald-700 font-semibold"
                          : "text-slate-700"
                      }`}
                      data-testid={`resumen-${o.origen}-conciliacion-pct`}
                    >
                      {pctConciliacion}%
                    </span>
                  </div>
                  <div className="h-1.5 bg-slate-100">
                    <div
                      className={`h-full ${
                        pctConciliacionExact ? "bg-emerald-600" : "bg-slate-700"
                      }`}
                      style={{ width: `${pctConciliacion}%` }}
                    />
                  </div>
                  <div className="flex items-center justify-between pt-1 text-[11px]">
                    <span className="text-green-700">
                      {o.coincidencias.toLocaleString("es-ES")} match
                    </span>
                    <span className="text-red-600">
                      {o.discrepancias.toLocaleString("es-ES")} discrep.
                    </span>
                    <span className="text-amber-600">
                      {o.sin_match_sii.toLocaleString("es-ES")} sólo com.
                    </span>
                  </div>
                  {/* Desglose: dos métricas secundarias claramente etiquetadas
                      para que el usuario entienda por qué la métrica principal
                      es la que es (cuando no llega al 100%). */}
                  {!pctConciliacionExact && (
                    <div
                      className="text-[11px] text-slate-500 pt-1 leading-snug space-y-0.5"
                      data-testid={`resumen-${o.origen}-desglose`}
                    >
                      <div>
                        <span className="font-mono">{pctMatch}%</span> con
                        contrapartida en SII (
                        {o.matches_sii.toLocaleString("es-ES")} /{" "}
                        {o.total_facturas.toLocaleString("es-ES")}).
                      </div>
                      <div>
                        De ésas,{" "}
                        <span className="font-mono">{pctCoincide}%</span>{" "}
                        coinciden campo a campo.
                      </div>
                    </div>
                  )}
                  {pctConciliacionExact && (
                    <div className="text-[11px] text-emerald-700 pt-1">
                      Todas las {o.total_facturas.toLocaleString("es-ES")}{" "}
                      facturas tienen contrapartida exacta en SII.
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Selector de Sociedad (NIF titular) — primer nivel */}
      {nifsDisponibles.length > 0 && (
        <div
          className="border border-slate-200 bg-white px-4 py-3 mb-4 flex items-center flex-wrap gap-3"
          data-testid="nif-titular-selector"
        >
          <div className="text-xs uppercase tracking-[0.18em] text-slate-500 font-semibold">
            Sociedad
          </div>
          <div className="flex items-center flex-wrap gap-1">
            {nifsDisponibles.length > 1 && (
              <button
                type="button"
                onClick={() => setFiltroNif("__all__")}
                data-testid="nif-toggle-all"
                className={`text-xs font-mono px-3 py-1.5 border transition-colors ${
                  filtroNif === "__all__"
                    ? "border-slate-900 bg-slate-900 text-white"
                    : "border-slate-300 bg-white text-slate-700 hover:border-slate-500"
                }`}
              >
                Todas
              </button>
            )}
            {nifsDisponibles.map((nif) => {
              const nombre = sociedadesMap[nif];
              return (
                <button
                  key={nif}
                  type="button"
                  onClick={() => setFiltroNif(nif)}
                  data-testid={`nif-toggle-${nif}`}
                  className={`text-xs px-3 py-1.5 border transition-colors flex items-center gap-2 ${
                    filtroNif === nif
                      ? "border-slate-900 bg-slate-900 text-white"
                      : "border-slate-300 bg-white text-slate-700 hover:border-slate-500"
                  }`}
                  title={nombre ? `${nombre} (${nif})` : nif}
                >
                  {nombre && (
                    <span className="font-semibold tracking-tight">
                      {nombre}
                    </span>
                  )}
                  <span
                    className={`font-mono ${
                      nombre ? "opacity-70 text-[10px]" : ""
                    }`}
                  >
                    {nif}
                  </span>
                </button>
              );
            })}
          </div>
          {comercialSinNif > 0 && filtroNif !== "__all__" && (
            <span
              className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 px-2 py-1"
              data-testid="comercial-sin-nif-warning"
              title="Hay facturas comerciales sin NIF titular asignado. Se incluyen al filtrar por cualquier sociedad."
            >
              ⚠ {comercialSinNif.toLocaleString("es-ES")} comerciales sin NIF
            </span>
          )}
        </div>
      )}

      {/* Filtro de Periodo — dos líneas mutuamente excluyentes (Quarters / Meses) */}
      <div
        className="border border-slate-200 bg-white px-4 py-3 mb-4 space-y-3"
        data-testid="periodo-selector"
      >
        {/* Línea 1: Quarters */}
        <div className="flex items-center flex-wrap gap-3">
          <div className="text-xs uppercase tracking-[0.18em] text-slate-500 font-semibold min-w-[110px]">
            Trimestre
          </div>
          <div className="flex items-center flex-wrap gap-1">
            {QUARTERS.map((q) => {
              const active = quartersSel.includes(q);
              const disabledByMonths = monthsSel.length > 0;
              const meses = QUARTERS_TO_MONTHS[q].join("-");
              return (
                <button
                  key={q}
                  type="button"
                  onClick={() => toggleQuarter(q)}
                  data-testid={`quarter-toggle-${q}`}
                  title={
                    disabledByMonths
                      ? "Hay meses seleccionados — click para sustituirlos"
                      : `${q} (meses ${meses})`
                  }
                  className={`text-xs px-3 py-1.5 border transition-colors flex flex-col items-center leading-tight ${
                    active
                      ? "border-slate-900 bg-slate-900 text-white"
                      : disabledByMonths
                        ? "border-slate-200 bg-slate-50 text-slate-400"
                        : "border-slate-300 bg-white text-slate-700 hover:border-slate-500"
                  }`}
                >
                  <span className="font-mono font-semibold">{q}</span>
                  <span
                    className={`text-[9px] mt-0.5 ${
                      active ? "text-white/80" : "text-slate-400"
                    }`}
                  >
                    {meses}
                  </span>
                </button>
              );
            })}
          </div>
          {quartersSel.length === 0 && monthsSel.length === 0 && (
            <span className="text-[10px] text-slate-400 italic">
              Sin filtro de periodo
            </span>
          )}
        </div>

        {/* Línea 2: Meses */}
        <div className="flex items-start flex-wrap gap-3">
          <div className="text-xs uppercase tracking-[0.18em] text-slate-500 font-semibold min-w-[110px] mt-1">
            Mes
          </div>
          <div className="flex items-center flex-wrap gap-1">
            {PERIODOS.map((p, idx) => {
              const active = monthsSel.includes(p);
              const disabledByQ = quartersSel.length > 0;
              const label = MONTH_LABELS[idx];
              return (
                <button
                  key={p}
                  type="button"
                  onClick={() => toggleMonth(p)}
                  data-testid={`month-toggle-${p}`}
                  title={
                    disabledByQ
                      ? "Hay trimestres seleccionados — click para sustituirlos"
                      : `Mes ${p} (${label})`
                  }
                  className={`text-xs px-2.5 py-1.5 border transition-colors min-w-[58px] flex items-center justify-center gap-1.5 ${
                    active
                      ? "border-slate-900 bg-slate-900 text-white"
                      : disabledByQ
                        ? "border-slate-200 bg-slate-50 text-slate-400"
                        : "border-slate-300 bg-white text-slate-700 hover:border-slate-500"
                  }`}
                >
                  <span className="font-mono text-[10px] opacity-70">{p}</span>
                  <span className="font-medium">{label}</span>
                </button>
              );
            })}
          </div>
        </div>

        {/* Pill resumen + botón limpiar */}
        {(quartersSel.length > 0 || monthsSel.length > 0) && (
          <div className="flex items-center gap-2 pt-1 border-t border-slate-100">
            <span
              className="text-[11px] text-slate-600 font-mono"
              data-testid="periodo-summary"
            >
              {quartersSel.length > 0 ? (
                <>
                  Filtrando: <strong>{quartersSel.sort().join(" + ")}</strong> (
                  {effectivePeriodo.split(",").length} meses)
                </>
              ) : (
                <>
                  Filtrando:{" "}
                  <strong>
                    {monthsSel
                      .sort()
                      .map((m) => MONTH_LABELS[parseInt(m, 10) - 1])
                      .join(", ")}
                  </strong>{" "}
                  ({monthsSel.length} mes{monthsSel.length === 1 ? "" : "es"})
                </>
              )}
            </span>
            <button
              onClick={limpiarPeriodos}
              className="text-[11px] text-slate-500 hover:text-slate-900 underline"
              data-testid="periodo-clear"
            >
              ✕ limpiar
            </button>
          </div>
        )}

        <p className="text-[10px] text-slate-400 leading-relaxed">
          Multi-selección. Trimestres y meses son mutuamente excluyentes:
          marcar un trimestre limpia los meses, y viceversa.
        </p>
      </div>

      {/* Tabla de comparativa */}
      <div className="border border-slate-200 bg-slate-50/40 p-4 mb-4 flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3 text-sm flex-wrap">
          <Label className="text-xs uppercase tracking-wider text-slate-600">
            Mostrar:
          </Label>
          <Select
            value={filtroEstado}
            onValueChange={setFiltroEstado}
          >
            <SelectTrigger className="rounded-none h-8 w-[220px] text-xs" data-testid="filter-estado">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="diffs">Sólo con diferencias</SelectItem>
              <SelectItem value="all">Todas las facturas</SelectItem>
              <SelectItem value="coincide">Match (coinciden)</SelectItem>
              <SelectItem value="discrepancia">Con discrepancias</SelectItem>
              <SelectItem value="solo_sii">Sólo en SII</SelectItem>
              <SelectItem value="solo_comercial">Sólo en Comercial</SelectItem>
            </SelectContent>
          </Select>

          <Select value={filtroEjercicio} onValueChange={setFiltroEjercicio}>
            <SelectTrigger className="rounded-none h-8 w-[140px] text-xs" data-testid="filter-ejercicio">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">Todos los ejercicios</SelectItem>
              {periodosDisponibles.ejercicios.map((e) => (
                <SelectItem key={e} value={e}>
                  Ejercicio {e}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Input
            value={filtroNumSerie}
            onChange={(e) => setFiltroNumSerie(e.target.value)}
            placeholder="Nº factura (contiene)"
            className="rounded-none h-8 w-[200px] text-xs font-mono"
            data-testid="filter-num-serie"
          />
          {filtroNumSerie && (
            <button
              onClick={() => setFiltroNumSerie("")}
              className="text-[11px] text-slate-500 hover:text-slate-900 -ml-1"
              data-testid="filter-num-serie-clear"
              title="Limpiar"
            >
              ×
            </button>
          )}

          <label
            className="inline-flex items-center gap-2 text-xs text-slate-700 cursor-pointer select-none px-2 py-1 border border-slate-200 hover:bg-white"
            data-testid="filter-iva-mismatch-label"
          >
            <input
              type="checkbox"
              checked={onlyIvaErr}
              onChange={(e) => setOnlyIvaErr(e.target.checked)}
              className="accent-rose-600"
              data-testid="filter-iva-mismatch"
            />
            <span className="text-rose-600">●</span>
            Sólo redondeo IVA incorrecto
          </label>
          <span className="text-xs text-slate-500" data-testid="comp-total-count">
            · {total.toLocaleString("es-ES")} resultado{total === 1 ? "" : "s"}
          </span>
          {/* Pista cuando el total incluye solo-SII que no aparecen en la
              tabla por la optimización de no cargar 800k+ docs. Solo aplica
              en modo "Todas las facturas" sin búsqueda específica. Si el
              usuario filtra por nº de serie, el backend YA incluye solo_sii
              en items, así que el aviso se oculta. */}
          {filtroEstado === "all" &&
            !filtroNumSerieDebounced.trim() &&
            items.length > 0 &&
            total > items.length * (page + 5) && (
              <span
                className="text-[11px] text-slate-500 italic hidden lg:inline"
                data-testid="solo-sii-hint"
                title="Para ver las facturas que sólo existen en SII (no en comercial), cambia el filtro a 'Sólo en SII'."
              >
                · facturas sólo en SII no listadas aquí — usa{" "}
                <button
                  type="button"
                  onClick={() => setFiltroEstado("solo_sii")}
                  className="underline hover:text-slate-900"
                  data-testid="switch-to-solo-sii"
                >
                  Sólo en SII
                </button>
              </span>
            )}
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            className="rounded-none"
            onClick={exportar}
            data-testid="export-comparativa"
            disabled={total === 0 || exporting}
          >
            {exporting ? (
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
            ) : (
              <Download className="h-4 w-4 mr-2" />
            )}
            {exporting ? "Exportando…" : "Exportar"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="rounded-none"
            onClick={load}
            data-testid="refresh-comparativa"
          >
            <RefreshCw className="h-4 w-4 mr-2" />
            Recargar
          </Button>
        </div>
      </div>

      <ResumenTotales
        refreshKey={refreshTick}
        enabled={filtroNif !== null}
        initialData={bundleTotales}
        filtros={{
          ejercicio: filtroEjercicio !== "__all__" ? filtroEjercicio : undefined,
          periodo: effectivePeriodo || undefined,
          num_serie: filtroNumSerieDebounced.trim() || undefined,
          nif_titular: filtroNif && filtroNif !== "__all__" ? filtroNif : undefined,
        }}
      />

      <div className="border border-slate-200">
        <Table>
          <TableHeader>
            <TableRow className="bg-slate-50 hover:bg-slate-50">
              <SortableHead
                label="Nº factura"
                sortKey="num_serie_factura"
                sortBy={sortBy}
                sortDir={sortDir}
                onClick={toggleSort}
              />
              <SortableHead
                label="Estado"
                sortKey="estado"
                sortBy={sortBy}
                sortDir={sortDir}
                onClick={toggleSort}
              />
              <SortableHead
                label="Fecha expedición"
                sortKey="fecha_expedicion"
                sortBy={sortBy}
                sortDir={sortDir}
                onClick={toggleSort}
              />
              <SortableHead
                label="Importe SII"
                sortKey="importe_sii"
                sortBy={sortBy}
                sortDir={sortDir}
                onClick={toggleSort}
                align="right"
              />
              <SortableHead
                label="Importe comercial"
                sortKey="importe_comercial"
                sortBy={sortBy}
                sortDir={sortDir}
                onClick={toggleSort}
                align="right"
              />
              <TableHead className="text-xs uppercase tracking-wider">
                Campos con diferencias
              </TableHead>
              <TableHead></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow>
                <TableCell colSpan={7} className="text-center py-10 text-slate-500">
                  Cargando…
                </TableCell>
              </TableRow>
            ) : sortedItems.length === 0 ? (
              <TableRow>
                <TableCell colSpan={7} className="text-center py-10 text-slate-500">
                  {onlyIvaErr
                    ? "Ninguna factura con redondeo IVA incorrecto"
                    : filtroEstado === "diffs"
                      ? "Sin diferencias detectadas"
                      : filtroEstado === "coincide"
                        ? "Ninguna factura coincide entre SII y Comercial"
                        : filtroEstado === "discrepancia"
                          ? "Sin discrepancias detectadas"
                          : filtroEstado === "solo_sii"
                            ? "Ninguna factura presente sólo en SII"
                            : filtroEstado === "solo_comercial"
                              ? "Ninguna factura presente sólo en Comercial"
                              : "Sin datos"}
                </TableCell>
              </TableRow>
            ) : (
              sortedItems.map((r) => {
                const meta = ESTADO_PILL[r.estado];
                const Icon = meta.Icon;
                return (
                  <TableRow
                    key={r.num_serie_factura}
                    className="data-row"
                    data-testid={`comp-row-${r.num_serie_factura}`}
                  >
                    <TableCell className="font-mono text-xs">
                      {r.num_serie_factura}
                    </TableCell>
                    <TableCell>
                      <span className={`pill ${meta.cls}`}>
                        <Icon className="h-3 w-3" /> {meta.label}
                      </span>
                    </TableCell>
                    <TableCell className="font-mono text-xs tabular-nums text-slate-700">
                      {r.sii?.fecha_expedicion ||
                        r.comercial?.fecha_expedicion ||
                        "—"}
                    </TableCell>
                    <TableCell className="font-mono text-xs tabular-nums text-right">
                      {r.sii?.importe_total != null
                        ? r.sii.importe_total.toFixed(2)
                        : "—"}
                    </TableCell>
                    <TableCell className="font-mono text-xs tabular-nums text-right">
                      <div className="flex items-center justify-end gap-2">
                        {r.comercial?.origen_comercial ? (
                          <span
                            className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 bg-slate-100 text-slate-600 font-sans"
                            data-testid={`origen-${r.num_serie_factura}`}
                          >
                            {labelOrigenComercial(r.comercial.origen_comercial)}
                          </span>
                        ) : null}
                        <span>
                          {r.comercial?.importe_total != null
                            ? r.comercial.importe_total.toFixed(2)
                            : "—"}
                        </span>
                      </div>
                    </TableCell>
                    <TableCell className="text-xs text-slate-700">
                      {Object.keys(r.diferencias).length
                        ? Object.keys(r.diferencias).join(", ")
                        : "—"}
                    </TableCell>
                    <TableCell className="text-right">
                      <button
                        onClick={() => setDetail(r)}
                        className="text-slate-500 hover:text-slate-900"
                        data-testid={`view-comp-${r.num_serie_factura}`}
                      >
                        <Eye className="h-4 w-4" />
                      </button>
                    </TableCell>
                  </TableRow>
                );
              })
            )}
          </TableBody>
        </Table>
      </div>

      {/* Paginación */}
      <div
        className="border border-t-0 border-slate-200 bg-slate-50/40 px-4 py-2 flex items-center justify-between text-xs"
        data-testid="comp-pagination"
      >
        <div className="flex items-center gap-2 text-slate-600">
          <span>Por página:</span>
          <Select
            value={String(pageSize)}
            onValueChange={(v) => setPageSize(Number(v))}
          >
            <SelectTrigger className="rounded-none h-7 w-[80px] text-xs" data-testid="page-size">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {[25, 50, 100, 200, 500].map((n) => (
                <SelectItem key={n} value={String(n)}>
                  {n}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <span className="text-slate-500 ml-2">
            {total === 0
              ? "0"
              : `${((page - 1) * pageSize + 1).toLocaleString("es-ES")}–${Math.min(
                  page * pageSize,
                  total,
                ).toLocaleString("es-ES")}`}{" "}
            de {total.toLocaleString("es-ES")}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="sm"
            className="rounded-none h-7 w-7 p-0"
            onClick={() => setPage(1)}
            disabled={page === 1}
            data-testid="page-first"
          >
            <ChevronsLeft className="h-4 w-4" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="rounded-none h-7 w-7 p-0"
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
            data-testid="page-prev"
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <span className="px-2 font-mono">
            {page} / {Math.max(1, Math.ceil(total / pageSize))}
          </span>
          <Button
            variant="ghost"
            size="sm"
            className="rounded-none h-7 w-7 p-0"
            onClick={() =>
              setPage((p) =>
                Math.min(Math.ceil(total / pageSize) || 1, p + 1),
              )
            }
            disabled={page >= Math.ceil(total / pageSize)}
            data-testid="page-next"
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="rounded-none h-7 w-7 p-0"
            onClick={() => setPage(Math.max(1, Math.ceil(total / pageSize)))}
            disabled={page >= Math.ceil(total / pageSize)}
            data-testid="page-last"
          >
            <ChevronsRight className="h-4 w-4" />
          </Button>
        </div>
      </div>

      <Sheet open={!!detail} onOpenChange={(o) => !o && setDetail(null)}>
        <SheetContent
          side="right"
          className="w-full sm:max-w-3xl overflow-y-auto"
          data-testid="comp-detail"
        >
          {detail && (
            <>
              <SheetHeader>
                <SheetTitle className="font-display text-xl">
                  {detail.num_serie_factura}
                </SheetTitle>
                <div className="flex items-center gap-2 flex-wrap">
                  <span className={`pill ${ESTADO_PILL[detail.estado].cls}`}>
                    {ESTADO_PILL[detail.estado].label}
                  </span>
                  {detail.comercial?.origen_comercial && (
                    <span
                      className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 bg-slate-100 text-slate-700"
                      data-testid="detail-origen-comercial"
                    >
                      Comercial · {labelOrigenComercial(detail.comercial.origen_comercial)}
                    </span>
                  )}
                </div>
              </SheetHeader>
              <div className="mt-4 border border-slate-200">
                <Table>
                  <TableHeader>
                    <TableRow className="bg-slate-50 hover:bg-slate-50">
                      <TableHead className="text-xs uppercase tracking-wider">Campo</TableHead>
                      <TableHead className="text-xs uppercase tracking-wider">SII</TableHead>
                      <TableHead className="text-xs uppercase tracking-wider">Comercial</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {Object.entries({
                      ...(detail.sii || {}),
                      ...(detail.comercial || {}),
                    })
                      .filter(([k]) =>
                        ![
                          "versiones",
                          "ultima_actualizacion",
                          "fuente_ultima",
                          "detalle_iva",
                          "_id",
                        ].includes(k),
                      )
                      .map(([campo]) => {
                        const hasTramoDiff = Array.isArray(
                          detail.diferencias?.detalle_iva,
                        );
                        // Cuando hay diff a nivel tramo, base/cuota/tipo a nivel
                        // cabecera no se incluyen en `diferencias` (los gestiona
                        // el desglose), pero visualmente queremos marcar la fila
                        // para que el usuario tenga el indicador en rojo aunque
                        // el detalle "real" esté en la tabla de tramos.
                        const isCampoDesglose = [
                          "base_imponible",
                          "tipo_impositivo",
                          "cuota_repercutida",
                        ].includes(campo);
                        const isDiff =
                          !!detail.diferencias[campo] ||
                          (hasTramoDiff && isCampoDesglose);
                        const vs = detail.sii?.[campo];
                        const vc = detail.comercial?.[campo];
                        return (
                          <TableRow
                            key={campo}
                            className={isDiff ? "bg-rose-50/40" : ""}
                          >
                            <TableCell className="font-mono text-xs text-slate-700">
                              {campo}
                              {isDiff && (
                                <span className="text-rose-600 ml-1">●</span>
                              )}
                            </TableCell>
                            <TableCell className="font-mono text-xs">
                              {vs == null || vs === "" ? "—" : String(vs)}
                            </TableCell>
                            <TableCell className="font-mono text-xs">
                              {vc == null || vc === "" ? "—" : String(vc)}
                            </TableCell>
                          </TableRow>
                        );
                      })}
                  </TableBody>
                </Table>
              </div>

              {(() => {
                const tramos = Array.isArray(detail?.diferencias?.detalle_iva)
                  ? detail.diferencias.detalle_iva
                  : null;
                if (!tramos) return null;
                return <LineasIvaCompare tramos={tramos} />;
              })()}

              {(() => {
                const sii = Array.isArray(detail.sii?.detalle_iva)
                  ? detail.sii.detalle_iva
                  : [];
                const com = Array.isArray(detail.comercial?.detalle_iva)
                  ? detail.comercial.detalle_iva
                  : [];
                if (sii.length === 0 && com.length === 0) return null;
                const bothSides = sii.length > 0 && com.length > 0;
                return (
                  <div
                    className={`mt-6 grid gap-4 ${
                      bothSides ? "grid-cols-1 lg:grid-cols-2" : "grid-cols-1"
                    }`}
                    data-testid="detalle-iva-block"
                  >
                    {sii.length > 0 && (
                      <DetalleIvaTable
                        label="Detalle IVA · SII"
                        lineas={sii}
                        testIdSuffix="sii"
                      />
                    )}
                    {com.length > 0 && (
                      <DetalleIvaTable
                        label="Detalle IVA · Comercial"
                        lineas={com}
                        testIdSuffix="comercial"
                      />
                    )}
                  </div>
                );
              })()}
            </>
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
}
