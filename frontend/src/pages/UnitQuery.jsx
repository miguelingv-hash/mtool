import { useState } from "react";
import { api, PERIODOS, ESTADO_META } from "@/lib/api";
import { useEnv } from "@/contexts/EnvContext";
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
import { toast } from "sonner";
import { Loader2, Send, RotateCcw, FileSearch } from "lucide-react";
import EstadoBadge from "@/components/EstadoBadge";
import QueryDetailSheet from "@/components/QueryDetailSheet";
import CertUploader from "@/components/CertUploader";

const INITIAL = {
  nif_titular: "",
  nombre_titular: "",
  ejercicio: String(new Date().getFullYear()),
  periodo: "01",
  nif_emisor: "",
  nombre_emisor: "",
  num_serie_factura: "",
  fecha_expedicion: "",
};

const FECHA_RE = /^\d{2}-\d{2}-\d{4}$/;

const Field = ({ label, hint, children, required, span = 1, testId }) => (
  <div
    className={`flex flex-col gap-1.5 ${span === 2 ? "md:col-span-2" : ""}`}
    data-testid={testId}
  >
    <Label className="text-xs uppercase tracking-wider text-slate-600">
      {label}
      {required && <span className="text-rose-500 ml-0.5">*</span>}
    </Label>
    {children}
    {hint && <span className="text-[11px] text-slate-400">{hint}</span>}
  </div>
);

