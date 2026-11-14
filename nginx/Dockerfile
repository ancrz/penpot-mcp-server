FROM node:22-alpine AS ui_builder
WORKDIR /ui
COPY frontend/package.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY nginx/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=ui_builder /ui/dist /usr/share/nginx/html
EXPOSE 8080
