# SII Consulta — despliegue local con Docker

Aplicación full-stack para consultar el estado de facturas emitidas en el SII
de la Agencia Tributaria Española (servicio SOAP `ConsultaLRFactEmitidas`,
WSDL v1.1).

## Requisitos

- Docker ≥ 24
- Docker Compose v2 (incluido en Docker Desktop)

## Arranque en un comando

```bash
git clone <tu-repo> sii-consulta
cd sii-consulta
cp .env.example .env       # opcional; ajusta puertos/modo si quieres
docker compose up -d --build
```

Esto levanta tres contenedores:

| Servicio   | Imagen / Build         | Puerto host                | Puerto interno |
|------------|------------------------|----------------------------|----------------|
| `mongo`    | `mongo:7`              | — (interno)                | 27017          |
| `backend`  | `./backend/Dockerfile` | — (interno)                | 8001           |
| `frontend` | `./frontend/Dockerfile`| `${FRONTEND_PORT:-3000}`   | 80 (nginx)     |

Abre **http://localhost:3000** en el navegador. El nginx del contenedor
`frontend` hace de proxy `/api/*` → `backend:8001`, así que no hay CORS ni
URLs hardcodeadas.

## Comandos útiles

```bash
docker compose logs -f backend        # ver logs del backend
docker compose logs -f frontend       # ver logs de nginx
docker compose ps                     # estado de los contenedores
docker compose restart backend        # reiniciar tras cambios de .env
docker compose down                   # parar (preserva volumen Mongo)
docker compose down -v                # parar y borrar la base de datos
docker compose exec mongo mongosh sii_local   # shell de Mongo
docker compose exec backend pytest tests/     # ejecutar tests dentro del contenedor
```

## Activar el modo real con certificado AEAT

**Opción A — cert por sesión (UI)**: arranca en modo mock (por defecto) y
usa el toggle "Modo real · certificado AEAT" en *Consulta unitaria* o
*Consulta batch*. Subes el `.pfx` y la contraseña en cada llamada; no se
guarda nada.

**Opción B — cert permanente en el servidor**:

```bash
mkdir -p certs
cp /ruta/a/mi_certificado.pfx ./certs/

# Edita .env:
SII_MODE=real
SII_CERT_PATH=/certs/mi_certificado.pfx
SII_CERT_PASSWORD=mi_password_pfx

docker compose up -d --build backend
```

La carpeta `./certs` está montada como `/certs:ro` en el contenedor del
backend (sólo lectura). **No subas el `.pfx` ni el `.env` a git** — están
ignorados por defecto en los `.dockerignore` y deberían estarlo también en
tu `.gitignore`.

## Variables de entorno relevantes (`.env`)

| Variable             | Default      | Descripción                                                    |
|----------------------|--------------|----------------------------------------------------------------|
| `FRONTEND_PORT`      | `3000`       | Puerto host en el que se publica la UI                         |
| `DB_NAME`            | `sii_local`  | Nombre de la base de datos Mongo                               |
| `SII_MODE`           | `mock`       | `mock` o `real`                                                |
| `SII_CERT_PATH`      | *(vacío)*    | Ruta al `.pfx` dentro del contenedor (p.ej. `/certs/foo.pfx`)  |
| `SII_CERT_PASSWORD`  | *(vacío)*    | Contraseña del PFX                                             |

## Estructura mínima

```
sii-consulta/
├── docker-compose.yml
├── .env.example          → cópialo a .env
├── certs/                → (opcional) certificados .pfx
├── backend/
│   ├── Dockerfile
│   ├── .dockerignore
│   ├── server.py
│   ├── sii_client.py
│   ├── requirements.txt
│   └── tests/
└── frontend/
    ├── Dockerfile
    ├── .dockerignore
    ├── nginx.conf
    ├── package.json
    └── src/
```

## Persistencia y backups

El histórico de consultas vive en el volumen Docker `mongo_data`. Para
hacer un backup:

```bash
docker compose exec mongo mongodump --db=sii_local --archive=/tmp/sii.dump
docker cp sii_mongo:/tmp/sii.dump ./sii-$(date +%F).dump
```

Restaurar:

```bash
docker cp ./sii-2026-02-13.dump sii_mongo:/tmp/sii.dump
docker compose exec mongo mongorestore --drop --archive=/tmp/sii.dump
```

## Solución de problemas

| Síntoma                                                | Causa habitual                                                        |
|--------------------------------------------------------|-----------------------------------------------------------------------|
| `port is already allocated` al hacer `up`              | El 3000 está ocupado: cambia `FRONTEND_PORT` en `.env`.               |
| La UI carga pero todas las llamadas dan 502            | El backend aún no está listo (`docker compose logs -f backend`).      |
| `Could not deserialize PKCS12 data` al activar real    | El `.pfx` está corrupto o la contraseña es incorrecta (validación OK).|
| `MONGO_URL not set` en logs del backend                | El servicio `mongo` no levantó; revisa `docker compose ps`.           |
| Modificas código y no se refleja                       | Las imágenes están cacheadas: `docker compose up -d --build`.         |

## Producción

Esta configuración está pensada para uso local y entornos internos. Si vas a
exponerlo públicamente:

1. Pon un proxy TLS delante (Traefik, Caddy o nginx con Let's Encrypt).
2. Añade autenticación a la app (no incluida por defecto).
3. Cambia `CORS_ORIGINS` por el dominio público.
4. Activa autenticación en MongoDB (`MONGO_INITDB_ROOT_USERNAME/PASSWORD`).
5. Almacena el certificado en un secreto (Docker secrets, Vault, etc.) en
   lugar de un volumen montado.
