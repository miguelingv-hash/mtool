import { useState } from "react";
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
import { AlertTriangle, Loader2, Trash2, Database } from "lucide-react";

/**
 * Página de mantenimiento administrativo del módulo SII.
 *
 * Por ahora ofrece una única acción destructiva: vaciar las colecciones
 * `facturas_sii`, `facturas_comercial`, `consultas` y `jobs`. Útil para
 * empezar de cero antes de re-importar tras un cambio en el pipeline
 * (ej. nueva versión de `extraer_csv.py`).
 *
 * Salvaguardas:
 *  - Permiso `sii.wipe` (admin por defecto).
 *  - Diálogo de confirmación con campo de texto: el usuario debe escribir
 *    literalmente `VACIAR` para que el botón se habilite.
 *  - Dry-run previo: muestra los totales actuales antes de borrar nada.
 */
export default function AdminMantenimiento() {
  const [counts, setCounts] = useState(null);
  const [loadingCounts, setLoadingCounts] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmText, setConfirmText] = useState("");
  const [wiping, setWiping] = useState(false);

  const dryRun = async () => {
    setLoadingCounts(true);
    try {
      const { data } = await api.post(
        "/admin/sii/vaciar-modulo?dry_run=true",
        { confirmacion: "VACIAR" },
      );
      setCounts(data.resumen);
    } catch (e) {
      toast.error(
        formatApiErrorDetail(e?.response?.data?.detail) ||
          "Error consultando el estado",
      );
    } finally {
      setLoadingCounts(false);
    }
  };

  const wipe = async () => {
    setWiping(true);
    try {
      const { data } = await api.post("/admin/sii/vaciar-modulo", {
        confirmacion: "VACIAR",
      });
      setCounts(data.resumen);
      const totalBorrados = Object.values(data.resumen).reduce(
        (acc, c) => acc + (c.borrados || 0),
        0,
      );
      toast.success(
        `Módulo SII vaciado · ${totalBorrados.toLocaleString("es-ES")} documentos borrados.`,
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

      <Card className="border-rose-200">
        <CardHeader>
          <div className="flex items-start gap-3">
            <div className="rounded-full bg-rose-100 p-2 mt-0.5">
              <Database className="h-4 w-4 text-rose-700" />
            </div>
            <div className="flex-1">
              <CardTitle className="text-base">Vaciar módulo SII</CardTitle>
              <CardDescription className="mt-1">
                Borra todas las facturas SII, todas las facturas comerciales,
                el log de consultas SOAP y los jobs asíncronos. No toca usuarios,
                roles, configuración de la comparativa ni el módulo de Tasas
                Municipales.
              </CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <Alert
            variant="destructive"
            className="bg-rose-50 border-rose-200 text-rose-900"
          >
            <AlertTriangle className="h-4 w-4" />
            <AlertTitle>Operación irreversible</AlertTitle>
            <AlertDescription>
              Esto borra datos de la base de datos. No hay papelera ni
              deshacer. Asegúrate de tener los CSV de origen disponibles
              para re-importar.
            </AlertDescription>
          </Alert>

          <div className="flex flex-wrap items-center gap-3">
            <Button
              variant="outline"
              onClick={dryRun}
              disabled={loadingCounts}
              data-testid="btn-sii-wipe-dryrun"
            >
              {loadingCounts ? (
                <Loader2 className="h-4 w-4 animate-spin mr-2" />
              ) : null}
              Ver estado actual
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                setConfirmText("");
                setConfirmOpen(true);
              }}
              disabled={wiping}
              data-testid="btn-sii-wipe-open"
            >
              <Trash2 className="h-4 w-4 mr-2" />
              Vaciar módulo SII
            </Button>
          </div>

          {counts && (
            <div className="rounded-md border border-slate-200 overflow-hidden">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Colección</TableHead>
                    <TableHead className="text-right">Antes</TableHead>
                    <TableHead className="text-right">Borrados</TableHead>
                    <TableHead className="text-right">Después</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody data-testid="sii-wipe-resumen">
                  {Object.entries(counts).map(([col, vals]) => (
                    <TableRow key={col}>
                      <TableCell className="font-mono text-xs">{col}</TableCell>
                      <TableCell className="text-right font-mono text-xs">
                        {(vals.antes || 0).toLocaleString("es-ES")}
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs text-rose-700">
                        {(vals.borrados || 0).toLocaleString("es-ES")}
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs">
                        {(vals.despues || 0).toLocaleString("es-ES")}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent data-testid="sii-wipe-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2 text-rose-700">
              <AlertTriangle className="h-5 w-5" />
              Vaciar módulo SII
            </AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-2 text-sm text-slate-700">
                <p>
                  Vas a borrar las colecciones <code>facturas_sii</code>,{" "}
                  <code>facturas_comercial</code>, <code>consultas</code> y{" "}
                  <code>jobs</code>.
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
