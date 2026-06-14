# ---------- Stage 1: build estático con CRA + craco ----------
FROM node:20-alpine AS build

WORKDIR /app

# Copiar primero los manifiestos para aprovechar la caché de capas.
# `yarn.loc[k]` es un truco de glob: si yarn.lock existe se copia;
# si no existe, el COPY no falla (BuildKit lo trata como patrón vacío).
COPY package.json ./
COPY yarn.loc[k] ./

# `yarn install` con frozen-lockfile cuando hay yarn.lock; sin él si no
# existe (se generará en el build). Esto evita que el build se rompa al
# bajar el código de zips o repos que excluyen el lockfile.
RUN if [ -f yarn.lock ]; then \
        yarn install --frozen-lockfile --network-timeout 600000; \
    else \
        echo "WARNING: yarn.lock no encontrado, generando uno..." && \
        yarn install --network-timeout 600000; \
    fi

# Build args
ARG REACT_APP_BACKEND_URL=""
ENV REACT_APP_BACKEND_URL=$REACT_APP_BACKEND_URL \
    ENABLE_HEALTH_CHECK=false \
    CI=false \
    GENERATE_SOURCEMAP=false

COPY . .
RUN yarn build

# ---------- Stage 2: nginx servir SPA + proxy /api ----------
FROM nginx:1.27-alpine

# Copia el build estático
COPY --from=build /app/build /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD wget -qO- http://localhost/ >/dev/null || exit 1
