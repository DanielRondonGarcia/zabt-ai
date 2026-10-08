// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
"use client";

import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  Check,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import {
  getApiErrorMessage,
  getApiErrorStatus,
  getAuthenticationMode,
  getAuthenticationModeConfiguration,
  getCalendarEvents,
  getIntegrations,
  getMicrosoftOidcConfiguration,
  getMicrosoftOidcStatus,
  updateAuthenticationMode,
  updateMicrosoftOidcConfiguration,
  type AuthenticationMode,
  type AuthenticationModeConfiguration,
  type CalendarEventRead,
  type IntegrationRead,
  type MicrosoftOidcConfiguration,
  type MicrosoftOidcStatus,
} from "@/app/lib/api";
import {
  getDefaultMicrosoftOidcRedirectUri,
  initializeMicrosoftOidc,
  isMicrosoftOidcRedirectUriForCurrentSpa,
  linkMicrosoftAccount,
  MICROSOFT_OIDC_SPA_REDIRECT_FALLBACK,
} from "@/app/lib/microsoft-oidc";
import { IntegrationCard } from "@/app/components/integration-card";
import { CalendarEventList } from "@/app/components/calendar-event-list";
import { Badge } from "@/app/components/ui/badge";
import { Button } from "@/app/components/ui/button";
import { Input } from "@/app/components/ui/input";

const SUPPORTED_PROVIDERS = ["microsoft"];
const PASSWORDLESS_UNLINKED_REMEDIATION =
  "Passwordless active accounts require identity reconciliation or deactivation by the system superuser before switching to Microsoft OIDC.";
const MICROSOFT_GRAPH_ERRORS: Record<string, string> = {
  cancelled: "Microsoft Graph connection was cancelled.",
  configuration: "Microsoft Graph connection is not configured for this deployment.",
  state: "Microsoft Graph connection could not be verified. Please try again.",
  account: "This Zabt account cannot connect Microsoft Graph.",
  oauth_failed: "Microsoft Graph connection could not be completed. Please try again.",
};

type Feedback = {
  kind: "success" | "error";
  message: string;
};

