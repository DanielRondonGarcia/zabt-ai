// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, ShieldCheck } from "lucide-react";

import { Button } from "@/app/components/ui/button";
import {
  approveMcpOAuthAuthorization,
  getApiErrorStatus,
  getCurrentUserProfile,
  getMcpOAuthConsentDetails,
  type McpOAuthConsentDetails,
} from "@/app/lib/api";
import { getSafeOAuthRedirectUrl } from "@/app/lib/safe-navigation";

const REQUEST_ID_RE = /^[A-Za-z0-9_-]{32,128}$/;

function consentPath(requestId: string): string {
  return `/mcp/consent?request_id=${encodeURIComponent(requestId)}`;
}

export default function McpConsentPage() {
  const [requestId, setRequestId] = useState<string | null>(null);
  const [details, setDetails] = useState<McpOAuthConsentDetails | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");

  useEffect(() => {
    let cancelled = false;
    const candidate = new URLSearchParams(window.location.search).get("request_id");

    if (!candidate || !REQUEST_ID_RE.test(candidate)) {
      setError("This authorization request is missing or invalid.");
      setLoading(false);
      return () => {
        cancelled = true;
      };
    }

    setRequestId(candidate);
    void (async () => {
      try {
        const nextDetails = await getMcpOAuthConsentDetails(candidate);
        if (cancelled) return;
        setDetails(nextDetails);

        try {
          await getCurrentUserProfile();
        } catch (authError) {
          if (cancelled) return;
          if (getApiErrorStatus(authError) === 401) {
            window.location.replace(`/login?next=${encodeURIComponent(consentPath(candidate))}`);
            return;
          }
          throw authError;
        }

        if (!cancelled) setAnnouncement("Authorization request ready for your decision.");
      } catch {
        if (!cancelled) setError("This authorization request is invalid, expired, or unavailable.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  async function handleDecision(approved: boolean) {
    if (!requestId || !details || submitting) return;
    setSubmitting(true);
    setError(null);
    setAnnouncement(approved ? "Approving access..." : "Denying access...");
    try {
      const result = await approveMcpOAuthAuthorization(requestId, approved);
      if (!result.redirect_url) {
        throw new Error("Missing OAuth redirect");
      }
      const safeRedirectUrl =
        result.redirect_uri === details.redirect_uri
          ? getSafeOAuthRedirectUrl(result.redirect_url, details.redirect_uri)
          : null;
      if (!safeRedirectUrl) {
        setError("The authorization response contained an unsafe redirect. No navigation was performed.");
        setAnnouncement("Authorization stopped because the redirect was unsafe.");
        setSubmitting(false);
        return;
      }
      window.location.assign(safeRedirectUrl);
    } catch (approvalError) {
      if (getApiErrorStatus(approvalError) === 401) {
        window.location.replace(`/login?next=${encodeURIComponent(consentPath(requestId))}`);
        return;
      }
      setError("Your choice could not be completed. The request may have expired; start again from the MCP client.");
      setAnnouncement("Authorization could not be completed.");
      setSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-stone-50 px-4 py-8 dark:bg-stone-950">
      <section
        className="w-full max-w-xl space-y-6 rounded-lg border border-stone-200 bg-white p-6 dark:border-stone-800 dark:bg-stone-900 sm:p-8"
        aria-labelledby="mcp-consent-heading"
      >
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-primary">
            <ShieldCheck className="size-5" aria-hidden="true" />
            <p className="text-sm font-medium">Zabt MCP authorization</p>
          </div>
          <h1 id="mcp-consent-heading" className="text-2xl font-bold text-stone-900 dark:text-stone-100">
            Connect an MCP client
          </h1>
          <p className="text-sm text-stone-600 dark:text-stone-300">
            Review exactly what this client will be allowed to access before continuing.
          </p>
        </div>

        {loading && (
          <p className="text-sm text-stone-600 dark:text-stone-300" role="status" aria-live="polite">
            Loading authorization details...
          </p>
        )}

        {error && (
          <div className="space-y-3 rounded-lg border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive dark:border-destructive/50 dark:bg-destructive/10" role="alert" aria-live="assertive">
            <div className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              <p>{error}</p>
            </div>
            <Button type="button" variant="outline" onClick={() => window.location.reload()}>
              Try again
            </Button>
          </div>
        )}

        {!loading && !error && details && (
          <>
            <dl className="min-w-0 divide-y divide-stone-200 overflow-hidden rounded-lg border border-stone-200 dark:divide-stone-800 dark:border-stone-800">
              <div className="grid gap-1 px-4 py-3 sm:grid-cols-[9rem_minmax(0,1fr)] sm:gap-4">
                <dt className="text-sm font-medium text-stone-600 dark:text-stone-400">Client</dt>
                <dd className="break-words text-sm font-semibold text-stone-900 dark:text-stone-100">{details.client_name}</dd>
              </div>
              <div className="grid gap-1 px-4 py-3 sm:grid-cols-[9rem_minmax(0,1fr)] sm:gap-4">
                <dt className="text-sm font-medium text-stone-600 dark:text-stone-400">Redirect URI</dt>
                <dd className="break-all font-mono text-xs text-stone-900 dark:text-stone-100">{details.redirect_uri}</dd>
              </div>
              <div className="grid gap-1 px-4 py-3 sm:grid-cols-[9rem_minmax(0,1fr)] sm:gap-4">
                <dt className="text-sm font-medium text-stone-600 dark:text-stone-400">Resource</dt>
                <dd className="break-all font-mono text-xs text-stone-900 dark:text-stone-100">{details.resource}</dd>
              </div>
              <div className="grid gap-1 px-4 py-3 sm:grid-cols-[9rem_minmax(0,1fr)] sm:gap-4">
                <dt className="text-sm font-medium text-stone-600 dark:text-stone-400">Scope</dt>
                <dd className="break-words font-mono text-xs text-stone-900 dark:text-stone-100">{details.scope}</dd>
              </div>
            </dl>

            <div className="space-y-3">
              <p className="text-sm text-stone-600 dark:text-stone-300">
                Approving creates a linked OAuth app for this Zabt account. You can revoke it later from Settings.
              </p>
              <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => void handleDecision(false)}
                  disabled={submitting}
                >
                  Deny
                </Button>
                <Button
                  type="button"
                  onClick={() => void handleDecision(true)}
                  loading={submitting}
                  disabled={submitting}
                >
                  Approve access
                </Button>
              </div>
            </div>
          </>
        )}

        <p className="sr-only" role="status" aria-live="polite">{announcement}</p>
      </section>
    </main>
  );
}
