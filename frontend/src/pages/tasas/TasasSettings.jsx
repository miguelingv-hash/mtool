import { useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";
import { api, formatApiError  } from "@/lib/api";
import { motion } from "framer-motion";
import { Save as FloppyDisk, Upload as CloudArrowUp, Download as CloudArrowDown, ShieldCheck, AlertTriangle as Warning } from "lucide-react";

export default function Settings() {
  const { user } = useAuth();
  const [form, setForm] = useState(null);
  const [refactForm, setRefactForm] = useState(null);
  const [saving, setSaving] = useState(false);
  const [savingRefact, setSavingRefact] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [savedRefact, setSavedRefact] = useState(false);

  useEffect(() => {
    if (user && user.role === "admin") {
      Promise.all([
        api.get("/tasas-municipales/settings"),
        api.get("/settings/refacturacion"),
      ]).then(([sp, rf]) => {
        setForm(sp.data);
        setRefactForm(rf.data);
        setLoading(false);
      });
    }
  }, [user]);

  if (user && user.role !== "admin") return <Navigate to="/tasas-municipales" replace />;
  if (loading || !form || !refactForm) return <div className="text-sm text-zinc-500">Cargando…</div>;

  const save = async (e) => {
    e.preventDefault();
    setSaving(true); setError(""); setSaved(false);
    try {
      const { data } = await api.put("/tasas-municipales/settings", form);
      setForm(data);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (e) {
      setError(formatApiError(e.response?.data?.detail));
    } finally { setSaving(false); }
  };

  const saveRefact = async (e) => {
    e.preventDefault();
    setSavingRefact(true); setError(""); setSavedRefact(false);
    try {
      const { data } = await api.put("/settings/refacturacion", refactForm);
      setRefactForm(data);
      setSavedRefact(true);
      setTimeout(() => setSavedRefact(false), 3000);
    } catch (e) {
      setError(formatApiError(e.response?.data?.detail));
    } finally { setSavingRefact(false); }
  };

  const onField = (k, v) => setForm({ ...form, [k]: v });
  const onRefactField = (k, v) => setRefactForm({ ...refactForm, [k]: v });

  return (
    <div className="space-y-10">
      <section className="pb-6 border-b border-zinc-200">
        <div className="flex items-center gap-3 mb-3">
          <ShieldCheck size={24} className="text-finapp-primary" />
          <span className="label-track">Solo administrador</span>
        </div>
        <h1 className="font-heading font-black text-4xl sm:text-5xl tracking-tighter leading-none">
          Ajustes.
        </h1>
        <p className="text-zinc-600 mt-3 max-w-3xl">
          Configura la integración con SharePoint para importar CSVs desde una ubicación compartida
          y depositar los PDFs generados en carpetas organizadas por ayuntamiento y mes.
        </p>
      </section>

      {form.mock_mode && (
        <div className="border border-[#FFD600] bg-[#FFF9DB] p-4 flex items-start gap-3" data-testid="mock-banner">
          <Warning size={20} className="text-zinc-900 flex-shrink-0 mt-0.5" />
          <div className="text-sm">
            <div className="font-bold mb-1">Modo simulado activo</div>
            <p className="text-zinc-700">
              Los ficheros se leen y escriben en carpetas locales (<code className="font-mono">storage/sharepoint_mock/</code>).
              Desactiva esta opción cuando tengas las credenciales reales de Microsoft Graph configuradas.
            </p>
          </div>
        </div>
      )}

      <motion.form
        onSubmit={save}
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        className="grid grid-cols-1 gap-10"
        data-testid="settings-form"
      >
        {/* Toggles */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <label className={`border p-5 cursor-pointer ${form.enabled_input ? "border-finapp-primary bg-finapp-primary/5" : "border-zinc-200 hover:border-zinc-400"}`}>
            <div className="flex items-start gap-3">
              <input type="checkbox" checked={form.enabled_input} onChange={(e) => onField("enabled_input", e.target.checked)} className="mt-1" data-testid="toggle-input" />
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <CloudArrowDown size={18} className="text-finapp-primary" />
                  <span className="font-heading font-bold">Importar CSV desde SharePoint</span>
                </div>
                <p className="text-sm text-zinc-600">
                  Añade un botón en la pantalla de Tasas para importar archivos CSV desde la carpeta configurada.
                </p>
              </div>
            </div>
          </label>
          <label className={`border p-5 cursor-pointer ${form.enabled_output ? "border-finapp-primary bg-finapp-primary/5" : "border-zinc-200 hover:border-zinc-400"}`}>
            <div className="flex items-start gap-3">
              <input type="checkbox" checked={form.enabled_output} onChange={(e) => onField("enabled_output", e.target.checked)} className="mt-1" data-testid="toggle-output" />
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <CloudArrowUp size={18} className="text-finapp-primary" />
                  <span className="font-heading font-bold">Subir PDFs a SharePoint</span>
                </div>
                <p className="text-sm text-zinc-600">
                  Al generar, los PDFs se organizan en <code className="font-mono text-xs">/{`{ayto}`}/{`{YYYY-MM}`}/{`{ayto}_{YYYY-MM-DD}`}.pdf</code>.
                </p>
              </div>
            </div>
          </label>
        </div>

        <label className="flex items-center gap-3">
          <input type="checkbox" checked={form.mock_mode} onChange={(e) => onField("mock_mode", e.target.checked)} data-testid="toggle-mock" />
          <span className="text-sm">
            <span className="font-bold uppercase tracking-widest text-xs">Modo simulado</span>
            <span className="text-zinc-600 ml-2">— usa almacenamiento local en lugar de SharePoint real</span>
          </span>
        </label>

        {/* Credentials */}
        <fieldset className="border border-zinc-200 p-6">
          <legend className="label-track px-2">Credenciales Microsoft Graph</legend>
          <p className="text-sm text-zinc-600 mb-5">
            Regístra una aplicación en Azure AD con permiso <code className="font-mono">Sites.ReadWrite.All</code> (App-only).
            Con modo simulado activo, estos campos no se usan.
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            <div>
              <label className="label-track block mb-2">Tenant ID</label>
              <input className="field-input" value={form.tenant_id || ""} onChange={(e) => onField("tenant_id", e.target.value)} placeholder="00000000-0000-0000-0000-000000000000" data-testid="sp-tenant" />
            </div>
            <div>
              <label className="label-track block mb-2">Client ID</label>
              <input className="field-input" value={form.client_id || ""} onChange={(e) => onField("client_id", e.target.value)} placeholder="00000000-0000-0000-0000-000000000000" data-testid="sp-client" />
            </div>
            <div className="md:col-span-2">
              <label className="label-track block mb-2">Client Secret</label>
              <input type="password" className="field-input" value={form.client_secret || ""} onChange={(e) => onField("client_secret", e.target.value)} placeholder={form.client_secret === "***" ? "Conservar actual (dejar vacío) o escribir uno nuevo" : "Valor del secreto generado en Azure"} data-testid="sp-secret" />
              <div className="text-xs text-zinc-500 mt-1">Se almacena en base de datos. Deja el campo vacío para mantener el valor actual.</div>
            </div>
          </div>
        </fieldset>

        {/* Site / Folders */}
        <fieldset className="border border-zinc-200 p-6">
          <legend className="label-track px-2">Sitio y carpetas</legend>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            <div className="md:col-span-2">
              <label className="label-track block mb-2">URL del sitio SharePoint</label>
              <input className="field-input" value={form.site_url || ""} onChange={(e) => onField("site_url", e.target.value)} placeholder="https://empresa.sharepoint.com/sites/Tasas" data-testid="sp-site" />
            </div>
            <div>
              <label className="label-track block mb-2">Carpeta de entrada (CSVs)</label>
              <input className="field-input" value={form.input_folder || ""} onChange={(e) => onField("input_folder", e.target.value)} data-testid="sp-input-folder" />
            </div>
            <div>
              <label className="label-track block mb-2">Carpeta de salida (PDFs)</label>
              <input className="field-input" value={form.output_folder || ""} onChange={(e) => onField("output_folder", e.target.value)} data-testid="sp-output-folder" />
            </div>
          </div>
        </fieldset>

        {/* Branding */}
        <fieldset className="border border-zinc-200 p-6">
          <legend className="label-track px-2">Marca del PDF</legend>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            <div>
              <label className="label-track block mb-2">Teléfono Atención al Cliente</label>
              <input className="field-input" value={form.atencion_telefono || ""} onChange={(e) => onField("atencion_telefono", e.target.value)} placeholder="900 907 000" data-testid="atencion-tel" />
              <div className="text-xs text-zinc-500 mt-1">Aparece en rojo en la cabecera de cada página.</div>
            </div>
            <div>
              <label className="label-track block mb-2">Logos por Sociedad (placeholder)</label>
              <textarea
                className="field-input font-mono text-xs"
                rows={4}
                value={form.logos_by_sociedad ? JSON.stringify(form.logos_by_sociedad, null, 2) : "{}"}
                onChange={(e) => {
                  try { onField("logos_by_sociedad", JSON.parse(e.target.value || "{}")); } catch { /* ignore */ }
                }}
                data-testid="logos-json"
                placeholder='{"NC":"LOGO NC"}'
              />
              <div className="text-xs text-zinc-500 mt-1">JSON: clave = Sociedad (columna 1 del CSV), valor = texto/URL del logo. Hasta integrar imágenes, se usará como texto en el recuadro.</div>
            </div>
          </div>
        </fieldset>

        {error && <div className="border border-[#FF2A00] text-[#FF2A00] text-sm px-3 py-2">{error}</div>}
        {saved && <div className="border border-[#008A27] text-[#008A27] text-sm px-3 py-2">Ajustes guardados correctamente.</div>}

        <div className="flex justify-end">
          <button type="submit" disabled={saving} className="btn-primary flex items-center gap-2" data-testid="settings-save">
            <FloppyDisk size={16} /> {saving ? "Guardando…" : "Guardar ajustes"}
          </button>
        </div>
      </motion.form>

      {/* === Refacturación / API externa === */}
      <motion.form
        onSubmit={saveRefact}
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        className="grid grid-cols-1 gap-6 pt-10 border-t border-finapp-border"
        data-testid="settings-refact-form"
      >
        <div>
          <div className="label-track mb-2">Facturación histórica</div>
          <h2 className="font-heading font-black text-3xl tracking-tighter leading-none">
            API externa de refacturación.
          </h2>
          <p className="text-finapp-muted mt-2 max-w-2xl text-sm">
            Configura el endpoint destino y la autenticación. Si lo dejas vacío, el módulo
            funcionará en modo previsualización (genera el JSON pero no lo envía).
          </p>
        </div>

        <fieldset className="border border-finapp-border p-6 rounded-lg">
          <legend className="label-track px-2">Endpoints</legend>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            <div className="md:col-span-2">
              <label className="label-track block mb-2">URL destino (POST)</label>
              <input
                className="field-input"
                value={refactForm.target_url || ""}
                onChange={(e) => onRefactField("target_url", e.target.value)}
                placeholder="https://api.proveedor.com/transactions"
                data-testid="refact-target-url"
              />
            </div>
            <div>
              <label className="label-track block mb-2">Modo de autenticación</label>
              <select
                className="field-input"
                value={refactForm.auth_mode || "manual_token"}
                onChange={(e) => onRefactField("auth_mode", e.target.value)}
                data-testid="refact-auth-mode"
              >
                <option value="manual_token">Bearer manual (pegar en pantalla)</option>
                <option value="oauth_client_credentials">OAuth2 client_credentials</option>
                <option value="static_bearer">Bearer estático</option>
                <option value="api_key_header">API Key (header)</option>
              </select>
            </div>
            <div>
              <label className="label-track block mb-2">URL de autenticación</label>
              <input
                className="field-input"
                value={refactForm.auth_url || ""}
                onChange={(e) => onRefactField("auth_url", e.target.value)}
                placeholder="https://auth.proveedor.com/oauth/token"
                data-testid="refact-auth-url"
              />
            </div>
            <div>
              <label className="label-track block mb-2">Client ID</label>
              <input
                className="field-input"
                value={refactForm.client_id || ""}
                onChange={(e) => onRefactField("client_id", e.target.value)}
                data-testid="refact-cid"
              />
            </div>
            <div>
              <label className="label-track block mb-2">Client secret</label>
              <input
                type="password"
                className="field-input"
                value={refactForm.client_secret || ""}
                onChange={(e) => onRefactField("client_secret", e.target.value)}
                data-testid="refact-csec"
              />
            </div>
            <div className="md:col-span-2">
              <label className="label-track block mb-2">
                Token estático / API Key (según modo)
              </label>
              <input
                type="password"
                className="field-input"
                value={refactForm.static_token || ""}
                onChange={(e) => onRefactField("static_token", e.target.value)}
                data-testid="refact-token"
                placeholder="Sólo si auth = Bearer estático o API Key"
              />
            </div>
            <div>
              <label className="label-track block mb-2">Nombre header API Key</label>
              <input
                className="field-input"
                value={refactForm.api_key_header_name || ""}
                onChange={(e) => onRefactField("api_key_header_name", e.target.value)}
                placeholder="X-API-Key"
                data-testid="refact-key-header"
              />
            </div>
          </div>
        </fieldset>

        <fieldset className="border border-finapp-border p-6 rounded-lg">
          <legend className="label-track px-2">Valores por defecto</legend>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
            <div>
              <label className="label-track block mb-2">Import supplier</label>
              <input
                className="field-input"
                value={refactForm.default_supplier || ""}
                onChange={(e) => onRefactField("default_supplier", e.target.value)}
                data-testid="refact-default-supplier"
              />
            </div>
            <div>
              <label className="label-track block mb-2">Indicador IVA</label>
              <select
                className="field-input"
                value={refactForm.default_iva_indicador || "T6"}
                onChange={(e) => onRefactField("default_iva_indicador", e.target.value)}
                data-testid="refact-default-iva-ind"
              >
                <option value="T6">T6</option>
                <option value="T7">T7</option>
              </select>
            </div>
            <div>
              <label className="label-track block mb-2">% IVA</label>
              <input
                type="number" step="0.01"
                className="field-input"
                value={refactForm.default_iva_porcentaje ?? 10}
                onChange={(e) => onRefactField("default_iva_porcentaje", Number(e.target.value))}
                data-testid="refact-default-iva-pct"
              />
            </div>
          </div>
        </fieldset>

        {savedRefact && <div className="border border-[#008A27] text-[#008A27] text-sm px-3 py-2 rounded">Ajustes de refacturación guardados.</div>}

        <div className="flex justify-end">
          <button type="submit" disabled={savingRefact} className="btn-primary flex items-center gap-2" data-testid="settings-refact-save">
            <FloppyDisk size={16} /> {savingRefact ? "Guardando…" : "Guardar refacturación"}
          </button>
        </div>
      </motion.form>
    </div>
  );
}
