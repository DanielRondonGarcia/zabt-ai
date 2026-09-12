// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
import { createApiClient } from "@zabt/shared";
import { router } from "expo-router";
import { clearTokens, getAccessToken } from "./auth-storage";
import { refreshTokens } from "./auth";

const API_URL = process.env.EXPO_PUBLIC_API_URL;

if (!API_URL) {
  throw new Error("EXPO_PUBLIC_API_URL must be set.");
}

export const api = createApiClient({
  baseURL: API_URL,
  getAuthToken: getAccessToken,
  refreshAuthToken: refreshTokens,
  onUnauthorized: async () => {
    await clearTokens();
    router.replace("/(auth)/login");
  },
});
