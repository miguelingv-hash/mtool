import { useEffect, useState } from "react";
import { api, formatApiErrorDetail } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { toast } from "sonner";
import { AlertTriangle, Loader2, Trash2, Database, FileSpreadsheet, FileSearch, RefreshCw, CheckCircle2 } from "lucide-react";

/**
 * Página de mantenimiento administrativo del módulo SII.
 *
 * Permite vaciar la BD de forma **selectiva**:
 *  - `todo`      → SII + Comercial + log SOAP + jobs async
 *  - `sii`       → sólo facturas SII (lo que se sube con Newman) + log SOAP
 *  - `comercial` → sólo facturas comerciales (SAP FI + SIGLO)
 *
 * Salvaguardas:
 *  - Permiso `sii.wipe` (admin por defecto).
 *  - Diálogo de confirmación con campo de texto: el usuario debe escribir
 *    literalmente `VACIAR` para que el botón se habilite.
 *  - Dry-run previo: muestra los totales actuales antes de borrar.
 */

const SCOPES = {
  todo: {
    label: "Vaciar TODO",
    short: "todo (SII + Comercial)",
    desc: "Borra facturas SII, facturas comerciales, log de consultas SOAP y jobs asíncronos. Reset completo del módulo.",
    icon: Database,
    color: "rose",
    collections: ["facturas_sii", "facturas_comercial", "consultas", "jobs"],
  },
  sii: {
    label: "Vaciar sólo SII",
    short: "sólo SII (Newman)",
    desc: "Borra únicamente las facturas SII subidas con Newman y el log de consultas SOAP. Conserva las facturas comerciales.",
    icon: FileSearch,
    color: "amber",
    collections: ["facturas_sii", "consultas"],
  },
  comercial: {
    label: "Vaciar sólo Comercial",
    short: "sólo Comercial (SAP FI + SIGLO)",
    desc: "Borra únicamente las facturas comerciales (cargas SAP FI y SIGLO). Conserva las facturas SII y el log SOAP.",
    icon: FileSpreadsheet,
    color: "sky",
    collections: ["facturas_comercial"],
  },
};

