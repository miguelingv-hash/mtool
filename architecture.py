"""
Diagrama de arquitectura Corporate App en AWS EC2.

Requiere:
    pip install diagrams
    (Graphviz en el SO: `brew install graphviz` en Mac,
                        `apt install graphviz` en Ubuntu)

Uso:
    python architecture.py       → genera corporate_app_architecture.png

El diagrama refleja el despliegue actual documentado en:
    /app/deploy/docker-compose.yml
    /app/deploy/Caddyfile
    /app/.github/workflows/deploy.yml
    /app/backend/scripts/CLI_IMPORTS.md
"""

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import EC2
from diagrams.aws.database import DocumentdbMongodbCompatibility as MongoDB
from diagrams.aws.network import (
    VPC, InternetGateway, ELB, PublicSubnet, PrivateSubnet, Route53,
)
from diagrams.aws.security import IdentityAndAccessManagementIamAccessAnalyzer as SG
from diagrams.aws.storage import ElasticBlockStoreEBS, S3
from diagrams.aws.devtools import Codepipeline
from diagrams.aws.integration import SimpleNotificationServiceSns
from diagrams.onprem.client import User, Client
from diagrams.onprem.container import Docker
from diagrams.onprem.network import Caddy
from diagrams.onprem.vcs import Github
from diagrams.programming.framework import React, Fastapi
from diagrams.saas.chat import Slack  # usaremos como stand-in para Resend

graph_attr = {
    "fontsize": "18",
    "bgcolor": "white",
    "pad": "0.8",
    "splines": "spline",
}

with Diagram(
    "Corporate App — Arquitectura AWS EC2",
    filename="corporate_app_architecture",
    show=False,
    direction="TB",
    graph_attr=graph_attr,
):

    # ------------------------------------------------------------------
    # Usuarios y sistemas externos (columna izquierda)
    # ------------------------------------------------------------------
    with Cluster("Usuarios finales"):
        browser = Client("Navegador\n(admin / usuario)")
        laptop = User("Portátil admin\n(scp CSV + SSH)")

    with Cluster("Servicios externos SaaS"):
        aeat = SimpleNotificationServiceSns("AEAT SII\nSOAP · mTLS")
        resend = Slack("Resend\nEmail OTP / MFA")

    with Cluster("CI/CD"):
        gh_repo = Github("GitHub\n(repo main)")
        gh_actions = Codepipeline("GitHub Actions\n.github/workflows/deploy.yml")
        gh_repo >> Edge(label="push main") >> gh_actions

    # ------------------------------------------------------------------
    # AWS Cloud — VPC + EC2 + servicios AWS
    # ------------------------------------------------------------------
    with Cluster("AWS Region · eu-west-1"):
        r53 = Route53("Route 53\ncorporate.example.com")

        with Cluster("VPC · corporate-vpc (10.0.0.0/16)"):
            igw = InternetGateway("Internet Gateway")

            with Cluster("Public Subnet · 10.0.1.0/24"):
                sg = SG("Security Group\n22/443/80 ← usuario")

                with Cluster("EC2 · t3.small (Docker Compose)"):
                    ec2_host = EC2("ec2-user@host\n/home/ec2-user/data")

                    with Cluster("Contenedores gestionados por Compose"):
                        caddy = Caddy(
                            "Caddy\nHTTPS + Let's Encrypt\nrequest_body 600 MB",
                        )
                        frontend = React("frontend\nnginx + build React")
                        backend = Fastapi(
                            "backend\nFastAPI + Motor\nUvicorn 8001",
                        )
                        mongo = MongoDB("mongo:7\nfacturas_sii, comercial,\njobs, users, roles")

                    ebs = ElasticBlockStoreEBS("EBS gp3\n mongo_data,\nbackend_storage,\ncaddy_data")

                    # Compose services
                    caddy >> Edge(color="darkgreen") >> frontend
                    caddy >> Edge(label="/api/*", color="darkgreen") >> backend
                    backend >> Edge(color="darkblue") >> mongo
                    mongo - Edge(style="dotted", label="volumes") - ebs

        # Storage opcional para imports asíncronos (backlog)
        s3_optional = S3("S3 (opcional)\ncorporate-imports/\n(pipeline nocturno)")

    # ------------------------------------------------------------------
    # Flujos externos
    # ------------------------------------------------------------------

    # 1) Cliente → app vía HTTPS
    browser >> Edge(label="HTTPS", color="black") >> r53
    r53 >> Edge(label="A → EIP") >> igw
    igw >> Edge() >> sg >> Edge() >> caddy

    # 2) CLI carga masiva (scp + docker exec)
    laptop >> Edge(label="scp CSV\nSSH :22", color="firebrick", style="bold") >> ec2_host
    ec2_host >> Edge(label="/data volume", color="firebrick") >> backend

    # 3) CI/CD deploy
    gh_actions >> Edge(label="SSH deploy.sh", color="purple") >> ec2_host

    # 4) Outbound a servicios externos
    backend >> Edge(label="SOAP + mTLS\n.pfx cert", color="darkorange") >> aeat
    backend >> Edge(label="API HTTPS", color="darkorange") >> resend

    # 5) Opcional: pipeline nocturno de imports
    s3_optional >> Edge(style="dashed", label="aws s3 cp\n(cron)") >> ec2_host