export default function UnitQuery() {
  const { entorno } = useEnv();
  const [form, setForm] = useState(INITIAL);
  const [cert, setCert] = useState({ enabled: false, file: null, password: "" });
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [detailOpen, setDetailOpen] = useState(false);

  const update = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const validate = () => {
    if (!form.nif_titular || form.nif_titular.length < 8)
      return "NIF titular inválido";
    if (!form.nombre_titular) return "Nombre titular requerido";
    if (!/^\d{4}$/.test(form.ejercicio)) return "Ejercicio inválido (YYYY)";
    if (!form.nif_emisor) return "NIF emisor requerido";
    if (!form.num_serie_factura) return "Nº serie factura requerido";
    if (!FECHA_RE.test(form.fecha_expedicion))
      return "Fecha de expedición debe tener formato DD-MM-YYYY";
    return null;
  };

  const onSubmit = async (e) => {
    e.preventDefault();
    const err = validate();
    if (err) {
      toast.error(err);
      return;
    }
    setLoading(true);
    setResult(null);
    try {
      const payload = { ...form, entorno };
      if (!payload.nombre_emisor) delete payload.nombre_emisor;
      const { data } = await api.post("/sii/consulta-unitaria", payload);
      setResult(data);
      toast.success(
        `Consulta enviada · ${ESTADO_META[data.respuesta.estado_factura]?.label}`,
      );
    } catch (e) {
      toast.error(
        e.response?.data?.detail
          ? typeof e.response.data.detail === "string"
            ? e.response.data.detail
            : "Datos inválidos"
          : "Error al consultar SII",
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="px-8 py-8 max-w-[1100px]">
      <div className="mb-8">
        <div className="text-xs uppercase tracking-[0.2em] text-slate-500 mb-2">
          ConsultaLRFactEmitidas
        </div>
        <h1 className="font-display text-4xl font-bold tracking-tight text-slate-900">
          Consulta unitaria
        </h1>
        <p className="text-sm text-slate-600 mt-2 max-w-2xl">
          Invocación individual del servicio SOAP del SII. Introduce los datos
          de la factura emitida y obtén el estado registrado en la Agencia
          Tributaria.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <form
          onSubmit={onSubmit}
          className="lg:col-span-2 border border-slate-200 p-6"
          data-testid="unit-query-form"
        >
          <div className="flex items-center justify-between mb-6">
            <h2 className="font-display text-lg font-bold tracking-tight">
              Datos de entrada
            </h2>
            <span className="font-mono text-[11px] uppercase tracking-wider text-slate-400">
              entorno: {entorno}
            </span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            <Field label="NIF titular" required testId="field-nif-titular">
              <Input
                value={form.nif_titular}
                onChange={(e) =>
                  update("nif_titular", e.target.value.toUpperCase())
                }
                placeholder="B12345678"
                className="rounded-none font-mono"
                data-testid="input-nif-titular"
              />
            </Field>
            <Field label="Nombre / Razón social titular" required testId="field-nombre-titular">
              <Input
                value={form.nombre_titular}
                onChange={(e) => update("nombre_titular", e.target.value)}
                placeholder="Mi Empresa S.L."
                className="rounded-none"
                data-testid="input-nombre-titular"
              />
            </Field>
            <Field label="Ejercicio" required testId="field-ejercicio">
              <Input
                value={form.ejercicio}
                onChange={(e) => update("ejercicio", e.target.value)}
                placeholder="2025"
                maxLength={4}
                className="rounded-none font-mono"
                data-testid="input-ejercicio"
              />
            </Field>
            <Field label="Período" required testId="field-periodo">
              <Select
                value={form.periodo}
                onValueChange={(v) => update("periodo", v)}
              >
                <SelectTrigger
                  className="rounded-none"
                  data-testid="select-periodo"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PERIODOS.map((p) => (
                    <SelectItem key={p.value} value={p.value}>
                      {p.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>

            <div className="md:col-span-2 border-t border-slate-200 pt-5 mt-1">
              <div className="text-xs uppercase tracking-wider text-slate-500 mb-3">
                Identificación de la factura
              </div>
            </div>

            <Field label="NIF emisor" required testId="field-nif-emisor">
              <Input
                value={form.nif_emisor}
                onChange={(e) =>
                  update("nif_emisor", e.target.value.toUpperCase())
                }
                placeholder="A87654321"
                className="rounded-none font-mono"
                data-testid="input-nif-emisor"
              />
            </Field>
            <Field label="Nombre emisor (opcional)" testId="field-nombre-emisor">
              <Input
                value={form.nombre_emisor}
                onChange={(e) => update("nombre_emisor", e.target.value)}
                placeholder="Proveedor Ejemplo SA"
                className="rounded-none"
                data-testid="input-nombre-emisor"
              />
            </Field>
            <Field label="Nº serie / Nº factura" required testId="field-num-serie">
              <Input
                value={form.num_serie_factura}
                onChange={(e) => update("num_serie_factura", e.target.value)}
                placeholder="F2025-001"
                className="rounded-none font-mono"
                data-testid="input-num-serie"
              />
            </Field>
            <Field
              label="Fecha expedición"
              required
              hint="Formato DD-MM-YYYY"
              testId="field-fecha"
            >
              <Input
                value={form.fecha_expedicion}
                onChange={(e) => update("fecha_expedicion", e.target.value)}
                placeholder="15-01-2025"
                className="rounded-none font-mono"
                data-testid="input-fecha"
              />
            </Field>
          </div>

          <div className="mt-6">
            <CertUploader value={cert} onChange={setCert} testIdPrefix="unit-cert" />
          </div>

          <div className="flex items-center gap-3 mt-7 pt-5 border-t border-slate-200">
            <Button
              type="submit"
              disabled={loading}
              className="rounded-none bg-slate-900 hover:bg-slate-700 text-white"
              data-testid="submit-unit-query"
            >
              {loading ? (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              ) : (
                <Send className="h-4 w-4 mr-2" />
              )}
              {cert.enabled ? "Consultar SII (real)" : "Consultar SII (mock)"}
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                setForm(INITIAL);
                setResult(null);
              }}
              className="rounded-none"
              data-testid="reset-form"
            >
              <RotateCcw className="h-4 w-4 mr-2" />
              Limpiar
            </Button>
          </div>
        </form>

        <div className="border border-slate-200 p-6 bg-slate-50/40 h-fit">
          <div className="flex items-center justify-between mb-4">
            <h2 className="font-display text-lg font-bold tracking-tight">
              Respuesta
            </h2>
            {result && (
              <EstadoBadge estado={result.respuesta.estado_factura} />
            )}
          </div>
          {!result ? (
            <div className="text-sm text-slate-500 py-12 text-center border border-dashed border-slate-300 bg-white">
              Esperando consulta…
            </div>
          ) : (
            <div className="space-y-3 text-sm" data-testid="unit-result">
              <div>
                <div className="text-[11px] uppercase tracking-wider text-slate-500">
                  Estado envío
                </div>
                <div className="text-slate-900">
                  {result.respuesta.estado_envio}
                </div>
              </div>
              {result.respuesta.codigo_error_registro && (
                <div>
                  <div className="text-[11px] uppercase tracking-wider text-slate-500">
                    Error
                  </div>
                  <div className="font-mono text-xs text-rose-700">
                    {result.respuesta.codigo_error_registro}
                  </div>
                  <div className="text-xs text-slate-700 mt-0.5">
                    {result.respuesta.descripcion_error_registro}
                  </div>
                </div>
              )}
              {result.respuesta.num_registro_presentacion && (
                <div>
                  <div className="text-[11px] uppercase tracking-wider text-slate-500">
                    Nº registro
                  </div>
                  <div className="font-mono text-xs break-all">
                    {result.respuesta.num_registro_presentacion}
                  </div>
                </div>
              )}
              {result.respuesta.csv && (
                <div>
                  <div className="text-[11px] uppercase tracking-wider text-slate-500">
                    CSV AEAT
                  </div>
                  <div className="font-mono text-xs break-all">
                    {result.respuesta.csv}
                  </div>
                </div>
              )}
              <Button
                onClick={() => setDetailOpen(true)}
                variant="outline"
                className="w-full rounded-none mt-2"
                data-testid="view-detail-btn"
              >
                <FileSearch className="h-4 w-4 mr-2" />
                Ver SOAP completo
              </Button>
            </div>
          )}
        </div>
      </div>

      <QueryDetailSheet
        open={detailOpen}
        onOpenChange={setDetailOpen}
        record={result}
      />
    </div>
  );
}
