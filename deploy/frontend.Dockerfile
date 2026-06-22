# Multi-stage: 1) build con Node 20  2) nginx alpine sirve el bundle
FROM node:20-alpine AS build

ARG REACT_APP_BACKEND_URL
ENV REACT_APP_BACKEND_URL=$REACT_APP_BACKEND_URL

WORKDIR /app
COPY frontend/package.json frontend/yarn.lock /app/
RUN yarn install --frozen-lockfile

COPY frontend/ /app/
RUN yarn build

# ----------------------------------------------------------------------------
FROM nginx:1.27-alpine

COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/build /usr/share/nginx/html

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD wget -qO- http://127.0.0.1/ > /dev/null || exit 1

CMD ["nginx", "-g", "daemon off;"]
