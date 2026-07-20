// Mapa hardcodeado de NIF titular → sociedad, para uso en la UI (no
// requiere permiso admin, alineado con `router_admin._SOCIEDADES_SEED`).
export const SOCIEDADES_POR_NIF = {
  A74251836: {
    codigo: "BASER",
    nombre: "BASER Comercializadora de Referencia S.A.",
    color: "bg-orange-100 text-orange-800 border-orange-200",
  },
  A95000295: {
    codigo: "TOTALENERGIES",
    nombre: "TotalEnergies Clientes S.A.U.",
    color: "bg-blue-100 text-blue-800 border-blue-200",
  },
};

export function detectarSociedad(nifTitular) {
  if (!nifTitular) return null;
  const nif = String(nifTitular).trim().toUpperCase();
  return SOCIEDADES_POR_NIF[nif] || null;
}
