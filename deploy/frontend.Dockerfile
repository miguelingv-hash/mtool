# Multi-stage: 1) build con Node 20  2) nginx alpine sirve el bundle
FROM node:20-alpine AS build

ARG REACT_APP_BACKEND_URL
ENV REACT_APP_BACKEND_URL=$REACT_APP_BACKEND_URL
# Sube el heap de Node a 3 GB para que webpack no muera (CRA es ávido de RAM).
# Si tu host tiene <2 GB, añade swap antes (4 GB recomendado).
ENV NODE_OPTIONS=--max-old-space-size=3072
# Desactiva sourcemaps en build (-40 % RAM, -30 % tamaño bundle)
ENV GENERATE_SOURCEMAP=false

WORKDIR /app
COPY frontend/package.json /app/
# yarn.lock se omite porque no se versiona — se regenera durante el install.
# Trade-off: el build NO es 100 % reproducible respecto a versiones minor de deps.
RUN yarn install --network-timeout 600000

COPY frontend/ /app/
RUN yarn build

# ----------------------------------------------------------------------------
FROM nginx:1.27-alpine

# Genera certificado self-signed (válido 10 años) para HTTPS con IP literal.
# Sustitúyelo montando /etc/nginx/certs cuando tengas un dominio real.
RUN apk add --no-cache openssl && \
    mkdir -p /etc/nginx/certs && \
    openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
      -keyout /etc/nginx/certs/selfsigned.key \
      -out    /etc/nginx/certs/selfsigned.crt \
      -subj "/CN=corporate-app" \
      -addext "subjectAltName=IP:0.0.0.0,DNS:localhost"

COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/build /usr/share/nginx/html

EXPOSE 80 443
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD wget -qO- http://127.0.0.1/healthz > /dev/null || exit 1

CMD ["nginx", "-g", "daemon off;"]
