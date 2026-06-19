# Colección Postman: AEAT SII - ConsultaLRFacturasEmitidas (Loop)

Replica la lógica del botón **"Lanzar en background"** de la app: hace
iteraciones de `ConsultaLRFacturasEmitidas` paginando con `ClavePaginacion`
hasta vaciar el periodo, y emite cada factura como **línea CSV** en la consola.

## Ficheros

- `AEAT_SII_Loop.postman_collection.json` — la colección.
- `AEAT_SII_env.postman_environment.json` — environment de ejemplo.

## Cabeceras CSV (alineadas con el XSD AEAT)

Delimitador: `|` (más seguro que coma para evitar conflictos con razones sociales).

```
PeriodoEjercicio | PeriodoPeriodo
IDEmisorFacturaNIF | IDEmisorFacturaNombre
NumSerieFacturaEmisor | NumSerieFacturaEmisorFin
FechaExpedicionFacturaEmisor
TipoFactura | ClaveRegimenEspecial | ImporteTotal | DescripcionOperacion
FechaOperacion | BaseImponible | TipoImpositivo | CuotaRepercutida
ContraparteNIF | ContraparteNombre | EstadoFactura
CSVAEAT | NumRegistroPresentacion | TimestampPresentacion
```

## Uso en Postman Desktop (modo interactivo)

1. **Importa** los dos JSON en Postman Desktop (no funciona en la versión web por mTLS).
2. **Settings → Certificates → Add Certificate**:
   - Host: `www1.agenciatributaria.gob.es` (producción) o `prewww1.aeat.es` (preprod).
   - Convierte tu `.pfx` a `.crt` + `.key` (Postman no acepta `.pfx` directamente):
     ```bash
     openssl pkcs12 -in cert.pfx -out cert.crt -clcerts -nokeys
     openssl pkcs12 -in cert.pfx -out cert.key -nocerts -nodes
     ```
   - Adjunta ambos al host.
3. Edita el environment con tu NIF, ejercicio, periodo.
4. Ejecuta la colección con el **Runner** (Run → seleccionar la colección).
5. La consola del Runner imprime `CSVHEAD:`, `CSVROW:` por cada factura, `PAGE:` por página y `DONE:` al terminar.

## Uso con Newman CLI (volcado a fichero — recomendado)

Postman sandbox no escribe a disco, pero **Newman sí**. Para usar `.pfx/.p12`,
Newman 6.x requiere un fichero JSON aparte mapeando el certificado al host.

**Paso 1 — Crea `ssl_certs.json` junto a la colección:**

```json
[
  {
    "name": "AEAT SII",
    "matches": [
      "https://*.agenciatributaria.gob.es/*",
      "https://prewww1.aeat.es/*"
    ],
    "pfx": { "src": "./cert.p12" },
    "passphrase": "TU_PASSWORD"
  }
]
```

**Paso 2 — Ejecuta Newman:**

```bash
npm install -g newman

# Linux / Mac
newman run AEAT_SII_Loop.postman_collection.json \
  -e AEAT_SII_env.postman_environment.json \
  --ssl-client-cert-list ssl_certs.json \
  --reporter-cli-no-summary --reporter-cli-no-assertions \
  --timeout-request 60000 \
  2>&1 | grep "^CSV" > facturas.csv

# Windows (cmd)
newman run AEAT_SII_Loop.postman_collection.json ^
  -e AEAT_SII_env.postman_environment.json ^
  --ssl-client-cert-list ssl_certs.json ^
  --reporter-cli-no-summary --reporter-cli-no-assertions ^
  --timeout-request 60000 > export.txt 2>&1

:: ⚠️  NO uses `findstr` aquí. Newman parte las líneas largas
:: en varios renglones con bordes │ ... │ y findstr sólo recoge
:: trozos. Usa el script Python que reensambla:
python extraer_csv.py export.txt facturas.csv
```

### Extracción robusta del CSV (Windows / Mac / Linux)

