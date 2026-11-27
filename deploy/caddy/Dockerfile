# Production Caddy image: builds the checkout page and staff dashboard bundles, so the server
# needs no Node.js. Built from the repository root by deploy/compose.prod.yaml.
FROM node:22.23.5-bookworm-slim@sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee AS frontend
WORKDIR /src
COPY frontend/checkout ./frontend/checkout
COPY frontend/dashboard ./frontend/dashboard
RUN cd frontend/checkout && npm ci && npm run build
RUN cd frontend/dashboard && npm ci && npm run build

FROM caddy:2.11.9@sha256:4851e54a46b206517c574821bb5cc67f170d2156041e9fbc4c9193b45e84a75b
COPY --from=frontend /src/frontend/checkout/dist /srv/checkout
COPY --from=frontend /src/frontend/dashboard/dist /srv/dashboard
COPY deploy/Caddyfile.prod /etc/caddy/Caddyfile
COPY deploy/caddy/old-domain.caddy.tmpl /etc/caddy/old-domain.caddy.tmpl
# Staging (deploy/STAGING.md): the forward used by production, and the generator the staging
# Caddy runs on this same Caddyfile.
COPY deploy/caddy/staging.caddy.tmpl /etc/caddy/staging.caddy.tmpl
COPY deploy/caddy/staging-caddyfile.sh /etc/caddy/staging-caddyfile.sh
