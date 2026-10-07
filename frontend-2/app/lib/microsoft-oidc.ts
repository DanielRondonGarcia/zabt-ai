// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
"use client";

import {
  PublicClientApplication,
  type AuthenticationResult,
} from "@azure/msal-browser";
import {
  exchangeMicrosoftOidcToken,
  linkMicrosoftOidcToken,
  type MicrosoftOidcStatus,
} from "@/app/lib/api";

const MICROSOFT_OIDC_SCOPES = ["openid", "profile", "email"];
export const MICROSOFT_OIDC_SPA_REDIRECT_FALLBACK = "http://localhost:3001/login";

let msalApplication: PublicClientApplication | null = null;
let msalConfigurationKey: string | null = null;

/** Return the current SPA login URL without touching `window` during SSR. */
export function getDefaultMicrosoftOidcRedirectUri(): string {
  if (typeof window === "undefined") {
    return MICROSOFT_OIDC_SPA_REDIRECT_FALLBACK;
  }
  return `${window.location.origin}/login`;
}

export function isMicrosoftOidcRedirectUriForCurrentSpa(value: string): boolean {
  if (typeof window === "undefined") {
    return value === MICROSOFT_OIDC_SPA_REDIRECT_FALLBACK;
  }
  try {
    const redirect = new URL(value);
    return (
      redirect.origin === window.location.origin &&
      redirect.pathname === "/login" &&
      !redirect.search &&
      !redirect.hash
    );
  } catch {
    return false;
  }
}

function assertPublicConfiguration(configuration: MicrosoftOidcStatus): asserts configuration is MicrosoftOidcStatus & {
  client_id: string;
  tenant: string;
  redirect_uri: string;
} {
  if (
    !configuration.configured ||
    !configuration.client_id ||
    !configuration.tenant ||
    !configuration.redirect_uri
  ) {
    throw new Error("Microsoft sign-in is not configured");
  }
  if (typeof window !== "undefined") {
    if (!isMicrosoftOidcRedirectUriForCurrentSpa(configuration.redirect_uri)) {
      throw new Error("Microsoft sign-in redirect must use the current SPA login URL");
    }
  }
}

async function getMsalApplication(
  configuration: MicrosoftOidcStatus,
): Promise<PublicClientApplication> {
  assertPublicConfiguration(configuration);
  const key = [
    configuration.client_id,
    configuration.tenant,
    configuration.redirect_uri,
  ].join("|");

  if (msalApplication && msalConfigurationKey === key) {
    return msalApplication;
  }

  const application = new PublicClientApplication({
    auth: {
      clientId: configuration.client_id,
      authority: `https://login.microsoftonline.com/${encodeURIComponent(configuration.tenant)}`,
      redirectUri: configuration.redirect_uri,
    },
    cache: {
      cacheLocation: "sessionStorage",
    },
  });
  await application.initialize();
  msalApplication = application;
  msalConfigurationKey = key;
  return application;
}

/** Warm MSAL after public configuration loads; loginPopup still remains user initiated. */
export async function initializeMicrosoftOidc(
  configuration: MicrosoftOidcStatus,
): Promise<void> {
  await getMsalApplication(configuration);
}

async function loginPopup(configuration: MicrosoftOidcStatus): Promise<AuthenticationResult> {
  const application = await getMsalApplication(configuration);
  return application.loginPopup({
    scopes: MICROSOFT_OIDC_SCOPES,
    redirectUri: configuration.redirect_uri ?? undefined,
  });
}

/** Sign in with the configured public SPA and establish Zabt HttpOnly cookies. */
export async function signInWithMicrosoft(
  configuration: MicrosoftOidcStatus,
): Promise<void> {
  const result = await loginPopup(configuration);
  if (!result.idToken) {
    throw new Error("Microsoft sign-in did not return an ID token");
  }
  await exchangeMicrosoftOidcToken(result.idToken);
}

/** Link the selected Microsoft identity without changing the current Zabt session. */
export async function linkMicrosoftAccount(
  configuration: MicrosoftOidcStatus,
): Promise<void> {
  const result = await loginPopup(configuration);
  if (!result.idToken) {
    throw new Error("Microsoft linking did not return an ID token");
  }
  await linkMicrosoftOidcToken(result.idToken);
}
