import { useEffect, useState } from "react";
import { api, formatApiErrorDetail } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "sonner";
import { Loader2, Plus, Save, Trash2 } from "lucide-react";

export default function AdminRoles() {
  const [roles, setRoles] = useState([]);
  const [catalog, setCatalog] = useState([]);
  const [loading, setLoading] = useState(true);
  const [savingName, setSavingName] = useState(null);

  // New role form
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      const [r, c] = await Promise.all([api.get("/admin/roles"), api.get("/admin/permissions/catalog")]);
      setRoles(r.data); setCatalog(c.data);
    } catch (e) {
      toast.error(formatApiErrorDetail(e?.response?.data?.detail) || "Error cargando roles");
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); }, []);

  const togglePerm = (role, perm) => {
    setRoles((rs) => rs.map((r) => r.name === role.name
      ? { ...r, permissions: r.permissions.includes(perm)
          ? r.permissions.filter((p) => p !== perm)
          : [...r.permissions, perm] }
      : r,
    ));
  };

  const save = async (role) => {
    setSavingName(role.name);
    try {
      await api.patch(`/admin/roles/${role.name}`, {
        description: role.description,
        permissions: role.permissions,
      });
      toast.success(`Rol "${role.name}" guardado`);
    } catch (e) {
      toast.error(formatApiErrorDetail(e?.response?.data?.detail) || "Error al guardar");
    } finally {
      setSavingName(null);
    }
  };

  const create = async (e) => {
    e.preventDefault();
    try {
      await api.post("/admin/roles", { name: name.trim(), description, permissions: [] });
      toast.success(`Rol "${name}" creado`);
      setName(""); setDescription("");
      load();
    } catch (e) {
      toast.error(formatApiErrorDetail(e?.response?.data?.detail) || "Error al crear rol");
    }
  };

  const remove = async (role) => {
    if (!window.confirm(`¿Eliminar rol "${role.name}"?`)) return;
    try {
      await api.delete(`/admin/roles/${role.name}`);
      toast.success("Rol eliminado");
      load();
    } catch (e) {
      toast.error(formatApiErrorDetail(e?.response?.data?.detail) || "Error al eliminar");
    }
  };

  return (
    <div className="space-y-6" data-testid="page-admin-roles">
      <header>
        <h1 className="text-3xl font-semibold tracking-tight">Roles y permisos</h1>
        <p className="text-muted-foreground">Crea perfiles a medida de tu organización y asigna permisos granulares por funcionalidad.</p>
      </header>

      <Card>
        <CardHeader><CardTitle className="text-base flex items-center gap-2"><Plus className="h-4 w-4" /> Nuevo rol</CardTitle></CardHeader>
        <CardContent>
          <form onSubmit={create} className="grid gap-3 md:grid-cols-3">
            <div className="space-y-1.5"><Label>Nombre (slug)</Label>
              <Input required value={name} onChange={(e)=>setName(e.target.value)} pattern="^[a-z0-9_-]+$" placeholder="auditor" data-testid="new-role-name" />
              <p className="text-xs text-muted-foreground">Sólo minúsculas, números, _ y -</p>
            </div>
            <div className="space-y-1.5 md:col-span-2"><Label>Descripción</Label>
              <Input value={description} onChange={(e)=>setDescription(e.target.value)} placeholder="Para qué sirve este rol" data-testid="new-role-description" />
            </div>
            <div className="md:col-span-3">
              <Button type="submit" data-testid="new-role-submit"><Plus className="h-4 w-4 mr-2" />Crear rol</Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {loading ? (
        <div className="text-muted-foreground py-4 flex items-center gap-2"><Loader2 className="h-4 w-4 animate-spin" /> Cargando...</div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {roles.map((role) => {
            const isAdmin = role.name === "admin";
            return (
              <Card key={role.name} data-testid={`role-card-${role.name}`}>
                <CardHeader className="pb-3 flex flex-row items-center justify-between">
                  <div>
                    <CardTitle className="text-base">{role.name}{isAdmin ? <Badge className="ml-2 bg-slate-900">admin</Badge> : null}</CardTitle>
                    <p className="text-xs text-muted-foreground mt-0.5">{role.description || <em>(sin descripción)</em>}</p>
                  </div>
                  {!isAdmin ? (
                    <Button size="sm" variant="ghost" onClick={() => remove(role)} data-testid={`role-delete-${role.name}`}>
                      <Trash2 className="h-3.5 w-3.5 text-red-600" />
                    </Button>
                  ) : null}
                </CardHeader>
                <CardContent>
                  <div className="space-y-2 max-h-96 overflow-y-auto pr-1">
                    {catalog.map((p) => {
                      const checked = role.permissions.includes(p.key);
                      const lockedWildcard = isAdmin && p.key === "*";
                      return (
                        <label key={p.key} className="flex items-start gap-2 text-sm py-1 rounded hover:bg-slate-50 px-1">
                          <Checkbox
                            checked={checked}
                            disabled={lockedWildcard}
                            onCheckedChange={() => togglePerm(role, p.key)}
                            data-testid={`role-${role.name}-perm-${p.key}`}
                          />
                          <div>
                            <code className="font-mono text-xs">{p.key}</code>
                            <div className="text-xs text-muted-foreground">{p.label}</div>
                          </div>
                        </label>
                      );
                    })}
                  </div>
                  <div className="pt-3 mt-3 border-t flex justify-end">
                    <Button size="sm" onClick={() => save(role)} disabled={savingName === role.name} data-testid={`role-save-${role.name}`}>
                      {savingName === role.name ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : <Save className="h-4 w-4 mr-2" />}
                      Guardar cambios
                    </Button>
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
