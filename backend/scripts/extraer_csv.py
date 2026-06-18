"""
extraer_csv.py
==============

Convierte el log de Newman (export.txt) en un CSV limpio (facturas.csv).

Maneja correctamente las particularidades de Newman:
  - Códigos ANSI de color (\x1b[...m)
  - Bordes de tabla:  │  ...  │
  - Líneas largas partidas (wrap) en varias filas de la tabla
  - BOM UTF-8 al inicio del fichero
  - Marcadores CSVHEAD: y CSVROW: emitidos por la colección Postman

USO  (desde CMD o PowerShell, NO desde el REPL `>>>` de Python):
    python extraer_csv.py
    python extraer_csv.py export.txt facturas.csv
    python extraer_csv.py "C:\\ruta\\export.txt" "C:\\ruta\\facturas.csv"

Si lo ejecutas sin argumentos buscará `export.txt` en el directorio actual
y generará `facturas.csv` en el mismo directorio.
"""

import os
import re
import sys

# ---------------------------------------------------------------------------
# Regex utilidades
# ---------------------------------------------------------------------------
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# Quita SOLO los bordes laterales (│ ó |) y los espacios de padding adyacentes.
# Mantiene el contenido intacto.
BORDER_LEFT_RE = re.compile(r"^[\s\u2502|]+")
BORDER_RIGHT_RE = re.compile(r"[\s\u2502|]+$")
# Líneas que sólo contienen bordes de tabla (┌─┬─┐ ├─┤ └─┴─┘ etc.)
BOX_LINE_RE = re.compile(r"^[\s\u2500-\u257F]+$")

MARKER_HEAD = "CSVHEAD:"
MARKER_ROW = "CSVROW:"


def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


def strip_borders(s: str) -> str:
    s = BORDER_LEFT_RE.sub("", s)
    s = BORDER_RIGHT_RE.sub("", s)
    return s


def normalizar_lineas(raw_lines):
    """
    Limpia ANSI/bordes de cada línea y descarta líneas que son solo borde de tabla.
    Devuelve una lista de strings ya 'pelados'.
    """
    limpio = []
    for line in raw_lines:
        line = line.rstrip("\r\n")
        line = strip_ansi(line)
        if BOX_LINE_RE.match(line):
            continue
        line = strip_borders(line)
        if not line:
            continue
        limpio.append(line)
    return limpio


def reensamblar_marcadores(lineas):
    """
    Newman parte líneas largas en varios renglones dentro de la 'tabla' de output.
    Recorremos las líneas y, cuando encontramos un marcador (CSVHEAD: o CSVROW:),
    seguimos concatenando renglones hasta que aparezca el siguiente marcador o
    una línea claramente fuera de marcador.

    Devolvemos una lista de (tipo, contenido) donde tipo ∈ {'HEAD', 'ROW'}
    """
    bloques = []
    actual_tipo = None
    actual_buffer = []

    def flush():
        nonlocal actual_tipo, actual_buffer
        if actual_tipo is not None:
            texto = "".join(actual_buffer).strip()
            if texto:
                bloques.append((actual_tipo, texto))
        actual_tipo = None
        actual_buffer = []

    for line in lineas:
        # Detecta inicio de marcador en cualquier punto de la línea
        idx_head = line.find(MARKER_HEAD)
        idx_row = line.find(MARKER_ROW)

        # Tomamos el marcador que aparezca primero (si hay alguno)
        if idx_head != -1 and (idx_row == -1 or idx_head < idx_row):
            flush()
            actual_tipo = "HEAD"
            actual_buffer = [line[idx_head + len(MARKER_HEAD):]]
            continue
        if idx_row != -1:
            flush()
            actual_tipo = "ROW"
            actual_buffer = [line[idx_row + len(MARKER_ROW):]]
            continue

        # No es marcador nuevo: si estamos dentro de uno, es continuación (wrap)
        if actual_tipo is not None:
            # Heurística: si la línea contiene texto típico de Newman fuera de
            # console.log (p.ej. 'GET', 'POST', 'iteration', 'response', etc.)
            # cortamos. Pero como ya quitamos los bordes, lo más fiable es
            # detectar líneas que empiezan claramente con palabras de status.
            stripped = line.strip()
            if stripped.startswith(("→", "GET ", "POST ", "iteration ",
                                     "executed", "requests", "test-scripts",
                                     "prerequest-scripts", "assertions",
                                     "total run duration", "total data received",
                                     "average response time", "↳", "✓", "✗",
                                     "#", "[", "executing")):
                flush()
                continue
            actual_buffer.append(stripped)
        # else: ignoramos líneas fuera de marcador

    flush()
    return bloques


def extraer(input_path: str, output_path: str) -> int:
    if not os.path.isfile(input_path):
        print(f"[ERROR] No existe el fichero de entrada: {input_path}")
        return 2

    # Leemos en utf-8-sig para descartar BOM si existe
    with open(input_path, "r", encoding="utf-8-sig", errors="replace") as f:
        raw_lines = f.readlines()

    lineas = normalizar_lineas(raw_lines)
    bloques = reensamblar_marcadores(lineas)

    cabecera = None
    filas = []
    for tipo, contenido in bloques:
        if tipo == "HEAD" and cabecera is None:
            cabecera = contenido
        elif tipo == "ROW":
            filas.append(contenido)

    if cabecera is None and not filas:
        print("[ERROR] No se encontraron marcadores CSVHEAD:/CSVROW: en el log.")
        print("        Asegúrate de que la colección Postman emite ambos marcadores.")
        return 3

    with open(output_path, "w", encoding="utf-8", newline="") as out:
        if cabecera:
            out.write(cabecera + "\n")
        for row in filas:
            out.write(row + "\n")

    print(f"[OK] Cabecera detectada: {'sí' if cabecera else 'no'}")
    print(f"[OK] Filas extraídas:    {len(filas)}")
    print(f"[OK] CSV generado en:    {output_path}")
    return 0


def main(argv):
    if len(argv) >= 3:
        inp, out = argv[1], argv[2]
    elif len(argv) == 2:
        inp, out = argv[1], "facturas.csv"
    else:
        inp, out = "export.txt", "facturas.csv"

    return extraer(inp, out)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
