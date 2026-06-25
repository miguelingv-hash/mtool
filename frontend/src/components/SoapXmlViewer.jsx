import { useMemo, useState } from "react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { ChevronRight, ChevronDown, Copy, Check } from "lucide-react";

const LOCAL = (tag) => tag.split(":").pop() || tag;

const TEXT_FOR_TAGS = new Set([
  "IDVersionSii", "TipoComunicacion",
  "NombreRazon", "NIF", "IDOtro", "NombreCompleto",
  "Ejercicio", "Periodo",
  "NumSerieFacturaEmisor", "FechaExpedicionFacturaEmisor",
  "TipoFactura", "ClaveRegimenEspecialOTrascendencia",
  "ImporteTotal", "DescripcionOperacion",
  "FechaOperacion", "BaseImponible", "TipoImpositivo", "CuotaRepercutida",
  "EstadoEnvio", "EstadoRegistro", "CSV", "TimestampPresentacion",
  "NumRegistroPresentacion", "CodigoErrorRegistro", "DescripcionErrorRegistro",
]);

function extraerResumen(doc) {
  if (!doc || !doc.documentElement) return [];
  const out = [];
  const seen = new Map();
  const stack = [doc.documentElement];
  while (stack.length) {
    const node = stack.pop();
    if (!node || node.nodeType !== 1) continue;
    const local = LOCAL(node.nodeName);
    if (TEXT_FOR_TAGS.has(local) && (!node.children || node.children.length === 0)) {
      const txt = node.textContent ? node.textContent.trim() : "";
      if (txt) {
        const count = (seen.get(local) || 0) + 1;
        seen.set(local, count);
        out.push({ label: count > 1 ? local + " [" + count + "]" : local, value: txt });
      }
    }
    // Push children al stack — recorrido en pre-order
    if (node.children) {
      for (let i = node.children.length - 1; i >= 0; i--) {
        stack.push(node.children[i]);
      }
    }
  }
  return out;
}

