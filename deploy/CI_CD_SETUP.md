# 🤖 CI/CD — Deploy automático con GitHub Actions

Workflow: [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml)

Cada vez que hagas push a `main`, GitHub Actions se conectará por SSH a tu EC2
y ejecutará el script [`deploy/deploy.sh`](./deploy.sh) que:
1. `git pull` (descarta cambios locales)
2. Detecta qué carpetas cambiaron (`backend/` o `frontend/`)
3. Rebuild **solo** las imágenes necesarias (rápido, usa caché Docker)
4. `docker-compose up -d` + healthcheck
5. Limpia imágenes Docker viejas para no llenar el disco

---

## 🔧 Configuración inicial (una vez)

### 1) Genera una clave SSH dedicada para CI/CD

En **tu máquina local** (no en la EC2):

```bash
ssh-keygen -t ed25519 -C "github-actions-deploy" -f ~/.ssh/corporate_app_deploy -N ""
```

Esto crea dos archivos:
- `~/.ssh/corporate_app_deploy` → clave **privada** (para GitHub Secrets)
- `~/.ssh/corporate_app_deploy.pub` → clave **pública** (para autorizar en EC2)

> 💡 Genera una clave nueva específica para CI/CD en vez de reusar tu clave
> personal. Si se compromete, sólo revocas esta y no pierdes acceso normal.

### 2) Autoriza la clave en tu EC2

Copia la **clave pública** a la EC2 (sustituye con tu IP/usuario):

```bash
ssh-copy-id -i ~/.ssh/corporate_app_deploy.pub ec2-user@3.125.115.81
```

O manualmente: copia el contenido de `corporate_app_deploy.pub` y añádelo al
final de `~/.ssh/authorized_keys` en la EC2.

Comprueba que funciona:

```bash
ssh -i ~/.ssh/corporate_app_deploy ec2-user@3.125.115.81 'echo OK'
# → debe imprimir: OK
```

### 3) Añade los secretos en GitHub

Ve a tu repo en GitHub → **Settings** → **Secrets and variables** → **Actions**
→ **New repository secret**. Crea los 3 secretos:

| Secret | Valor | Cómo obtenerlo |
|---|---|---|
| `EC2_HOST` | `3.125.115.81` | IP pública de tu EC2 |
| `EC2_USER` | `ec2-user` | Usuario SSH (por defecto en Amazon Linux) |
| `EC2_SSH_KEY` | Todo el contenido de `~/.ssh/corporate_app_deploy` | `cat ~/.ssh/corporate_app_deploy` — incluye las líneas `-----BEGIN ... PRIVATE KEY-----` y `-----END ... PRIVATE KEY-----` |

> ⚠️ El `EC2_SSH_KEY` debe ser **la clave privada completa**, no la pública.

### 4) (Opcional) Restringe el Security Group

Por defecto, el puerto 22 está abierto a `0.0.0.0/0`. Si quieres restringirlo,
GitHub Actions usa rangos de IP que publican (lista en
[meta API](https://api.github.com/meta) → campo `actions`). Configurar esto es
opcional; sin restricción funciona también.

---

## 🚀 Uso

### Deploy automático
Cada `push` o `merge` a `main` que toque `backend/`, `frontend/` o `deploy/`
dispara el workflow automáticamente. Verás el progreso en la pestaña
**Actions** de tu repo.

### Deploy manual desde GitHub
1. GitHub → repo → pestaña **Actions**.
2. Workflow: **Deploy to AWS EC2** → botón **Run workflow**.
3. Opción `force_rebuild`:
   - **Desmarcada**: build rápido con caché Docker (recomendado).
   - **Marcada**: `--no-cache` (más lento, sólo si quieres asegurar
     dependencias frescas).

### Deploy manual desde SSH
Si necesitas saltarte GitHub (e.g., probando algo):

```bash
ssh ec2-user@3.125.115.81
cd ~/corporate-app
./deploy/deploy.sh
```

---

## 🔍 Diagnóstico

### El workflow falla en el paso "Deploy via SSH"
- **`Permission denied`** → la clave pública no está en `~/.ssh/authorized_keys`
  de la EC2, o el `EC2_USER` no es correcto.
- **`Connection timed out`** → puerto 22 cerrado en el Security Group, o
  `EC2_HOST` mal.
- **`Host key verification failed`** → no debería pasar con `appleboy/ssh-action`
  pero si ocurre, añade `script_stop: true` en el workflow.

### El workflow falla en `deploy.sh`
Mira la salida en GitHub Actions. Causas típicas:
- `git pull` rechazado por cambios locales → el script hace `git reset --hard`
  que los descarta, debería funcionar.
- Build OOM → falta swap en la EC2 (ver `DEPLOY_GUIDE.md`).
- `.env.production` sin alguna variable nueva → edítalo en la EC2.

### El smoke test falla
El backend público devuelve algo distinto de 401. Conéctate por SSH y mira:

```bash
docker-compose -f deploy/docker-compose.yml --env-file deploy/.env.production logs --tail=80 backend
```

---

## 🛡️ Buenas prácticas

1. **Branch protection** en GitHub → no se mergea a `main` sin PR aprobado.
2. **Tag de release** para puntos estables → puedes hacer rollback fácil con:
   ```bash
   git reset --hard v1.2.0 && ./deploy/deploy.sh
   ```
3. **Backup de Mongo antes de cambios grandes** (ver `DEPLOY_GUIDE.md`).
4. **Rota la clave SSH del workflow** cada 6-12 meses.

---

**Última actualización**: Feb 2026
