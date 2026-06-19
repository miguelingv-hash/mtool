import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, formatApiErrorDetail } from "@/lib/api";

/**
 * AuthContext
 * -----------
 * `user`:
 *   - `undefined`  → checking initial session (mostrar splash/loader)
 *   - `null`       → no autenticado
 *   - objeto user  → autenticado (incluye `permisos: string[]`)
 */
const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(undefined);

  const refreshMe = useCallback(async () => {
    try {
      const { data } = await api.get("/auth/me");
      setUser(data);
      return data;
    } catch (e) {
      // 401 esperado si no hay sesión
      setUser(null);
      return null;
    }
  }, []);

  useEffect(() => { refreshMe(); }, [refreshMe]);

  // Interceptor: ante 401, intentar refresh transparente UNA vez antes de
  // marcar al usuario como no logueado. Evita logouts molestos por access
  // token expirado. NO se aplica a las rutas /auth/* (login, refresh, etc.)
  // para evitar bucles infinitos.
  useEffect(() => {
    const id = api.interceptors.response.use(
      (r) => r,
      async (err) => {
        const cfg = err?.config || {};
        const status = err?.response?.status;
        const url = String(cfg.url || "");
        if (status === 401 && !cfg.__retried && !url.includes("/auth/")) {
          cfg.__retried = true;
          try {
            await api.post("/auth/refresh");
            return api(cfg);
          } catch {
            // refresh falló → la sesión no es recuperable
            setUser(null);
            // Devolvemos un error "silencioso" para que el componente que
            // hizo la petición original no muestre el 401 como runtime error.
            return Promise.reject({ __auth_required: true, status: 401 });
          }
        }
        return Promise.reject(err);
      },
    );
    return () => api.interceptors.response.eject(id);
  }, []);

  const login = useCallback(async (email, password) => {
    try {
      const { data } = await api.post("/auth/login", { email, password });
      setUser(data);
      return { ok: true, user: data };
    } catch (e) {
      return { ok: false, error: formatApiErrorDetail(e?.response?.data?.detail) || e.message };
    }
  }, []);

  const logout = useCallback(async () => {
    try { await api.post("/auth/logout"); } catch { /* no-op */ }
    setUser(null);
  }, []);

  const hasPermission = useCallback((perm) => {
    if (!user || !Array.isArray(user.permisos)) return false;
    if (user.permisos.includes("*")) return true;
    return user.permisos.includes(perm);
  }, [user]);

  return (
    <AuthContext.Provider value={{ user, login, logout, refreshMe, setUser, hasPermission }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth fuera de AuthProvider");
  return ctx;
}