function prettyXml(xml) {
  if (!xml) return "";
  try {
    const reg = /(>)(<)(\/*)/g;
    const formatted = String(xml).replace(reg, "$1\n$2$3");
    let pad = 0;
    const lines = formatted.split("\n");
    const out = [];
    for (const line of lines) {
      let indent = 0;
      if (/^<\/\w/.test(line)) pad = Math.max(pad - 1, 0);
      else if (/^<\w[^>]*[^/]>.*$/.test(line) && !/<\/\w/.test(line)) indent = 1;
      out.push("  ".repeat(pad) + line);
      pad += indent;
    }
    return out.join("\n");
  } catch (e) {
    return xml;
  }
}

/**
 * Aplana el árbol DOM en una lista lineal con depth, para evitar recursión JSX
 * (que confunde al transpilador de Babel en algunos casos).
 */
function flattenXmlNode(root) {
  const items = [];
  const walk = (node, depth) => {
    if (!node || node.nodeType !== 1) return;
    const local = LOCAL(node.nodeName);
    const children = node.children ? Array.from(node.children) : [];
    const attrs = node.attributes
      ? Array.from(node.attributes).filter((a) => !a.name.startsWith("xmlns"))
      : [];
    const text = node.textContent ? node.textContent.trim() : "";
    items.push({
      depth,
      local,
      attrs,
      isLeaf: children.length === 0,
      text: children.length === 0 ? text : "",
      childCount: children.length,
    });
    for (const c of children) walk(c, depth + 1);
  };
  walk(root, 0);
  return items;
}

function XmlRow({ item, collapsed, onToggle }) {
  const padding = item.depth * 14;
  if (item.isLeaf) {
    return (
      <div
        className="flex flex-wrap items-baseline gap-2 py-0.5 font-mono text-xs"
        style={{ paddingLeft: padding }}
      >
        <span className="text-indigo-700 font-semibold">{item.local}</span>
        <span className="text-slate-500">:</span>
        <span className="text-slate-900 break-all">
          {item.text || <em className="text-slate-400">vacío</em>}
        </span>
      </div>
    );
  }
  return (
    <button
      type="button"
      onClick={onToggle}
      className="flex items-center gap-1 py-0.5 font-mono text-xs text-indigo-700 font-semibold hover:underline"
      style={{ paddingLeft: padding }}
    >
      {collapsed ? <ChevronRight className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
      <span>{item.local}</span>
      <span className="ml-2 text-slate-400 font-normal">
        {item.childCount} {item.childCount === 1 ? "campo" : "campos"}
      </span>
    </button>
  );
}

function XmlTree({ items }) {
  // collapsed: índice → bool. Por defecto desplegados los 2 primeros niveles.
  const [collapsed, setCollapsed] = useState({});

  // Calcula visibilidad: una fila es visible si ningún ancestro está colapsado.
  const visible = [];
  const collapseStack = []; // pila de {depth, collapsed}
  for (let i = 0; i < items.length; i++) {
    const it = items[i];
    while (collapseStack.length && collapseStack[collapseStack.length - 1].depth >= it.depth) {
      collapseStack.pop();
    }
    const hiddenByAncestor = collapseStack.some((c) => c.collapsed);
    if (!hiddenByAncestor) visible.push({ ...it, index: i });
    if (!it.isLeaf) {
      collapseStack.push({ depth: it.depth, collapsed: !!collapsed[i] });
    }
  }

  return (
    <div>
      {visible.map((row) => (
        <XmlRow
          key={row.index}
          item={row}
          collapsed={!!collapsed[row.index]}
          onToggle={() =>
            setCollapsed((prev) => ({ ...prev, [row.index]: !prev[row.index] }))
          }
        />
      ))}
    </div>
  );
}

export default function SoapXmlViewer({ xml, testid }) {
  const [copied, setCopied] = useState(false);

  const { items, resumen, pretty, error } = useMemo(() => {
    if (!xml) return { items: [], resumen: [], pretty: "", error: null };
    try {
      const parser = new DOMParser();
      const d = parser.parseFromString(xml, "application/xml");
      const err = d.getElementsByTagName("parsererror")[0];
      if (err) {
        return {
          items: [],
          resumen: [],
          pretty: prettyXml(xml),
          error: err.textContent || "XML inválido",
        };
      }
      return {
        items: flattenXmlNode(d.documentElement),
        resumen: extraerResumen(d),
        pretty: prettyXml(xml),
        error: null,
      };
    } catch (e) {
      return { items: [], resumen: [], pretty: xml, error: e.message };
    }
  }, [xml]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(pretty || xml);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch (e) {
      // best effort
    }
  };

  if (!xml) {
    return (
      <div className="border border-slate-200 p-4 text-sm text-slate-500">
        Sin contenido SOAP disponible.
      </div>
    );
  }

  return (
    <Tabs defaultValue="resumen" className="w-full" data-testid={testid}>
      <TabsList className="grid grid-cols-3 w-full rounded-none">
        <TabsTrigger value="resumen" data-testid={testid + "-tab-resumen"}>Resumen</TabsTrigger>
        <TabsTrigger value="detalle" data-testid={testid + "-tab-detalle"}>Detalle</TabsTrigger>
        <TabsTrigger value="xml" data-testid={testid + "-tab-xml"}>XML</TabsTrigger>
      </TabsList>

      <TabsContent value="resumen" className="mt-3 border border-slate-200 p-4">
        {error ? (
          <p className="text-sm text-rose-700">No se pudo parsear el XML: {error}</p>
        ) : resumen.length === 0 ? (
          <p className="text-sm text-slate-500">Sin campos legibles. Mira el XML crudo.</p>
        ) : (
          <div className="space-y-1">
            {resumen.map((r, i) => (
              <div key={i} className="grid grid-cols-[180px_1fr] gap-3 py-1.5">
                <div className="text-xs uppercase tracking-wider text-slate-500">
                  {r.label}
                </div>
                <div className="text-sm text-slate-900 font-mono break-all">
                  {r.value}
                </div>
              </div>
            ))}
          </div>
        )}
      </TabsContent>

      <TabsContent value="detalle" className="mt-3">
        <ScrollArea className="h-[420px] border border-slate-200 bg-slate-50/60">
          <div className="p-3">
            {items.length > 0 ? (
              <XmlTree items={items} />
            ) : (
              <p className="text-sm text-slate-500">XML no estructurado.</p>
            )}
          </div>
        </ScrollArea>
      </TabsContent>

      <TabsContent value="xml" className="mt-3">
        <div className="relative">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={copy}
            className="absolute top-2 right-2 z-10 h-7 text-xs"
            data-testid={testid + "-copy-btn"}
          >
            {copied ? <Check className="h-3 w-3 mr-1" /> : <Copy className="h-3 w-3 mr-1" />}
            {copied ? "Copiado" : "Copiar"}
          </Button>
          <ScrollArea className="h-[420px] border border-slate-200 bg-slate-950">
            <pre
              className="p-4 pr-20 text-xs text-slate-100 font-mono whitespace-pre"
              data-testid={testid + "-xml-raw"}
            >
              {pretty}
            </pre>
          </ScrollArea>
        </div>
      </TabsContent>
    </Tabs>
  );
}

