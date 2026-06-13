# ---------- Stage 1: build estático con CRA + craco ----------
FROM node:20-alpine AS build

WORKDIR /app

# Instala dependencias primero para aprovechar la caché de capas
COPY package.json yarn.lock ./
RUN yarn install --frozen-lockfile --network-timeout 600000

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
