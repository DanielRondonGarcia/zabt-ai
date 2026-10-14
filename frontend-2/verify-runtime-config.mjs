// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { setTimeout as delay } from "node:timers/promises";

const frontendDirectory = path.dirname(fileURLToPath(import.meta.url));
const nextCli = path.resolve(
  frontendDirectory,
  "..",
  "node_modules",
  "next",
  "dist",
  "bin",
  "next",
);
const cloudProdConfig = {
  apiUrl: "https://runtime-api.example.test/api/v1",
  frontendUrl: "https://runtime-web.example.test",
};

await verifyRuntimeConfig(
  {
    NEXT_PUBLIC_API_URL: "https://build-time-api.example.test/api/v1",
    NEXT_PUBLIC_FRONTEND_URL: "https://build-time-web.example.test",
    ZABT_PUBLIC_API_URL: cloudProdConfig.apiUrl,
    ZABT_PUBLIC_FRONTEND_URL: cloudProdConfig.frontendUrl,
  },
  cloudProdConfig,
);

const fullCloudConfig = {
  apiUrl: "https://full-cloud-api.example.test/api/v1",
  frontendUrl: "https://full-cloud-web.example.test",
};
await verifyRuntimeConfig(
  {
    NEXT_PUBLIC_API_URL: fullCloudConfig.apiUrl,
    NEXT_PUBLIC_FRONTEND_URL: fullCloudConfig.frontendUrl,
  },
  fullCloudConfig,
);

console.log("Runtime public URLs are read from the running container environment.");

async function verifyRuntimeConfig(runtimeEnvironment, expectedConfig) {
  const port = await findAvailablePort();
  const serverOutput = [];
  const server = spawn(
    process.execPath,
    [nextCli, "start", "--hostname", "127.0.0.1", "--port", String(port)],
    {
      cwd: frontendDirectory,
      env: { ...process.env, ...runtimeEnvironment },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  server.stdout.on("data", (chunk) => serverOutput.push(chunk.toString()));
  server.stderr.on("data", (chunk) => serverOutput.push(chunk.toString()));

  try {
    const deadline = Date.now() + 60_000;
    let response;

    while (Date.now() < deadline) {
      if (server.exitCode !== null) {
        throw new Error(
          `Next.js exited before serving runtime config (exit ${server.exitCode}):\n${serverOutput.join("").slice(-4_000)}`,
        );
      }

      try {
        response = await fetch(`http://127.0.0.1:${port}/api/runtime-config`);
        if (response.ok) break;
      } catch {
        // The production server is still starting.
      }

      await delay(500);
    }

    assert.ok(response?.ok, "runtime config route did not become available");
    assert.deepEqual(await response.json(), expectedConfig);
    assert.match(response.headers.get("cache-control") ?? "", /no-store/);
  } finally {
    server.kill();
  }
}

async function findAvailablePort() {
  const listener = net.createServer();
  await new Promise((resolve, reject) => {
    listener.once("error", reject);
    listener.listen(0, "127.0.0.1", resolve);
  });
  const address = listener.address();
  if (!address || typeof address === "string") {
    throw new Error("Could not determine an available port");
  }
  await new Promise((resolve, reject) => {
    listener.close((error) => (error ? reject(error) : resolve()));
  });
  return address.port;
}
