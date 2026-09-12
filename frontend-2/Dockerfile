FROM node:20-alpine

WORKDIR /app

# Install the monorepo workspace from the root lockfile. The shared package is
# private and must be resolved from the workspace, not from the npm registry.
COPY package.json package-lock.json ./
COPY frontend-2/package.json ./frontend-2/package.json
COPY packages/shared/package.json ./packages/shared/package.json

RUN npm ci

# Keep development dependencies available for the shared workspace and Next.js
# build, then switch the runtime image to production mode.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1
ARG NEXT_PUBLIC_FRONTEND_URL=http://localhost:3000
ARG NEXT_PUBLIC_POSTHOG_KEY=
ARG NEXT_PUBLIC_POSTHOG_HOST=https://us.i.posthog.com
ARG NEXT_PUBLIC_SENTRY_DSN=
ARG NEXT_PUBLIC_SENTRY_ENVIRONMENT=development
ENV NODE_ENV=production \
    NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL} \
    NEXT_PUBLIC_FRONTEND_URL=${NEXT_PUBLIC_FRONTEND_URL} \
    NEXT_PUBLIC_POSTHOG_KEY=${NEXT_PUBLIC_POSTHOG_KEY} \
    NEXT_PUBLIC_POSTHOG_HOST=${NEXT_PUBLIC_POSTHOG_HOST} \
    NEXT_PUBLIC_SENTRY_DSN=${NEXT_PUBLIC_SENTRY_DSN} \
    NEXT_PUBLIC_SENTRY_ENVIRONMENT=${NEXT_PUBLIC_SENTRY_ENVIRONMENT}

# Build the shared workspace before Next.js starts so its package entrypoints
# resolve from the generated dist directory in the production image.
COPY packages/shared/tsconfig.json ./packages/shared/tsconfig.json
COPY packages/shared/src ./packages/shared/src
RUN npm run build:shared

# Copy only the frontend workspace after dependencies are installed.
COPY frontend-2/ ./frontend-2/

WORKDIR /app/frontend-2

RUN npm run build

EXPOSE 3000

CMD ["npm", "run", "start"]
