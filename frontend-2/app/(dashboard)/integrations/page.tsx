// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
"use client";

import { useState, useEffect, useCallback } from "react";
import { useSearchParams } from "next/navigation";
import {
  IntegrationRead,
  CalendarEventRead,
  MicrosoftOidcStatus,
  getIntegrations,
  getCalendarEvents,
  getMicrosoftOidcLinkUrl,
  getMicrosoftOidcStatus,
} from "@/app/lib/api";
import { IntegrationCard } from "@/app/components/integration-card";
import { CalendarEventList } from "@/app/components/calendar-event-list";
import { Badge } from "@/app/components/ui/badge";
import { Button } from "@/app/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/app/components/ui/dialog";
import { Check, Copy, ExternalLink, Info, RefreshCw } from "lucide-react";

const SUPPORTED_PROVIDERS = ["microsoft"];
const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api/v1"
).replace(/\/+$/, "");
const LOCAL_OIDC_REDIRECT_URI = `${API_BASE_URL}/auth/microsoft/callback`;
const MICROSOFT_ENTRA_APP_REGISTRATIONS_URL =
  "https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade";
const MICROSOFT_LINK_RESULTS: Record<string, string> = {
  linked: "Microsoft account linked successfully.",
  cancelled: "Microsoft account linking was cancelled.",
  link_failed: "Microsoft account could not be linked. Please try again.",
  already_linked: "This Microsoft account is already linked to another Zabt account.",
};
const MICROSOFT_GRAPH_ERRORS: Record<string, string> = {
  cancelled: "Microsoft Graph connection was cancelled.",
  configuration: "Microsoft Graph connection is not configured for this deployment.",
  state: "Microsoft Graph connection could not be verified. Please try again.",
  account: "This Zabt account cannot connect Microsoft Graph.",
  oauth_failed: "Microsoft Graph connection could not be completed. Please try again.",
};

type CallbackUrlKey = "oidc" | "graph";

type ConfigurationFeedback = {
  kind: "success" | "error";
  message: string;
};

interface CallbackUrlRowProps {
  label: string;
  value: string;
  helperText: string;
  copied: boolean;
  onCopy: () => void;
}

function CallbackUrlRow({
  label,
  value,
  helperText,
  copied,
  onCopy,
}: CallbackUrlRowProps) {
  const canCopy = Boolean(value);

  return (
    <div className="flex min-w-0 items-start justify-between gap-3 rounded-lg border border-border bg-muted/30 p-3">
      <div className="min-w-0 flex-1">
        <p className="font-medium text-foreground">{label}</p>
        <code className="mt-1 block break-all text-xs text-muted-foreground" translate="no">
          {value || "Not configured or unavailable"}
        </code>
        <p className="mt-1 text-xs text-muted-foreground">{helperText}</p>
      </div>
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="shrink-0"
        onClick={onCopy}
        disabled={!canCopy}
        aria-label={canCopy ? `Copy ${label}` : `${label} is unavailable to copy`}
      >
        {copied ? (
          <Check className="size-3.5" aria-hidden="true" />
        ) : (
          <Copy className="size-3.5" aria-hidden="true" />
        )}
        {copied ? "Copied" : "Copy"}
      </Button>
    </div>
  );
}

