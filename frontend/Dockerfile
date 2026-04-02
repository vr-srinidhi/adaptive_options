FROM node:20-alpine AS builder

# VITE_API_URL is baked into the JS bundle at build time.
# In Railway: set this to the backend service's public URL.
# Locally (Docker Compose): leave unset — nginx proxies /api/ instead.
ARG VITE_API_URL
ENV VITE_API_URL=$VITE_API_URL

WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:1.25-alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf.template
EXPOSE 80
CMD ["/bin/sh", "-c", "PORT=${PORT:-80} envsubst '$PORT' < /etc/nginx/conf.d/default.conf.template > /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'"]
