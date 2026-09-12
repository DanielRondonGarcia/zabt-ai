// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
/** Local token authentication for the Expo client. */

import axios from "axios";
import { useEffect, useState } from "react";
import type { AuthToken } from "@zabt/shared";
import {
  clearTokens,
  getAccessToken,
  getRefreshToken,
  saveTokens,
} from "./auth-storage";

const API_URL = process.env.EXPO_PUBLIC_API_URL;

if (!API_URL) {
  throw new Error("EXPO_PUBLIC_API_URL must be set.");
}

const authClient = axios.create({
  baseURL: API_URL,
  headers: { "Content-Type": "application/json" },
});

export type LocalSession = Pick<
  AuthToken,
  "access_token" | "refresh_token" | "token_type" | "expires_in"
>;

async function persistTokenResponse(response: AuthToken): Promise<LocalSession> {
  await saveTokens(response.access_token, response.refresh_token);
  return {
    access_token: response.access_token,
    refresh_token: response.refresh_token,
    token_type: response.token_type,
    expires_in: response.expires_in,
  };
}

export async function signIn(email: string, password: string): Promise<LocalSession> {
  const { data } = await authClient.post<AuthToken>("/auth/login", {
    email,
    password,
    client: "mobile",
  });
  return persistTokenResponse(data);
}

export async function register(
  email: string,
  password: string,
  fullName: string
): Promise<LocalSession> {
  const { data } = await authClient.post<AuthToken>("/auth/register", {
    email,
    password,
    full_name: fullName,
    client: "mobile",
  });
  return persistTokenResponse(data);
}

/** Refresh the current mobile session once and persist the rotated tokens. */
export async function refreshTokens(): Promise<boolean> {
  const refreshToken = await getRefreshToken();
  if (!refreshToken) return false;

  try {
    const { data } = await authClient.post<AuthToken>("/auth/refresh", {
      client: "mobile",
      refresh_token: refreshToken,
    });
    await persistTokenResponse(data);
    return true;
  } catch {
    await clearTokens();
    return false;
  }
}

export async function restoreSession(): Promise<LocalSession | null> {
  const [accessToken, refreshToken] = await Promise.all([
    getAccessToken(),
    getRefreshToken(),
  ]);
  if (accessToken && refreshToken) {
    return {
      access_token: accessToken,
      refresh_token: refreshToken,
      token_type: "bearer",
      expires_in: 0,
    };
  }
  if (refreshToken && (await refreshTokens())) {
    const refreshedAccessToken = await getAccessToken();
    const rotatedRefreshToken = await getRefreshToken();
    if (refreshedAccessToken && rotatedRefreshToken) {
      return {
        access_token: refreshedAccessToken,
        refresh_token: rotatedRefreshToken,
        token_type: "bearer",
        expires_in: 0,
      };
    }
  }
  await clearTokens();
  return null;
}

export async function signOut(): Promise<void> {
  const refreshToken = await getRefreshToken();
  try {
    if (refreshToken) {
      await authClient.post("/auth/logout", {
        client: "mobile",
        refresh_token: refreshToken,
      });
    }
  } finally {
    await clearTokens();
  }
}

/** Restore the persisted local session for the file-based auth gate. */
export function useAuth() {
  const [session, setSession] = useState<LocalSession | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    restoreSession()
      .then((restored) => {
        if (mounted) setSession(restored);
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });

    return () => {
      mounted = false;
    };
  }, []);

  return { session, user: null, loading };
}