export default function IntegrationsPage() {
  const searchParams = useSearchParams();
  const [integrations, setIntegrations] = useState<IntegrationRead[]>([]);
  const [events, setEvents] = useState<CalendarEventRead[]>([]);
  const [microsoftOidcStatus, setMicrosoftOidcStatus] = useState<MicrosoftOidcStatus | null>(null);
  const [microsoftOidcStatusError, setMicrosoftOidcStatusError] = useState(false);
  const [microsoftOidcStatusLoading, setMicrosoftOidcStatusLoading] = useState(true);
  const [configurationFeedback, setConfigurationFeedback] = useState<ConfigurationFeedback | null>(null);
  const [configurationDialogOpen, setConfigurationDialogOpen] = useState(false);
  const [copiedCallback, setCopiedCallback] = useState<CallbackUrlKey | null>(null);
  const [copyFeedback, setCopyFeedback] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [intData, evData] = await Promise.all([
        getIntegrations(),
        getCalendarEvents(),
      ]);
      setIntegrations(intData);
      setEvents(evData);
    } finally {
      setLoading(false);
    }
  }, []);

  const refreshMicrosoftOidcStatus = useCallback(async (): Promise<boolean> => {
    setMicrosoftOidcStatusLoading(true);
    setMicrosoftOidcStatusError(false);

    try {
      const status = await getMicrosoftOidcStatus();
      setMicrosoftOidcStatus(status);
      return true;
    } catch {
      setMicrosoftOidcStatusError(true);
      return false;
    } finally {
      setMicrosoftOidcStatusLoading(false);
    }
  }, []);

  useEffect(() => {
    void Promise.resolve().then(() => load());
  }, [load]);

  useEffect(() => {
    void Promise.resolve().then(() => refreshMicrosoftOidcStatus());
  }, [refreshMicrosoftOidcStatus]);

  useEffect(() => {
    if (searchParams.get("connected")) {
      void Promise.resolve().then(() => load());
    }
  }, [searchParams, load]);

  const getIntegration = (provider: string): IntegrationRead | null => {
    return integrations.find((i) => i.provider === provider) || null;
  };

  const hasAnyConnection = integrations.some((i) => i.status === "active");
  const microsoftResult = searchParams.get("microsoft");
  const microsoftResultMessage = microsoftResult
    ? MICROSOFT_LINK_RESULTS[microsoftResult] ?? MICROSOFT_LINK_RESULTS.link_failed
    : null;
  const microsoftGraphError = searchParams.get("microsoft_error");
  const microsoftGraphErrorMessage = microsoftGraphError
    ? MICROSOFT_GRAPH_ERRORS[microsoftGraphError] ?? MICROSOFT_GRAPH_ERRORS.oauth_failed
    : null;
  const graphConnectDisabled =
    !microsoftOidcStatus?.graph_configured || !microsoftOidcStatus.token_storage_configured;
  const reportedOidcRedirectUri = microsoftOidcStatus?.oidc_redirect_uri?.trim() ?? "";
  const oidcRedirectUri = reportedOidcRedirectUri || LOCAL_OIDC_REDIRECT_URI;
  const graphRedirectUri = microsoftOidcStatus?.graph_redirect_uri?.trim() ?? "";
  const oidcScopes = Array.isArray(microsoftOidcStatus?.oidc_scopes)
    ? microsoftOidcStatus.oidc_scopes
    : [];
  const microsoftNeedsConfiguration =
    microsoftOidcStatusError ||
    !microsoftOidcStatus?.configured ||
    !microsoftOidcStatus?.graph_configured ||
    !microsoftOidcStatus?.token_storage_configured;

  const handleRecheckConfiguration = async () => {
    setConfigurationFeedback(null);
    const refreshed = await refreshMicrosoftOidcStatus();
    setConfigurationFeedback(
      refreshed
        ? { kind: "success", message: "Microsoft configuration status refreshed." }
        : {
            kind: "error",
            message: "Could not refresh Microsoft configuration. Check the API connection and try again.",
          },
    );
  };

  const handleCopyCallbackUrl = async (
    key: CallbackUrlKey,
    label: string,
    value: string,
  ) => {
    if (!value) return;

    setCopiedCallback(null);
    setCopyFeedback(null);
    try {
      if (!navigator.clipboard) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(value);
      setCopiedCallback(key);
      setCopyFeedback(`${label} copied to the clipboard.`);
    } catch {
      setCopyFeedback(`Unable to copy ${label}. Select the URL and copy it manually.`);
    }
  };

  const handleConfigurationDialogChange = (open: boolean) => {
    setConfigurationDialogOpen(open);
    if (!open) {
      setCopiedCallback(null);
      setCopyFeedback(null);
    }
  };

  return (
    <div className="px-8 py-8 max-w-3xl">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-foreground">Integrations</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Connect your accounts to sync calendars and automate meeting transcription.
        </p>
      </div>

      <section className="mb-10 rounded-lg border border-border bg-muted/30 p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-foreground">Microsoft Entra sign-in</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Use your Microsoft work account to sign in to Zabt. This is separate from the
              Microsoft Graph connection used for Calendar and email features below.
            </p>
          </div>
          {microsoftOidcStatus && (
            <Badge
              variant="outline"
              className={
                microsoftOidcStatus.configured
                  ? "border-primary/40 bg-primary/10 text-primary"
                  : "border-border bg-background text-muted-foreground"
              }
            >
              {microsoftOidcStatus.configured ? "Configured" : "Not configured"}
            </Badge>
          )}
        </div>
        {microsoftOidcStatusError && (
          <p
            className="mt-4 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive"
            role="status"
            aria-live="polite"
          >
            {microsoftOidcStatus
              ? "Unable to refresh Microsoft sign-in configuration. Showing the last known values."
              : "Unable to load Microsoft sign-in configuration. Recheck the deployment status or try again."}
          </p>
        )}
        {microsoftOidcStatus ? (
          <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
            <div>
              <dt className="font-medium text-foreground">Tenant</dt>
              <dd className="mt-1 break-all text-muted-foreground">
                {microsoftOidcStatus.tenant?.trim() || "Not reported"}
              </dd>
            </div>
            <div>
              <dt className="font-medium text-foreground">OIDC scopes</dt>
              <dd className="mt-1 break-words text-muted-foreground">
                {oidcScopes.length > 0 ? oidcScopes.join(", ") : "Not reported"}
              </dd>
            </div>
            <div>
              <dt className="font-medium text-foreground">OIDC redirect URI</dt>
              <dd className="mt-1 break-all font-mono text-xs text-muted-foreground">
                {reportedOidcRedirectUri || "Not configured"}
              </dd>
            </div>
            <div>
              <dt className="font-medium text-foreground">Graph redirect URI</dt>
              <dd className="mt-1 break-all font-mono text-xs text-muted-foreground">
                {graphRedirectUri || "Not configured"}
              </dd>
            </div>
          </dl>
        ) : microsoftOidcStatusLoading ? (
          <p className="mt-4 text-sm text-muted-foreground">Checking Microsoft sign-in configuration…</p>
        ) : (
          <p className="mt-4 text-sm text-muted-foreground">
            Configuration status is unavailable. Use “Recheck configuration” after confirming the API is reachable.
          </p>
        )}
        {microsoftResultMessage && (
          <p
            className="mt-4 rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground"
            role="status"
            aria-live="polite"
          >
            {microsoftResultMessage}
          </p>
        )}
        <Dialog open={configurationDialogOpen} onOpenChange={handleConfigurationDialogChange}>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <DialogTrigger
              render={
                <Button
                  type="button"
                  variant={microsoftNeedsConfiguration ? "default" : "outline"}
                  size="sm"
                />
              }
            >
              <Info className="size-4" aria-hidden="true" />
              How to configure Microsoft
            </DialogTrigger>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={handleRecheckConfiguration}
              disabled={microsoftOidcStatusLoading}
              aria-busy={microsoftOidcStatusLoading}
            >
              <RefreshCw
                className={microsoftOidcStatusLoading ? "size-4 animate-spin" : "size-4"}
                aria-hidden="true"
              />
              {microsoftOidcStatusLoading
                ? microsoftOidcStatus
                  ? "Rechecking…"
                  : "Checking…"
                : "Recheck configuration"}
            </Button>
          </div>
          {configurationFeedback && (
            <p
              className={
                configurationFeedback.kind === "success"
                  ? "mt-2 text-sm text-foreground"
                  : "mt-2 text-sm text-destructive"
              }
              role="status"
              aria-live="polite"
            >
              {configurationFeedback.message}
            </p>
          )}
          <DialogContent className="max-h-[calc(100vh-2rem)] overflow-y-auto overscroll-contain sm:max-w-2xl">
            <DialogHeader>
              <DialogTitle>How to configure Microsoft</DialogTitle>
              <DialogDescription>
                Microsoft Entra OIDC sign-in and delegated Microsoft Graph are configured by the deployment operator.
              </DialogDescription>
            </DialogHeader>

            <div className="space-y-6 py-2 text-sm">
              <section aria-labelledby="microsoft-secrets-heading">
                <h3 id="microsoft-secrets-heading" className="text-base font-semibold text-foreground">
                  1. Configure deployment secrets
                </h3>
                <p className="mt-2 text-muted-foreground">
                  Set these names in the server <code className="font-mono text-foreground">.env</code> file or a
                  secret manager, not in the browser. Never expose client secrets or token encryption keys in the UI.
                </p>
                <ul className="mt-3 grid gap-2 sm:grid-cols-2">
                  {[
                    "MICROSOFT_CLIENT_ID",
                    "MICROSOFT_CLIENT_SECRET",
                    "MICROSOFT_TENANT_ID",
                    "MICROSOFT_OIDC_REDIRECT_URI",
                    "MICROSOFT_REDIRECT_URI",
                    "TOKEN_ENCRYPTION_KEY",
                  ].map((name) => (
                    <li key={name}>
                      <code className="break-all rounded bg-muted px-2 py-1 font-mono text-xs text-foreground" translate="no">
                        {name}
                      </code>
                    </li>
                  ))}
                </ul>
                <p className="mt-3 text-muted-foreground">
                  <code className="font-mono text-foreground" translate="no">MICROSOFT_TENANT_ID</code> accepts a tenant
                  GUID or <code className="font-mono text-foreground" translate="no">common</code>,
                  <code className="font-mono text-foreground" translate="no">organizations</code>, or
                  <code className="font-mono text-foreground" translate="no">consumers</code>. Use a valid
                  <code className="font-mono text-foreground" translate="no">TOKEN_ENCRYPTION_KEY</code> for Graph token storage.
                </p>
              </section>

              <section aria-labelledby="microsoft-callbacks-heading">
                <h3 id="microsoft-callbacks-heading" className="text-base font-semibold text-foreground">
                  2. Register both callback URLs
                </h3>
                <p className="mt-2 text-muted-foreground">
                  In the Microsoft Entra app registration, add both values below as Web redirect URIs. Register the
                  exact URLs shown by this deployment.
                </p>
                <div className="mt-3 space-y-3">
                  <CallbackUrlRow
                    label="OIDC callback URL"
                    value={oidcRedirectUri}
                    helperText={
                      reportedOidcRedirectUri
                        ? "Reported by the API."
                        : "The API did not report an OIDC value. This is the expected local development value derived from the configured API base; do not use it as a production value."
                    }
                    copied={copiedCallback === "oidc"}
                    onCopy={() =>
                      void handleCopyCallbackUrl("oidc", "OIDC callback URL", oidcRedirectUri)
                    }
                  />
                  <CallbackUrlRow
                    label="Graph callback URL"
                    value={graphRedirectUri}
                    helperText={
                      graphRedirectUri
                        ? "Reported by the API."
                        : "No Graph callback URL was reported. Set MICROSOFT_REDIRECT_URI on the server and recheck."
                    }
                    copied={copiedCallback === "graph"}
                    onCopy={() =>
                      void handleCopyCallbackUrl("graph", "Graph callback URL", graphRedirectUri)
                    }
                  />
                </div>
                {copyFeedback && (
                  <p className="mt-3 text-sm text-muted-foreground" role="status" aria-live="polite">
                    {copyFeedback}
                  </p>
                )}
                <Button
                  className="mt-4"
                  variant="outline"
                  size="sm"
                  render={
                    <a
                      href={MICROSOFT_ENTRA_APP_REGISTRATIONS_URL}
                      target="_blank"
                      rel="noopener noreferrer"
                      aria-label="Open Microsoft Entra app registrations in a new tab"
                      translate="no"
                    />
                  }
                >
                  <ExternalLink className="size-4" aria-hidden="true" />
                  Open Microsoft Entra app registrations
                </Button>
              </section>

              <section aria-labelledby="microsoft-capabilities-heading">
                <h3 id="microsoft-capabilities-heading" className="text-base font-semibold text-foreground">
                  3. Understand the separate capabilities
                </h3>
                <p className="mt-2 text-muted-foreground">
                  OIDC login and the delegated Microsoft Graph connection are separate. OIDC uses the
                  <code className="font-mono text-foreground" translate="no">openid profile email</code> scopes. Graph delegated
                  scopes are controlled server-side; they are not entered or selected in the browser.
                </p>
              </section>
            </div>

            <DialogFooter>
              <DialogClose render={<Button type="button" variant="outline" />}>
                Close
              </DialogClose>
            </DialogFooter>
          </DialogContent>
        </Dialog>
        {microsoftOidcStatus?.configured && (
          <a
            href={getMicrosoftOidcLinkUrl("/integrations")}
            className="mt-3 inline-flex h-9 items-center justify-center rounded-lg border border-border bg-background px-3 text-sm font-medium text-foreground transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
          >
            Link Microsoft account
          </a>
        )}
      </section>

      <section className="mb-10">
        <h2 className="mb-4 text-lg font-semibold text-foreground">Microsoft Graph connections</h2>
        <p className="mb-4 text-sm text-muted-foreground">
          Connect Microsoft Graph separately to sync Calendar events and send email from Zabt.
        </p>
        {microsoftGraphErrorMessage && (
          <p
            className="mb-4 rounded-lg border border-border bg-muted/30 px-3 py-2 text-sm text-foreground"
            role="status"
            aria-live="polite"
          >
            {microsoftGraphErrorMessage}
          </p>
        )}
        {microsoftOidcStatus && (
          <div className="mb-4 rounded-lg border border-border bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
            <p>
              Graph delegated OAuth: {microsoftOidcStatus.graph_configured ? "Configured" : "Not configured"}
            </p>
            <p>
              Token storage: {microsoftOidcStatus.token_storage_configured ? "Configured" : "Not configured"}
            </p>
            {!microsoftOidcStatus.token_storage_configured && (
              <p className="mt-1 font-medium text-foreground">
                The deployment must set TOKEN_ENCRYPTION_KEY before connecting Graph. Open “How to configure Microsoft” for the required server settings.
              </p>
            )}
          </div>
        )}
        <div className="space-y-3">
          {SUPPORTED_PROVIDERS.map((provider) => (
            <IntegrationCard
              key={provider}
              provider={provider}
              integration={getIntegration(provider)}
              onStatusChange={load}
              connectDisabled={graphConnectDisabled}
            />
          ))}
        </div>
      </section>

      <section>
        <h2 className="mb-4 text-lg font-semibold text-foreground">Upcoming Meetings</h2>
        {loading ? (
          <div className="py-8 text-center text-muted-foreground">Loading…</div>
        ) : hasAnyConnection ? (
          <CalendarEventList events={events} onEventUpdated={load} />
        ) : (
          <div className="rounded-lg border border-dashed border-border py-12 text-center text-muted-foreground">
            <p className="font-medium text-foreground">No accounts connected</p>
            <p className="mt-1 text-sm">Connect Microsoft above to see your upcoming meetings.</p>
          </div>
        )}
      </section>
    </div>
  );
}
