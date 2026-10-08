// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Eye, EyeOff } from "lucide-react";
import {
  getApiErrorStatus,
  getAuthenticationMode,
  register,
  type AuthenticationModeResponse,
} from "@/app/lib/api";
import { Button } from "@/app/components/ui/button";
import { Input } from "@/app/components/ui/input";

export default function RegisterPage() {
  const router = useRouter();
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [authenticationMode, setAuthenticationMode] = useState<AuthenticationModeResponse | null>(null);
  const [modeLoading, setModeLoading] = useState(true);

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
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (authenticationMode?.mode !== "local") return;
    setError(null);
    setLoading(true);
    try {
      await register(email, password, fullName);
      router.push("/");
    } catch (err: unknown) {
      const status = getApiErrorStatus(err);
      if (status === 400 || status === 409) {
        setError("This email is already registered. Try signing in instead.");
      } else {
        setError("Registration is temporarily unavailable. Please try again.");
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-stone-50 px-4 dark:bg-stone-950">
      <div className="w-full max-w-md rounded-lg border border-stone-200 bg-white p-8 dark:border-stone-800 dark:bg-stone-900">
        <div className="mb-6">
          <p className="mb-4 text-xl font-bold text-stone-900 dark:text-stone-100">Zabt</p>
          <h1 className="mb-1 text-2xl font-bold text-stone-900 dark:text-stone-100">Create your account</h1>
          <p className="text-sm text-stone-500 dark:text-stone-400">
            {authenticationMode?.mode === "microsoft_oidc"
              ? "This instance uses Microsoft sign-in for new accounts."
              : "Create a local Zabt account with your email and password."}
          </p>
        </div>

        {modeLoading ? (
          <p className="text-sm text-stone-500 dark:text-stone-400" role="status" aria-live="polite">
            Checking registration options…
          </p>
        ) : authenticationMode?.mode === "local" ? (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label htmlFor="full-name" className="mb-1 block text-sm font-medium text-stone-700 dark:text-stone-300">
                Full name
              </label>
              <Input
                id="full-name"
                name="name"
                type="text"
                autoComplete="name"
                required
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                placeholder="Your name"
              />
            </div>

            <div>
              <label htmlFor="email" className="mb-1 block text-sm font-medium text-stone-700 dark:text-stone-300">
                Email address
              </label>
              <Input
                id="email"
                name="email"
                type="email"
                autoComplete="email"
                spellCheck={false}
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="name@company.com"
              />
            </div>

            <div>
              <label htmlFor="password" className="mb-1 block text-sm font-medium text-stone-700 dark:text-stone-300">
                Password
              </label>
              <div className="relative">
              <Input
                id="password"
                name="password"
                type={showPassword ? "text" : "password"}
                required
                minLength={8}
                autoComplete="new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="pr-10"
                placeholder="At least 8 characters"
              />
              <button
                type="button"
                onClick={() => setShowPassword((v) => !v)}
                className="absolute inset-y-0 right-0 flex items-center px-3 text-stone-400 hover:text-stone-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary dark:hover:text-stone-200"
                aria-label={showPassword ? "Hide password" : "Show password"}
              >
                {showPassword ? <EyeOff size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}
              </button>
              </div>
            </div>

            {error && (
              <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-300" role="alert" aria-live="polite">
                {error}
              </p>
            )}

            <Button type="submit" loading={loading} className="w-full">
              Sign up
            </Button>
          </form>
        ) : (
          <p className="rounded-lg border border-stone-200 bg-stone-50 px-3 py-2 text-sm text-stone-600 dark:border-stone-800 dark:bg-stone-950 dark:text-stone-300" role="status">
            {authenticationMode
              ? "Local registration is disabled because this instance uses Microsoft sign-in. Continue from the sign-in page."
              : "Registration options are temporarily unavailable. Please return to sign-in or try again later."}
          </p>
        )}

        <p className="mt-6 text-center text-sm text-stone-500 dark:text-stone-400">
          Already have an account?{" "}
          <Link href="/login" className="text-primary hover:underline">
            Sign in
          </Link>
        </p>
      </div>
    </main>
  );
}
