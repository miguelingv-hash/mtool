import { BookOpen, HelpCircle } from "lucide-react";

/**
 * Manual de usuario del Monitor SII.
 * Ruta: /manual-monitor-sii (protegida por comparativa.view).
 * Formato: página estática con secciones, glosario de conceptos y FAQs.
 */
export default function ManualMonitorSII() {
  return (
    <div
      className="max-w-4xl mx-auto px-6 py-8 space-y-8"
      data-testid="manual-monitor-sii"
    >
      <div className="flex items-center gap-3">
        <div className="flex h-9 w-9 items-center justify-center bg-slate-900 text-white">
          <BookOpen className="h-5 w-5" strokeWidth={1.75} />
        </div>
        <div>
          <h1 className="text-2xl font-display font-bold tracking-tight text-slate-900">
            Manual de Usuario · Monitor SII
          </h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Guía de uso, glosario de conceptos y explicación de métricas
          </p>
        </div>
      </div>

      {/* Introducción */}
      <section className="prose prose-sm max-w-none">
        <h2 className="text-lg font-semibold text-slate-900 mt-6 mb-2 border-b border-slate-200 pb-1">
          ¿Qué es el Monitor SII?
        </h2>
        <p className="text-slate-700">
          El Monitor SII compara las facturas que has declarado en el{" "}
          <b>SII de la AEAT</b> (Suministro Inmediato de Información) con las
          que tienes registradas en tu <b>sistema comercial</b> (SIGLO o
          SAP FI). Detecta desviaciones a nivel factura y agrega KPIs para
          entender el grado de conciliación de tu empresa.
        </p>
      </section>

      {/* Estructura del módulo */}
      <section>
        <h2 className="text-lg font-semibold text-slate-900 mt-6 mb-2 border-b border-slate-200 pb-1">
          Secciones del módulo
        </h2>
        <ul className="text-sm text-slate-700 space-y-2 list-disc pl-6">
          <li>
            <b>Comparativa SII</b>: listado detallado de facturas cruzadas SII
            vs Comercial. Permite filtrar por sociedad, ejercicio, periodo,
            tipo de factura y estado de conciliación. Al clicar una fila se
            despliega el detalle campo a campo.
          </li>
          <li>
            <b>Cuadro mensual</b>: vista pivotada por sociedad + tipo de
            factura + periodo. Compara Base + Cuota + Nº de facturas de
            SII vs SIGLO vs SAP FI en una única tabla. Filas expandibles
            con el detalle de facturas.
          </li>
          <li>
            <b>Consulta individual</b> / <b>Batch CSV</b> / <b>Mensual</b>:
            para descargar facturas del SII on-demand.
          </li>
        </ul>
      </section>

      {/* Glosario */}
      <section>
        <h2 className="text-lg font-semibold text-slate-900 mt-6 mb-2 border-b border-slate-200 pb-1">
          Glosario de conceptos
        </h2>

        <ConceptCard title="% conciliación en €" testId="glosario-pct-importe">
          Porcentaje de conciliación por <b>importe económico</b>. Se calcula
          como <code>1 − |Δ| / SII</code> tanto sobre <b>base imponible</b>{" "}
          como sobre <b>cuota repercutida</b>, y se muestra el mínimo de
          ambos. Un 100% indica que SII y Comercial cuadran al céntimo.
        </ConceptCard>

        <ConceptCard title="% conciliación en Nº facturas" testId="glosario-pct-facturas">
          Porcentaje de facturas conciliadas por <b>número</b>. Fórmula:
          <br />
          <code>matches / (SII ∪ Comercial)</code>
          <ul className="text-xs text-slate-600 mt-1 list-disc pl-5">
            <li>
              <b>matches</b> = facturas presentes en ambas fuentes (cruce
              por <code>num_serie_factura</code>).
            </li>
            <li>
              <b>Universo</b> = SII + Comercial − matches (unión sin
              duplicar los que existen en ambas).
            </li>
          </ul>
          Por definición este valor <b>nunca puede superar el 100%</b>.
        </ConceptCard>

        <ConceptCard title="Facturas SII / Comerciales" testId="glosario-counts">
          <ul className="text-sm text-slate-700 space-y-1 list-disc pl-5">
            <li>
              <b>Facturas SII</b>: registros descargados de la AEAT mediante
              el servicio SOAP. Reflejan lo que oficialmente hemos declarado.
            </li>
            <li>
              <b>Comerciales</b>: registros importados desde tu sistema
              contable/comercial (SIGLO + SAP FI) vía CSV. Reflejan lo que
              tu ERP tiene facturado.
            </li>
            <li>
              <b>X con contraparte de Y</b>: X facturas están cruzadas entre
              las dos fuentes; Y es el universo total sin duplicar.
            </li>
          </ul>
        </ConceptCard>

        <ConceptCard title="Estados de conciliación" testId="glosario-estados">
          <ul className="text-sm text-slate-700 space-y-1 list-disc pl-5">
            <li>
              <span className="pill pill-success">Coincide</span> — la factura
              existe en ambas fuentes y los campos configurados cuadran
              (con tolerancia 0,01€).
            </li>
            <li>
              <span className="pill pill-warning">Discrepancia</span> — existe
              en ambas fuentes pero algún campo comparado difiere. Ver la
              columna <i>Campos con diferencias</i> para el detalle.
            </li>
            <li>
              <span className="pill pill-info">Sólo SII</span> — la factura
              está en el SII pero no en tu sistema comercial. Posibles causas:
              CSV comercial desactualizado, error de facturación no
              contabilizado.
            </li>
            <li>
              <span className="pill pill-danger">Sólo Comercial</span> — la
              factura está en tu comercial pero no en el SII. Posibles causas:
              factura pendiente de enviar a AEAT, error en el envío.
            </li>
          </ul>
        </ConceptCard>

        <ConceptCard title="Importe canónico y Δ Canónico" testId="glosario-canonico">
          <p className="text-sm text-slate-700">
            El <b>importe canónico</b> de una factura es la referencia usada
            para conciliar cuando el desglose (base + cuota) no coincide
            entre las dos fuentes:
          </p>
          <ul className="text-sm text-slate-700 space-y-1 list-disc pl-5 mt-1">
            <li>
              Si la factura tiene <code>importe_total</code> → usamos ese
              valor.
            </li>
            <li>
              Si no → usamos <code>base + cuota</code>.
            </li>
          </ul>
          <p className="text-sm text-slate-700 mt-2">
            <b>¿Para qué sirve?</b> Cubre facturas con desglose asimétrico:
          </p>
          <ul className="text-sm text-slate-700 space-y-1 list-disc pl-5 mt-1">
            <li>
              <b>No Sujeta</b>: el SII sólo declara <code>importe_total</code>
              , sin desglose base/cuota. El comercial sí desglosa. Base y
              cuota individuales no cuadran, pero el importe canónico sí.
            </li>
            <li>
              <b>Partes exentas</b>: <code>importe_total ≠ base + cuota</code>{" "}
              porque hay líneas exentas o suplidos. El importe canónico usa
              el total real.
            </li>
          </ul>
          <p className="text-sm text-slate-700 mt-2">
            <b>Δ Canónico</b> = SII.canonico − Comercial.canonico. En{" "}
            <span className="text-emerald-700 font-semibold">verde</span>{" "}
            cuando la conciliación real cuadra (aunque los Δ Base y Δ Cuota
            individuales no cuadren por asimetrías legítimas).
          </p>
        </ConceptCard>

        <ConceptCard title="Δ Base / Δ Cuota" testId="glosario-deltas">
          <p className="text-sm text-slate-700">
            Diferencia agregada entre SII y Comercial:
          </p>
          <ul className="text-sm text-slate-700 space-y-1 list-disc pl-5 mt-1">
            <li>
              <b>Δ Base</b> = SII.base − Comercial.base
            </li>
            <li>
              <b>Δ Cuota</b> = SII.cuota_repercutida − Comercial.cuota
            </li>
          </ul>
          <p className="text-sm text-slate-700 mt-2">
            Los orígenes con <b>inversión de signo</b> activa (SIGLO y SAP FI
            en esta app) ya tienen el signo compensado, así que los deltas
            representan la diferencia real entre lo declarado y lo
            contabilizado.
          </p>
        </ConceptCard>

        <ConceptCard title="Reconciliación por importe canónico (badge amarillo)" testId="glosario-reconciliada">
          Cuando el desglose base/cuota difiere pero el <b>importe canónico</b>{" "}
          cuadra, la factura se marca como{" "}
          <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 bg-amber-50 text-amber-800 border border-amber-200 font-sans">
            Coincide por importe canónico
          </span>
          . Aparece en el listado y en el detalle expandido. Es habitual
          en facturas No Sujeta o con partes exentas.
        </ConceptCard>

        <ConceptCard title="Origen comercial · SIGLO vs SAP FI" testId="glosario-origenes">
          <ul className="text-sm text-slate-700 space-y-1 list-disc pl-5">
            <li>
              <b>SIGLO</b>: sistema comercial principal. Sus importes vienen
              con signo invertido respecto al SII (por eso la app aplica
              inversión automática al comparar).
            </li>
            <li>
              <b>SAP FI</b>: sistema financiero SAP. También con inversión
              de signo activa.
            </li>
          </ul>
        </ConceptCard>

        <ConceptCard title="Tipo de factura (F1, F2, R1...)" testId="glosario-tipos">
          <p className="text-sm text-slate-700">
            Códigos definidos por la AEAT en el modelo SII:
          </p>
          <ul className="text-sm text-slate-700 space-y-1 list-disc pl-5 mt-1">
            <li>
              <b>F1</b> — Factura normal.
            </li>
            <li>
              <b>F2</b> — Factura simplificada / tique.
            </li>
            <li>
              <b>F3</b> — Reemplaza a una simplificada.
            </li>
            <li>
              <b>F4</b> — Factura resumen (varias operaciones en una).
            </li>
            <li>
              <b>R1</b>, <b>R2</b>, <b>R3</b>, <b>R4</b>, <b>R5</b> —
              Facturas rectificativas por distintos motivos (art. 80 LIVA).
            </li>
            <li>
              <b>Sin clasificar</b>: comerciales sin match SII (no tienen
              tipo).
            </li>
          </ul>
        </ConceptCard>

        <ConceptCard title="Filtros" testId="glosario-filtros">
          <ul className="text-sm text-slate-700 space-y-1 list-disc pl-5">
            <li>
              <b>Sociedad (NIF)</b>: acota a una empresa concreta.
            </li>
            <li>
              <b>Ejercicio / Periodo</b>: año y mes(es) de emisión.
            </li>
            <li>
              <b>Tipo de factura</b>: filtro múltiple (F1, F2, R1...).
            </li>
            <li>
              <b>Con discrepancias</b> (<i>only_diffs</i>): sólo muestra
              facturas con estado ≠ coincide (útil para revisar rápidamente
              los errores).
            </li>
            <li>
              <b>Estado</b>: filtrar por coincide, discrepancia, sólo_sii o
              sólo_comercial.
            </li>
          </ul>
        </ConceptCard>
      </section>

      {/* FAQs */}
      <section>
        <h2 className="text-lg font-semibold text-slate-900 mt-6 mb-2 border-b border-slate-200 pb-1">
          Preguntas frecuentes
        </h2>

        <FAQ q="¿Por qué el % en Nº facturas nunca supera 100%?">
          Porque matches (facturas en ambas fuentes) siempre es ≤ Universo
          (facturas únicas en cualquier fuente). Si viste alguna vez un
          porcentaje &gt; 100% era un bug de cálculo (matches vs universo
          usaban filtros ligeramente distintos). Está corregido: ahora se
          capa a máximo 100%.
        </FAQ>

        <FAQ q="¿Por qué una factura aparece 'Coincide' aunque los importes difieran ligeramente?">
          Toleramos ±0,01€ por redondeos. Si la diferencia es mayor pero{" "}
          <b>el importe canónico cuadra</b> (por partes exentas o No Sujeta),
          la marcamos como reconciliada por importe canónico (badge amarillo).
        </FAQ>

        <FAQ q="¿Cómo se importa el CSV comercial?">
          Desde <i>Comercial → Importar CSV</i>. El sistema mapea automáticamente
          las columnas (num_serie, base, cuota, fechas, etc.) y auto-calcula
          <code> importe_total</code> cuando falta (típico en SIGLO) sumando
          las líneas del <code>detalle_iva</code>.
        </FAQ>

        <FAQ q="¿Con qué frecuencia se actualizan los datos del SII?">
          Depende de tus jobs configurados. Puedes descargar manualmente
          desde <i>Consulta Individual</i> (una factura), <i>Batch CSV</i>{" "}
          (lote) o <i>Mensual</i> (todo un periodo). Cada descarga inserta o
          actualiza en la base local.
        </FAQ>

        <FAQ q="¿Qué es el snapshot iter26 y por qué las queries son tan rápidas?">
          Denormalizamos ciertos campos del SII directamente en el doc
          comercial (<code>_sii_base</code>, <code>_sii_cuota</code>,{" "}
          <code>_sii_importe_total</code>, <code>_has_sii</code>) para evitar
          costosos <code>$lookup</code> cross-collection. Las queries que
          antes tardaban 30-60s ahora responden en menos de 1s.
        </FAQ>
      </section>

      <div className="border border-slate-200 bg-slate-50 p-4 text-xs text-slate-600 italic">
        Este manual se actualiza automáticamente con cada release de la app.
        Si detectas algo confuso o incorrecto, avisa al equipo técnico.
      </div>
    </div>
  );
}

function ConceptCard({ title, children, testId }) {
  return (
    <div
      className="border border-slate-200 bg-white p-4 mb-3"
      data-testid={testId}
    >
      <div className="flex items-center gap-2 mb-2">
        <HelpCircle className="h-4 w-4 text-slate-500" />
        <h3 className="text-sm font-semibold text-slate-900 uppercase tracking-wider">
          {title}
        </h3>
      </div>
      <div className="text-sm text-slate-700 leading-relaxed">{children}</div>
    </div>
  );
}

function FAQ({ q, children }) {
  return (
    <details className="border border-slate-200 bg-white mb-2 p-3">
      <summary className="cursor-pointer font-medium text-sm text-slate-900">
        {q}
      </summary>
      <div className="text-sm text-slate-700 mt-2 leading-relaxed">
        {children}
      </div>
    </details>
  );
}
