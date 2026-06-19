import { useEffect, useState } from "react";
import { api, formatApiErrorDetail } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { toast } from "sonner";
import { Loader2, UserPlus, Trash2, Send, AlertTriangle } from "lucide-react";

const STATUS_LABELS = {
  active: { label: "Activo", className: "bg-emerald-100 text-emerald-900 border-emerald-200" },
  pending: { label: "Pendiente activación", className: "bg-amber-100 text-amber-900 border-amber-200" },
  disabled: { label: "Deshabilitado", className: "bg-slate-100 text-slate-700 border-slate-200" },
};

export default function AdminUsuarios() {
  const { user: me } = useAuth();
  const [users, setUsers] = useState([]);
  const [roles, setRoles] = useState([]);
  const [loading, setLoading] = useState(true);

  // Invite form
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState("usuario");
  const [inviting, setInviting] = useState(false);

  // Delete confirm
  const [toDelete, setToDelete] = useState(null);

  const load = async () => {
    setLoading(true);
    try {
      const [u, r] = await Promise.all([api.get("/admin/users"), api.get("/admin/roles")]);
      setUsers(u.data); setRoles(r.data);
    } catch (e) {
      toast.error(formatApiErrorDetail(e?.response?.data?.detail) || "Error cargando usuarios");
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); }, []);

  const invite = async (e) => {
    e.preventDefault();
    setInviting(true);
    try {
      const { data } = await api.post("/admin/users", { email: email.trim().toLowerCase(), name: name.trim(), role });
      toast.success(`Invitación enviada a ${data.user.email}`);
      if (data.activation_link_status !== "sent" && data.activation_token) {
        // Resend no envió: damos al admin el link directamente
        toast.warning(`Email no enviado. Enlace de activación: /activar/${data.activation_token}`, { duration: 20000 });
      }
      setEmail(""); setName(""); setRole("usuario");
      load();
    } catch (e) {
      toast.error(formatApiErrorDetail(e?.response?.data?.detail) || "Error al invitar");
    } finally {
      setInviting(false);
    }
  };

  const updateUser = async (id, patch) => {
    try {
      await api.patch(`/admin/users/${id}`, patch);
      toast.success("Usuario actualizado");
      load();
    } catch (e) {
      toast.error(formatApiErrorDetail(e?.response?.data?.detail) || "Error al actualizar");
    }
  };

  const resend = async (id) => {
    try {
      const { data } = await api.post(`/admin/users/${id}/resend`);
      toast.success("Enlace reenviado");
      if (data.activation_link_status !== "sent" && data.activation_token) {
        toast.warning(`Email no enviado. Enlace: /activar/${data.activation_token}`, { duration: 20000 });
      }
    } catch (e) {
      toast.error(formatApiErrorDetail(e?.response?.data?.detail) || "Error al reenviar");
    }
  };

  const remove = async () => {
    if (!toDelete) return;
    try {
      await api.delete(`/admin/users/${toDelete._id}`);
      toast.success("Usuario eliminado");
      setToDelete(null);
      load();
    } catch (e) {
      toast.error(formatApiErrorDetail(e?.response?.data?.detail) || "Error al eliminar");
    }
  };

  return (
    <div className="space-y-6" data-testid="page-admin-users">
      <header>
        <h1 className="text-3xl font-semibold tracking-tight">Usuarios</h1>
        <p className="text-muted-foreground">Invita nuevos usuarios, asigna roles y gestiona el ciclo de vida de las cuentas.</p>
      </header>

      <Card>
        <CardHeader><CardTitle className="text-base flex items-center gap-2"><UserPlus className="h-4 w-4" /> Invitar usuario</CardTitle></CardHeader>
        <CardContent>
          <form onSubmit={invite} className="grid gap-3 md:grid-cols-4">
            <div className="space-y-1.5"><Label>Email</Label>
              <Input type="email" required value={email} onChange={(e)=>setEmail(e.target.value)} placeholder="user@empresa.com" data-testid="invite-email-input" /></div>
            <div className="space-y-1.5"><Label>Nombre</Label>
              <Input required value={name} onChange={(e)=>setName(e.target.value)} placeholder="Nombre y apellidos" data-testid="invite-name-input" /></div>
            <div className="space-y-1.5"><Label>Rol</Label>
              <Select value={role} onValueChange={setRole}>
                <SelectTrigger data-testid="invite-role-select"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {roles.map((r) => <SelectItem key={r.name} value={r.name}>{r.name}</SelectItem>)}
                </SelectContent>
              </Select></div>
            <div className="flex items-end">
              <Button type="submit" disabled={inviting} className="w-full" data-testid="invite-submit-btn">
                {inviting ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : <Send className="h-4 w-4 mr-2" />} Enviar invitación
              </Button>
            </div>
          </form>
          <p className="text-xs text-muted-foreground mt-3">
            El usuario recibirá un enlace por email para definir su contraseña. Si no llega, lo verás aquí mismo como notificación tras invitar.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">Usuarios ({users.length})</CardTitle></CardHeader>
        <CardContent>
          {loading ? (
            <div className="text-muted-foreground py-4 flex items-center gap-2"><Loader2 className="h-4 w-4 animate-spin" /> Cargando...</div>
          ) : (
            <div className="overflow-x-auto rounded-lg border">
              <table className="w-full text-sm" data-testid="users-table">
                <thead className="bg-slate-50/60 text-xs uppercase tracking-wider text-slate-500">
                  <tr>
                    <th className="text-left px-4 py-2">Email</th>
                    <th className="text-left px-4 py-2">Nombre</th>
                    <th className="text-left px-4 py-2">Rol</th>
                    <th className="text-left px-4 py-2">Estado</th>
                    <th className="text-left px-4 py-2">Último login</th>
                    <th className="text-right px-4 py-2">Acciones</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((u) => {
                    const isMe = u._id === me?._id;
                    const st = STATUS_LABELS[u.status] || { label: u.status, className: "" };
                    return (
                      <tr key={u._id} className="border-t hover:bg-slate-50/60" data-testid={`user-row-${u.email}`}>
                        <td className="px-4 py-2 font-mono text-xs">{u.email}{isMe ? <span className="text-emerald-700 ml-2">(tú)</span> : null}</td>
                        <td className="px-4 py-2">{u.name}</td>
                        <td className="px-4 py-2">
                          <Select value={u.role} onValueChange={(v) => updateUser(u._id, { role: v })} disabled={isMe}>
                            <SelectTrigger className="h-8 w-32"><SelectValue /></SelectTrigger>
                            <SelectContent>{roles.map((r) => <SelectItem key={r.name} value={r.name}>{r.name}</SelectItem>)}</SelectContent>
                          </Select>
                        </td>
                        <td className="px-4 py-2"><Badge variant="outline" className={st.className}>{st.label}</Badge></td>
                        <td className="px-4 py-2 text-xs text-muted-foreground">{u.last_login ? new Date(u.last_login).toLocaleString("es-ES") : "—"}</td>
                        <td className="px-4 py-2 text-right space-x-1">
                          {u.status === "pending" ? (
                            <Button size="sm" variant="outline" onClick={() => resend(u._id)} data-testid={`resend-${u.email}`}>
                              <Send className="h-3.5 w-3.5 mr-1" /> Reenviar
                            </Button>
                          ) : null}
                          <Select value={u.status} onValueChange={(v) => updateUser(u._id, { status: v })} disabled={isMe}>
                            <SelectTrigger className="h-8 w-36 inline-flex"><SelectValue /></SelectTrigger>
                            <SelectContent>
                              <SelectItem value="active">Activo</SelectItem>
                              <SelectItem value="disabled">Deshabilitado</SelectItem>
                            </SelectContent>
                          </Select>
                          {!isMe && (
                            <Button size="sm" variant="ghost" onClick={() => setToDelete(u)} data-testid={`delete-${u.email}`}>
                              <Trash2 className="h-3.5 w-3.5 text-red-600" />
                            </Button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <AlertDialog open={!!toDelete} onOpenChange={(open) => !open && setToDelete(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>¿Eliminar usuario?</AlertDialogTitle>
            <AlertDialogDescription>
              Esta acción borrará permanentemente a <strong>{toDelete?.email}</strong> y todos sus tokens. No se puede deshacer.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction onClick={remove} data-testid="confirm-delete-user">Eliminar</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
