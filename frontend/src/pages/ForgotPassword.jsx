import { useState } from "react";
import { Link } from "react-router-dom";
import { api, formatApiErrorDetail } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Loader2, KeyRound, AlertTriangle, CheckCircle2, ArrowLeft } from "lucide-react";

/**
 * Página pública de recuperación de contraseña.
 * - Usuario introduce su email.
 * - Backend (POST /api/auth/forgot-password) genera un token de reset y envía
 *   email con el link `/activar/{token}` (mismo flujo que activación inicial).
 * - Por seguridad la respuesta es uniforme: nunca revela si el email existe.
 */
export default function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await api.post("/auth/forgot-password", { email: email.trim().toLowerCase() });
      setSent(true);
    } catch (err) {
      setError(formatApiErrorDetail(err?.response?.data?.detail) || err.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50 p-4">
      <Card className="w-full max-w-md" data-testid="page-forgot-password">
        <CardHeader className="space-y-1">
          <div className="mx-auto h-10 w-10 rounded-full bg-slate-900 text-white flex items-center justify-center">
            <KeyRound className="h-5 w-5" />
          </div>
          <CardTitle className="text-xl text-center">Recuperar contraseña</CardTitle>
        </CardHeader>
        <CardContent>
          {sent ? (
            <div className="space-y-4">
              <Alert data-testid="forgot-success">
                <CheckCircle2 className="h-4 w-4" />
                <AlertDescription>
                  Si el email existe en el sistema, recibirás un enlace para restablecer
                  tu contraseña en los próximos minutos. Revisa también la carpeta de spam.
                </AlertDescription>
              </Alert>
              <Link to="/login" className="block">
                <Button variant="outline" className="w-full" data-testid="forgot-back-btn-success">
                  <ArrowLeft className="h-4 w-4 mr-2" /> Volver al login
                </Button>
              </Link>
            </div>
          ) : (
            <form onSubmit={submit} className="space-y-4">
              <p className="text-sm text-muted-foreground">
                Introduce el email de tu cuenta. Te enviaremos un enlace para crear una
                contraseña nueva.
              </p>
              <div className="space-y-1.5">
                <Label htmlFor="forgot-email">Email</Label>
                <Input
                  id="forgot-email"
                  type="email"
                  autoComplete="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  data-testid="forgot-email-input"
                />
              </div>
              {error ? (
                <Alert variant="destructive" data-testid="forgot-error">
                  <AlertTriangle className="h-4 w-4" />
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              ) : null}
              <Button
                type="submit"
                className="w-full"
                disabled={submitting || !email}
                data-testid="forgot-submit-btn"
              >
                {submitting ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}
                Enviar enlace de recuperación
              </Button>
              <Link to="/login" className="block">
                <Button
                  type="button"
                  variant="ghost"
                  className="w-full"
                  data-testid="forgot-back-btn"
                >
                  <ArrowLeft className="h-4 w-4 mr-2" /> Volver al login
                </Button>
              </Link>
            </form>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