Newman pinta su salida como una "tabla" con bordes `│ ... │` y, cuando
una línea de `console.log` es muy larga, la **parte en varios renglones**.
Por eso un `findstr /B "CSVROW"` o un `grep` simple **trunca filas**.

Para extraer el CSV correctamente usa el script `extraer_csv.py` (incluido
en esta carpeta). Reensambla las líneas partidas, quita los códigos ANSI
de color y los bordes de tabla.

**⚠️  IMPORTANTE — Cómo lanzar el script:**

NO lo ejecutes desde el REPL interactivo de Python (el que muestra `>>>`).
Si pones `run` o `python ...` dentro del `>>>` verás
`NameError: name 'run' is not defined` o `SyntaxError`.

Hay que lanzarlo desde **CMD** o **PowerShell** de Windows:

```cmd
:: Abre CMD (Win+R → cmd) y cd a la carpeta donde está export.txt
cd C:\ruta\donde\tienes\export.txt

:: Lanza el script (asumiendo que extraer_csv.py está en la misma carpeta)
python extraer_csv.py
```

```cmd
:: O con rutas explícitas (lo más fiable):
python C:\ruta\al\extraer_csv.py C:\ruta\export.txt C:\ruta\facturas.csv
```

```powershell
# Desde PowerShell es idéntico:
python .\extraer_csv.py .\export.txt .\facturas.csv
```

Salida esperada:

```
[OK] Cabecera detectada: sí
[OK] Filas extraídas:    128453
[OK] CSV generado en:    facturas.csv
```

**Smoke test rápido** (sólo 1 página): añade `-n 1` al final del `newman run`.

El fichero `facturas.csv` queda con la cabecera (sin prefijo `CSVHEAD:`)
en la 1ª línea y luego una línea por factura. Para abrirlo en Excel:

```bash
# Quita el prefijo CSVHEAD:/CSVROW: y convierte | en ; para Excel español
# (sólo necesario si NO usaste extraer_csv.py; el script ya quita los prefijos)
sed -e 's/^CSVHEAD://' -e 's/^CSVROW://' -e 's/|/;/g' facturas.csv > facturas_excel.csv
```

```powershell
# Windows PowerShell — convierte | en ; para que Excel español lo abra bien
(Get-Content facturas.csv) -replace '\|',';' |
  Set-Content -Encoding utf8 facturas_excel.csv
```

## Notas técnicas

- **Paginación**: tras cada página la colección extrae la última factura,
  construye la `ClavePaginacion` siguiente (`NIF + NumSerie + FechaExp`) y
  reagenda la misma request con `postman.setNextRequest(...)`. Idéntica
  semántica a la del backend Python.
- **Indicador**: cuando AEAT devuelve `IndicadorPaginacion=N` (sin más) la
  colección imprime `DONE:` y termina.
- **Tamaño página**: AEAT devuelve hasta 10.000 facturas por respuesta. Para
  un periodo de 1.3M facturas son ~128 páginas (~2-3 horas con AEAT real).
- **Recovery**: Newman no tiene reanudación nativa. Si se corta a mitad,
  guarda el último `clave_pag` que veas en la consola y mete su JSON en
  el environment (`clave_pag`) antes de relanzar.

## Diferencias vs. el script CLI Python

| Aspecto                  | Newman / Postman                | `descargar_sii.py`                |
| ------------------------ | ------------------------------- | --------------------------------- |
| Persistencia             | Fichero CSV plano               | MongoDB con upsert + bulk_write   |
| Retry transitorios       | Manual (relanzar)               | Automático con backoff            |
| Reanudación tras fallo   | Manual (copiar clave\_pag)      | Automática vía `.state.json`      |
| Mapeo a modelo canónico  | Sí, vía `ingestar_csv_a_mongo.py`     | Sí (`facturas_sii` con índices)   |
| Filtro de campos `null`  | Sí, vía `ingestar_csv_a_mongo.py`     | Sí (modelo Pydantic)              |

## Pipeline completo recomendado (Windows local)

