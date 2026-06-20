/**
 * PdfViewer — visor PDF simple basado en iframe del blob URL.
 *
 * Se sustituye la integración con `react-pdf` del proyecto original por
 * un iframe nativo del navegador. Suficiente para previsualizar los PDFs
 * generados y compatible con cualquier navegador moderno sin dependencias.
 */
export default function PdfViewer({ src }) {
  if (!src) return null;
  return (
    <iframe
      src={src}
      title="Vista previa PDF"
      className="w-full h-full min-h-[70vh] border-0 bg-white"
      data-testid="pdf-viewer-iframe"
    />
  );
}
