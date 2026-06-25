# 🚀 Guía de re-despliegue: Emergent → AWS EC2

Esta guía cubre el flujo habitual cuando haces cambios en la plataforma
**Emergent** y necesitas llevarlos a tu instancia de **AWS EC2**
(`https://3-125-115-81.sslip.io`).

---

## 📋 Información de tu entorno

| | |
|---|---|
| **Instancia EC2** | `3.125.115.81` (Frankfurt, eu-central-1) |
| **DNS público AWS** | `ec2-3-125-115-81.eu-central-1.compute.amazonaws.com` |
| **URL pública app** | `https://3-125-115-81.sslip.io` |
| **Directorio en EC2** | `~/corporate-app` |
| **Archivos de despliegue** | `~/corporate-app/deploy/` |
| **Variables de entorno** | `~/corporate-app/deploy/.env.production` (NO versionado) |
| **SO de la instancia** | Amazon Linux 2023 |
| **Runtime** | Docker + Docker Compose |
| **Contenedores** | `frontend` (Caddy), `backend` (FastAPI), `mongo` (MongoDB 7) |
| **HTTPS** | Let's Encrypt automático vía Caddy + sslip.io |

---

## 🔄 Flujo estándar de re-despliegue

### Paso 1 — En la plataforma Emergent
1. Haz tus cambios y verifica que funcionan en el preview.
2. Pulsa el botón **"Save to Github"** arriba a la derecha del chat.
3. Espera la confirmación del push.

### Paso 2 — Conéctate a la EC2 por SSH

```bash
ssh -i /ruta/a/tu-clave.pem ec2-user@3.125.115.81
```

### Paso 3 — Descarga los cambios

```bash
cd ~/corporate-app
git pull
```

> 💡 Si `git pull` falla por conflictos con archivos locales (por ejemplo
> PDFs generados en `backend/storage/`), descarta los cambios locales:
> ```bash
> git checkout -- .
> git pull
> ```

### Paso 4 — Decide QUÉ rebuild necesitas

| Tipo de cambio | Comando |
|---|---|
| Solo **backend** (Python) | `docker-compose -f deploy/docker-compose.yml --env-file deploy/.env.production up -d --build backend` |
| Solo **frontend** (React) | `docker-compose -f deploy/docker-compose.yml --env-file deploy/.env.production up -d --build frontend` |
| **Ambos** | `docker-compose -f deploy/docker-compose.yml --env-file deploy/.env.production up -d --build` |
| Cambios en `Caddyfile` | igual que frontend (se copia en build) |
| Cambios en `requirements.txt` | igual que backend |
| Cambios en `package.json` | igual que frontend |

> ⚠️ **Importante**: nunca uses `--no-cache` salvo que sea necesario. Cada
> `--no-cache` consume ~50 % de tu disco EBS y tiempo. Usa rebuilds normales
> que aprovechan la caché de Docker.

### Paso 5 — Comprueba el estado

```bash
# Estado de los contenedores
docker-compose -f deploy/docker-compose.yml --env-file deploy/.env.production ps

# Logs del backend (los últimos 50)
docker-compose -f deploy/docker-compose.yml --env-file deploy/.env.production logs --tail=50 backend

# Logs del frontend (Caddy)
docker-compose -f deploy/docker-compose.yml --env-file deploy/.env.production logs --tail=50 frontend
```

Los 3 contenedores deben estar `Up X minutes (healthy)`.

### Paso 6 — Smoke test rápido

```bash
# Backend devuelve JSON 401 (sin autenticar)
curl -s https://3-125-115-81.sslip.io/api/auth/me

# Frontend devuelve HTML
curl -s https://3-125-115-81.sslip.io/ | head -3
```

Si ambos responden correctamente, ya está. Abre el navegador y verifica.

---

## 🆘 Casos especiales

### Cambios en variables de entorno (.env.production)

Edita el archivo y **recrea** los contenedores afectados:

```bash
nano ~/corporate-app/deploy/.env.production
docker-compose -f deploy/docker-compose.yml --env-file deploy/.env.production up -d --force-recreate backend
```

> ⚠️ **`REACT_APP_BACKEND_URL`** se embebe en el bundle JS en build-time, así
> que si cambia hay que **rebuild** del frontend (`--build`, no solo
> `--force-recreate`).

### Cambios sólo de código sin tocar dependencias

Si los cambios son solo en archivos `.py` o `.jsx`/`.js`, Docker reaprovecha
la caché y el rebuild tarda 10-30 segundos.

### Migraciones / cambios de esquema en Mongo

Conecta al shell de Mongo:

```bash
docker exec -it corporate-app-mongo-1 mongosh corporate_app
```

Ejecuta los comandos `db.collection.updateMany(...)` que necesites.

