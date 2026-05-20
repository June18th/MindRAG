# Frontend build stage
FROM node:20-alpine AS build

WORKDIR /app
RUN npm install -g pnpm@8

# Install dependencies (use Aliyun mirror for China)
COPY . .
RUN pnpm config set registry https://registry.npmmirror.com && \
    pnpm install --no-frozen-lockfile && \
    pnpm build

# Nginx serve stage
FROM nginx:alpine

COPY --from=build /app/dist /usr/share/nginx/html

# Nginx config for SPA routing
RUN echo 'server { \
    listen 80; \
    server_name localhost; \
    root /usr/share/nginx/html; \
    index index.html; \
    location / { try_files $uri $uri/ /index.html; } \
    location /api { proxy_pass http://backend:8000; } \
    location /chat { proxy_pass http://backend:8000; proxy_http_version 1.1; proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade"; } \
}' > /etc/nginx/conf.d/default.conf

EXPOSE 80