export default function IntegrationsPage() {
  const searchParams = useSearchParams();
  const [integrations, setIntegrations] = useState<IntegrationRead[]>([]);
  const [events, setEvents] = useState<CalendarEventRead[]>([]);
  const [microsoftConfiguration, setMicrosoftConfiguration] =
    useState<MicrosoftOidcConfiguration | null>(null);
  const [microsoftStatus, setMicrosoftStatus] = useState<MicrosoftOidcStatus | null>(null);
  const [authenticationMode, setAuthenticationMode] = useState<AuthenticationMode | null>(null);
  const [modeConfiguration, setModeConfiguration] =
    useState<AuthenticationModeConfiguration | null>(null);
  const [selectedMode, setSelectedMode] = useState<AuthenticationMode>("local");
  const [savingMode, setSavingMode] = useState(false);
  const [modeFeedback, setModeFeedback] = useState<Feedback | null>(null);
  const [configurationLoading, setConfigurationLoading] = useState(true);
  const [configurationError, setConfigurationError] = useState(false);
  const [configurationFeedback, setConfigurationFeedback] = useState<Feedback | null>(null);
  const [clientId, setClientId] = useState("");
  const [tenant, setTenant] = useState("");
  const [redirectUri, setRedirectUri] = useState(MICROSOFT_OIDC_SPA_REDIRECT_FALLBACK);
  const [enabled, setEnabled] = useState(true);
  const [savingConfiguration, setSavingConfiguration] = useState(false);
  const [linkingMicrosoft, setLinkingMicrosoft] = useState(false);
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

  const refreshMicrosoftConfiguration = useCallback(async (): Promise<boolean> => {
    setConfigurationLoading(true);
    setConfigurationError(false);
    try {
      const status = await getMicrosoftOidcStatus();
      setMicrosoftStatus(status);
      const modeResult = await getAuthenticationMode().catch(() => null);
      setAuthenticationMode(modeResult?.mode ?? null);
      const modeConfig = await getAuthenticationModeConfiguration().catch(() => null);
      setModeConfiguration(modeConfig);
      if (modeConfig) setSelectedMode(modeConfig.mode);

      if (modeConfig) {
        const configuration = await getMicrosoftOidcConfiguration();
        setMicrosoftConfiguration(configuration);
        setClientId(configuration.client_id ?? "");
        setTenant(configuration.tenant ?? "");
        setRedirectUri(configuration.redirect_uri ?? getDefaultMicrosoftOidcRedirectUri());
        setEnabled(configuration.enabled);
        if (configuration.configured) {
          void initializeMicrosoftOidc(configuration).catch(() => undefined);
        }
      } else {
        setMicrosoftConfiguration(null);
      }
      return true;
    } catch {
      setConfigurationError(true);
      return false;
    } finally {
      setConfigurationLoading(false);
    }
  }, []);

  useEffect(() => {
    void Promise.resolve().then(() => load());
  }, [load]);

  useEffect(() => {
    void Promise.resolve().then(() => refreshMicrosoftConfiguration());
  }, [refreshMicrosoftConfiguration]);

  useEffect(() => {
    if (searchParams.get("connected")) {
      void Promise.resolve().then(() => load());
    }
  }, [searchParams, load]);

  const getIntegration = (provider: string): IntegrationRead | null => {
    return integrations.find((integration) => integration.provider === provider) || null;
  };

  const hasAnyConnection = integrations.some((integration) => integration.status === "active");
  const microsoftGraphError = searchParams.get("microsoft_error");
  const microsoftGraphErrorMessage = microsoftGraphError
    ? MICROSOFT_GRAPH_ERRORS[microsoftGraphError] ?? MICROSOFT_GRAPH_ERRORS.oauth_failed
    : null;
  const graphConnectDisabled =
    !microsoftStatus?.graph_configured ||
    !microsoftStatus?.token_storage_configured;

  const handleSaveConfiguration = async (event: React.FormEvent) => {
    event.preventDefault();
    setConfigurationFeedback(null);
    if (!isMicrosoftOidcRedirectUriForCurrentSpa(redirectUri.trim())) {
      setConfigurationFeedback({
        kind: "error",
        message: "The SPA redirect URI must be the current frontend origin followed by /login.",
      });
      return;
    }
    setSavingConfiguration(true);
    try {
      const saved = await updateMicrosoftOidcConfiguration({
        client_id: clientId.trim(),
        tenant: tenant.trim(),
        redirect_uri: redirectUri.trim(),
        enabled,
      });
      setMicrosoftConfiguration(saved);
      setConfigurationFeedback({
        kind: "success",
        message: "Microsoft Entra public-client configuration saved.",
      });
      setMicrosoftStatus(saved);
    } catch (error) {
      setConfigurationFeedback({
        kind: "error",
        message:
          getApiErrorStatus(error) === 403
            ? "Only the system administrator can update Microsoft Entra configuration."
            : "Microsoft Entra configuration could not be saved. Check the public values and try again.",
      });
    } finally {
      setSavingConfiguration(false);
    }
  };

  const handleRecheckConfiguration = async () => {
    setConfigurationFeedback(null);
    const refreshed = await refreshMicrosoftConfiguration();
    setConfigurationFeedback(
      refreshed
        ? { kind: "success", message: "Microsoft Entra configuration status refreshed." }
        : {
            kind: "error",
            message: "Could not refresh Microsoft Entra configuration. Try again later.",
          },
    );
  };

  const handleLinkMicrosoft = async () => {
    if (!microsoftStatus?.configured || authenticationMode !== "local") return;
    setConfigurationFeedback(null);
    setLinkingMicrosoft(true);
    try {
      await linkMicrosoftAccount(microsoftStatus);
      setConfigurationFeedback({
        kind: "success",
        message: "Microsoft account linked successfully.",
      });
    } catch (error) {
      setConfigurationFeedback({
        kind: "error",
        message:
          getApiErrorStatus(error) === 409
            ? "This Microsoft account is already linked to another Zabt account."
            : "Microsoft account could not be linked. Please try again.",
      });
    } finally {
      setLinkingMicrosoft(false);
    }
  };

  const handleSaveAuthenticationMode = async () => {
    setModeFeedback(null);
    setSavingMode(true);
    try {
      const saved = await updateAuthenticationMode(selectedMode);
      setModeConfiguration(saved);
      setAuthenticationMode(saved.mode);
      setSelectedMode(saved.mode);
      setModeFeedback({ kind: "success", message: "Authentication method saved." });
    } catch (error) {
      setModeFeedback({
        kind: "error",
        message:
          getApiErrorStatus(error) === 409
            ? getApiErrorMessage(error) ??
              (modeConfiguration?.unlinked_passwordless_user_count
                ? PASSWORDLESS_UNLINKED_REMEDIATION
                : "Some active local accounts are not linked yet. Each affected user must sign in locally and link Microsoft from Integrations before switching.")
            : "Authentication method could not be saved. Check Microsoft readiness and try again.",
      });
    } finally {
      setSavingMode(false);
    }
  };

  const oidcConfigured = microsoftStatus?.configured === true;
  const canManage =
    microsoftConfiguration?.can_manage === true || microsoftConfiguration?.is_superuser === true;

  return (
    <div className="max-w-3xl px-8 py-8">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-foreground">Integrations</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Configure instance sign-in and connect delegated services for your account.
        </p>
      </div>

      {canManage && modeConfiguration && (
        <section className="mb-10 rounded-lg border border-border bg-muted/30 p-5">
          <h2 className="text-lg font-semibold text-foreground">Authentication method</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Choose the regular-user sign-in method for web and mobile. The system administrator always uses the separate administrator login.
          </p>
          <p className="mt-2 text-sm text-muted-foreground">
            This setting governs new login and registration attempts. Existing sessions are not revoked and remain active while their access and refresh credentials are valid, until normal expiration or another revocation event.
          </p>

          <fieldset className="mt-4 space-y-3">
            <legend className="mb-2 text-sm font-medium text-foreground">Regular-user sign-in</legend>
            <label className="flex cursor-pointer items-start gap-3 rounded-lg border border-border bg-background p-3 text-sm text-foreground">
              <input
                type="radio"
                name="authentication-mode"
                value="local"
                checked={selectedMode === "local"}
                onChange={() => setSelectedMode("local")}
                className="mt-0.5 size-4 accent-primary"
              />
              <span>
                <span className="block font-medium">Local email and password</span>
                <span className="mt-1 block text-muted-foreground">Allow users to sign in or register with a local Zabt account.</span>
              </span>
            </label>
            <label className="flex cursor-pointer items-start gap-3 rounded-lg border border-border bg-background p-3 text-sm text-foreground">
              <input
                type="radio"
                name="authentication-mode"
                value="microsoft_oidc"
                checked={selectedMode === "microsoft_oidc"}
                onChange={() => setSelectedMode("microsoft_oidc")}
                disabled={!modeConfiguration.oidc_configured || modeConfiguration.unlinked_user_count > 0}
                className="mt-0.5 size-4 accent-primary"
              />
              <span>
                <span className="block font-medium">Microsoft OIDC</span>
                <span className="mt-1 block text-muted-foreground">Use Microsoft sign-in on both web and mobile.</span>
              </span>
            </label>
          </fieldset>

          <div className="mt-4 rounded-lg border border-border bg-background px-3 py-3 text-sm text-muted-foreground">
            <p>
              Microsoft OIDC readiness: {modeConfiguration.oidc_configured ? "Configured" : "Not configured"}
            </p>
            <p className="mt-1">
              Active non-superuser accounts still needing a Microsoft link: {modeConfiguration.unlinked_user_count}
            </p>
            <p className="mt-1">
              Passwordless active accounts still needing a Microsoft link: {modeConfiguration.unlinked_passwordless_user_count}
            </p>
            {!modeConfiguration.oidc_configured && (
              <p className="mt-2">Configure and enable Microsoft OIDC below before switching this instance to Microsoft sign-in.</p>
            )}
            {modeConfiguration.unlinked_user_count > 0 && (
              <p className="mt-2">
                {modeConfiguration.unlinked_passwordless_user_count > 0
                  ? PASSWORDLESS_UNLINKED_REMEDIATION
                  : "Each affected user must sign in with their local account and use “Link Microsoft account” below before the mode can be changed."}
              </p>
            )}
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Button
              type="button"
              onClick={handleSaveAuthenticationMode}
              loading={savingMode}
              disabled={
                savingMode ||
                selectedMode === modeConfiguration.mode ||
                (selectedMode === "microsoft_oidc" &&
                  (!modeConfiguration.oidc_configured || modeConfiguration.unlinked_user_count > 0))
              }
            >
              Save authentication method
            </Button>
            {modeFeedback && (
              <p
                className={modeFeedback.kind === "success" ? "text-sm text-foreground" : "text-sm text-destructive"}
                role="status"
                aria-live="polite"
              >
                {modeFeedback.message}
              </p>
            )}
          </div>
        </section>
      )}

      <section className="mb-10 rounded-lg border border-border bg-muted/30 p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-foreground">Microsoft Entra sign-in</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Global instance configuration for the Microsoft public SPA client. OIDC uses
              authorization code + PKCE in the browser and does not require a client secret.
            </p>
          </div>
          {(microsoftConfiguration || microsoftStatus) && (
            <Badge
              variant="outline"
              className={
                oidcConfigured
                  ? "border-primary/40 bg-primary/10 text-primary"
                  : "border-border bg-background text-muted-foreground"
              }
            >
              {oidcConfigured ? "Configured" : "Not configured"}
            </Badge>
          )}
        </div>

        {configurationError && (
          <p
            className="mt-4 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive"
            role="alert"
          >
            Unable to load the Microsoft Entra configuration. Recheck the instance status or try again.
          </p>
        )}

        {configurationLoading ? (
          <p className="mt-4 text-sm text-muted-foreground">Loading Microsoft Entra configuration…</p>
        ) : (
          <>
            <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
              <div>
                <dt className="font-medium text-foreground">Client ID</dt>
                <dd className="mt-1 break-all font-mono text-xs text-muted-foreground">
                  {microsoftConfiguration?.client_id || microsoftStatus?.client_id || "Not configured"}
                </dd>
              </div>
              <div>
                <dt className="font-medium text-foreground">Tenant</dt>
                <dd className="mt-1 break-all text-muted-foreground">
                  {microsoftConfiguration?.tenant || microsoftStatus?.tenant || "Not configured"}
                </dd>
              </div>
              <div className="sm:col-span-2">
                <dt className="font-medium text-foreground">Registered SPA redirect URI</dt>
                <dd className="mt-1 break-all font-mono text-xs text-muted-foreground">
                  {microsoftConfiguration?.redirect_uri || microsoftStatus?.redirect_uri || "Not configured"}
                </dd>
              </div>
              <div>
                <dt className="font-medium text-foreground">OIDC scopes</dt>
                <dd className="mt-1 break-words text-muted-foreground">
                  {microsoftConfiguration?.scopes.join(", ") || microsoftStatus?.scopes.join(", ") || "openid, profile, email"}
                </dd>
              </div>
              <div>
                <dt className="font-medium text-foreground">Last updated</dt>
                <dd className="mt-1 text-muted-foreground">
                  {microsoftConfiguration?.updated_at
                    ? new Date(microsoftConfiguration.updated_at).toLocaleString()
                    : "Not configured"}
                </dd>
              </div>
            </dl>

            {canManage ? (
              <form onSubmit={handleSaveConfiguration} className="mt-6 space-y-4 border-t border-border pt-5">
                <div>
                  <h3 className="text-base font-semibold text-foreground">Configure public SPA client</h3>
                  <p className="mt-1 text-sm text-muted-foreground">
                    Enter values registered as a Microsoft Entra Single-page application. These values are public;
                    never enter a client secret here.
                  </p>
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div>
                    <label htmlFor="microsoft-client-id" className="mb-1 block text-sm font-medium text-foreground">
                      Client ID
                    </label>
                    <Input
                      id="microsoft-client-id"
                      value={clientId}
                      onChange={(event) => setClientId(event.target.value)}
                      placeholder="00000000-0000-0000-0000-000000000000"
                      autoComplete="off"
                      required
                    />
                  </div>
                  <div>
                    <label htmlFor="microsoft-tenant" className="mb-1 block text-sm font-medium text-foreground">
                      Tenant
                    </label>
                    <Input
                      id="microsoft-tenant"
                      value={tenant}
                      onChange={(event) => setTenant(event.target.value)}
                      placeholder="common or tenant GUID"
                      autoComplete="off"
                      required
                    />
                  </div>
                </div>
                <div>
                  <label htmlFor="microsoft-redirect-uri" className="mb-1 block text-sm font-medium text-foreground">
                    SPA redirect URI
                  </label>
                  <Input
                    id="microsoft-redirect-uri"
                    type="url"
                    value={redirectUri}
                    onChange={(event) => setRedirectUri(event.target.value)}
                    placeholder={MICROSOFT_OIDC_SPA_REDIRECT_FALLBACK}
                    autoComplete="off"
                    required
                  />
                  <p className="mt-1 text-xs text-muted-foreground">
                    Use the current frontend origin followed by <code className="font-mono text-foreground">/login</code>.
                    Local development defaults to {MICROSOFT_OIDC_SPA_REDIRECT_FALLBACK}; production must use the actual
                    frontend HTTPS origin. This is separate from the backend Graph callback.
                  </p>
                </div>
                <label className="flex items-center gap-2 text-sm text-foreground">
                  <input
                    type="checkbox"
                    checked={enabled}
                    onChange={(event) => setEnabled(event.target.checked)}
                    className="size-4 rounded border-border accent-primary"
                  />
                  Enable Microsoft sign-in for this instance
                </label>
                <Button type="submit" loading={savingConfiguration}>
                  Save Microsoft Entra configuration
                </Button>
              </form>
            ) : (
              <p className="mt-5 rounded-lg border border-border bg-background px-3 py-2 text-sm text-muted-foreground">
                Only the system administrator can change the global Microsoft Entra settings. Ask them to configure
                Microsoft Entra if this instance is not ready.
              </p>
            )}

            <div className="mt-4 flex flex-wrap items-center gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={handleRecheckConfiguration}
                disabled={configurationLoading}
                aria-busy={configurationLoading}
              >
                <RefreshCw
                  className={configurationLoading ? "size-4 animate-spin" : "size-4"}
                  aria-hidden="true"
                />
                {configurationLoading ? "Rechecking…" : "Recheck configuration"}
              </Button>
              {oidcConfigured && authenticationMode === "local" && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={handleLinkMicrosoft}
                  loading={linkingMicrosoft}
                >
                  <ShieldCheck className="size-4" aria-hidden="true" />
                  Link Microsoft account
                </Button>
              )}
            </div>
            {configurationFeedback && (
              <p
                className={
                  configurationFeedback.kind === "success"
                    ? "mt-3 text-sm text-foreground"
                    : "mt-3 text-sm text-destructive"
                }
                role="status"
                aria-live="polite"
              >
                {configurationFeedback.kind === "success" && (
                  <Check className="mr-1 inline size-4" aria-hidden="true" />
                )}
                {configurationFeedback.message}
              </p>
            )}
          </>
        )}
      </section>

      <section className="mb-10">
        <h2 className="mb-4 text-lg font-semibold text-foreground">Microsoft Graph connections</h2>
        <p className="mb-4 text-sm text-muted-foreground">
          Connect delegated Microsoft Graph separately to sync Calendar events and send email from Zabt.
        </p>
        <div className="mb-4 rounded-lg border border-border bg-muted/30 px-3 py-3 text-sm text-muted-foreground">
          <p>
            Graph OAuth readiness: {microsoftStatus?.graph_configured ? "Configured" : "Not configured"}
          </p>
          <p>
            Token storage: {microsoftStatus?.token_storage_configured ? "Configured" : "Not configured"}
          </p>
          <p className="mt-2">
            Graph uses deployment-managed <code className="font-mono text-foreground">MICROSOFT_CLIENT_ID</code>,{" "}
            <code className="font-mono text-foreground">MICROSOFT_CLIENT_SECRET</code>,{" "}
            <code className="font-mono text-foreground">MICROSOFT_TENANT_ID</code>,{" "}
            <code className="font-mono text-foreground">MICROSOFT_REDIRECT_URI</code>, and{" "}
            <code className="font-mono text-foreground">TOKEN_ENCRYPTION_KEY</code>. OIDC above never needs a client
            secret, and these Graph settings do not control OIDC sign-in.
          </p>
        </div>
        {microsoftGraphErrorMessage && (
          <p
            className="mb-4 rounded-lg border border-border bg-muted/30 px-3 py-2 text-sm text-foreground"
            role="status"
            aria-live="polite"
          >
            {microsoftGraphErrorMessage}
          </p>
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
            <p className="mt-1 text-sm">Connect Microsoft Graph above to see your upcoming meetings.</p>
          </div>
        )}
      </section>
    </div>
  );
}
