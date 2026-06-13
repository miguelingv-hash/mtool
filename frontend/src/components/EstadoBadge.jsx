import { ESTADO_META } from "@/lib/api";
import { CheckCircle2, AlertTriangle, XCircle, Ban } from "lucide-react";

const ICONS = {
  Correcta: CheckCircle2,
  AceptadaConErrores: AlertTriangle,
  Anulada: Ban,
  NoRegistrada: XCircle,
};

export default function EstadoBadge({ estado }) {
  const meta = ESTADO_META[estado] || ESTADO_META.NoRegistrada;
  const Icon = ICONS[estado] || XCircle;
  return (
    <span className={`pill ${meta.pill}`} data-testid={`estado-${estado}`}>
      <Icon className="h-3 w-3" strokeWidth={2} />
      {meta.label}
    </span>
  );
}
