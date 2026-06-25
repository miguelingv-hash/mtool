import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, formatApiErrorDetail } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Loader2, KeyRound, AlertTriangle, CheckCircle2 } from "lucide-react";

export default function SetupPassword() {
  const { token } = useParams();
  const navigate = useNavigate();
  const { refreshMe } = useAuth();

  const [checking, setChecking] = useState(true);
  const [tokenInfo, setTokenInfo] = useState(null);
  const [tokenError, setTokenError] = useState(null);

  const [pwd, setPwd] = useState("");
  const [pwd2, setPwd2] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    let alive = true;
    setChecking(true);
    api.get(`/auth/setup/${token}/check`)
      .then(({ data }) => { if (alive) { setTokenInfo(data); setTokenError(null); } })
      .catch((e) => { if (alive) setTokenError(formatApiErrorDetail(e?.response?.data?.detail) || e.message); })
      .finally(() => { if (alive) setChecking(false); });
    return () => { alive = false; };
  }, [token]);

  const submit = async (e) => {
    e.preventDefault();
    setSubmitError(null);
    if (pwd.length < 8) {
      setSubmitError("La contraseña debe tener al menos 8 caracteres");
      return;
    }
    if (pwd !== pwd2) {
      setSubmitError("Las contraseñas no coinciden");
      return;
    }
    setSubmitting(true);
    try {
      await api.post(`/auth/setup/${token}`, { password: pwd });
      setDone(true);
      // Auto-login: el backend ha emitido las cookies. Refrescamos contexto.
      await refreshMe();
      // Pequeño delay para mostrar el "éxito" antes de navegar
      setTimeout(() => navigate("/", { replace: true }), 1200);
    } catch (e) {
      setSubmitError(formatApiErrorDetail(e?.response?.data?.detail) || e.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50 p-4">
      <Card className="w-full max-w-md" data-testid="page-setup">
        <CardHeader className="space-y-1">
          <div className="mx-auto h-10 w-10 rounded-full bg-slate-900 text-white flex items-center justify-center">
            <KeyRound className="h-5 w-5" />
          </div>
          <CardTitle className="text-xl text-center">
            {tokenInfo?.motivo === "reset" ? "Restablece tu contraseña" : "Establece tu contraseña"}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {checking ? (
            <div className="flex items-center gap-2 text-muted-foreground py-4">
              <Loader2 className="h-4 w-4 animate-spin" /> Comprobando enlace...
            </div>
          ) : tokenError ? (
            <Alert variant="destructive" data-testid="setup-token-error">
              <AlertTriangle className="h-4 w-4" />
              <AlertDescription>{tokenError}. Solicita un nuevo enlace al administrador.</AlertDescription>
            </Alert>
          ) : done ? (
            <Alert data-testid="setup-success">
              <CheckCircle2 className="h-4 w-4" />
              <AlertDescription>
                {tokenInfo?.motivo === "reset"
                  ? "¡Contraseña restablecida! Redirigiendo..."
                  : "¡Cuenta activada! Redirigiendo..."}
              </AlertDescription>
            </Alert>
          ) : (
            <form onSubmit={submit} className="space-y-4">
              <div className="text-sm text-muted-foreground">
                Hola <strong>{tokenInfo?.name || tokenInfo?.email}</strong>,{" "}
                {tokenInfo?.motivo === "reset" ? "define una contraseña nueva." : "define tu contraseña."}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="pwd">Nueva contraseña</Label>
                <Input id="pwd" type="password" autoComplete="new-password"
                  value={pwd} onChange={(e) => setPwd(e.target.value)}
                  data-testid="setup-pwd-input" required minLength={8} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="pwd2">Repite la contraseña</Label>
                <Input id="pwd2" type="password" autoComplete="new-password"
                  value={pwd2} onChange={(e) => setPwd2(e.target.value)}
                  data-testid="setup-pwd2-input" required minLength={8} />
              </div>
              {submitError ? (
                <Alert variant="destructive" data-testid="setup-error">
                  <AlertTriangle className="h-4 w-4" />
                  <AlertDescription>{submitError}</AlertDescription>
                </Alert>
              ) : null}
              <Button type="submit" className="w-full" disabled={submitting} data-testid="setup-submit-btn">
                {submitting ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}
                {tokenInfo?.motivo === "reset" ? "Restablecer contraseña" : "Activar cuenta"}
              </Button>
            </form>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