El flujo más rápido es separar **descarga** de **ingesta**: Newman saca el CSV
a velocidad de red (sin Cloudflare en medio) y luego el script
`ingestar_csv_a_mongo.py` hace `bulk_write` con `upsert` directo a tu Mongo
(local o cloud).

```text
                          (mTLS local, ~2000 facturas/s)
   ┌─────────────────────────────────────────────────────────────┐
   │                       NEWMAN CLI                            │
   │   newman run AEAT_SII_Loop.postman_collection.json          │
   │      --ssl-client-cert-list ssl_certs.json                  │
   │      > export.txt 2>&1                                      │
   └─────────────────────────────────────────────────────────────┘
                              │
                              ▼
   ┌─────────────────────────────────────────────────────────────┐
   │   py extraer_csv.py export.txt facturas.csv                 │
   │   (reensambla líneas partidas + quita ANSI / bordes │ │ )   │
   └─────────────────────────────────────────────────────────────┘
                              │
                              ▼
   ┌─────────────────────────────────────────────────────────────┐
   │   py ingestar_csv_a_mongo.py --config config_ingesta.json   │
   │   bulk_write con upsert por num_serie_factura (~2000 docs/s)│
   └─────────────────────────────────────────────────────────────┘
                              │
                              ▼
   ┌─────────────────────────────────────────────────────────────┐
   │       MongoDB → facturas_sii (idéntica al job web)          │
   │       Comparativa y UI ya ven los datos sin cambios.        │
   └─────────────────────────────────────────────────────────────┘
```

### Configuración (fichero JSON)

Copia `config_ingesta.example.json` a `config_ingesta.json` y rellena.
Soporta dos perfiles típicos cambiando `mongo_url`:

```jsonc
{
  "csv": "facturas.csv",
  "mongo_url": "mongodb://localhost:27017",      // Docker local
  // "mongo_url": "mongodb+srv://user:pass@cluster.xxx.mongodb.net/...",  // Emergent cloud preview
  "db": "sii_db",
  "coleccion": "facturas_sii",
  "nif_titular": "B12345678",
  "nombre_titular": "MI EMPRESA S.L.",
  "batch_size": 2000
}
```

### Comandos

```cmd
:: 1) Validar el mapeo sin tocar Mongo (imprime 3 primeras filas)
py ingestar_csv_a_mongo.py --config config_ingesta.json --dry-run

:: 2) Ingestar de verdad
py ingestar_csv_a_mongo.py --config config_ingesta.json

:: 3) Sobreescribir cualquier campo del JSON por CLI si hace falta
py ingestar_csv_a_mongo.py --config config_ingesta.json --csv otro.csv --db otra_bd
```

### Garantías de la ingesta

- **Idempotente**: relanzarla con el mismo CSV no crea duplicados; reusa
  el índice único `num_serie_factura`.
- **Misma colección que la app web** (`facturas_sii`): la Comparativa
  funciona idéntica con independencia de si la factura llegó por consulta
  unitaria web, por job mensual o por este CSV.
- **Marca de origen**: cada documento queda con `fuente_ultima: "newman_csv"`,
  útil para auditoría.
- **Tipos correctos**: importes parseados a `float` (acepta `1.234,56`,
  `1234.56`, `1234,56`), cadenas strip-eadas, vacíos → `null`.

### Sustituye al import via /api/comercial/csv?

No. Aquel endpoint sube CSV **comercial** (SAP FI / SIGLO) a
`facturas_comercial`. Éste pisa la colección **SII** (`facturas_sii`).
Son las dos caras de la comparativa.


Para auditoría puntual o exports rápidos, esta colección es ideal. Para
descargas masivas recurrentes con persistencia y reanudación nativa, sigue
siendo mejor el `descargar_sii.py`. **Pero** si tu cuello de botella es la
red contra el servicio web (Cloudflare 520, timeouts, etc.), el pipeline
**Newman → extraer_csv.py → ingestar_csv_a_mongo.py** te da lo mejor de
los dos mundos: velocidad de Newman + persistencia idéntica al backend.
