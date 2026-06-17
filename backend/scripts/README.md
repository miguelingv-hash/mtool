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
- Las dependencias del backend instaladas (ya están en `requirements.txt`).
- El certificado `.pfx` accesible localmente.

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

- **Ctrl-C** detiene el script limpiamente. Todas las facturas descargadas
  hasta esa página quedan persistidas en `facturas_sii`.
- Para reanudar **desde donde se quedó** sin re-descargar, ejecuta de nuevo
  el mismo `mi_descarga.txt`. El upsert con índice único garantiza que las
  ya existentes no se dupliquen (se sobrescriben con los mismos datos).
- Si quieres saltar páginas iniciales por completo, usa la UI web
  (sheet **Jobs → Reanudar**) que pasa la `ClavePaginacion` exacta al
  worker. El CLI siempre arranca desde el principio del periodo.

## Notas

- El certificado **nunca se escribe a disco** en el proceso del script
  (sólo se lee del fichero que tú indicas).
- La conexión a MongoDB usa `MONGO_URL` y `DB_NAME` del `.env` del backend
  si no las pones explícitamente en el `.txt` de configuración.
- El script comparte el código de parseo SOAP con el backend, así que
  cualquier mejora en `_consultar_mensual_real` se aplica automáticamente
  a ambos canales.
