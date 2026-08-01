# Stage 1: build the static Vue app
FROM node:20-slim AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
# VITE_API_URL is baked in at build time since this becomes static JS served
# to the browser — the browser calls this URL directly, so it must be an
# address reachable from the USER'S machine (localhost:8000), not the
# Docker-internal service name (that distinction matters: unlike the
# server-side Streamlit app, this code runs in the browser, not a container).
ARG VITE_API_URL=http://localhost:8000
ENV VITE_API_URL=$VITE_API_URL
RUN npm run build

# Stage 2: serve the built static files with nginx
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
