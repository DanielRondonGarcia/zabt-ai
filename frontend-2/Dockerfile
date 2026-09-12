FROM node:20-alpine

WORKDIR /app

# Install the monorepo workspace from the root lockfile. The shared package is
# private and must be resolved from the workspace, not from the npm registry.
COPY package.json package-lock.json ./
COPY frontend-2/package.json ./frontend-2/package.json
COPY packages/shared/package.json ./packages/shared/package.json

RUN npm ci

# Build the shared workspace before Next.js starts so its package entrypoints
# resolve from the generated dist directory in the local development image.
COPY packages/shared/tsconfig.json ./packages/shared/tsconfig.json
COPY packages/shared/src ./packages/shared/src
RUN npm run build:shared

# Copy only the frontend workspace after dependencies are installed.
COPY frontend-2/ ./frontend-2/

WORKDIR /app/frontend-2

EXPOSE 3000

CMD ["npm", "run", "dev"]
