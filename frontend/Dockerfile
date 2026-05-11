FROM node:22-alpine AS build

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .

ARG VITE_APP_ENV=production
ARG VITE_GIT_SHA=unknown
ENV VITE_APP_ENV=$VITE_APP_ENV
ENV VITE_GIT_SHA=$VITE_GIT_SHA

RUN npm run build

FROM nginx:1.27-alpine

ENV PORT=8080

COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080