### Liberar espacio en disco EBS

Docker acumula imágenes/capas viejas. Limpia periódicamente:

```bash
docker system prune -af --volumes
df -h
```

> ⚠️ `--volumes` borra volúmenes no usados pero **NO toca** los volúmenes
> declarados en `docker-compose.yml` (mongo_data, backend_storage,
> caddy_data, caddy_config).

### Reiniciar un contenedor sin rebuild

```bash
docker-compose -f deploy/docker-compose.yml --env-file deploy/.env.production restart backend
```

### Ver logs en tiempo real

```bash
docker-compose -f deploy/docker-compose.yml --env-file deploy/.env.production logs -f backend
```

(Ctrl+C para salir.)

### Bajar TODO temporalmente (mantenimiento)

```bash
docker-compose -f deploy/docker-compose.yml --env-file deploy/.env.production down
# ... (mantenimiento) ...
docker-compose -f deploy/docker-compose.yml --env-file deploy/.env.production up -d
```

> ⚠️ `down` NO borra los volúmenes (datos a salvo). `down -v` SÍ los borra
> (¡pierdes la BD!). Nunca uses `-v` en producción.

---

## 🔐 Backup de la base de datos

### Backup manual

```bash
docker exec corporate-app-mongo-1 mongodump \
  --db=corporate_app \
  --archive=/data/db/backup-$(date +%F).archive

# Copia el backup fuera del contenedor
docker cp corporate-app-mongo-1:/data/db/backup-$(date +%F).archive ~/backups/
```

### Restaurar

```bash
docker cp ~/backups/backup-XXXX.archive corporate-app-mongo-1:/tmp/
docker exec corporate-app-mongo-1 mongorestore \
  --archive=/tmp/backup-XXXX.archive \
  --drop
```

### Backup automático con cron

```bash
# Crea el script
mkdir -p ~/backups
cat > ~/backups/backup.sh <<'SH'
#!/bin/bash
DATE=$(date +%F)
docker exec corporate-app-mongo-1 mongodump --db=corporate_app --archive=/data/db/backup-$DATE.archive
docker cp corporate-app-mongo-1:/data/db/backup-$DATE.archive /home/ec2-user/backups/
# Mantén solo los últimos 7 días
find /home/ec2-user/backups -name "backup-*.archive" -mtime +7 -delete
SH
chmod +x ~/backups/backup.sh

# Añade al cron diario a las 03:00
(crontab -l 2>/dev/null; echo "0 3 * * * /home/ec2-user/backups/backup.sh >> /home/ec2-user/backups/backup.log 2>&1") | crontab -
```

---

## 🐛 Resolución de problemas frecuentes

| Síntoma | Causa probable | Solución |
|---|---|---|
| `git pull` falla por archivos modificados | PDFs generados o cambios manuales | `git checkout -- .` |
| Build falla por OOM (`Killed`) | Falta RAM en EC2 t3.small | Añadir swap (4 GB) — ver abajo |
| Backend `unhealthy` | Variables `.env.production` mal | `docker logs corporate-app-backend-1` |
| Frontend muestra HTML para `/api/*` | Caddyfile mal configurado | Ver `deploy/Caddyfile`, regla `handle /api/*` |
| Bucle login ↔ MFA | `COOKIE_SECURE` mal según protocolo | HTTPS → `true`, HTTP → `false` |
| Let's Encrypt rate limit | Demasiados rebuilds con `--no-cache` | Esperar 1 semana o usar volumen `caddy_data` |
| OTP no llega | `RESEND_API_KEY=re_dummy` | Pon una API key real de [resend.com](https://resend.com) |

### Añadir swap a la EC2 (si el build muere por OOM)

```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h
```

---

## 📞 Acceso de emergencia: deshacer el último despliegue

Si tras un `git pull + build` la app falla, vuelve al commit anterior:

```bash
cd ~/corporate-app
git log --oneline -5            # Ve los últimos commits
git reset --hard <hash-anterior>
docker-compose -f deploy/docker-compose.yml --env-file deploy/.env.production up -d --build
```

> 💡 Mejor aún: la plataforma Emergent permite **rollback** del proyecto desde
> el panel del chat (gratis). Eso es mucho más seguro que tocar `git reset`.

---

## ✅ Checklist post-despliegue

- [ ] `docker-compose ps` → los 3 contenedores `healthy`
- [ ] `curl https://3-125-115-81.sslip.io/api/auth/me` → JSON 401
- [ ] Login en navegador funciona
- [ ] El OTP llega por email (si has cambiado algo de auth)
- [ ] Las funciones nuevas funcionan según lo esperado
- [ ] (Opcional) Backup de Mongo ejecutado tras un cambio crítico

---

**Última actualización**: Feb 2026
