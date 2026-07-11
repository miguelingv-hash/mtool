import "@/App.css";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Toaster } from "@/components/ui/sonner";
import { EnvProvider } from "@/contexts/EnvContext";
import { AuthProvider } from "@/contexts/AuthContext";
import ProtectedRoute from "@/components/ProtectedRoute";
import Layout from "@/components/Layout";
import Dashboard from "@/pages/Dashboard";
import UnitQuery from "@/pages/UnitQuery";
import BatchQuery from "@/pages/BatchQuery";
import History from "@/pages/History";
import Logs from "@/pages/Logs";
import Comparativa from "@/pages/Comparativa";
import CargaDatos from "@/pages/CargaDatos";
import Configuracion from "@/pages/Configuracion";
import ConciliacionNewman from "@/pages/ConciliacionNewman";
import Login from "@/pages/Login";
import SetupPassword from "@/pages/SetupPassword";
import ForgotPassword from "@/pages/ForgotPassword";
import AdminUsuarios from "@/pages/AdminUsuarios";
import AdminRoles from "@/pages/AdminRoles";
import AdminMantenimiento from "@/pages/AdminMantenimiento";
import AdminImportsLog from "@/pages/AdminImportsLog";
import TasasPanel from "@/pages/tasas/TasasPanel";
import TasasTasas from "@/pages/tasas/TasasTasas";
import TasasMunicipios from "@/pages/tasas/TasasMunicipios";
import TasasSettings from "@/pages/tasas/TasasSettings";
import TasasJobDetail from "@/pages/tasas/TasasJobDetail";
import PagosVentanillaGeneracion from "@/pages/pagos_ventanilla/Generacion";
import PagosVentanillaHistorico from "@/pages/pagos_ventanilla/Historico";

function App() {
  return (
    <AuthProvider>
      <EnvProvider>
        <BrowserRouter>
          <Routes>
            {/* Públicas */}
            <Route path="/login" element={<Login />} />
            <Route path="/activar/:token" element={<SetupPassword />} />
            <Route path="/olvide-password" element={<ForgotPassword />} />

            {/* Privadas */}
            <Route element={<ProtectedRoute><Layout /></ProtectedRoute>}>
              <Route index element={<Dashboard />} />
              <Route path="/comparativa" element={<ProtectedRoute requires="comparativa.view"><Comparativa /></ProtectedRoute>} />
              <Route path="/carga-datos" element={<ProtectedRoute requiresAny={["conciliacion.view", "conciliacion.import", "comercial.import", "consultas.mensual"]}><CargaDatos /></ProtectedRoute>} />
              <Route path="/consulta" element={<ProtectedRoute requires="consultas.unitaria"><UnitQuery /></ProtectedRoute>} />
              <Route path="/batch" element={<ProtectedRoute requires="consultas.batch"><BatchQuery /></ProtectedRoute>} />
              <Route path="/historico" element={<History />} />
              <Route path="/logs" element={<ProtectedRoute requires="logs.view"><Logs /></ProtectedRoute>} />
              {/* /conciliacion mantiene la URL para back-compat, pero ahora
                  vive como tab dentro de /carga-datos. Lo redirigimos para no
                  romper bookmarks ni cualquier deep-link previo del usuario. */}
              <Route path="/conciliacion" element={<Navigate to="/carga-datos?tab=newman" replace />} />
              <Route path="/configuracion" element={<ProtectedRoute requires="comparativa.edit_config"><Configuracion /></ProtectedRoute>} />
              <Route path="/tasas-municipales" element={<ProtectedRoute requires="tasas.view"><TasasPanel /></ProtectedRoute>} />
              <Route path="/tasas-municipales/tasas" element={<ProtectedRoute requires="tasas.manage"><TasasTasas /></ProtectedRoute>} />
              <Route path="/tasas-municipales/municipios" element={<ProtectedRoute requires="tasas.view"><TasasMunicipios /></ProtectedRoute>} />
              <Route path="/tasas-municipales/ajustes" element={<ProtectedRoute requires="tasas.admin"><TasasSettings /></ProtectedRoute>} />
              <Route path="/tasas-municipales/jobs/:jobId" element={<ProtectedRoute requires="tasas.view"><TasasJobDetail /></ProtectedRoute>} />
              <Route path="/pagos-ventanilla/generacion" element={<ProtectedRoute requires="pagos_ventanilla.manage"><PagosVentanillaGeneracion /></ProtectedRoute>} />
              <Route path="/pagos-ventanilla/historico" element={<ProtectedRoute requires="pagos_ventanilla.view"><PagosVentanillaHistorico /></ProtectedRoute>} />
              <Route path="/admin/usuarios" element={<ProtectedRoute requires="users.manage"><AdminUsuarios /></ProtectedRoute>} />
              <Route path="/admin/roles" element={<ProtectedRoute requires="roles.manage"><AdminRoles /></ProtectedRoute>} />
              <Route path="/admin/mantenimiento" element={<ProtectedRoute requires="sii.wipe"><AdminMantenimiento /></ProtectedRoute>} />
              <Route path="/admin/imports-log" element={<ProtectedRoute requires="audit.view"><AdminImportsLog /></ProtectedRoute>} />
            </Route>
          </Routes>
        </BrowserRouter>
        <Toaster position="top-right" richColors closeButton />
      </EnvProvider>
    </AuthProvider>
  );
}

export default App;
