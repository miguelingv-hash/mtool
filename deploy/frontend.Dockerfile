# Multi-stage: 1) build con Node 20  2) Caddy sirve el bundle + HTTPS automático
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
RUN yarn install --network-timeout 600000

COPY frontend/ /app/
RUN yarn build

# ----------------------------------------------------------------------------
# Caddy v2 — HTTPS automático con Let's Encrypt
FROM caddy:2.8-alpine

COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/build /usr/share/caddy

EXPOSE 80 443

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD wget -qO- http://127.0.0.1/healthz > /dev/null || exit 1

CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
