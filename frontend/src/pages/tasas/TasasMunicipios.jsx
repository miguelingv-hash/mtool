import { useEffect, useState, useCallback } from "react";
import { api, formatApiError  } from "@/lib/api";
import { motion } from "framer-motion";
import { Plus, Pencil as PencilSimple, Trash2 as Trash, Building as Buildings, X, Search as MagnifyingGlass, ChevronLeft as CaretLeft, ChevronRight as CaretRight } from "lucide-react";
import ProvinciaCombobox from "../../components/ProvinciaCombobox";

const EMPTY = { codigo: "", nombre: "", calle: "", numero: "", codigo_postal: "", provincia: "", telefono_contacto: "", persona_contacto: "" };

export default function Municipios() {
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(1);
  const [page, setPage] = useState(1);
  const [limit, setLimit] = useState(25);
  const [search, setSearch] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(EMPTY);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get("/tasas-municipales/municipios", { params: { page, limit, q: search } });
      setItems(r.data.items || []);
      setTotal(r.data.total || 0);
      setPages(r.data.pages || 1);
    } finally { setLoading(false); }
  }, [page, limit, search]);

  useEffect(() => { refresh(); }, [refresh]);

  // Debounced search
  useEffect(() => {
    const t = setTimeout(() => { setSearch(searchInput); setPage(1); }, 300);
    return () => clearTimeout(t);
  }, [searchInput]);

  const openNew = () => { setForm(EMPTY); setEditing({}); setError(""); };
  const openEdit = (m) => { setForm(m); setEditing(m); setError(""); };
  const close = () => { setEditing(null); setError(""); };

  const save = async (e) => {
    e.preventDefault();
    setError(""); setSaving(true);
    try {
      if (editing && editing.codigo) {
        await api.put(`/tasas-municipales/municipios/${encodeURIComponent(editing.codigo)}`, form);
      } else {
        await api.post("/tasas-municipales/municipios", form);
      }
      await refresh();
      close();
    } catch (e) {
      setError(formatApiError(e.response?.data?.detail));
    } finally { setSaving(false); }
  };

  const remove = async (m) => {
    if (!window.confirm(`¿Eliminar municipio ${m.codigo} - ${m.nombre}?`)) return;
    await api.delete(`/tasas-municipales/municipios/${encodeURIComponent(m.codigo)}`);
    refresh();
  };

  const from = total === 0 ? 0 : (page - 1) * limit + 1;
  const to = Math.min(page * limit, total);

  return (
    <div className="space-y-10">
      <section className="pb-6 border-b border-finapp-border flex flex-col lg:flex-row lg:items-end lg:justify-between gap-4">
        <div>
          <div className="label-track mb-3">Maestro de municipios</div>
          <h1 className="font-heading font-extrabold text-4xl sm:text-5xl tracking-tight leading-none">
            Ayuntamientos.
          </h1>
          <p className="text-finapp-muted mt-3 max-w-2xl">
            Gestiona el directorio de municipios. Los códigos de ayuntamiento del CSV se cruzan con este maestro para producir las cartas trimestrales.
          </p>
        </div>
        <button onClick={openNew} className="btn-primary flex items-center gap-2" data-testid="muni-new-btn">
          <Plus size={16} /> Nuevo municipio
        </button>
      </section>

      <section>
        <div className="flex flex-wrap justify-between items-center mb-4 gap-4">
          <div className="relative max-w-md w-full">
            <MagnifyingGlass size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-finapp-muted" />
            <input
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              className="field-input pl-10"
              placeholder="Buscar por código, nombre o provincia…"
              data-testid="muni-filter"
            />
          </div>
          <div className="flex items-center gap-4 text-sm text-finapp-muted">
            <span className="label-track">{total.toLocaleString("es-ES")} municipios</span>
            <label className="flex items-center gap-2">
              <span className="label-track">Por página</span>
              <select
                value={limit}
                onChange={(e) => { setLimit(parseInt(e.target.value, 10)); setPage(1); }}
                className="field-input w-20 py-1.5"
                data-testid="muni-limit"
              >
                {[25, 50, 100, 200].map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
          </div>
        </div>

        {loading ? (
          <div className="border border-finapp-border rounded-xl p-16 text-center text-sm text-finapp-muted">
            Cargando…
          </div>
        ) : items.length === 0 ? (
          <div className="border border-dashed border-finapp-border rounded-xl p-16 text-center">
            <Buildings size={36} className="text-finapp-muted mx-auto mb-3" />
            <div className="font-heading font-bold text-xl mb-2">
              {search ? "Sin coincidencias" : "Sin municipios todavía"}
            </div>
            <p className="text-finapp-muted mb-6 max-w-sm mx-auto">
              {search
                ? `No hay municipios que coincidan con "${search}".`
                : "Crea uno manualmente o sube un CSV de Tasas — los códigos no encontrados se crearán automáticamente."}
            </p>
            {!search && (
              <button onClick={openNew} className="btn-primary" data-testid="muni-empty-new">Crear primero</button>
            )}
          </div>
        ) : (
          <div className="border border-finapp-border rounded-xl overflow-hidden" data-testid="muni-table">
            <table className="w-full text-sm">
              <thead className="bg-finapp-surface">
                <tr>
                  <th className="text-left px-5 py-3 label-track">Código</th>
                  <th className="text-left px-5 py-3 label-track">Nombre</th>
                  <th className="text-left px-5 py-3 label-track">Dirección</th>
                  <th className="text-left px-5 py-3 label-track">Contacto</th>
                  <th className="text-right px-5 py-3 label-track">Acciones</th>
                </tr>
              </thead>
              <tbody>
                {items.map((m) => (
                  <tr key={m.codigo} className="border-t border-finapp-border hover:bg-finapp-surface/60" data-testid={`muni-row-${m.codigo}`}>
                    <td className="px-5 py-3 font-mono text-xs">{m.codigo}</td>
                    <td className="px-5 py-3 font-semibold">{m.nombre}</td>
                    <td className="px-5 py-3 text-finapp-muted text-xs">
                      {[m.calle, m.numero].filter(Boolean).join(" ")}
                      {(m.codigo_postal || m.provincia) && (
                        <span className="block">
                          {m.codigo_postal}{m.codigo_postal && m.provincia ? " - " : ""}{m.provincia}
                        </span>
                      )}
                    </td>
                    <td className="px-5 py-3 text-finapp-muted text-xs">
                      {m.persona_contacto && <span className="block">{m.persona_contacto}</span>}
                      {m.telefono_contacto && <span className="font-mono">{m.telefono_contacto}</span>}
                    </td>
                    <td className="px-5 py-3 text-right">
                      <div className="flex justify-end gap-2">
                        <button onClick={() => openEdit(m)} className="text-finapp-muted hover:text-finapp-primary p-1" data-testid={`muni-edit-${m.codigo}`}>
                          <PencilSimple size={16} />
                        </button>
                        <button onClick={() => remove(m)} className="text-finapp-muted hover:text-finapp-accent p-1" data-testid={`muni-delete-${m.codigo}`}>
                          <Trash size={16} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Pagination */}
        {!loading && total > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-4 mt-4" data-testid="muni-pagination">
            <div className="text-xs text-finapp-muted">
              Mostrando <span className="font-semibold text-finapp-ink">{from}</span>–<span className="font-semibold text-finapp-ink">{to}</span> de <span className="font-semibold text-finapp-ink">{total.toLocaleString("es-ES")}</span>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setPage(1)}
                disabled={page <= 1}
                className="btn-ghost text-xs disabled:opacity-40 disabled:cursor-not-allowed"
                data-testid="muni-page-first"
              >
                « Primera
              </button>
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1}
                className="btn-ghost text-xs disabled:opacity-40 disabled:cursor-not-allowed"
                data-testid="muni-page-prev"
              >
                <CaretLeft size={14} /> Anterior
              </button>
              <span className="text-xs text-finapp-muted tabular-nums px-2">
                Página <span className="font-semibold text-finapp-ink">{page}</span> de <span className="font-semibold text-finapp-ink">{pages}</span>
              </span>
              <button
                onClick={() => setPage((p) => Math.min(pages, p + 1))}
                disabled={page >= pages}
                className="btn-ghost text-xs disabled:opacity-40 disabled:cursor-not-allowed"
                data-testid="muni-page-next"
              >
                Siguiente <CaretRight size={14} />
              </button>
              <button
                onClick={() => setPage(pages)}
                disabled={page >= pages}
                className="btn-ghost text-xs disabled:opacity-40 disabled:cursor-not-allowed"
                data-testid="muni-page-last"
              >
                Última »
              </button>
            </div>
          </div>
        )}
      </section>

      {/* Modal */}
      {editing !== null && (
        <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
          <motion.form
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            onSubmit={save}
            className="bg-white border border-finapp-border rounded-xl max-w-2xl w-full p-8 max-h-[90vh] overflow-auto"
            data-testid="muni-form"
          >
            <div className="flex items-start justify-between mb-6">
              <div>
                <div className="label-track mb-1">{editing.codigo ? "Editar" : "Nuevo"}</div>
                <h3 className="font-heading font-bold text-2xl">{editing.codigo ? form.nombre || form.codigo : "Crear municipio"}</h3>
              </div>
              <button type="button" onClick={close} className="p-2 hover:bg-finapp-surface rounded-md"><X size={18} /></button>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
              <div>
                <label className="label-track block mb-2">Código *</label>
                <input className="field-input" required disabled={!!editing.codigo} name="codigo"
                  value={form.codigo} onChange={(e) => setForm({ ...form, codigo: e.target.value })} data-testid="muni-codigo" />
              </div>
              <div>
                <label className="label-track block mb-2">Nombre *</label>
                <input className="field-input" required value={form.nombre} name="nombre"
                  onChange={(e) => setForm({ ...form, nombre: e.target.value })} data-testid="muni-nombre" placeholder="Ayuntamiento de …" />
              </div>
              <div>
                <label className="label-track block mb-2">Calle</label>
                <input className="field-input" value={form.calle} onChange={(e) => setForm({ ...form, calle: e.target.value })} data-testid="muni-calle" />
              </div>
              <div>
                <label className="label-track block mb-2">Número</label>
                <input className="field-input" value={form.numero} onChange={(e) => setForm({ ...form, numero: e.target.value })} data-testid="muni-numero" />
              </div>
              <div>
                <label className="label-track block mb-2">Código postal</label>
                <input className="field-input" value={form.codigo_postal} onChange={(e) => setForm({ ...form, codigo_postal: e.target.value })} data-testid="muni-cp" />
              </div>
              <div>
                <label className="label-track block mb-2">Provincia</label>
                <ProvinciaCombobox
                  value={form.provincia}
                  onChange={(v) => setForm({ ...form, provincia: v })}
                  testId="muni-provincia"
                />
              </div>
              <div>
                <label className="label-track block mb-2">Teléfono</label>
                <input className="field-input" value={form.telefono_contacto} onChange={(e) => setForm({ ...form, telefono_contacto: e.target.value })} data-testid="muni-tel" />
              </div>
              <div className="sm:col-span-2">
                <label className="label-track block mb-2">Persona de contacto</label>
                <input className="field-input" value={form.persona_contacto} onChange={(e) => setForm({ ...form, persona_contacto: e.target.value })} data-testid="muni-persona" />
              </div>
            </div>
            {error && <div className="bg-[#FDF0EB] border border-[#C45B3A]/40 text-[#8B3A22] text-sm px-3 py-2 rounded-md mt-4">{error}</div>}
            <div className="flex justify-end gap-3 mt-8 pt-4 border-t border-finapp-border">
              <button type="button" onClick={close} className="btn-ghost">Cancelar</button>
              <button type="submit" disabled={saving} className="btn-primary" data-testid="muni-save">
                {saving ? "Guardando…" : "Guardar"}
              </button>
            </div>
          </motion.form>
        </div>
      )}
    </div>
  );
}
