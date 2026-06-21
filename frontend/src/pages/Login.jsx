import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate, Link } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Loader2, ShieldCheck, AlertTriangle, Mail, RotateCw } from "lucide-react";

export default function Login() {
  const { login, verifyMfa, resendMfa, user } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = location.state?.from || "/";

  const [step, setStep] = useState("creds"); // "creds" | "mfa"
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [challenge, setChallenge] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [info, setInfo] = useState(null);
  const [resendIn, setResendIn] = useState(0); // segundos restantes para reenvío

  const otpRef = useRef(null);

  useEffect(() => {
    if (user) navigate(from, { replace: true });
  }, [user, from, navigate]);

  // Cuenta atrás para habilitar el botón "Reenviar"
  useEffect(() => {
    if (resendIn <= 0) return;
    const t = setTimeout(() => setResendIn((s) => Math.max(0, s - 1)), 1000);
    return () => clearTimeout(t);
  }, [resendIn]);

  // Foco automático al input OTP al pasar al paso MFA
  useEffect(() => {
    if (step === "mfa" && otpRef.current) otpRef.current.focus();
  }, [step]);

  const submitCreds = async (e) => {
    e.preventDefault();
    setError(null); setInfo(null); setSubmitting(true);
    const res = await login(email.trim().toLowerCase(), password);
    setSubmitting(false);
    if (!res.ok) { setError(res.error || "Error de autenticación"); return; }
    if (res.mfaRequired) {
      setChallenge(res.challenge);
      setStep("mfa");
      setResendIn(60);
      setInfo(`Hemos enviado un código a ${res.challenge.email_hint}. Caduca en ${res.challenge.ttl_minutes} min.`);
    } else {
      navigate(from, { replace: true });
    }
  };

  const submitOtp = async (e) => {
    e.preventDefault();
    if (!challenge) return;
    setError(null); setInfo(null); setSubmitting(true);
    const res = await verifyMfa(challenge.challenge_id, code.trim());
    setSubmitting(false);
    if (!res.ok) { setError(res.error || "Código incorrecto"); return; }
    navigate(from, { replace: true });
  };

  const doResend = async () => {
    if (!challenge || resendIn > 0) return;
    setError(null); setInfo(null);
    const res = await resendMfa(challenge.challenge_id);
    if (!res.ok) { setError(res.error || "No se pudo reenviar el código"); return; }
    setInfo(`Código reenviado a ${res.email_hint}. Revisa tu bandeja de entrada.`);
    setCode("");
    setResendIn(60);
  };

  const cancelMfa = () => {
    setStep("creds");
    setChallenge(null);
    setCode("");
    setError(null);
    setInfo(null);
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50 p-4">
      <Card className="w-full max-w-sm" data-testid="page-login">
        <CardHeader className="space-y-1">
          <div className="mx-auto h-10 w-10 rounded-full bg-slate-900 text-white flex items-center justify-center">
            {step === "creds" ? <ShieldCheck className="h-5 w-5" /> : <Mail className="h-5 w-5" />}
          </div>
          <CardTitle className="text-xl text-center">
            {step === "creds" ? "Corporate App" : "Verificación en 2 pasos"}
          </CardTitle>
          <p className="text-center text-sm text-muted-foreground">
            {step === "creds" ? "Inicia sesión para continuar"
                              : "Introduce el código de 6 dígitos que te hemos enviado por email"}
          </p>
        </CardHeader>
        <CardContent>
          {step === "creds" ? (
            <form onSubmit={submitCreds} className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="login-email">Email</Label>
                <Input id="login-email" type="email" autoComplete="username"
                  value={email} onChange={(e) => setEmail(e.target.value)} required
                  data-testid="login-email-input" />
              </div>
              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <Label htmlFor="login-password">Contraseña</Label>
                  <Link to="/olvide-password" className="text-xs text-muted-foreground hover:underline">
                    ¿Olvidaste tu contraseña?
                  </Link>
                </div>
                <Input id="login-password" type="password" autoComplete="current-password"
                  value={password} onChange={(e) => setPassword(e.target.value)} required
                  data-testid="login-password-input" />
              </div>
              {error && (
                <Alert variant="destructive" data-testid="login-error">
                  <AlertTriangle className="h-4 w-4" />
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              )}
              <Button type="submit" className="w-full" disabled={submitting} data-testid="login-submit-btn">
                {submitting && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
                Iniciar sesión
              </Button>
            </form>
          ) : (
            <form onSubmit={submitOtp} className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="mfa-code">Código de 6 dígitos</Label>
                <Input
                  id="mfa-code" ref={otpRef} inputMode="numeric"
                  pattern="[0-9]{6}" maxLength={6} autoComplete="one-time-code"
                  value={code}
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                  required placeholder="••••••"
                  className="text-center text-2xl tracking-[0.6em] font-mono"
                  data-testid="login-otp-input"
                />
              </div>
              {info && (
                <Alert data-testid="login-info">
                  <Mail className="h-4 w-4" />
                  <AlertDescription>{info}</AlertDescription>
                </Alert>
              )}
              {error && (
                <Alert variant="destructive" data-testid="login-error">
                  <AlertTriangle className="h-4 w-4" />
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              )}
              <Button type="submit" className="w-full" disabled={submitting || code.length < 6}
                data-testid="login-verify-btn">
                {submitting && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
                Verificar y entrar
              </Button>
              <div className="flex items-center justify-between text-xs">
                <Button type="button" variant="ghost" size="sm" onClick={cancelMfa}
                  data-testid="login-cancel-mfa">← Volver</Button>
                <Button type="button" variant="ghost" size="sm" onClick={doResend}
                  disabled={resendIn > 0} data-testid="login-resend-btn">
                  <RotateCw className="h-3 w-3 mr-1" />
                  {resendIn > 0 ? `Reenviar (${resendIn}s)` : "Reenviar código"}
                </Button>
              </div>
            </form>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
