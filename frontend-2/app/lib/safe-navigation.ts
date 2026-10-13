// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua

const SAFE_ORIGIN = "https://zabt.invalid";
const OAUTH_SCHEME_RE = /^([A-Za-z][A-Za-z0-9+.-]{1,62}):/;
const OAUTH_NATIVE_SCHEME_RE =
  /^(?=.{3,63}$)(?:[a-z][a-z0-9+.-]*\.)+[a-z][a-z0-9+.-]*$/;
const OAUTH_UNSAFE_CUSTOM_SCHEMES = new Set([
  "about",
  "blob",
  "chrome",
  "chrome-extension",
  "data",
  "file",
  "javascript",
  "mailto",
  "ms-appx",
  "ms-appx-web",
  "resource",
  "sms",
  "tel",
  "vbscript",
  "view-source",
]);
const OAUTH_LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "::1", "[::1]"]);

function getOAuthScheme(value: string): string | null {
  return OAUTH_SCHEME_RE.exec(value)?.[1].toLowerCase() ?? null;
}

function hasControlCharacter(value: string): boolean {
  return [...value].some((character) => {
    const code = character.charCodeAt(0);
    return code < 0x20 || code === 0x7f;
  });
}

/** Validate an OAuth response against the exact redirect URI shown to the user. */
export function getSafeOAuthRedirectUrl(
  value: string | null | undefined,
  expectedRedirectUri: string | null | undefined,
): string | null {
  if (
    typeof value !== "string" ||
    typeof expectedRedirectUri !== "string" ||
    !value ||
    !expectedRedirectUri ||
    value.length > 2048 ||
    expectedRedirectUri.length > 2048 ||
    value !== value.trim() ||
    expectedRedirectUri !== expectedRedirectUri.trim() ||
    hasControlCharacter(value) ||
    hasControlCharacter(expectedRedirectUri) ||
    value.includes("#") ||
    expectedRedirectUri.includes("#")
  ) {
    return null;
  }

  const expectedScheme = getOAuthScheme(expectedRedirectUri);
  const actualScheme = getOAuthScheme(value);
  if (!expectedScheme || actualScheme !== expectedScheme) return null;
  if (
    expectedScheme !== "https" &&
    expectedScheme !== "http" &&
    (!OAUTH_NATIVE_SCHEME_RE.test(expectedScheme) ||
      OAUTH_UNSAFE_CUSTOM_SCHEMES.has(expectedScheme))
  ) {
    return null;
  }

  let expectedUrl: URL;
  let actualUrl: URL;
  try {
    expectedUrl = new URL(expectedRedirectUri);
    actualUrl = new URL(value);
  } catch {
    return null;
  }

  if (
    expectedUrl.protocol !== actualUrl.protocol ||
    expectedUrl.username ||
    expectedUrl.password ||
    actualUrl.username ||
    actualUrl.password ||
    expectedUrl.hash ||
    actualUrl.hash
  ) {
    return null;
  }

  if (expectedScheme === "http") {
    const expectedHost = expectedUrl.hostname.toLowerCase();
    if (!OAUTH_LOOPBACK_HOSTS.has(expectedHost)) return null;
    if (actualUrl.hostname.toLowerCase() !== expectedHost) return null;
    if (expectedUrl.origin !== actualUrl.origin) return null;
  } else if (expectedScheme === "https") {
    if (expectedUrl.origin !== actualUrl.origin) return null;
  } else if (
    expectedUrl.host !== actualUrl.host ||
    expectedUrl.pathname !== actualUrl.pathname
  ) {
    return null;
  }

  const separator = expectedRedirectUri.includes("?") ? "&" : "?";
  const expectedPrefix = `${expectedRedirectUri}${separator}`;
  if (!value.startsWith(expectedPrefix)) return null;

  const responseParameters = new URLSearchParams(value.slice(expectedPrefix.length));
  const seen = new Set<string>();
  let hasResult = false;
  for (const [key, parameterValue] of responseParameters) {
    if (seen.has(key) || !["code", "error", "state"].includes(key)) return null;
    seen.add(key);
    if (key === "code") {
      if (!/^zabt_mcp_code_[A-Za-z0-9_-]{32,128}$/.test(parameterValue)) return null;
      hasResult = true;
    } else if (key === "error") {
      if (parameterValue !== "access_denied") return null;
      hasResult = true;
    } else if (!parameterValue || parameterValue.length > 512) {
      return null;
    }
  }

  return hasResult ? value : null;
}

/** Keep login continuations on this frontend and discard unsafe values. */
export function getSafeNextPath(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "/";
  if (
    typeof value !== "string" ||
    value.length > 2048 ||
    !value.startsWith("/") ||
    value.startsWith("//") ||
    value.includes("\\") ||
    value.includes("#") ||
    [...value].some((character) => {
      const code = character.charCodeAt(0);
      return code < 0x20 || code === 0x7f;
    })
  ) {
    return "/";
  }

  try {
    const parsed = new URL(value, SAFE_ORIGIN);
    if (parsed.origin !== SAFE_ORIGIN || !parsed.pathname.startsWith("/")) return "/";
    return `${parsed.pathname}${parsed.search}`;
  } catch {
    return "/";
  }
}
