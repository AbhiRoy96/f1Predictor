FROM node:24-alpine AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:24-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0
# npm is not needed to run the standalone Next.js server. Remove it and its
# launchers so its bundled dependencies (including vulnerable undici versions)
# are not shipped in the production image.
RUN rm -rf /usr/local/lib/node_modules/npm /usr/local/bin/npm /usr/local/bin/npx
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
