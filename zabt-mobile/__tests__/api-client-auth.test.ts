// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua

import { AxiosError } from "axios";
import { createApiClient } from "@zabt/shared";

test("injects the current Bearer token and refreshes once after a 401", async () => {
  let accessToken = "access-v1";
  const observedHeaders: unknown[] = [];
  const unauthorized = jest.fn();

  const adapter = async (config: any) => {
    observedHeaders.push(config.headers.Authorization);
    if (config.headers.Authorization === "Bearer access-v1") {
      throw new AxiosError(
        "Unauthorized",
        "ERR_BAD_REQUEST",
        config,
        undefined,
        {
          data: { detail: "Unauthorized" },
          status: 401,
          statusText: "Unauthorized",
          headers: {},
          config,
        }
      );
    }
    return {
      data: { ok: true },
      status: 200,
      statusText: "OK",
      headers: {},
      config,
    };
  };

  const api = createApiClient({
    baseURL: "http://localhost:8000/api/v1",
    getAuthToken: async () => accessToken,
    refreshAuthToken: async () => {
      accessToken = "access-v2";
      return true;
    },
    onUnauthorized: unauthorized,
    axiosConfig: { adapter },
  });

  await expect(api.get("/users/me")).resolves.toMatchObject({
    data: { ok: true },
  });
  expect(observedHeaders).toEqual(["Bearer access-v1", "Bearer access-v2"]);
  expect(unauthorized).not.toHaveBeenCalled();
});

test("shares one refresh across concurrent 401 responses", async () => {
  let accessToken = "access-v1";
  let initial401Count = 0;
  let resolveInitial401s!: () => void;
  const initial401s = new Promise<void>((resolve) => {
    resolveInitial401s = resolve;
  });
  let resolveRefreshStarted!: () => void;
  const refreshStarted = new Promise<void>((resolve) => {
    resolveRefreshStarted = resolve;
  });
  let resolveRefresh!: (success: boolean) => void;
  const refreshResult = new Promise<boolean>((resolve) => {
    resolveRefresh = resolve;
  });
  const observedHeaders: unknown[] = [];
  const unauthorized = jest.fn();

  const adapter = async (config: any) => {
    observedHeaders.push(config.headers.Authorization);
    if (config.headers.Authorization === "Bearer access-v1") {
      initial401Count += 1;
      if (initial401Count === 2) {
        resolveInitial401s();
      }
      throw new AxiosError(
        "Unauthorized",
        "ERR_BAD_REQUEST",
        config,
        undefined,
        {
          data: { detail: "Unauthorized" },
          status: 401,
          statusText: "Unauthorized",
          headers: {},
          config,
        }
      );
    }
    return {
      data: { ok: true },
      status: 200,
      statusText: "OK",
      headers: {},
      config,
    };
  };
  const refreshAuthToken = jest.fn(async () => {
    await initial401s;
    resolveRefreshStarted();
    const refreshed = await refreshResult;
    if (refreshed) {
      accessToken = "access-v2";
    }
    return refreshed;
  });
  const api = createApiClient({
    baseURL: "http://localhost:8000/api/v1",
    getAuthToken: async () => accessToken,
    refreshAuthToken,
    onUnauthorized: unauthorized,
    axiosConfig: { adapter },
  });

  const requests = [api.get("/users/me"), api.get("/meetings")];
  await refreshStarted;

  expect(refreshAuthToken).toHaveBeenCalledTimes(1);
  expect(observedHeaders).toEqual(["Bearer access-v1", "Bearer access-v1"]);

  resolveRefresh(true);
  const responses = await Promise.all(requests);

  expect(responses.map(({ data }) => data)).toEqual([{ ok: true }, { ok: true }]);
  expect(observedHeaders).toEqual([
    "Bearer access-v1",
    "Bearer access-v1",
    "Bearer access-v2",
    "Bearer access-v2",
  ]);
  expect(unauthorized).not.toHaveBeenCalled();
});

test("does not retry refresh failures and invokes the platform logout handler", async () => {
  const unauthorized = jest.fn();
  const adapter = async (config: any) => {
    throw new AxiosError(
      "Unauthorized",
      "ERR_BAD_REQUEST",
      config,
      undefined,
      {
        data: { detail: "Unauthorized" },
        status: 401,
        statusText: "Unauthorized",
        headers: {},
        config,
      }
    );
  };
  const api = createApiClient({
    baseURL: "http://localhost:8000/api/v1",
    getAuthToken: async () => "access-v1",
    refreshAuthToken: async () => false,
    onUnauthorized: unauthorized,
    axiosConfig: { adapter },
  });

  await expect(api.get("/users/me")).rejects.toBeInstanceOf(AxiosError);
  expect(unauthorized).toHaveBeenCalledTimes(1);
});
