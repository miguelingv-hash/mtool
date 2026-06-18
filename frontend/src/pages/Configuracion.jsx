import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Checkbox } from "@/components/ui/checkbox";
import { toast } from "sonner";
import { Loader2, Save, RotateCcw, Settings } from "lucide-react";

// Etiquetas legibles para cada campo canónico
const FIELD_LABELS = {
  num_serie_factura: "Nº serie factura",
  fecha_expedicion: "Fecha de expedición",
  nif_emisor: "NIF emisor",
  nombre_emisor: "Nombre / Razón social emisor",
  ejercicio: "Ejercicio",
  periodo: "Periodo",
  nif_titular: "NIF titular",
  contraparte_nif: "NIF contraparte",
  contraparte_nombre: "Nombre contraparte",
  tipo_factura: "Tipo de factura",
  clave_regimen_especial: "Clave régimen especial",
  descripcion_operacion: "Descripción operación",
  fecha_operacion: "Fecha operación",
  base_imponible: "Base imponible",
  tipo_impositivo: "Tipo impositivo (IVA %)",
  cuota_repercutida: "Cuota repercutida (IVA €)",
  importe_total: "Importe total",
};

// Campos que técnicamente identifican a la factura — no tiene sentido
// "comparar" porque son la clave de cruce o redundantes con ella.
const CAMPOS_INMUTABLES = new Set(["num_serie_factura", "nif_titular"]);

