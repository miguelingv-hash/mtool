# Scripts CLI de carga directa a BD

Carga masiva de facturas en la base de datos **sin pasar por HTTP**
(sin Caddy, sin límites de body, sin timeouts axios, sin Cloudflare).

Útil para:
- CSVs Newman de cientos de MB / millones de filas.
- Carga programada / scheduled (cron en el EC2).
- Recovery rápido tras un wipe.

## 📋 Prerequisitos (configuración en EC2)

### 1. Carpeta de imports en el host

```bash
ssh ec2-user@<IP_EC2>
mkdir -p ~/data
```

### 2. Volumen montado (ya configurado en `docker-compose.yml`)

```yaml
backend:
  volumes:
    - /home/ec2-user/data:/data   # host → contenedor, RW
```

Si ya tenías la app desplegada antes de añadir el volumen, recrea el
contenedor para que aplique:

```bash
cd ~/corporate-app
docker-compose -f deploy/docker-compose.yml --env-file deploy/.env.production \
    up -d --force-recreate backend
```

## 📦 Cómo copiar el CSV al EC2

```bash
# Desde tu portátil (Opción A — scp directo)
scp facturas_TEC_Junio.csv ec2-user@<IP_EC2>:/home/ec2-user/data/
```

## 🚀 Uso

### 1. Importar SII desde CSV Newman

```bash
ssh ec2-user@<IP_EC2>
cd ~/corporate-app

ALIAS="docker-compose -f deploy/docker-compose.yml --env-file deploy/.env.production"

$ALIAS exec -T backend python -m scripts.import_newman_sii \
    --csv /data/facturas_TEC_Junio.csv \
    --nif-titular A95000295 \
    --nombre "TotalEnergies Clientes S.A.U." \
    --ejercicio 2026 \
    --periodo 06
```

**Comportamiento por defecto (upsert ciego, máximo rendimiento):**
- Inserta o actualiza por `num_serie_factura`.
- Idempotente: ejecutar 2 veces no duplica.
- Borra el CSV de origen al terminar con éxito.

**Variantes útiles:**

```bash
# Modo "solo faltantes" (lento: consulta BD antes — equivalente al UI)
$ALIAS exec -T backend python -m scripts.import_newman_sii \
    --csv /data/facturas_TEC_Junio.csv --nif-titular A95000295 \
    --ejercicio 2026 --periodo 06 \
    --only-faltantes

# Dry-run: parsea, cuenta, NO toca Mongo
$ALIAS exec -T backend python -m scripts.import_newman_sii \
    --csv /data/facturas_TEC_Junio.csv --nif-titular A95000295 \
    --dry-run

# Conservar el CSV tras carga (no borrar)
$ALIAS exec -T backend python -m scripts.import_newman_sii \
    --csv /data/facturas_TEC_Junio.csv --nif-titular A95000295 \
    --keep-csv

# Batch más pequeño si Mongo tose con bulks grandes
$ALIAS exec -T backend python -m scripts.import_newman_sii \
    --csv /data/facturas_TEC_Junio.csv --nif-titular A95000295 \
    --batch-size 500
```

### 2. Importar comercial SAP FI / SIGLO

```bash
$ALIAS exec -T backend python -m scripts.import_comercial \
    --csv /data/sap_junio.txt
```

El parser **autodetecta** SAP FI vs SIGLO por la cabecera y mapea
automáticamente la columna `Soc.` al `nif_titular` + `nombre_titular`
usando el catálogo (4432→TotalEnergies, 2239→BASER, etc.).

**Variantes:**

```bash
# Forzar Soc si el CSV no la trae (raro, pero útil)
$ALIAS exec -T backend python -m scripts.import_comercial \
    --csv /data/legacy_sin_soc.txt --soc-override 4432

# Dry-run + conservar fichero
$ALIAS exec -T backend python -m scripts.import_comercial \
    --csv /data/sap_junio.txt --dry-run --keep-csv
```

## 📊 Output esperado

```
2026-06-28 22:00:01  INFO  Iniciando import Newman SII · csv=/data/X.csv (180.2 MB) · ...
2026-06-28 22:00:01  INFO  Leyendo CSV en memoria…
2026-06-28 22:00:05  INFO  Parseando CSV (~180.2 MB)…
2026-06-28 22:00:42  INFO  Parseo OK · filas válidas=865435 · errores=0 · debug={...}
2026-06-28 22:00:42  INFO  Insertando 865435 documentos en facturas_sii…
2026-06-28 22:00:52  INFO    ⏳ facturas_sii · 50000/865435 (5.8%) · 4923 docs/s · inserted=49872 modified=128
2026-06-28 22:01:02  INFO    ⏳ facturas_sii · 100000/865435 (11.6%) · 4901 docs/s · ...
...
2026-06-28 22:03:48  INFO  ✅ Carga completada · procesadas=865435 · insertadas_nuevas=100435 · actualizadas=765000 · duración=187.3 s · 4621 docs/s
2026-06-28 22:03:48  INFO  CSV de origen borrado tras carga exitosa: /data/X.csv
```

## 🔒 Exit codes (útil para cron / scripting)

| Code | Significado |
|---|---|
| `0` | Éxito |
| `1` | Errores de parsing o argumentos |
| `2` | Error de conexión / config (Mongo, env vars) |
| `130` | Interrumpido (Ctrl+C) |

## 🚧 Comportamiento de seguridad

- **Lock file en `/tmp/import_{newman_sii,comercial}.lock`**: si lanzas el
  mismo script dos veces a la vez, el segundo aborta inmediatamente.
- **Idempotencia**: relanzar tras un fallo no duplica.
- **Borrado del CSV** solo si la carga termina con exit code 0 (por defecto;
  se desactiva con `--keep-csv`).

## 🛠 Resolución de problemas

**Error: `MONGO_URL no está en el entorno`**
→ Solo se da si ejecutas fuera del contenedor. Usa siempre `docker-compose exec`.

**Error: `Hay otra ejecución en curso (lock /tmp/import_*.lock)`**
→ Hay otro proceso del mismo script. Si estás seguro de que no es así
(p.ej. crashed sin liberar el lock):
```bash
$ALIAS exec backend rm /tmp/import_newman_sii.lock
```

**`KillSignal received` / contenedor reiniciado a mitad del import**
→ OOM por carga del CSV completo en memoria. Si el EC2 es modesto:
- Reduce `--batch-size 500`.
- Sube la instancia a t3.medium (4 GB RAM) — recomendado para CSVs >100 MB.

**El CSV se ha quedado en `/data` aunque terminó OK**
→ Permisos. Verifica que el volumen del docker-compose es RW (sin `:ro`).

## 🤖 Automatización con cron (opcional)

```bash
# /etc/cron.d/sii-imports — todos los días a las 03:00
0 3 * * * ec2-user /usr/local/bin/run-import-newman.sh

# /usr/local/bin/run-import-newman.sh
#!/bin/bash
cd /home/ec2-user/corporate-app
docker-compose -f deploy/docker-compose.yml --env-file deploy/.env.production \
    exec -T backend python -m scripts.import_newman_sii \
        --csv /data/daily.csv --nif-titular A95000295 \
        >> /var/log/sii-imports.log 2>&1
```
