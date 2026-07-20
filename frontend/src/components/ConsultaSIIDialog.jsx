import { useMemo, useRef, useState } from "react";
import { API } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Loader2,
  ShieldAlert,
  Upload,
  Eye,
  EyeOff,
  X,
  CheckCircle2,
  AlertCircle,
  RefreshCw,
} from "lucide-react";
import { toast } from "sonner";
import { detectarSociedad } from "@/lib/sociedades";

const ENTORNOS = [
  { value: "produccion", label: "Producción (AEAT real)" },
  { value: "preproduccion", label: "Preproducción (AEAT test)" },
  { value: "produccion_sello", label: "Producción · sello electrónico" },
  { value: "preproduccion_sello", label: "Preproducción · sello electrónico" },
];

/**
 * Modal para lanzar una consulta unitaria al SII con certificado propio.
 *
 * Props:
 * - open, onOpenChange
 * - factura: {
 *     num_serie_factura, nif_titular, nif_emisor,
 *     ejercicio, periodo, fecha_expedicion,
 *     nombre_titular?, nombre_emisor?, estado_previo?
 *   }
 * - onSuccess?: (record) => void  // callback tras respuesta OK del SII
 */
export default function ConsultaSIIDialog({
  open,
  onOpenChange,
  factura,
  onSuccess,
}) {
  const [certFile, setCertFile] = useState(null);
  const [password, setPassword] = useState("");
  const [showPwd, setShowPwd] = useState(false);
  const [entorno, setEntorno] = useState("produccion");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [errorMsg, setErrorMsg] = useState(null);
  const [showXml, setShowXml] = useState(false);
  const inputRef = useRef(null);

  const sociedad = useMemo(
    () => detectarSociedad(factura?.nif_titular),
    [factura?.nif_titular],
  );

  const camposFaltantes = useMemo(() => {
    const req = [
      "nif_titular",
      "nif_emisor",
      "num_serie_factura",
      "fecha_expedicion",
      "ejercicio",
      "periodo",
    ];
    return req.filter((k) => !factura?.[k]);
  }, [factura]);

  const cerrar = () => {
    setCertFile(null);
    setPassword("");
    setEntorno("produccion");
    setLoading(false);
    setResult(null);
    setErrorMsg(null);
    setShowXml(false);
    onOpenChange(false);
  };

  const puedeConsultar =
    !!sociedad &&
    !!certFile &&
    camposFaltantes.length === 0 &&
    !loading;

  const ejecutar = async () => {
    if (!puedeConsultar) return;
    setLoading(true);
    setResult(null);
    setErrorMsg(null);

    const fd = new FormData();
    fd.append("nif_titular", String(factura.nif_titular).trim().toUpperCase());
    fd.append(
      "nombre_titular",
      factura.nombre_titular || sociedad.nombre,
    );
    fd.append("ejercicio", String(factura.ejercicio));
    fd.append("periodo", String(factura.periodo));
    fd.append("nif_emisor", String(factura.nif_emisor).trim().toUpperCase());
    if (factura.nombre_emisor) {
      fd.append("nombre_emisor", factura.nombre_emisor);
    }
    fd.append("num_serie_factura", String(factura.num_serie_factura));
    fd.append("fecha_expedicion", String(factura.fecha_expedicion));
    fd.append("entorno", entorno);
    fd.append("certificate", certFile);
    if (password) fd.append("cert_password", password);

    try {
      const r = await fetch(`${API}/sii/consulta-unitaria-cert`, {
        method: "POST",
        body: fd,
        credentials: "include",
      });
      if (!r.ok) {
        let msg = `HTTP ${r.status}`;
        try {
          const j = await r.json();
          msg = j?.detail || msg;
          if (Array.isArray(j?.detail)) {
            msg = j.detail.map((e) => e.msg || JSON.stringify(e)).join(" · ");
          }
        } catch {
          const t = await r.text();
          if (t) msg = t;
        }
        setErrorMsg(msg);
        toast.error("Consulta al SII fallida", { description: msg });
        return;
      }
      const record = await r.json();
      setResult(record);
      toast.success("Consulta al SII completada");
      if (typeof onSuccess === "function") {
        try {
          onSuccess(record);
        } catch {
          // no-op
        }
      }
    } catch (err) {
      const msg = err?.message || String(err);
      setErrorMsg(msg);
      toast.error("Error de red al consultar SII", { description: msg });
    } finally {
      setLoading(false);
    }
  };

  const estadoFactura = result?.respuesta?.datos_factura?.estado_factura;
  const estadoPrevio = factura?.estado_previo;
  const estadoDifiere =
    estadoFactura &&
    estadoPrevio &&
    estadoFactura.toLowerCase() !== String(estadoPrevio).toLowerCase();

  return (
    <Dialog open={open} onOpenChange={(o) => (o ? onOpenChange(true) : cerrar())}>
      <DialogContent
        className="sm:max-w-2xl max-h-[90vh] overflow-y-auto"
        data-testid="consulta-sii-dialog"
      >
        <DialogHeader>
          <DialogTitle className="font-display text-lg">
            Consultar SII en vivo · {factura?.num_serie_factura}
          </DialogTitle>
          <DialogDescription className="text-xs">
            Se enviará una consulta SOAP al SII de la AEAT usando tu
            certificado digital. El resultado quedará persistido en el
            histórico y, si el estado difiere del almacenado, se
            actualizará en la BD.
          </DialogDescription>
        </DialogHeader>

        {/* Sociedad detectada */}
        <div className="border border-slate-200 bg-slate-50/40 px-4 py-3">
          <div className="text-[11px] uppercase tracking-wider text-slate-500 mb-1">
            Sociedad detectada
          </div>
          {sociedad ? (
            <div className="flex items-center gap-2" data-testid="sociedad-detectada">
              <span
                className={`text-xs uppercase tracking-wider px-2 py-0.5 border ${sociedad.color}`}
              >
                {sociedad.codigo}
              </span>
              <span className="text-sm text-slate-700 truncate">
                {sociedad.nombre}
              </span>
              <span className="text-xs text-slate-400 font-mono ml-auto">
                {factura?.nif_titular}
              </span>
            </div>
          ) : (
            <div
              className="flex items-center gap-2 text-sm text-rose-700"
              data-testid="sociedad-no-detectada"
            >
              <AlertCircle className="h-4 w-4" />
              <span>
                NIF <span className="font-mono">{factura?.nif_titular || "—"}</span> no
                mapeado a BASER/TotalEnergies. No se puede seleccionar
                certificado automáticamente.
              </span>
            </div>
          )}
        </div>

        {camposFaltantes.length > 0 && (
          <div className="border border-amber-200 bg-amber-50 text-amber-800 px-3 py-2 text-xs">
            Faltan datos para la consulta:{" "}
            <span className="font-mono">{camposFaltantes.join(", ")}</span>
          </div>
        )}

        {/* Certificado + password */}
        <div className="border border-slate-200 p-4 space-y-3">
          <div className="flex items-center gap-2 text-sm font-medium text-slate-800">
            <ShieldAlert className="h-4 w-4 text-amber-600" />
            Certificado {sociedad ? `de ${sociedad.codigo}` : "digital"} (.pfx / .p12)
          </div>

          <div>
            <Label className="text-xs uppercase tracking-wider text-slate-600">
              Archivo <span className="text-rose-500">*</span>
            </Label>
            {certFile ? (
              <div className="flex items-center justify-between mt-1.5 border border-slate-300 px-3 py-2 bg-white">
                <div className="flex items-center gap-2 min-w-0">
                  <Upload className="h-4 w-4 text-slate-400 shrink-0" />
                  <span className="text-sm truncate font-mono">
                    {certFile.name}
                  </span>
                  <span className="text-[11px] text-slate-400 shrink-0">
                    {(certFile.size / 1024).toFixed(1)} KB
                  </span>
                </div>
                <button
                  type="button"
                  className="text-slate-400 hover:text-rose-600 ml-2"
                  onClick={() => {
                    setCertFile(null);
                    if (inputRef.current) inputRef.current.value = "";
                  }}
                  data-testid="consulta-sii-cert-clear"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            ) : (
              <label className="mt-1.5 border-2 border-dashed border-slate-300 px-3 py-3 bg-white flex items-center gap-3 cursor-pointer hover:border-slate-400">
                <Upload className="h-4 w-4 text-slate-400" />
                <span className="text-xs text-slate-500">
                  Selecciona el certificado…
                </span>
                <input
                  ref={inputRef}
                  type="file"
                  accept=".pfx,.p12,application/x-pkcs12"
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) setCertFile(f);
                  }}
                  data-testid="consulta-sii-cert-input"
                />
              </label>
            )}
          </div>

          <div>
            <Label className="text-xs uppercase tracking-wider text-slate-600">
              Contraseña
            </Label>
            <div className="relative mt-1.5">
              <Input
                type={showPwd ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                className="rounded-none font-mono pr-9"
                data-testid="consulta-sii-cert-password"
                autoComplete="off"
              />
              <button
                type="button"
                onClick={() => setShowPwd((s) => !s)}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-700"
              >
                {showPwd ? (
                  <EyeOff className="h-4 w-4" />
                ) : (
                  <Eye className="h-4 w-4" />
                )}
              </button>
            </div>
          </div>

          <div>
            <Label className="text-xs uppercase tracking-wider text-slate-600">
              Entorno AEAT
            </Label>
            <Select value={entorno} onValueChange={setEntorno}>
              <SelectTrigger
                className="rounded-none mt-1.5"
                data-testid="consulta-sii-entorno"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ENTORNOS.map((e) => (
                  <SelectItem key={e.value} value={e.value}>
                    {e.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 px-3 py-2 leading-relaxed">
            El certificado solo se envía al backend para esta petición. No
            se guarda ni en el navegador ni en el servidor.
          </div>
        </div>

        {/* Resultado */}
        {result && (
          <div
            className="border border-emerald-200 bg-emerald-50/40 px-4 py-3 space-y-2"
            data-testid="consulta-sii-result"
          >
            <div className="flex items-center gap-2">
              <CheckCircle2 className="h-4 w-4 text-emerald-600" />
              <span className="text-sm font-medium text-emerald-900">
                Respuesta AEAT recibida
              </span>
            </div>
            <div className="grid grid-cols-2 gap-2 text-xs">
              <div>
                <span className="text-slate-500 uppercase tracking-wider text-[10px]">
                  Estado factura
                </span>
                <div className="font-mono text-sm text-slate-900">
                  {estadoFactura || "—"}
                </div>
              </div>
              <div>
                <span className="text-slate-500 uppercase tracking-wider text-[10px]">
                  CSV AEAT
                </span>
                <div className="font-mono text-sm text-slate-900 truncate">
                  {result?.respuesta?.datos_factura?.csv_aeat || "—"}
                </div>
              </div>
              <div>
                <span className="text-slate-500 uppercase tracking-wider text-[10px]">
                  Nº registro
                </span>
                <div className="font-mono text-sm text-slate-900">
                  {result?.respuesta?.datos_factura?.num_registro || "—"}
                </div>
              </div>
              <div>
                <span className="text-slate-500 uppercase tracking-wider text-[10px]">
                  Timestamp
                </span>
                <div className="font-mono text-xs text-slate-900">
                  {result?.respuesta?.datos_factura?.timestamp_presentacion ||
                    "—"}
                </div>
              </div>
            </div>
            {estadoDifiere && (
              <div className="text-xs text-amber-800 bg-amber-100 border border-amber-300 px-3 py-2 mt-2">
                ⚠ El estado devuelto por AEAT (<b>{estadoFactura}</b>)
                difiere del almacenado (<b>{estadoPrevio}</b>). El estado
                en BD se ha actualizado automáticamente.
              </div>
            )}
            <button
              type="button"
              className="text-xs text-slate-500 underline hover:text-slate-800"
              onClick={() => setShowXml((s) => !s)}
              data-testid="consulta-sii-toggle-xml"
            >
              {showXml ? "Ocultar" : "Ver"} XML SOAP
            </button>
            {showXml && (
              <div className="grid gap-2 text-[10px] font-mono max-h-64 overflow-auto">
                <details open>
                  <summary className="cursor-pointer text-slate-600">
                    Request
                  </summary>
                  <pre className="bg-white border border-slate-200 p-2 whitespace-pre-wrap break-all">
                    {result?.soap_request_xml || "—"}
                  </pre>
                </details>
                <details>
                  <summary className="cursor-pointer text-slate-600">
                    Response
                  </summary>
                  <pre className="bg-white border border-slate-200 p-2 whitespace-pre-wrap break-all">
                    {result?.soap_response_xml || "—"}
                  </pre>
                </details>
              </div>
            )}
          </div>
        )}

        {errorMsg && (
          <div
            className="border border-rose-200 bg-rose-50 text-rose-800 px-3 py-2 text-xs"
            data-testid="consulta-sii-error"
          >
            {errorMsg}
          </div>
        )}

        <div className="flex items-center justify-end gap-2 pt-2 border-t border-slate-200">
          <Button
            variant="ghost"
            onClick={cerrar}
            disabled={loading}
            data-testid="consulta-sii-cancelar"
          >
            Cerrar
          </Button>
          <Button
            onClick={ejecutar}
            disabled={!puedeConsultar}
            data-testid="consulta-sii-ejecutar"
          >
            {loading ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Contactando AEAT…
              </>
            ) : (
              <>
                <RefreshCw className="h-4 w-4 mr-2" />
                Consultar SII
              </>
            )}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
