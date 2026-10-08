// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua

import AsyncStorage from "@react-native-async-storage/async-storage";
import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";
import { clearTokens, getAccessToken, saveTokens } from "../lib/auth-storage";

jest.mock("@react-native-async-storage/async-storage", () => ({
  __esModule: true,
  default: {
    getItem: jest.fn(),
    setItem: jest.fn(),
    removeItem: jest.fn(),
  },
}));

jest.mock("expo-secure-store", () => ({
  getItemAsync: jest.fn(),
  setItemAsync: jest.fn(),
  deleteItemAsync: jest.fn(),
}));

jest.mock("react-native", () => ({ Platform: { OS: "ios" } }));

const asyncStorage = AsyncStorage as jest.Mocked<typeof AsyncStorage>;
const secureStore = SecureStore as jest.Mocked<typeof SecureStore>;

function setPlatform(os: "ios" | "android" | "web"): void {
  Object.defineProperty(Platform, "OS", { configurable: true, value: os });
}

beforeEach(() => {
  jest.clearAllMocks();
  setPlatform("ios");
  asyncStorage.getItem.mockResolvedValue(null);
  asyncStorage.setItem.mockResolvedValue();
  asyncStorage.removeItem.mockResolvedValue();
  secureStore.getItemAsync.mockResolvedValue(null);
  secureStore.setItemAsync.mockResolvedValue();
  secureStore.deleteItemAsync.mockResolvedValue();
});

test.each(["ios", "android"] as const)(
  "%s does not fall back to AsyncStorage when SecureStore reads fail",
  async (platform) => {
    setPlatform(platform);
    const failure = new Error("SecureStore unavailable");
    secureStore.getItemAsync.mockRejectedValue(failure);

    await expect(getAccessToken()).rejects.toBe(failure);

    expect(secureStore.getItemAsync).toHaveBeenCalledWith("zabt.auth.access_token");
    expect(asyncStorage.getItem).not.toHaveBeenCalled();
    expect(asyncStorage.setItem).not.toHaveBeenCalled();
  },
);

test("native token writes fail closed without persisting tokens to AsyncStorage", async () => {
  const failure = new Error("SecureStore unavailable");
  secureStore.setItemAsync.mockRejectedValue(failure);

  await expect(saveTokens("access-token", "refresh-token")).rejects.toBe(failure);

  expect(secureStore.setItemAsync).toHaveBeenCalledTimes(2);
  expect(asyncStorage.setItem).not.toHaveBeenCalled();
  expect(asyncStorage.getItem).not.toHaveBeenCalled();
});

test("web stores and clears tokens with AsyncStorage without using SecureStore", async () => {
  setPlatform("web");
  asyncStorage.getItem.mockImplementation(async (key) => `${key}-value`);

  await expect(getAccessToken()).resolves.toBe("zabt.auth.access_token-value");
  await saveTokens("access-token", "refresh-token");
  await clearTokens();

  expect(asyncStorage.setItem).toHaveBeenCalledWith(
    "zabt.auth.access_token",
    "access-token",
  );
  expect(asyncStorage.setItem).toHaveBeenCalledWith(
    "zabt.auth.refresh_token",
    "refresh-token",
  );
  expect(asyncStorage.removeItem).toHaveBeenCalledWith("zabt.auth.access_token");
  expect(asyncStorage.removeItem).toHaveBeenCalledWith("zabt.auth.refresh_token");
  expect(secureStore.getItemAsync).not.toHaveBeenCalled();
  expect(secureStore.setItemAsync).not.toHaveBeenCalled();
  expect(secureStore.deleteItemAsync).not.toHaveBeenCalled();
});
