# Despliegue Corporate App en AWS EC2 — Cheat sheet

> Stack: Docker Compose (Mongo + Backend FastAPI + Frontend Nginx) detrás de Caddy (HTTPS).
> Ruta en el host: `~/corporate-app` (clon del repo). Script: `deploy/deploy.sh`.

---

## 🟢 Despliegue SOFT (rápido, normal)

**Cuándo usarlo (≈95% de las veces):**
- Cambios normales de código (backend, frontend, configs).
- Quieres aprovechar la caché de Docker → build mucho más rápido (segundos vs minutos).
- Confías en que `git` y Docker no han dejado nada sucio.

### Opción A — desde tu portátil (GitHub Actions, recomendado)

```
git push origin main
```

Eso es todo. El workflow `.github/workflows/deploy.yml` se dispara, hace SSH al EC2 y lanza `deploy.sh` con caché Docker activa. Se ve en GitHub → Actions.

### Opción B — manual por SSH (si Actions está caído)

```bash
ssh ec2-user@<IP_EC2>
cd ~/corporate-app
./deploy/deploy.sh
```

El script hace internamente:
1. `git fetch origin && git reset --hard origin/main`
2. Detecta qué carpetas cambiaron (`backend/` o `frontend/`) y **solo rebuildea esa imagen** (con caché).
3. `docker-compose up -d --force-recreate` (sustituye contenedores en caliente).
4. Smoke test contra `$APP_URL/api/auth/me` (espera 401 → backend vivo).
5. `docker image prune -f` para no llenar el disco EBS.

⏱ Tiempo típico: **30 s – 2 min**.

---

## 🔴 Despliegue HARD (build sin caché)

**Cuándo usarlo:**
- Has tocado `requirements.txt`, `package.json` o `yarn.lock` y sospechas que la caché Docker no lo refleja.
- Cambios en Dockerfile (`deploy/backend.Dockerfile`, `deploy/frontend.Dockerfile`) o en `nginx.conf` / `Caddyfile`.
- El frontend muestra cosas viejas tras un push (chunks JS cacheados en el build de Docker).
- Has tenido un build fallido a medias y quieres asegurar un estado limpio.
- Tras semanas sin desplegar (la caché puede estar contaminada).

### Opción A — desde GitHub Actions

GitHub → Actions → **Deploy to AWS EC2** → "Run workflow" → marca ☑️ `force_rebuild` → Run.

### Opción B — manual por SSH

```bash
ssh ec2-user@<IP_EC2>
cd ~/corporate-app
FORCE_REBUILD=1 ./deploy/deploy.sh
```

`FORCE_REBUILD=1` añade `--no-cache` al `docker-compose build` y fuerza el build completo de backend + frontend.

⏱ Tiempo típico: **4 – 10 min** (descarga dependencias desde 0).

### Opción C — HARD máximo (último recurso, vacía TODA la caché Docker)

Si lo anterior no basta (raro), limpia también la caché global de Docker:

```bash
ssh ec2-user@<IP_EC2>
cd ~/corporate-app

# 1) Para los contenedores
docker-compose -f deploy/docker-compose.yml --env-file deploy/.env.production down

# 2) Limpia builders/imagenes/redes huérfanas (NO toca volúmenes → Mongo a salvo)
docker system prune -af

# 3) Despliega forzando rebuild sin caché
FORCE_REBUILD=1 ./deploy/deploy.sh
```

⚠ **NO** uses `docker volume prune` ni `-volumes` en `down` — borrarías la BD de Mongo.

---

## 🛠 Variables del script (`deploy/deploy.sh`)

| Variable | Efecto |
|---|---|
| `FORCE_REBUILD=1` | Añade `--no-cache` y rebuild completo (soft → hard) |
| `NO_PULL=1` | Salta `git fetch + reset`. Útil si quieres probar un cambio local sin push |

Combinables: `FORCE_REBUILD=1 NO_PULL=1 ./deploy/deploy.sh`

---

## 🔎 Comandos útiles post-deploy

```bash
cd ~/corporate-app

# Estado de los contenedores
docker-compose -f deploy/docker-compose.yml --env-file deploy/.env.production ps

# Logs en vivo
docker-compose -f deploy/docker-compose.yml --env-file deploy/.env.production logs -f backend
docker-compose -f deploy/docker-compose.yml --env-file deploy/.env.production logs -f frontend
docker-compose -f deploy/docker-compose.yml --env-file deploy/.env.production logs -f caddy

# Reiniciar solo un servicio sin rebuild
docker-compose -f deploy/docker-compose.yml --env-file deploy/.env.production restart backend

# Entrar al contenedor para depurar
docker-compose -f deploy/docker-compose.yml --env-file deploy/.env.production exec backend bash

# Backup rápido de Mongo (volcado a fichero en el host)
docker-compose -f deploy/docker-compose.yml --env-file deploy/.env.production exec mongo \
  mongodump --db=<DB_NAME> --archive=/tmp/dump.gz --gzip
docker cp $(docker-compose -f deploy/docker-compose.yml --env-file deploy/.env.production ps -q mongo):/tmp/dump.gz ~/backups/
```

---

## 🚨 Si algo va mal

```bash
# Volver al commit anterior sin tener que hacer push
cd ~/corporate-app
git log --oneline -10               # localiza el SHA bueno
git reset --hard <SHA>
NO_PULL=1 FORCE_REBUILD=1 ./deploy/deploy.sh
```

Si el `healthcheck` falla, el script muestra los últimos 30 logs del backend y aborta con `exit 1`. Mira esos logs primero.

---

## 📌 Regla mental rápida

| Situación | Comando |
|---|---|
| Cambio normal de código | `git push` (Actions) |
| Cambio Dockerfile / lockfiles / Caddyfile | `FORCE_REBUILD=1 ./deploy/deploy.sh` |
| "Veo cosas viejas tras desplegar" | `FORCE_REBUILD=1 ./deploy/deploy.sh` |
| Estado roto / caché contaminada | `docker system prune -af` + `FORCE_REBUILD=1 ./deploy/deploy.sh` |
| Rollback inmediato | `git reset --hard <SHA>` + `NO_PULL=1 FORCE_REBUILD=1 ./deploy/deploy.sh` |
