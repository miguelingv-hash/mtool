/**
 * Helper de presentación para el campo `origen_comercial`.
 *
 * Internamente el modelo almacena el código corto ("SAP") pero en UI debe
 * mostrarse el nombre del módulo de origen ("SAP FI") para reflejar que es
 * el módulo de facturación financiera de SAP, no SAP genérico.
 */
export function labelOrigenComercial(origen) {
  if (!origen) return origen;
  if (origen === "SAP") return "SAP FI";
  return origen;
}
