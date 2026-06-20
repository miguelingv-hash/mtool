export const PROVINCIAS_ES = [
  "Álava", "Albacete", "Alicante", "Almería", "Asturias", "Ávila",
  "Badajoz", "Barcelona", "Burgos", "Cáceres", "Cádiz", "Cantabria",
  "Castellón", "Ceuta", "Ciudad Real", "Córdoba", "Cuenca", "Girona",
  "Granada", "Guadalajara", "Gipuzkoa", "Huelva", "Huesca", "Illes Balears",
  "Jaén", "La Coruña", "La Rioja", "Las Palmas", "León", "Lleida",
  "Lugo", "Madrid", "Málaga", "Melilla", "Murcia", "Navarra",
  "Ourense", "Palencia", "Pontevedra", "Salamanca", "Santa Cruz de Tenerife",
  "Segovia", "Sevilla", "Soria", "Tarragona", "Teruel", "Toledo",
  "Valencia", "Valladolid", "Bizkaia", "Zamora", "Zaragoza",
];

function normalize(s) {
  return (s || "").toString().toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
}

export function filterProvincias(query) {
  const q = normalize(query);
  if (!q) return PROVINCIAS_ES;
  return PROVINCIAS_ES.filter((p) => normalize(p).includes(q));
}
