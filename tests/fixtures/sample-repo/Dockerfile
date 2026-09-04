ARG NODE_VERSION=20

FROM node:${NODE_VERSION}-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci

FROM python:3.9-slim-bookworm
WORKDIR /srv
COPY --from=builder /app/dist /srv/dist
CMD ["python", "-m", "srv"]
