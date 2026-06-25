# Despliegue con Docker — Corporate App

Esta carpeta contiene todo lo necesario para levantar la aplicación en cualquier
servidor con Docker (Amazon Linux 2023, Ubuntu, etc.) con **un solo comando**.

## 📚 Documentación

- 📘 **[DEPLOY_GUIDE.md](./DEPLOY_GUIDE.md)** — Guía paso a paso para
  re-desplegar cambios desde Emergent → EC2. **Empieza aquí si vas a hacer un
  nuevo despliegue.**
- 🏗️ **[ARCHITECTURE.md](./ARCHITECTURE.md)** — Diagrama y descripción de
  componentes (frontend Caddy, backend FastAPI, MongoDB, integraciones).
- 🤖 **[CI_CD_SETUP.md](./CI_CD_SETUP.md)** — Configurar GitHub Actions para
  deploy automático en cada push a `main`.

## Contenido

- `backend.Dockerfile` — imagen Python 3.11 + FastAPI + uvicorn
- `frontend.Dockerfile` — multi-stage Node 20 build → Caddy 2.8 alpine
- `Caddyfile` — config Caddy del contenedor frontend (SPA + HTTPS automático)
- `docker-compose.yml` — orquesta `mongo` + `backend` + `frontend`
- `deploy.sh` — script idempotente que ejecuta el deploy en EC2
- `.env.production.example` — plantilla de variables de entorno

## Despliegue paso a paso en EC2 (Amazon Linux 2023)

```bash
# 1. Instala Docker + plugin compose
sudo dnf -y install docker
sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user
newgrp docker

DOCKER_COMPOSE_VERSION="v2.29.7"
sudo curl -fsSL "https://github.com/docker/compose/releases/download/${DOCKER_COMPOSE_VERSION}/docker-compose-linux-x86_64" \
  -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# 2. Clona el repo
sudo dnf -y install git
git clone https://github.com/TU_USUARIO/TU_REPO.git ~/corporate-app
cd ~/corporate-app

# 3. Configura las variables
cp deploy/.env.production.example .env.production
nano .env.production   # rellena REACT_APP_BACKEND_URL, JWT_SECRET, RESEND_API_KEY, etc.

# 4. Sube tus assets opcionales (logos + certificado SII)
mkdir -p backend/assets/logos backend/certs
# scp -i tu-key.pem cert.pfx ec2-user@<IP>:~/corporate-app/backend/certs/

# 5. Levanta todo (la primera vez tarda 4-8 min: build + descarga imágenes)
docker-compose -f deploy/docker-compose.yml --env-file .env.production up -d --build

# 6. Verifica
docker-compose -f deploy/docker-compose.yml ps
docker-compose -f deploy/docker-compose.yml logs -f backend
```

La app estará en **http://<IP_EC2>** (puerto 80). Para HTTPS, ver más abajo.

## HTTPS — añadir TLS

### Opción 1 · ALB de AWS (recomendado en EC2)
1. Crea un **Application Load Balancer** en la VPC.
2. Asocia un certificado de **ACM** (gratis).
3. Listener 443 → Target Group apuntando a tu EC2:80.
4. Listener 80 → redirect a 443.
5. En el security group de la EC2: permite tráfico **sólo desde el SG del ALB**.

### Opción 2 · Caddy delante (sin ALB)
Añade un servicio `caddy` al `docker-compose.yml`:
```yaml
  caddy:
    image: caddy:2-alpine
    restart: always
    ports: ["80:80", "443:443"]
    volumes:
      - ./deploy/Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
    depends_on: [frontend]
```
Y crea `deploy/Caddyfile`:
```
tu-dominio.com {
    reverse_proxy frontend:80
}
```
Quita el `ports: ["80:80"]` del servicio `frontend` para que sólo Caddy reciba tráfico externo.

## Comandos útiles
```bash
# Logs en vivo
docker-compose -f deploy/docker-compose.yml logs -f backend
docker-compose -f deploy/docker-compose.yml logs -f frontend

# Reiniciar solo el backend tras tocar .env.production
docker-compose -f deploy/docker-compose.yml up -d --no-deps backend

# Rebuild tras un git pull
git pull
docker-compose -f deploy/docker-compose.yml up -d --build

# Backup de Mongo
docker-compose -f deploy/docker-compose.yml exec mongo \
  mongodump --archive=/data/db/dump_$(date +%F).gz --gzip

# Conectar shell de Mongo
docker-compose -f deploy/docker-compose.yml exec mongo mongosh

# Borrar TODO (¡cuidado, incluye volúmenes!)
docker-compose -f deploy/docker-compose.yml down -v
```

## Volúmenes persistentes
| Volumen           | Contenido                              | Backup recomendado |
|-------------------|----------------------------------------|-------------------|
| `mongo_data`      | Toda la BD                             | Diario via cron + S3 |
| `backend_storage` | PDFs Tasas + Pagos Ventanilla          | Semanal a S3       |
| `./backend/assets`| Logos de sociedades (montado, no volumen) | git commit       |
| `./backend/certs` | Certificado .pfx AEAT (montado)        | KMS / Secrets Mgr  |

## Notas
- Si **el backend ya está corriendo** y cambias el `REACT_APP_BACKEND_URL`, **debes rebuild el frontend** (queda embebido en el bundle JS).
- El admin seed (`miguelingv@gmail.com`) sólo se crea si la colección `users` está vacía. Edita `backend/auth_seed.py` para cambiarlo antes del primer arranque.
- El TTL index de `auth_mfa_challenges` se crea automáticamente al arrancar.
