import { useRef, useState } from "react";
import { ShieldCheck, ShieldAlert, Upload, X, Eye, EyeOff } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/**
 * Componente para que el usuario aporte un certificado PKCS#12 (.pfx/.p12)
 * en cada llamada. Cuando se activa la subida en cliente:
 *   - debe subirse un archivo y opcionalmente su contraseña;
 *   - el componente notifica al padre vía `onChange({ file, password, enabled })`.
 *
 * Cuando está desactivado, se usará el certificado configurado en el servidor
 * (`SII_CERT_PATH`).
 */
export default function CertUploader({ value, onChange, testIdPrefix = "cert" }) {
  const [showPwd, setShowPwd] = useState(false);
  const inputRef = useRef(null);

  const { enabled = false, file = null, password = "" } = value || {};

  const set = (patch) => onChange({ enabled, file, password, ...patch });

  const onPickFile = (e) => {
    const f = e.target.files?.[0];
    if (f) set({ file: f });
  };

  const clearFile = () => {
    set({ file: null });
    if (inputRef.current) inputRef.current.value = "";
  };

  return (
    <div className="border border-slate-200 bg-slate-50/40">
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-200">
        <div className="flex items-center gap-2.5">
          {enabled ? (
            <ShieldAlert className="h-4 w-4 text-amber-600" strokeWidth={1.75} />
          ) : (
            <ShieldCheck className="h-4 w-4 text-emerald-600" strokeWidth={1.75} />
          )}
          <div>
            <div className="text-sm font-medium text-slate-900">
              {enabled ? "Certificado propio" : "Certificado del servidor"}
            </div>
            <div className="text-[11px] text-slate-500">
              {enabled
                ? "Cada consulta se firmará con tu certificado PKCS#12."
                : "Se usará el certificado configurado en el servidor (SII_CERT_PATH)."}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-500 hidden sm:inline">Subir mío</span>
          <Switch
            checked={enabled}
            onCheckedChange={(v) => set({ enabled: v, file: v ? file : null })}
            data-testid={`${testIdPrefix}-toggle`}
          />
        </div>
      </div>

      {enabled && (
        <div className="p-4 space-y-3">
          <div>
            <Label className="text-xs uppercase tracking-wider text-slate-600">
              Archivo .pfx / .p12 <span className="text-rose-500">*</span>
            </Label>
            {file ? (
              <div className="flex items-center justify-between mt-1.5 border border-slate-300 px-3 py-2 bg-white">
                <div className="flex items-center gap-2 min-w-0">
                  <Upload className="h-4 w-4 text-slate-400 shrink-0" />
                  <span className="text-sm truncate font-mono">
                    {file.name}
                  </span>
                  <span className="text-[11px] text-slate-400 shrink-0">
                    {(file.size / 1024).toFixed(1)} KB
                  </span>
                </div>
                <button
                  type="button"
                  className="text-slate-400 hover:text-rose-600 ml-2"
                  onClick={clearFile}
                  data-testid={`${testIdPrefix}-clear`}
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
                  onChange={onPickFile}
                  data-testid={`${testIdPrefix}-input`}
                />
              </label>
            )}
          </div>

          <div>
            <Label className="text-xs uppercase tracking-wider text-slate-600">
              Contraseña del certificado
            </Label>
            <div className="relative mt-1.5">
              <Input
                type={showPwd ? "text" : "password"}
                value={password}
                onChange={(e) => set({ password: e.target.value })}
                placeholder="••••••••"
                className="rounded-none font-mono pr-9"
                data-testid={`${testIdPrefix}-password`}
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

          <div className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 px-3 py-2 leading-relaxed">
            El certificado solo se envía al backend para esta petición y no se
            guarda. Se elimina del servidor al finalizar la llamada SOAP.
          </div>
        </div>
      )}
    </div>
  );
}
