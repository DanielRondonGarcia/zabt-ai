// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
import AsyncStorage from "@react-native-async-storage/async-storage";
import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";

const ACCESS_TOKEN_KEY = "zabt.auth.access_token";
const REFRESH_TOKEN_KEY = "zabt.auth.refresh_token";

let legacyTokenCleanup: Promise<void> | undefined;

/** Remove tokens written by older versions that fell back to AsyncStorage. */
function cleanupLegacyAsyncStorageTokens(): Promise<void> {
  legacyTokenCleanup ??= Promise.all([
    Promise.resolve()
      .then(() => AsyncStorage.removeItem(ACCESS_TOKEN_KEY))
      .catch(() => undefined),
    Promise.resolve()
      .then(() => AsyncStorage.removeItem(REFRESH_TOKEN_KEY))
      .catch(() => undefined),
  ]).then(() => undefined);
  return legacyTokenCleanup;
}

function assertNativePlatform(): void {
  if (Platform.OS !== "ios" && Platform.OS !== "android") {
    throw new Error(`Secure token storage is unsupported on ${Platform.OS}.`);
  }
}

/** Web uses AsyncStorage; native tokens are stored only in SecureStore. */
const storage = {
  async getItem(key: string): Promise<string | null> {
    if (Platform.OS === "web") {
      return AsyncStorage.getItem(key);
    }
    assertNativePlatform();
    await cleanupLegacyAsyncStorageTokens();
    return SecureStore.getItemAsync(key);
  },
  async setItem(key: string, value: string): Promise<void> {
    if (Platform.OS === "web") {
      await AsyncStorage.setItem(key, value);
      return;
    }
    assertNativePlatform();
    await cleanupLegacyAsyncStorageTokens();
    await SecureStore.setItemAsync(key, value);
  },
  async removeItem(key: string): Promise<void> {
    if (Platform.OS === "web") {
      await AsyncStorage.removeItem(key);
      return;
    }
    assertNativePlatform();
    await cleanupLegacyAsyncStorageTokens();
    await SecureStore.deleteItemAsync(key);
  },
};

export async function getAccessToken(): Promise<string | null> {
  return storage.getItem(ACCESS_TOKEN_KEY);
}

export async function getRefreshToken(): Promise<string | null> {
  return storage.getItem(REFRESH_TOKEN_KEY);
}

export async function saveTokens(
  accessToken: string,
  refreshToken: string
): Promise<void> {
  await Promise.all([
    storage.setItem(ACCESS_TOKEN_KEY, accessToken),
    storage.setItem(REFRESH_TOKEN_KEY, refreshToken),
  ]);
}

export async function clearTokens(): Promise<void> {
  await Promise.all([
    storage.removeItem(ACCESS_TOKEN_KEY),
    storage.removeItem(REFRESH_TOKEN_KEY),
  ]);
}
