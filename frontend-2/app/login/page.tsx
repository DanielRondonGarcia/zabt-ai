// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Eye, EyeOff, ShieldCheck } from "lucide-react";
import {
  getApiErrorStatus,
  getMicrosoftOidcStatus,
  login,
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
  const [microsoftStatus, setMicrosoftStatus] = useState<MicrosoftOidcStatus | null>(null);
  const [microsoftStatusLoading, setMicrosoftStatusLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    getMicrosoftOidcStatus()
      .then((status) => {
        if (!cancelled) {
          setMicrosoftStatus(status);
          if (status.configured) {
            void initializeMicrosoftOidc(status).catch(() => undefined);
          }
        }
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
    if (!microsoftStatus?.configured) return;
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

  const microsoftConfigured = microsoftStatus?.configured === true;

  return (
    <main className="flex min-h-screen items-center justify-center bg-stone-50 px-4 dark:bg-stone-950">
      <div className="w-full max-w-md rounded-lg border border-stone-200 bg-white p-8 dark:border-stone-800 dark:bg-stone-900">
        <div className="mb-6">
          <p className="mb-4 text-xl font-bold text-stone-900 dark:text-stone-100">Zabt</p>
          <h1 className="mb-1 text-2xl font-bold text-stone-900 dark:text-stone-100">
            Welcome back
          </h1>
          <p className="text-sm text-stone-500 dark:text-stone-400">
            Please enter your details to sign in.
          </p>
        </div>

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
              type="email"
              required
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
                type={showPassword ? "text" : "password"}
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className="pr-10"
                placeholder="••••••••"
              />
              <button
                type="button"
                onClick={() => setShowPassword((value) => !value)}
                className="absolute inset-y-0 right-0 flex items-center px-3 text-stone-400 hover:text-stone-600 dark:hover:text-stone-200"
                aria-label={showPassword ? "Hide password" : "Show password"}
              >
                {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
          </div>

          <div className="flex justify-end">
            <Link href="/forgot-password" className="text-sm text-primary hover:underline">
              Forgot password?
            </Link>
          </div>

          {error && (
            <p
              className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-300"
              role="alert"
            >
              {error}
            </p>
          )}

          <Button type="submit" loading={loading} className="w-full">
            Sign in
          </Button>
        </form>

        {!microsoftStatusLoading && microsoftConfigured && (
          <>
            <div className="my-5 flex items-center gap-3 text-xs text-stone-400">
              <span className="h-px flex-1 bg-stone-200 dark:bg-stone-800" />
              <span>or</span>
              <span className="h-px flex-1 bg-stone-200 dark:bg-stone-800" />
            </div>
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
        )}

        {!microsoftStatusLoading && !microsoftConfigured && (
          <p className="mt-5 rounded-lg border border-stone-200 bg-stone-50 px-3 py-2 text-sm text-stone-500 dark:border-stone-800 dark:bg-stone-950 dark:text-stone-400">
            Microsoft sign-in is not configured for this instance. An administrator can configure it in{" "}
            <Link href="/integrations" className="text-primary hover:underline">
              Integrations
            </Link>
            .
          </p>
        )}

        <p className="mt-6 text-center text-sm text-stone-500 dark:text-stone-400">
          Don&apos;t have an account?{" "}
          <Link href="/register" className="text-primary hover:underline">
            Register
          </Link>
        </p>
      </div>
    </main>
  );
}

export default function LoginPage() {
  return <LoginPageContent />;
}
