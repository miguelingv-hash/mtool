# Descarga SII por línea de comandos

Script standalone para descargas masivas del SII sin depender del backend web
(evita timeouts de Cloudflare / ingress / hot-reload).

## Por qué usarlo

El backend web puede sufrir reinicios o timeouts en descargas muy largas. Este
script se ejecuta directamente en tu PC / servidor, mantiene la conexión HTTPS
con la AEAT durante horas y persiste cada página en `facturas_sii` con
`bulk_write` (índice único en `num_serie_factura`, sin duplicados).

## Requisitos

- Python 3.10+
- Acceso de red a la AEAT y a la MongoDB de la aplicación.
- El certificado `.pfx` accesible localmente.

### Instalar dependencias (primera vez)

Desde la carpeta `backend/` del repo, en una terminal:

**Windows (CMD / PowerShell):**
```cmd
cd C:\Users\TUUSUARIO\Documents\GitHub\mtool\backend
python -m pip install -r scripts\requirements-cli.txt
```

**Linux / macOS:**
```bash
cd ~/mtool/backend
python3 -m pip install -r scripts/requirements-cli.txt
```

> Si prefieres instalar todas las dependencias del backend (necesarias si vas
> a ejecutar también el servidor web), usa `pip install -r requirements.txt`
> en su lugar.

## Uso rápido

1. Copia la plantilla:
   ```bash
   cp /app/backend/scripts/descarga.txt.example mi_descarga.txt
   ```

2. Edita `mi_descarga.txt` con tus datos (ruta del .pfx, password, NIF, ejercicio…).

3. Lanza:
   ```bash
   cd /app/backend
   python scripts/descargar_sii.py --config mi_descarga.txt
   ```

   Para limitar el número de páginas:
   ```bash
   python scripts/descargar_sii.py --config mi_descarga.txt --max-paginas 5
   ```

## Salida esperada

```
======================================================================
  Descarga masiva de facturas SII — CLI
======================================================================
  NIF titular  : A95000295
  Razón social : TotalEnergies Clientes S.A.U.
  Periodo      : 2026-05
  Entorno      : produccion
  Mongo        : sii_db@localhost:27017
  facturas_sii : 130,015 docs (antes)
  Máx páginas  : todas
======================================================================
[pág   1] +10000 facturas | total persistidas:  10,000 | bulk 0.8s | tiempo total: 0.6min
[pág   2] +10000 facturas | total persistidas:  20,000 | bulk 0.9s | tiempo total: 1.2min
...
[pág  47] + 3210 facturas | total persistidas: 463,210 | bulk 0.5s | tiempo total: 48.3min
======================================================================
  DESCARGA COMPLETADA
======================================================================
  Facturas devueltas por el SII : 463,210
  Persistidas en esta ejecución : 463,210
  facturas_sii (antes / después): 130,015 / 521,847
  Páginas procesadas            : 47
  Tiempo total                  : 48.3 min (2899 s)
======================================================================
```

## Interrumpir y reanudar

### Reanudación automática tras error o Ctrl-C

El script escribe un **state file** (`<config>.state.json`) tras cada página
persistida con la última `ClavePaginacion` exitosa. Si el script falla por
una desconexión transitoria del SII (típico `ConnectionResetError 10054`
tras muchas páginas), simplemente vuelve a lanzar el mismo comando:

```bash
python scripts/descargar_sii.py --config mi_descarga.txt
```

El script detecta el state file, te muestra "Reanudando desde página N
(M facturas ya descargadas en ejecuciones previas)" y continúa exactamente
desde la `ClavePaginacion` siguiente. Las facturas ya descargadas no se
re-descargan: la AEAT paginará a partir de ese punto.

Al terminar correctamente la descarga completa, el state file se borra
automáticamente. Si quieres descartarlo manualmente y arrancar desde cero,
añade `--from-start` o borra el `.state.json`.

### Retry automático ante errores de red

Si la conexión TCP con AEAT se corta durante una llamada SOAP
(`ConnectionResetError`, `ChunkedEncodingError`, timeouts…) el script
reintenta automáticamente esa página hasta 5 veces con backoff exponencial
(2, 5, 10, 20, 30 segundos). Sólo si **todos** los reintentos fallan se
aborta — en ese caso, relanza el comando y el script reanudará desde la
última página con éxito.

### Ctrl-C limpio

**Ctrl-C** detiene el script. Todas las facturas descargadas hasta esa
página quedan persistidas en `facturas_sii` y el state file mantiene la
posición exacta para que puedas reanudar después.

## Verificar completitud del periodo

¿Dudas de si el periodo está completo o le faltan facturas porque el script
falló a mitad? Lanza:

```bash
python scripts/descargar_sii.py --config mi_descarga.txt --verificar-completitud
```

Funcionamiento:
1. Si existe el `.state.json` de reanudación, usa esa `ClavePaginacion`.
2. Si no, toma de la BD la factura del periodo con mayor
   `num_serie_factura + fecha_expedicion` y construye la `ClavePaginacion`
   a partir de ella.
3. Lanza UNA llamada SOAP al SII con esa clave. AEAT devuelve sólo las
   facturas que vienen **después** en su ordenación interna:
   - Si devuelve **0 facturas** → `VERIFICACIÓN: PERIODO COMPLETO ✓`
   - Si devuelve **N facturas** → las inserta y avisa
     `VERIFICACIÓN: FALTABAN FACTURAS ⚠ (N nuevas)`.

Es una operación **barata** (1 sola llamada SOAP si el periodo está
completo) y te da certeza sin re-descargar 1M+ facturas.

## Notas

- El certificado **nunca se escribe a disco** en el proceso del script
  (sólo se lee del fichero que tú indicas).
- La conexión a MongoDB usa `MONGO_URL` y `DB_NAME` del `.env` del backend
  si no las pones explícitamente en el `.txt` de configuración.
- El script comparte el código de parseo SOAP con el backend, así que
  cualquier mejora en `_consultar_mensual_real` se aplica automáticamente
  a ambos canales.
