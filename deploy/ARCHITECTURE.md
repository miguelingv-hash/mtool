# 🏗️ Arquitectura de Componentes — Corporate App

> Vista general de la solución desplegada en AWS EC2.

---

## 📐 Diagrama de arquitectura

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              USUARIO FINAL                                  │
│                       (Navegador / Cliente HTTP)                            │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │ HTTPS (TLS 1.3)
                                  │ https://3-125-115-81.sslip.io
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        DNS PÚBLICO (sslip.io)                               │
│  3-125-115-81.sslip.io  →  resuelve a 3.125.115.81                          │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                  AWS EC2 — Amazon Linux 2023 (3.125.115.81)                 │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │  Security Group:                                                      │  │
│  │  • 22  (SSH)     ← admin                                              │  │
│  │  • 80  (HTTP)    ← redirige a 443                                     │  │
│  │  • 443 (HTTPS)   ← tráfico app                                        │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                         DOCKER COMPOSE STACK                          │  │
│  │                                                                       │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐  │  │
│  │  │  📦 frontend   (Caddy 2.8 + React 19 build)                     │  │  │
│  │  │  • Puertos: 80 → 80, 443 → 443                                  │  │  │
│  │  │  • Sirve la SPA estática desde /usr/share/caddy/                │  │  │
│  │  │  • Termina TLS (Let's Encrypt ACME, renovación automática)      │  │  │
│  │  │  • Proxy /api/* → backend:8001                                  │  │  │
│  │  │  • Volumes: caddy_data (certs), caddy_config                    │  │  │
│  │  └────────────────────────────────────────┬────────────────────────┘  │  │
│  │                                           │ /api/*                    │  │
│  │                                           │ HTTP interno              │  │
│  │  ┌────────────────────────────────────────▼────────────────────────┐  │  │
│  │  │  ⚙️  backend   (FastAPI + Uvicorn + Gunicorn)                    │  │  │
│  │  │  • Puerto interno: 8001 (no expuesto al host)                   │  │  │
│  │  │  • Workers: 2                                                   │  │  │
│  │  │  • Routers:                                                     │  │  │
│  │  │    - /api/auth/*          (login, MFA, JWT, RBAC)               │  │  │
│  │  │    - /api/sii/*           (consultas SOAP AEAT)                 │  │  │
│  │  │    - /api/comparativa/*   (SAP FI vs SII)                       │  │  │
│  │  │    - /api/tasas/*         (PDFs Tasas Municipales)              │  │  │
│  │  │    - /api/pagos-ventanilla/* (PDFs pagos con código de barras)  │  │  │
│  │  │    - /api/logs/*          (logs WS)                             │  │  │
│  │  │  • Volume: backend_storage (PDFs generados, uploads CSV)        │  │  │
│  │  └────────────────────────────────────────┬────────────────────────┘  │  │
│  │                                           │ MongoDB protocol          │  │
│  │                                           │ mongo:27017               │  │
│  │  ┌────────────────────────────────────────▼────────────────────────┐  │  │
│  │  │  💾 mongo   (MongoDB 7)                                          │  │  │
│  │  │  • Puerto interno: 27017 (no expuesto al host)                  │  │  │
│  │  │  • DB: corporate_app                                            │  │  │
│  │  │  • Colecciones principales:                                     │  │  │
│  │  │    - users, roles                                               │  │  │
│  │  │    - auth_mfa_challenges (TTL 5 min)                            │  │  │
│  │  │    - activation_tokens, login_attempts                          │  │  │
│  │  │    - facturas_sii, facturas_comercial                           │  │  │
│  │  │    - logs_ws, comparativa_config                                │  │  │
│  │  │    - tasas_jobs, tasas_municipios, tasas_tasas                  │  │  │
│  │  │    - pagos_ventanilla_jobs                                      │  │  │
│  │  │  • Volume: mongo_data (persistencia)                            │  │  │
│  │  └─────────────────────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└───────────┬───────────────────────────┬───────────────────────────┬─────────┘
            │                           │                           │
            ▼                           ▼                           ▼
   ┌──────────────────┐       ┌──────────────────┐       ┌──────────────────┐
   │   🌐 AEAT SII    │       │   📧 Resend      │       │   🐙 GitHub      │
   │   (SOAP/mTLS)    │       │   (Emails MFA +  │       │   (CI repo)      │
   │   sede.aeat.es   │       │    activación)   │       │   git pull       │
   └──────────────────┘       └──────────────────┘       └──────────────────┘
```

---

## 🧩 Detalle de componentes

### 1. Frontend — Caddy + React SPA

| Aspecto | Detalle |
|---|---|
| **Imagen base** | `caddy:2.8-alpine` (multi-stage build con `node:20-alpine`) |
| **Stack** | React 19, React Router 7, Shadcn UI, TailwindCSS, Recharts |
| **Tema visual** | Swiss / High-Contrast (Satoshi + IBM Plex Sans) |
| **HTTPS** | Let's Encrypt automático vía Caddy ACME |
| **Routing** | SPA con fallback `try_files {path} /index.html` |
| **Build args** | `REACT_APP_BACKEND_URL` (embebido en bundle JS en build-time) |
| **Volúmenes** | `caddy_data` (certs persistentes), `caddy_config` |
| **Healthcheck** | `GET /healthz` → 200 OK |

### 2. Backend — FastAPI + Uvicorn

| Aspecto | Detalle |
|---|---|
| **Imagen base** | `python:3.11-slim` |
| **Stack** | FastAPI, Motor (Mongo async), Zeep (SOAP), PyJWT, Passlib (bcrypt) |
| **Auth** | JWT en cookies HttpOnly + MFA por email (OTP 6 dígitos, TTL 5 min) |
| **PDF generation** | ReportLab + python-barcode (Tasas Municipales, Pagos Ventanilla) |
| **SOAP client** | Zeep con mTLS (certificado `.pfx` para AEAT SII) |
| **Email** | Resend API (activación de cuenta + códigos MFA) |
| **Volúmenes** | `backend_storage` (PDFs generados, CSVs subidos) |
| **Healthcheck** | `GET /api/health` → 200 OK |

### 3. Database — MongoDB 7

| Aspecto | Detalle |
|---|---|
| **Imagen** | `mongo:7` |
| **DB** | `corporate_app` |
| **Auth** | Sin credenciales (red privada Docker, no expuesto al host) |
| **Persistencia** | Volumen `mongo_data` |
| **Índices** | `users.email` único, TTLs en `auth_mfa_challenges.expires_at` y `activation_tokens.expires_at` |
| **Healthcheck** | `mongosh --eval "db.adminCommand('ping')"` |

---

## 🔐 Flujos clave de seguridad

### Login con MFA (Email OTP)

```
Usuario → POST /api/auth/login {email, password}
         ← {mfa_required: true, challenge_id, email_hint}
         
Backend  → Resend API (envía OTP de 6 dígitos al email)
Mongo    ← auth_mfa_challenges {challenge_id, code_hash, ttl 5min}

Usuario  → POST /api/auth/mfa/verify {challenge_id, code}
         ← Cookies HttpOnly: monitorsii_access, monitorsii_refresh
         ← Body: {user: {email, role, permisos}}

Usuario  → GET /api/* (con cookies)
Backend  → valida JWT → ejecuta lógica → responde
```

### Activación primer admin

```
Backend startup → seed_auth() lee ADMIN_EMAIL del .env
                → si no existe: crea user status='pending' + token activación
                → Resend envía email con link https://.../activar/{token}
                
Admin clica  → frontend pide nueva password
             → POST /api/auth/setup-password {token, password}
             → backend hash bcrypt → status='active' → auto-login
```

### Consulta SII con mTLS

```
Usuario → POST /api/sii/consulta-unitaria {nif_emisor, serie, fecha, ...}
Backend → Zeep client con SII_CERT_PATH (.pfx) + SII_CERT_PASSWORD
        → AEAT SII SOAP (https://www2.agenciatributaria.gob.es/...)
        ← {EstadoFactura, CSV, ...}
Backend → guarda en facturas_sii + logs_ws
        ← respuesta al usuario
```

---

## 🌐 Red Docker

Los 3 contenedores comparten la red interna creada por Compose:

```
network: corporate-app_default
├── frontend   (alias: frontend)   → expuesto al host por puertos 80, 443
├── backend    (alias: backend)    → solo accesible desde la red interna
└── mongo      (alias: mongo)      → solo accesible desde la red interna
```

- `frontend` resuelve `backend:8001` → contenedor backend.
- `backend` resuelve `mongo:27017` → contenedor mongo.
- Ni `backend` ni `mongo` son accesibles directamente desde Internet
  (defensa en profundidad).

---

## 💾 Volúmenes persistentes

| Volumen | Contenido | Tamaño aprox. | Backup recomendado |
|---|---|---|---|
| `mongo_data` | Toda la BD (users, facturas, logs, jobs) | Crece según uso | ✅ Diario |
| `backend_storage` | PDFs generados, CSVs subidos | Crece según uso | ⚠️ Semanal |
| `caddy_data` | Certificados Let's Encrypt + ACME account | < 1 MB | ✅ Importante (evita rate limit) |
| `caddy_config` | Estado runtime de Caddy | < 1 MB | Opcional |

> ⚠️ Un `docker-compose down` NO borra estos volúmenes. Un `docker-compose down -v` SÍ
> los borra (¡pierdes datos!). **Nunca uses `-v` en producción.**

---

## 📦 Variables de entorno críticas

Archivo: `~/corporate-app/deploy/.env.production` (NO versionado en GitHub)

```env
# URLs públicas (HTTPS obligado en producción)
REACT_APP_BACKEND_URL=https://3-125-115-81.sslip.io
APP_URL=https://3-125-115-81.sslip.io

# MongoDB (red interna Docker)
MONGO_URL=mongodb://mongo:27017
DB_NAME=corporate_app

# JWT
JWT_SECRET=<cadena aleatoria 64+ hex>
JWT_ACCESS_MIN=30
JWT_REFRESH_DAYS=14

# Cookies (HTTPS → secure=true; HTTP → secure=false + samesite=lax)
COOKIE_SECURE=true
COOKIE_SAMESITE=lax

# Admin seed (el password se establece vía email de activación)
ADMIN_EMAIL=miguelingv@gmail.com
ADMIN_NAME=Administrador

# Resend (emails MFA + activación)
RESEND_API_KEY=re_<tu-key>
SENDER_EMAIL=onboarding@resend.dev    # o tu dominio verificado

# AEAT SII (mTLS)
SII_CERT_PATH=/app/certs/dummy.pfx
SII_CERT_PASSWORD=<password del .pfx>

# Workers Gunicorn
WORKERS=2
```

---

## 🚦 Flujo de despliegue (CI/CD manual)

```
┌────────────────┐  Save to       ┌────────────┐  git pull   ┌─────────────┐
│  Emergent IDE  │ ──────────────►│   GitHub   │ ───────────►│  AWS EC2    │
│  (preview env) │  Github push   │  (repo)    │ (manual)    │  (prod env) │
└────────────────┘                └────────────┘             └─────────────┘
                                                                    │
                                                                    ▼
                                                         docker-compose up
                                                         --build --force-recreate
```

> 💡 **Próxima mejora**: configurar **GitHub Actions** para `docker-compose pull && up`
> automático al hacer push a `main`, evitando el SSH manual.

---

## 📊 Tecnologías y versiones

| Capa | Tecnología | Versión |
|---|---|---|
| OS | Amazon Linux | 2023 |
| Container runtime | Docker | 24+ |
| Orquestación | Docker Compose | v2 |
| Web server / TLS | Caddy | 2.8 |
| Frontend bundler | React Scripts | 5 |
| Backend | FastAPI | latest |
| Lenguaje backend | Python | 3.11 |
| Lenguaje frontend | JavaScript (React) | 19 |
| BD | MongoDB | 7 |
| TLS CA | Let's Encrypt (ACME) | — |
| DNS comodín | sslip.io | — |
| Email transactional | Resend | API v1 |

---

**Última actualización**: Feb 2026
