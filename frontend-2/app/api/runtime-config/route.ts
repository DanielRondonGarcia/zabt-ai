// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua

export const dynamic = "force-dynamic";

const DEFAULT_API_URL = "http://localhost:8000/api/v1";
const DEFAULT_FRONTEND_URL = "http://localhost:3000";
const NO_STORE_HEADERS = { "Cache-Control": "no-store, max-age=0" };

export function GET() {
  // Return only public URLs; bracket lookups keep Next.js from inlining them at build time.
  const apiUrl =
    process.env.ZABT_PUBLIC_API_URL ||
    process.env["NEXT_PUBLIC_API_URL"] ||
    (process.env.NODE_ENV === "production" ? "" : DEFAULT_API_URL);
  const frontendUrl =
    process.env.ZABT_PUBLIC_FRONTEND_URL ||
    process.env["NEXT_PUBLIC_FRONTEND_URL"] ||
    (process.env.NODE_ENV === "production" ? "" : DEFAULT_FRONTEND_URL);

  if (process.env.NODE_ENV === "production" && (!apiUrl || !frontendUrl)) {
    return Response.json(
      { error: "Public runtime URLs are not configured" },
      { status: 500, headers: NO_STORE_HEADERS },
    );
  }

  return Response.json(
    {
      apiUrl,
      frontendUrl,
    },
    { headers: NO_STORE_HEADERS },
  );
}
