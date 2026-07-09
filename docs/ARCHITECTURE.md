# Corporate App — Arquitectura AWS EC2

## Diagrama Mermaid (para GitHub / Notion / OneNote)

```mermaid
graph TB
    subgraph "Usuarios finales"
        BROWSER[🖥️ Navegador<br/>admin · usuario]
        LAPTOP[👤 Portátil admin<br/>scp + SSH]
    end

    subgraph "CI/CD"
        GH[GitHub<br/>repo main]
        GHA[GitHub Actions<br/>deploy.yml]
        GH -->|push main| GHA
    end

    subgraph "AWS Region · eu-west-1"
        R53[Route 53<br/>corporate.example.com]

        subgraph "VPC corporate-vpc · 10.0.0.0/16"
            IGW[Internet Gateway]

            subgraph "Public Subnet · 10.0.1.0/24"
                SG[Security Group<br/>22 · 80 · 443]

                subgraph "EC2 t3.small · Docker Compose"
                    HOST[ec2-user@host<br/>/home/ec2-user/data]

                    subgraph "Containers"
                        CADDY[Caddy · HTTPS<br/>Let's Encrypt<br/>request_body 600 MB]
                        FE[frontend<br/>nginx + React]
                        BE[backend<br/>FastAPI + Motor]
                        MONGO[(mongo:7<br/>facturas · users · jobs)]
                    end

                    EBS[(EBS gp3<br/>mongo_data<br/>backend_storage<br/>caddy_data)]
                end
            end
        end

        S3[(S3 opcional<br/>corporate-imports<br/>pipeline nocturno)]
    end

    subgraph "Servicios externos"
        AEAT[🏛️ AEAT SII<br/>SOAP + mTLS]
        RESEND[📧 Resend<br/>Email OTP / MFA]
    end

    BROWSER -->|HTTPS| R53
    R53 -->|A → EIP| IGW
    IGW --> SG --> CADDY
    CADDY -->|/| FE
    CADDY -->|/api/*| BE
    BE --> MONGO
    MONGO -.-|volumes| EBS

    LAPTOP ==>|scp CSV + SSH:22| HOST
    HOST -->|/data volume| BE

    GHA -->|SSH deploy.sh| HOST

    BE -->|SOAP + mTLS<br/>.pfx cert| AEAT
    BE -->|API HTTPS| RESEND

    S3 -.->|aws s3 cp cron| HOST

    style BROWSER fill:#e0f2fe
    style LAPTOP fill:#fecaca
    style CADDY fill:#dcfce7
    style BE fill:#e0e7ff
    style MONGO fill:#fef3c7
    style AEAT fill:#fde68a
    style RESEND fill:#fecaca
```

## Resumen de componentes

| Componente | Rol | Tecnología |
|---|---|---|
| **Route 53** | DNS público (opcional) | AWS |
| **Internet Gateway + VPC** | Networking | AWS VPC 10.0.0.0/16 |
| **Security Group** | Firewall | Solo 22 (SSH admin), 80/443 (público) |
| **EC2 t3.small** | Host | Amazon Linux 2023 · Docker + Compose |
| **Caddy** | Reverse proxy + HTTPS | Let's Encrypt auto, body limit 600 MB |
| **frontend** | UI React | Build estático servido por nginx interno |
| **backend** | API | FastAPI + Motor · Uvicorn 8001 |
| **MongoDB** | Persistencia | mongo:7 en contenedor, EBS gp3 |
| **EBS** | Disco persistente | Volúmenes Docker (`mongo_data` etc.) |
| **GitHub Actions** | CI/CD | SSH → deploy.sh en EC2 |
| **AEAT SII** | Servicio externo | SOAP 1.1 + mTLS con .pfx |
| **Resend** | Servicio externo | Email OTP para MFA |

## Flujos principales

1. **Usuario web**: navegador → HTTPS → Route 53 → EC2 :443 → Caddy → frontend (/) o backend (/api/*).
2. **Admin CLI**: portátil → scp/SSH → `/home/ec2-user/data/` (volumen `/data` en backend) → `python -m scripts.import_*` → Mongo directo.
3. **CI/CD**: `git push main` → GitHub Actions → SSH a EC2 → `./deploy/deploy.sh` → rebuild + `docker-compose up -d`.
4. **Outbound AEAT**: backend usa certificado .pfx montado en `/app/certs:ro` → SOAP con mTLS → AEAT.
5. **Outbound Resend**: backend con API key → POST HTTPS al envío de emails.
