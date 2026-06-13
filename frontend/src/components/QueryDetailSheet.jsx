import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import EstadoBadge from "@/components/EstadoBadge";
import { ESTADO_META } from "@/lib/api";

const Row = ({ label, value, mono = false }) => (
  <div className="grid grid-cols-[180px_1fr] gap-3 py-1.5">
    <div className="text-xs uppercase tracking-wider text-slate-500">
      {label}
    </div>
    <div className={`text-sm text-slate-900 ${mono ? "font-mono break-all" : ""}`}>
      {value || <span className="text-slate-400">—</span>}
    </div>
  </div>
);

export default function QueryDetailSheet({ open, onOpenChange, record }) {
  if (!record) return null;
  const e = record.entrada;
  const r = record.respuesta;
  const meta = ESTADO_META[r.estado_factura];
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="w-full sm:max-w-2xl overflow-y-auto"
        data-testid="detail-sheet"
      >
        <SheetHeader>
          <div className="flex items-center justify-between gap-3">
            <SheetTitle className="font-display text-xl">
              Detalle de consulta
            </SheetTitle>
            <EstadoBadge estado={r.estado_factura} />
          </div>
          <SheetDescription className="text-xs">
            {meta?.description}
          </SheetDescription>
        </SheetHeader>

        <div className="mt-4 border border-slate-200 bg-slate-50/40 p-4">
          <div className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-2">
            Identificación de la factura
          </div>
          <Row label="NIF emisor" value={e.nif_emisor} mono />
          <Row label="Nº serie factura" value={e.num_serie_factura} mono />
          <Row label="Fecha expedición" value={e.fecha_expedicion} mono />
          <Row label="Ejercicio / Período" value={`${e.ejercicio} / ${e.periodo}`} mono />
          <Separator className="my-3" />
          <Row label="NIF titular" value={e.nif_titular} mono />
          <Row label="Nombre titular" value={e.nombre_titular} />
          <Row label="Entorno" value={e.entorno} />
        </div>

        <div className="mt-4">
          <Tabs defaultValue="resumen">
            <TabsList className="grid grid-cols-3 w-full rounded-none">
              <TabsTrigger value="resumen" data-testid="tab-resumen">
                Resumen
              </TabsTrigger>
              <TabsTrigger value="request" data-testid="tab-request">
                SOAP Request
              </TabsTrigger>
              <TabsTrigger value="response" data-testid="tab-response">
                SOAP Response
              </TabsTrigger>
            </TabsList>

            <TabsContent value="resumen" className="mt-3 border border-slate-200 p-4">
              <Row label="Estado envío" value={r.estado_envio} />
              <Row label="Estado factura" value={r.estado_factura} />
              <Row label="Código error" value={r.codigo_error_registro} mono />
              <Row label="Descripción error" value={r.descripcion_error_registro} />
              <Separator className="my-3" />
              <Row label="Nº registro presentación" value={r.num_registro_presentacion} mono />
              <Row label="CSV AEAT" value={r.csv} mono />
              <Row label="Timestamp presentación" value={r.timestamp_presentacion} mono />
              <Separator className="my-3" />
              <Row label="Endpoint SOAP" value={r.endpoint} mono />
              <Row label="WSDL" value={r.wsdl} mono />
            </TabsContent>

            <TabsContent value="request" className="mt-3">
              <ScrollArea className="h-[420px] border border-slate-200 bg-slate-950">
                <pre
                  className="p-4 text-xs text-slate-100 font-mono whitespace-pre-wrap break-all"
                  data-testid="soap-request-xml"
                >
                  {record.soap_request_xml}
                </pre>
              </ScrollArea>
            </TabsContent>

            <TabsContent value="response" className="mt-3">
              <ScrollArea className="h-[420px] border border-slate-200 bg-slate-950">
                <pre
                  className="p-4 text-xs text-slate-100 font-mono whitespace-pre-wrap break-all"
                  data-testid="soap-response-xml"
                >
                  {record.soap_response_xml}
                </pre>
              </ScrollArea>
            </TabsContent>
          </Tabs>
        </div>
      </SheetContent>
    </Sheet>
  );
}
