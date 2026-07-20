import { useState } from "react";
import { useSearchParams, Link } from "react-router-dom";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { ShieldCheck, Upload, CalendarRange, RefreshCw, Info } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";

import ConciliacionNewman from "@/pages/ConciliacionNewman";
import CargaMensualSII from "@/components/CargaMensualSII";
import CargaComercialCSV from "@/components/CargaComercialCSV";

/**
 * Pantalla "Carga de datos" — concentra los 3 flujos de import:
 *
 *   1. **Conciliación Newman** (default) — carga masiva de exports SOAP
 *      preprocesados, es el flujo principal del usuario (cientos de miles
 *      de facturas vía job async).
 *   2. **Comercial (SAP FI / SIGLO)** — sube el report tabular del ERP.
 *   3. **Consulta mensual SII** — descarga directa desde AEAT vía SOAP/mTLS,
 *      con opción síncrona o background.
 *
 * Cada tab tiene su propio permiso de backend; ProtectedRoute de la página
 * sólo exige el "paraguas" (al menos uno de los tres), y cada panel se oculta
 * individualmente si el usuario no tiene el permiso correspondiente.
 *
 * El tab activo se sincroniza con `?tab=` en la URL para poder deep-linkear
 * (`/carga-datos?tab=comercial`) y para conservar el estado al hacer
 * back/forward del navegador.
 */
export default function CargaDatos() {
  const { hasPermission } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();

  const canNewman = hasPermission("conciliacion.import") || hasPermission("conciliacion.view");
  const canComercial = hasPermission("comercial.import");
  const canMensual = hasPermission("consultas.mensual");

  // Tab por defecto: el primero accesible siguiendo el orden de uso real.
  const defaultTab = canNewman
    ? "newman"
    : canComercial
      ? "comercial"
      : "mensual";

  const urlTab = searchParams.get("tab");
  const [tab, setTab] = useState(
    urlTab && ["newman", "comercial", "mensual"].includes(urlTab)
      ? urlTab
      : defaultTab,
  );

  const handleTabChange = (next) => {
    setTab(next);
    const newParams = new URLSearchParams(searchParams);
    newParams.set("tab", next);
    setSearchParams(newParams, { replace: true });
  };

  return (
    <div className="px-8 py-8 max-w-[1500px]" data-testid="carga-datos-page">
      <div className="mb-8">
        <div className="text-xs uppercase tracking-[0.2em] text-slate-500 mb-2">
          Ingesta
        </div>
        <h1 className="font-display text-4xl font-bold tracking-tight text-slate-900">
          Carga de datos
        </h1>
        <p className="text-sm text-slate-600 mt-2 max-w-3xl">
          Importa facturas en la base de datos desde tres orígenes distintos:
          la consulta SOAP directa al SII (AEAT), los reports tabulares del
          ERP (SAP FI / SIGLO) y el flujo masivo de Conciliación Newman.
        </p>
      </div>

      {hasPermission("sii.wipe") && (
        <div
          className="mb-6 border border-amber-200 bg-amber-50 px-4 py-3 flex items-start gap-3"
          data-testid="carga-datos-denorm-notice"
        >
          <Info className="h-4 w-4 text-amber-700 mt-0.5 shrink-0" />
          <div className="flex-1 text-xs text-amber-900 leading-relaxed">
            <b>Tras cada carga masiva</b>, ejecuta <b>&quot;Regenerar
            denormalización&quot;</b> para que los fast-paths de la Comparativa
            (listado, KPIs, resumen por origen) devuelvan los datos actualizados
            en tiempo sub-segundo.
          </div>
          <Link
            to="/admin/mantenimiento#denormalizacion"
            className="text-xs bg-amber-900 text-amber-50 hover:bg-amber-800 px-3 py-1.5 flex items-center gap-1.5 whitespace-nowrap"
            data-testid="carga-datos-goto-denorm"
          >
            <RefreshCw className="h-3 w-3" />
            Regenerar ahora
          </Link>
        </div>
      )}

      <Tabs value={tab} onValueChange={handleTabChange} className="w-full">
        <TabsList
          className="bg-transparent border-b border-slate-200 rounded-none p-0 h-auto w-full justify-start gap-0"
          data-testid="carga-datos-tabs"
        >
          {canNewman && (
            <TabsTrigger
              value="newman"
              data-testid="tab-newman"
              className="rounded-none border-b-2 border-transparent data-[state=active]:border-slate-900 data-[state=active]:bg-transparent data-[state=active]:shadow-none px-5 py-3 font-medium tracking-tight text-slate-600 data-[state=active]:text-slate-900"
            >
              <ShieldCheck className="h-4 w-4 mr-2" />
              Conciliación Newman
            </TabsTrigger>
          )}
          {canComercial && (
            <TabsTrigger
              value="comercial"
              data-testid="tab-comercial"
              className="rounded-none border-b-2 border-transparent data-[state=active]:border-slate-900 data-[state=active]:bg-transparent data-[state=active]:shadow-none px-5 py-3 font-medium tracking-tight text-slate-600 data-[state=active]:text-slate-900"
            >
              <Upload className="h-4 w-4 mr-2" />
              Comercial (SAP / SIGLO)
            </TabsTrigger>
          )}
          {canMensual && (
            <TabsTrigger
              value="mensual"
              data-testid="tab-mensual"
              className="rounded-none border-b-2 border-transparent data-[state=active]:border-slate-900 data-[state=active]:bg-transparent data-[state=active]:shadow-none px-5 py-3 font-medium tracking-tight text-slate-600 data-[state=active]:text-slate-900"
            >
              <CalendarRange className="h-4 w-4 mr-2" />
              Consulta mensual SII
            </TabsTrigger>
          )}
        </TabsList>

        {canNewman && (
          <TabsContent
            value="newman"
            className="mt-6 focus-visible:outline-none focus-visible:ring-0"
            data-testid="panel-newman"
          >
            {/* ConciliacionNewman ya trae su propio padding/wrapper, así que
                montamos sin marco adicional. */}
            <ConciliacionNewman embedded />
          </TabsContent>
        )}

        {canComercial && (
          <TabsContent
            value="comercial"
            className="mt-6"
            data-testid="panel-comercial"
          >
            <div className="border border-slate-200 p-6 max-w-3xl">
              <CargaComercialCSV />
            </div>
          </TabsContent>
        )}

        {canMensual && (
          <TabsContent
            value="mensual"
            className="mt-6"
            data-testid="panel-mensual"
          >
            <div className="border border-slate-200 p-6 max-w-3xl">
              <CargaMensualSII />
            </div>
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
}
