// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Button } from "@/app/components/ui/button";
import { Input } from "@/app/components/ui/input";
import { loginSuperuser } from "@/app/lib/api";

export default function SuperuserLoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await loginSuperuser(email, password);
      router.replace("/integrations");
    } catch {
      setError("Sign-in failed. Check your credentials and try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-stone-50 px-4 dark:bg-stone-950">
      <div className="w-full max-w-md rounded-lg border border-stone-200 bg-white p-8 dark:border-stone-800 dark:bg-stone-900">
        <div className="mb-6">
          <p className="mb-4 text-xl font-bold text-stone-900 dark:text-stone-100">Zabt</p>
          <h1 className="mb-1 text-2xl font-bold text-stone-900 dark:text-stone-100">
            System administrator
          </h1>
          <p className="text-sm text-stone-500 dark:text-stone-400">
            Sign in with the system administrator account provisioned for this instance.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label htmlFor="superuser-email" className="mb-1 block text-sm font-medium text-stone-700 dark:text-stone-300">
              Email address
            </label>
            <Input
              id="superuser-email"
              name="email"
              type="email"
              autoComplete="username"
              spellCheck={false}
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </div>
          <div>
            <label htmlFor="superuser-password" className="mb-1 block text-sm font-medium text-stone-700 dark:text-stone-300">
              Password
            </label>
            <Input
              id="superuser-password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </div>

          {error && (
            <p
              className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-300"
              role="alert"
              aria-live="polite"
            >
              {error}
            </p>
          )}

          <Button type="submit" loading={loading} className="w-full">
            Sign in as administrator
          </Button>
        </form>

        <p className="mt-6 text-center text-sm text-stone-500 dark:text-stone-400">
          <Link href="/login" className="text-primary hover:underline">
            Return to regular sign-in
          </Link>
        </p>
      </div>
    </main>
  );
}