export default function Configuracion() {
  const [cfg, setCfg] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const cargar = () => {
    setLoading(true);
    api
      .get("/comparativa/config")
      .then((r) => setCfg(r.data))
      .catch(() => toast.error("No se pudo cargar la configuración"))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    cargar();
  }, []);

  const toggleCampo = (campo) => {
    if (!cfg) return;
    const set = new Set(cfg.campos_comparados);
    if (set.has(campo)) set.delete(campo);
    else set.add(campo);
    setCfg({ ...cfg, campos_comparados: Array.from(set) });
  };

  const toggleInvertir = (origen) => {
    if (!cfg) return;
    const next = { ...(cfg.invertir_signo_por_origen || {}) };
    next[origen] = !next[origen];
    setCfg({ ...cfg, invertir_signo_por_origen: next });
  };

  const guardar = async () => {
    if (!cfg) return;
    setSaving(true);
    try {
      await api.put("/comparativa/config", {
        campos_comparados: cfg.campos_comparados,
        invertir_signo_por_origen: cfg.invertir_signo_por_origen,
      });
      toast.success("Configuración guardada", {
        description:
          "La nueva configuración se aplicará a la Comparativa SII↔Comercial.",
      });
    } catch (e) {
      toast.error("Error al guardar", {
        description: e.response?.data?.detail || "Inténtalo de nuevo",
      });
    } finally {
      setSaving(false);
    }
  };

  const restaurarDefaults = () => {
    if (!cfg) return;
    setCfg({
      ...cfg,
      campos_comparados: cfg.campos_comparados_default || [],
      invertir_signo_por_origen: {},
    });
  };

  if (loading || !cfg) {
    return (
      <div className="p-8 flex items-center gap-2 text-slate-500">
        <Loader2 className="h-4 w-4 animate-spin" /> Cargando configuración…
      </div>
    );
  }

  const seleccionados = new Set(cfg.campos_comparados);

  return (
    <div className="p-8 max-w-5xl">
      <div className="text-xs uppercase tracking-wider text-slate-500 mb-1">
        Ajustes
      </div>
      <h1
        className="font-display text-3xl font-bold tracking-tight mb-1 flex items-center gap-2"
        data-testid="config-title"
      >
        <Settings className="h-7 w-7 text-slate-400" />
        Configuración de comparativa
      </h1>
      <p className="text-sm text-slate-500 mb-8 max-w-2xl">
        Define qué campos se tienen en cuenta al comparar facturas SII vs
        comercial, y si los importes del comercial deben invertirse de signo
        según el origen del fichero.
      </p>

      {/* Bloque 1 — Campos a comparar */}
      <section className="mb-10" data-testid="seccion-campos">
        <div className="flex items-baseline justify-between mb-4">
          <div>
            <h2 className="font-display text-lg font-semibold text-slate-900">
              Campos incluidos en la comparativa
            </h2>
            <p className="text-xs text-slate-500 mt-1">
              Sólo los campos marcados se comparan al calcular discrepancias.
              Los grises son la clave de cruce y no se pueden desactivar.
            </p>
          </div>
          <span
            className="font-mono text-xs uppercase tracking-wider text-slate-500 tabular-nums"
            data-testid="config-campos-count"
          >
            {seleccionados.size} / {cfg.campos_disponibles.length} activos
          </span>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 border border-slate-200 p-4 bg-white">
          {cfg.campos_disponibles.map((campo) => {
            const isInmutable = CAMPOS_INMUTABLES.has(campo);
            const checked = seleccionados.has(campo) || isInmutable;
            const isNumeric = cfg.campos_numericos?.includes(campo);
            return (
              <label
                key={campo}
                className={`flex items-center gap-3 py-1.5 px-2 text-sm ${
                  isInmutable
                    ? "text-slate-400 cursor-not-allowed"
                    : "text-slate-800 hover:bg-slate-50 cursor-pointer"
                }`}
                data-testid={`campo-${campo}`}
              >
                <Checkbox
                  checked={checked}
                  disabled={isInmutable}
                  onCheckedChange={() => !isInmutable && toggleCampo(campo)}
                  data-testid={`checkbox-${campo}`}
                />
                <span className="flex-1">{FIELD_LABELS[campo] || campo}</span>
                {isNumeric && (
                  <span className="text-[10px] uppercase tracking-wider text-slate-400 font-mono">
                    importe
                  </span>
                )}
                {isInmutable && (
                  <span className="text-[10px] uppercase tracking-wider text-slate-400 font-mono">
                    clave
                  </span>
                )}
              </label>
            );
          })}
        </div>
      </section>

      {/* Bloque 2 — Inversión de signo por origen */}
      <section className="mb-10" data-testid="seccion-signo">
        <h2 className="font-display text-lg font-semibold text-slate-900 mb-1">
          Inversión de signo en importes comerciales
        </h2>
        <p className="text-xs text-slate-500 mb-4 max-w-2xl">
          Cuando los ficheros comerciales muestran importes en negativo (p.ej.
          notas de crédito / abono en SAP o SIGLO) pero el SII los reporta en
          positivo, activa la inversión para que la comparativa los enfrente
          con el signo correcto. Sólo afecta a los importes (
          <span className="font-mono">base imponible, cuota, importe total</span>
          ).
        </p>
        <div className="border border-slate-200 bg-white">
          {cfg.origenes_disponibles.length === 0 ? (
            <div className="px-4 py-6 text-sm text-slate-500">
              Aún no hay ficheros comerciales importados. Importa uno desde la
              Comparativa para configurar este bloque.
            </div>
          ) : (
            cfg.origenes_disponibles.map((origen, idx) => {
              const active = !!cfg.invertir_signo_por_origen?.[origen];
              const accent =
                origen === "SAP"
                  ? "border-l-blue-500"
                  : origen === "SIGLO"
                    ? "border-l-amber-500"
                    : "border-l-slate-400";
              return (
                <div
                  key={origen}
                  className={`flex items-center justify-between px-4 py-3 border-l-4 ${accent} ${
                    idx > 0 ? "border-t border-slate-200" : ""
                  }`}
                  data-testid={`origen-row-${origen}`}
                >
                  <div>
                    <div className="font-mono text-sm font-semibold text-slate-800">
                      {origen}
                    </div>
                    <div className="text-[11px] text-slate-500 mt-0.5">
                      Multiplicar importes del comercial por −1 antes de comparar
                    </div>
                  </div>
                  <Switch
                    checked={active}
                    onCheckedChange={() => toggleInvertir(origen)}
                    data-testid={`switch-invertir-${origen}`}
                  />
                </div>
              );
            })
          )}
        </div>
      </section>

      {/* Botonera */}
      <div className="flex items-center gap-3">
        <Button
          onClick={guardar}
          disabled={saving}
          className="rounded-none"
          data-testid="btn-guardar-config"
        >
          {saving ? (
            <Loader2 className="h-4 w-4 mr-2 animate-spin" />
          ) : (
            <Save className="h-4 w-4 mr-2" />
          )}
          Guardar configuración
        </Button>
        <Button
          onClick={restaurarDefaults}
          variant="outline"
          className="rounded-none"
          data-testid="btn-restaurar-defaults"
        >
          <RotateCcw className="h-4 w-4 mr-2" />
          Restaurar valores por defecto
        </Button>
      </div>
    </div>
  );
}
