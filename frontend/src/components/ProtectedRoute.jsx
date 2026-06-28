import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";
import { Loader2 } from "lucide-react";

/**
 * <ProtectedRoute requires="users.manage">
 *   <Page />
 * </ProtectedRoute>
 *
 *  - Si `user === undefined` (checking) → muestra spinner.
 *  - Si `user === null` → redirige a /login conservando la URL.
 *  - Si `requires` se especifica y el user no lo tiene → 403.
 *  - Si `requiresAny` se especifica (array de claves), pasa si el user tiene
 *    al menos UNA. Útil para pantallas que agregan varios flujos cada uno
 *    con su permiso (p.ej. "Carga de datos").
 */
export default function ProtectedRoute({ children, requires, requiresAny }) {
  const { user, hasPermission } = useAuth();
  const location = useLocation();

  if (user === undefined) {
    return (
      <div className="min-h-[60vh] flex items-center justify-center text-muted-foreground gap-2">
        <Loader2 className="h-4 w-4 animate-spin" />
        <span>Comprobando sesión...</span>
      </div>
    );
  }
  if (user === null) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  if (requires && !hasPermission(requires)) {
    return (
      <div className="min-h-[60vh] flex items-center justify-center" data-testid="page-forbidden">
        <div className="max-w-md text-center space-y-2">
          <h1 className="text-2xl font-semibold">Acceso restringido</h1>
          <p className="text-muted-foreground">
            Tu perfil no tiene el permiso <code className="font-mono">{requires}</code> necesario para esta página.
            Habla con un administrador para que te lo asigne.
          </p>
        </div>
      </div>
    );
  }
  if (
    Array.isArray(requiresAny) &&
    requiresAny.length > 0 &&
    !requiresAny.some((p) => hasPermission(p))
  ) {
    return (
      <div className="min-h-[60vh] flex items-center justify-center" data-testid="page-forbidden">
        <div className="max-w-md text-center space-y-2">
          <h1 className="text-2xl font-semibold">Acceso restringido</h1>
          <p className="text-muted-foreground">
            Tu perfil necesita al menos uno de estos permisos:{" "}
            {requiresAny.map((p, i) => (
              <span key={p}>
                <code className="font-mono">{p}</code>
                {i < requiresAny.length - 1 ? ", " : ""}
              </span>
            ))}
            . Habla con un administrador.
          </p>
        </div>
      </div>
    );
  }
  return children;
}
