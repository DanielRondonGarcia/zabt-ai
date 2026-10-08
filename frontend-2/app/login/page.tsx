// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Eye, EyeOff, ShieldCheck } from "lucide-react";
import {
  getAuthenticationMode,
  getApiErrorStatus,
  getMicrosoftOidcStatus,
  login,
  type AuthenticationModeResponse,
  type MicrosoftOidcStatus,
} from "@/app/lib/api";
import { initializeMicrosoftOidc, signInWithMicrosoft } from "@/app/lib/microsoft-oidc";
import { Button } from "@/app/components/ui/button";
import { Input } from "@/app/components/ui/input";

function LoginPageContent() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [microsoftLoading, setMicrosoftLoading] = useState(false);
  const [authenticationMode, setAuthenticationMode] = useState<AuthenticationModeResponse | null>(null);
  const [modeLoading, setModeLoading] = useState(true);
  const [microsoftStatus, setMicrosoftStatus] = useState<MicrosoftOidcStatus | null>(null);
  const [microsoftStatusLoading, setMicrosoftStatusLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    getAuthenticationMode()
      .then((mode) => {
        if (!cancelled) setAuthenticationMode(mode);
      })
      .catch(() => {
        if (!cancelled) setAuthenticationMode(null);
      })
      .finally(() => {
        if (!cancelled) setModeLoading(false);
      });
    getMicrosoftOidcStatus()
      .then((status) => {
        if (!cancelled) setMicrosoftStatus(status);
        if (status.configured) void initializeMicrosoftOidc(status).catch(() => undefined);
      })
      .catch(() => {
        if (!cancelled) setMicrosoftStatus(null);
      })
      .finally(() => {
        if (!cancelled) setMicrosoftStatusLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (authenticationMode?.mode !== "local") return;
    setError(null);
    setLoading(true);
    try {
      await login(email, password);
      window.location.assign("/");
    } catch {
      setError("Incorrect email or password. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  const handleMicrosoftSignIn = async () => {
    if (!oidcAvailable || !microsoftStatus) return;
    setError(null);
    setMicrosoftLoading(true);
    try {
      await signInWithMicrosoft(microsoftStatus);
      window.location.assign("/");
    } catch (microsoftError) {
      setError(
        getApiErrorStatus(microsoftError) === 409
          ? "A local account already uses this email. Sign in locally and link Microsoft from Integrations."
          : "Microsoft sign-in could not be completed. Please try again.",
      );
    } finally {
      setMicrosoftLoading(false);
    }
  };

  const oidcAvailable =
    authenticationMode?.mode === "microsoft_oidc" &&
    authenticationMode.oidc_configured &&
    microsoftStatus?.configured === true;
  const regularModeReady = authenticationMode !== null && !modeLoading;

  return (
    <main className="flex min-h-screen items-center justify-center bg-stone-50 px-4 dark:bg-stone-950">
      <div className="w-full max-w-md rounded-lg border border-stone-200 bg-white p-8 dark:border-stone-800 dark:bg-stone-900">
        <div className="mb-6">
          <p className="mb-4 text-xl font-bold text-stone-900 dark:text-stone-100">Zabt</p>
          <h1 className="mb-1 text-2xl font-bold text-stone-900 dark:text-stone-100">
            Welcome back
          </h1>
          <p className="text-sm text-stone-500 dark:text-stone-400">
            {authenticationMode?.mode === "microsoft_oidc"
              ? "Sign in with your Microsoft work account."
              : "Please enter your details to sign in."}
          </p>
        </div>

        {modeLoading ? (
          <p className="text-sm text-stone-500 dark:text-stone-400" role="status" aria-live="polite">
            Loading sign-in options…
          </p>
        ) : !regularModeReady ? (
          <p
            className="rounded-lg border border-stone-200 bg-stone-50 px-3 py-2 text-sm text-stone-600 dark:border-stone-800 dark:bg-stone-950 dark:text-stone-300"
            role="alert"
          >
            Sign-in options are temporarily unavailable. Please try again later.
          </p>
        ) : authenticationMode.mode === "local" ? (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label
                htmlFor="email"
                className="mb-1 block text-sm font-medium text-stone-700 dark:text-stone-300"
              >
                Email address
              </label>
              <Input
                id="email"
                name="email"
                type="email"
                required
                autoComplete="email"
                spellCheck={false}
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                placeholder="name@company.com"
              />
            </div>
            <div>
              <label
                htmlFor="password"
                className="mb-1 block text-sm font-medium text-stone-700 dark:text-stone-300"
              >
                Password
              </label>
              <div className="relative">
                <Input
                  id="password"
                  name="password"
                  type={showPassword ? "text" : "password"}
                  required
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  className="pr-10"
                  placeholder="Your password"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((value) => !value)}
                  className="absolute inset-y-0 right-0 flex items-center px-3 text-stone-400 hover:text-stone-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary dark:hover:text-stone-200"
                  aria-label={showPassword ? "Hide password" : "Show password"}
                >
                  {showPassword ? <EyeOff size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}
                </button>
              </div>
            </div>
            <div className="flex justify-end">
              <Link href="/forgot-password" className="text-sm text-primary hover:underline">
                Forgot password?
              </Link>
            </div>
            {error && <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-300" role="alert" aria-live="polite">{error}</p>}
            <Button type="submit" loading={loading} className="w-full">
              Sign in
            </Button>
          </form>
        ) : (
          <div className="space-y-4">
            {microsoftStatusLoading ? (
              <p className="text-sm text-stone-500 dark:text-stone-400" role="status" aria-live="polite">
                Checking Microsoft sign-in…
              </p>
            ) : oidcAvailable && microsoftStatus ? (
              <>
                {error && <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-300" role="alert" aria-live="polite">{error}</p>}
                <Button
                  type="button"
                  variant="outline"
                  loading={microsoftLoading}
                  onClick={handleMicrosoftSignIn}
                  className="w-full"
                >
                  <ShieldCheck size={16} aria-hidden="true" />
                  Continue with Microsoft
                </Button>
              </>
            ) : (
              <p className="rounded-lg border border-stone-200 bg-stone-50 px-3 py-2 text-sm text-stone-600 dark:border-stone-800 dark:bg-stone-950 dark:text-stone-300" role="status">
                Microsoft sign-in is selected for this instance but is not available right now. Local sign-in remains disabled; contact the system administrator.
              </p>
            )}
          </div>
        )}

        {regularModeReady && authenticationMode?.mode === "local" && (
          <p className="mt-6 text-center text-sm text-stone-500 dark:text-stone-400">
            Don&apos;t have an account?{" "}
            <Link href="/register" className="text-primary hover:underline">
              Register
            </Link>
          </p>
        )}
        <p className="mt-6 text-center text-xs text-stone-500 dark:text-stone-400">
          System administrator?{" "}
          <Link href="/superuser/login" className="text-primary hover:underline">
            Use the administrator sign-in
          </Link>
        </p>
      </div>
    </main>
  );
}

export default function LoginPage() {
  return <LoginPageContent />;
}