export default function AdminMantenimiento() {
  const [countsByScope, setCountsByScope] = useState({});
  const [loadingScope, setLoadingScope] = useState(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmScope, setConfirmScope] = useState("todo");
  const [confirmText, setConfirmText] = useState("");
  const [wiping, setWiping] = useState(false);

  // iter31: regenerar denormalización SII → Comercial. Ejecuta el
  // backfill completo (tipo_factura + snapshot _sii_* + importe_total)
  // en background. El backend responde inmediatamente con status=running
  // y el frontend hace polling al endpoint /status hasta que termine
  // (evita el timeout Cloudflare 100s para procesos largos).
  const [backfillRunning, setBackfillRunning] = useState(false);
  const [backfillPhase, setBackfillPhase] = useState(null);
  const [backfillReport, setBackfillReport] = useState(null);
  const [backfillLastRun, setBackfillLastRun] = useState(null);

  const PHASE_LABEL = {
    tipo_factura: "Fase 1/3 · tipo_factura",
    snapshot: "Fase 2/3 · snapshot _sii_*",
    importe_total: "Fase 3/3 · importe_total",
  };

  // Al montar, consulta si hay un job en curso o completado recientemente.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { data } = await api.get(
          "/admin/backfill-tipo-factura/status",
        );
        if (cancelled) return;
        if (data.status === "running") {
          setBackfillRunning(true);
          setBackfillPhase(data.phase);
        } else if (data.status === "done" && data.finished_at) {
          setBackfillReport(data);
          setBackfillLastRun(new Date(data.finished_at));
        }
      } catch (_e) {
        // ignore
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const runBackfill = async () => {
    setBackfillRunning(true);
    setBackfillReport(null);
    setBackfillPhase("tipo_factura");
    try {
      await api.post("/admin/backfill-tipo-factura");
    } catch (e) {
      const status = e?.response?.status;
      if (status !== 409) {
        // 409 = ya hay un job en curso: seguimos con el polling normal.
        toast.error(
          formatApiErrorDetail(e?.response?.data?.detail) ||
            "Error lanzando la regeneración",
        );
        setBackfillRunning(false);
        return;
      }
    }

    // Poll status cada 3s hasta status !== "running".
    const poll = async () => {
      try {
        const { data } = await api.get(
          "/admin/backfill-tipo-factura/status",
        );
        setBackfillPhase(data.phase);
        if (data.status === "running") {
          setTimeout(poll, 3000);
          return;
        }
        setBackfillRunning(false);
        if (data.status === "done") {
          setBackfillReport(data);
          setBackfillLastRun(
            data.finished_at ? new Date(data.finished_at) : new Date(),
          );
          toast.success("Denormalización regenerada correctamente");
        } else if (data.status === "error") {
          toast.error(
            `Error en la regeneración: ${data.error || "desconocido"}`,
          );
        }
      } catch (_e) {
        setTimeout(poll, 5000); // reintenta si el status endpoint falla
      }
    };
    setTimeout(poll, 2000);
  };

  const dryRun = async (scope) => {
    setLoadingScope(scope);
    try {
      const { data } = await api.post(
        "/admin/sii/vaciar-modulo?dry_run=true",
        { confirmacion: "VACIAR", scope },
      );
      setCountsByScope((prev) => ({ ...prev, [scope]: data.resumen }));
    } catch (e) {
      toast.error(
        formatApiErrorDetail(e?.response?.data?.detail) ||
          "Error consultando el estado",
      );
    } finally {
      setLoadingScope(null);
    }
  };

  const openConfirm = (scope) => {
    setConfirmScope(scope);
    setConfirmText("");
    setConfirmOpen(true);
  };

  const wipe = async () => {
    setWiping(true);
    try {
      const { data } = await api.post("/admin/sii/vaciar-modulo", {
        confirmacion: "VACIAR",
        scope: confirmScope,
      });
      setCountsByScope((prev) => ({ ...prev, [confirmScope]: data.resumen }));
      const totalBorrados = Object.values(data.resumen).reduce(
        (acc, c) => acc + (c.borrados || 0),
        0,
      );
      toast.success(
        `${SCOPES[confirmScope].short}: ${totalBorrados.toLocaleString("es-ES")} documentos borrados.`,
      );
      setConfirmOpen(false);
      setConfirmText("");
    } catch (e) {
      toast.error(
        formatApiErrorDetail(e?.response?.data?.detail) ||
          "Error vaciando el módulo",
      );
    } finally {
      setWiping(false);
    }
  };

  const scopeInfo = SCOPES[confirmScope];
  const counts = countsByScope[confirmScope];
  const totalDocs = counts
    ? Object.values(counts).reduce((acc, c) => acc + (c.antes || 0), 0)
    : 0;

  return (
    <div className="space-y-6" data-testid="admin-mantenimiento-page">
      <header>
        <h1 className="text-2xl font-semibold text-slate-900">
          Mantenimiento
        </h1>
        <p className="text-sm text-slate-600 mt-1">
          Operaciones administrativas avanzadas. Úsalas con cuidado.
        </p>
      </header>

      {/* iter31 · Denormalización SII → Comercial (operación no destructiva) */}
      <Card
        id="denormalizacion"
        className="border-emerald-200 bg-emerald-50/30"
        data-testid="backfill-denorm-card"
      >
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-emerald-900 text-lg">
            <RefreshCw className="h-5 w-5 text-emerald-700" />
            Regenerar denormalización SII → Comercial
          </CardTitle>
          <CardDescription>
            Recalcula los campos snapshot (`tipo_factura`, `_has_sii`,
            `_sii_base`, `_sii_cuota`, `_sii_importe_total`,
            `_sii_tipo_rectificativa`, `importe_total` derivado) que
            usan los fast-paths de la Comparativa. <b>Ejecuta esto
            después de cada carga masiva de CSVs</b> (SII o Comercial)
            para que el listado, KPIs y agregaciones muestren datos
            correctos. Operación idempotente y no destructiva.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-center gap-3">
            <Button
              onClick={runBackfill}
              disabled={backfillRunning}
              className="bg-emerald-600 hover:bg-emerald-700"
              data-testid="backfill-denorm-run"
            >
              {backfillRunning ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  {backfillPhase
                    ? PHASE_LABEL[backfillPhase] || "Regenerando…"
                    : "Regenerando…"}
                </>
              ) : (
                <>
                  <RefreshCw className="h-4 w-4 mr-2" />
                  Regenerar ahora
                </>
              )}
            </Button>
            {backfillLastRun && !backfillRunning && (
              <div className="flex items-center gap-1.5 text-xs text-emerald-800">
                <CheckCircle2 className="h-3.5 w-3.5" />
                Última ejecución: {backfillLastRun.toLocaleString("es-ES")}
              </div>
            )}
            {backfillRunning && (
              <div className="text-xs text-emerald-700">
                Corre en segundo plano · 1-2 min con 1M docs. Puedes cerrar
                esta pestaña; al volver verás el resultado.
              </div>
            )}
          </div>

          {backfillReport && (
            <div
              className="border border-emerald-200 bg-white p-3 space-y-2 text-xs"
              data-testid="backfill-denorm-report"
            >
              <div className="font-medium text-emerald-900">
                Resultado
              </div>
              <BackfillReportBlock
                label="Fase 1 · tipo_factura"
                data={backfillReport.report}
              />
              <BackfillReportBlock
                label="Fase 2 · snapshot _sii_*"
                data={backfillReport.report_snapshot}
              />
              <BackfillReportBlock
                label="Fase 3 · importe_total comercial"
                data={backfillReport.report_importe_total}
              />
            </div>
          )}
        </CardContent>
      </Card>

      <Alert
        variant="destructive"
        className="bg-rose-50 border-rose-200 text-rose-900"
      >
        <AlertTriangle className="h-4 w-4" />
        <AlertTitle>Operaciones irreversibles</AlertTitle>
        <AlertDescription>
          Las acciones de esta página borran datos de la base de datos. No
          hay papelera ni deshacer. Asegúrate de tener los CSV de origen
          disponibles para re-importar.
        </AlertDescription>
      </Alert>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4" data-testid="wipe-scopes">
        {Object.entries(SCOPES).map(([scope, info]) => {
          const Icon = info.icon;
          const c = countsByScope[scope];
          const total = c
            ? Object.values(c).reduce((acc, v) => acc + (v.antes || 0), 0)
            : null;
          const borderClass =
            info.color === "rose"
              ? "border-rose-200"
              : info.color === "amber"
                ? "border-amber-200"
                : "border-sky-200";
          const bgClass =
            info.color === "rose"
              ? "bg-rose-100 text-rose-700"
              : info.color === "amber"
                ? "bg-amber-100 text-amber-700"
                : "bg-sky-100 text-sky-700";
          const btnClass =
            info.color === "rose"
              ? "bg-rose-600 hover:bg-rose-700"
              : info.color === "amber"
                ? "bg-amber-600 hover:bg-amber-700"
                : "bg-sky-600 hover:bg-sky-700";
          return (
            <Card
              key={scope}
              className={borderClass}
              data-testid={`wipe-scope-card-${scope}`}
            >
              <CardHeader>
                <div className="flex items-start gap-3">
                  <div className={`rounded-full p-2 mt-0.5 ${bgClass}`}>
                    <Icon className="h-4 w-4" />
                  </div>
                  <div className="flex-1">
                    <CardTitle className="text-base">{info.label}</CardTitle>
                    <CardDescription className="mt-1">
                      {info.desc}
                    </CardDescription>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="text-[11px] font-mono text-slate-500 leading-relaxed">
                  Colecciones:{" "}
                  {info.collections.map((col, i) => (
                    <span key={col}>
                      <code className="bg-slate-100 px-1 py-0.5">{col}</code>
                      {i < info.collections.length - 1 ? ", " : ""}
                    </span>
                  ))}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => dryRun(scope)}
                    disabled={loadingScope === scope}
                    data-testid={`btn-dryrun-${scope}`}
                  >
                    {loadingScope === scope ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin mr-2" />
                    ) : null}
                    Ver estado
                  </Button>
                  <Button
                    size="sm"
                    onClick={() => openConfirm(scope)}
                    disabled={wiping}
                    className={btnClass}
                    data-testid={`btn-wipe-open-${scope}`}
                  >
                    <Trash2 className="h-3.5 w-3.5 mr-2" />
                    Vaciar
                  </Button>
                </div>
                {c && (
                  <div className="rounded-md border border-slate-200 overflow-hidden">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead className="text-[10px]">Colección</TableHead>
                          <TableHead className="text-right text-[10px]">Antes</TableHead>
                          <TableHead className="text-right text-[10px]">Borr.</TableHead>
                          <TableHead className="text-right text-[10px]">Después</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody data-testid={`wipe-resumen-${scope}`}>
                        {Object.entries(c).map(([col, vals]) => (
                          <TableRow key={col}>
                            <TableCell className="font-mono text-[10px]">{col}</TableCell>
                            <TableCell className="text-right font-mono text-[10px]">
                              {(vals.antes || 0).toLocaleString("es-ES")}
                            </TableCell>
                            <TableCell className="text-right font-mono text-[10px] text-rose-700">
                              {(vals.borrados || 0).toLocaleString("es-ES")}
                            </TableCell>
                            <TableCell className="text-right font-mono text-[10px]">
                              {(vals.despues || 0).toLocaleString("es-ES")}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                )}
                {total !== null && (
                  <p className="text-[11px] text-slate-600">
                    Total a borrar:{" "}
                    <span className="font-semibold text-slate-900">
                      {total.toLocaleString("es-ES")}
                    </span>{" "}
                    documentos
                  </p>
                )}
              </CardContent>
            </Card>
          );
        })}
      </div>

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent data-testid="sii-wipe-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2 text-rose-700">
              <AlertTriangle className="h-5 w-5" />
              {scopeInfo?.label}
            </AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-2 text-sm text-slate-700">
                <p>
                  Vas a borrar{" "}
                  {scopeInfo?.collections.map((col, i) => (
                    <span key={col}>
                      <code className="bg-slate-100 px-1">{col}</code>
                      {i < scopeInfo.collections.length - 1 ? ", " : ""}
                    </span>
                  ))}
                  .
                </p>
                {counts && (
                  <p className="text-rose-700 font-medium">
                    Se borrarán {totalDocs.toLocaleString("es-ES")} documentos
                    en total.
                  </p>
                )}
                <p>
                  Para confirmar, escribe <strong>VACIAR</strong> a
                  continuación:
                </p>
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="py-2">
            <Label htmlFor="confirm-input" className="sr-only">
              Texto de confirmación
            </Label>
            <Input
              id="confirm-input"
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              placeholder="VACIAR"
              autoComplete="off"
              data-testid="sii-wipe-confirm-input"
            />
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel
              disabled={wiping}
              data-testid="sii-wipe-cancel"
            >
              Cancelar
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={wipe}
              disabled={confirmText !== "VACIAR" || wiping}
              className="bg-rose-600 hover:bg-rose-700"
              data-testid="sii-wipe-confirm"
            >
              {wiping ? (
                <Loader2 className="h-4 w-4 animate-spin mr-2" />
              ) : (
                <Trash2 className="h-4 w-4 mr-2" />
              )}
              Vaciar definitivamente
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function BackfillReportBlock({ label, data }) {
  if (!data) return null;
  const entries = Object.entries(data).filter(
    ([k]) => !k.startsWith("_") && k !== "ok",
  );
  return (
    <div className="border-l-2 border-emerald-400 pl-2">
      <div className="text-[11px] font-medium text-slate-700">{label}</div>
      <div className="text-[10px] font-mono text-slate-600 grid grid-cols-2 gap-x-4">
        {entries.map(([k, v]) => (
          <div key={k}>
            <span className="text-slate-500">{k}:</span>{" "}
            <span className="text-slate-900">
              {typeof v === "number"
                ? v.toLocaleString("es-ES")
                : typeof v === "object"
                ? JSON.stringify(v)
                : String(v)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

