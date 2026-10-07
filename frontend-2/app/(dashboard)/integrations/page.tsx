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

const SUPPORTED_PROVIDERS = ["microsoft"];
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

export default function IntegrationsPage() {
  const searchParams = useSearchParams();
  const [integrations, setIntegrations] = useState<IntegrationRead[]>([]);
  const [events, setEvents] = useState<CalendarEventRead[]>([]);
  const [microsoftOidcStatus, setMicrosoftOidcStatus] = useState<MicrosoftOidcStatus | null>(null);
  const [microsoftOidcStatusError, setMicrosoftOidcStatusError] = useState(false);
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

  useEffect(() => {
    void Promise.resolve().then(() => load());
  }, [load]);

  useEffect(() => {
    getMicrosoftOidcStatus()
      .then((status) => {
        setMicrosoftOidcStatus(status);
        setMicrosoftOidcStatusError(false);
      })
      .catch(() => setMicrosoftOidcStatusError(true));
  }, []);

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

  return (
    <div className="px-8 py-8 max-w-3xl">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-stone-900">Integrations</h1>
        <p className="text-sm text-stone-500 mt-1">
          Connect your accounts to sync calendars and automate meeting transcription.
        </p>
      </div>

      <section className="mb-10 border border-stone-200 rounded-lg bg-stone-50 p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-lg font-semibold text-stone-900">Microsoft Entra sign-in</h2>
            <p className="mt-1 text-sm text-stone-600">
              Use your Microsoft work account to sign in to Zabt. This is separate from the
              Microsoft Graph connection used for Calendar and email features below.
            </p>
          </div>
          {microsoftOidcStatus && (
            <Badge
              variant="outline"
              className={
                microsoftOidcStatus.configured
                  ? "border-green-300 bg-green-50 text-green-700"
                  : "border-stone-300 bg-white text-stone-600"
              }
            >
              {microsoftOidcStatus.configured ? "Configured" : "Not configured"}
            </Badge>
          )}
        </div>
        {microsoftOidcStatusError ? (
          <p className="mt-4 text-sm text-stone-500">Unable to load Microsoft sign-in configuration.</p>
        ) : microsoftOidcStatus ? (
          <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
            <div>
              <dt className="font-medium text-stone-700">Tenant</dt>
              <dd className="mt-1 break-all text-stone-500">{microsoftOidcStatus.tenant || "Not configured"}</dd>
            </div>
            <div>
              <dt className="font-medium text-stone-700">OIDC scopes</dt>
              <dd className="mt-1 break-words text-stone-500">
                {microsoftOidcStatus.oidc_scopes.join(", ")}
              </dd>
            </div>
            <div>
              <dt className="font-medium text-stone-700">OIDC redirect URI</dt>
              <dd className="mt-1 break-all font-mono text-xs text-stone-500">
                {microsoftOidcStatus.oidc_redirect_uri || "Not configured"}
              </dd>
            </div>
            <div>
              <dt className="font-medium text-stone-700">Graph redirect URI</dt>
              <dd className="mt-1 break-all font-mono text-xs text-stone-500">
                {microsoftOidcStatus.graph_redirect_uri || "Not configured"}
              </dd>
            </div>
          </dl>
        ) : (
          <p className="mt-4 text-sm text-stone-500">Loading Microsoft sign-in configuration...</p>
        )}
        {microsoftResultMessage && (
          <p className="mt-4 rounded-lg border border-stone-200 bg-white px-3 py-2 text-sm text-stone-700" role="status">
            {microsoftResultMessage}
          </p>
        )}
        {microsoftOidcStatus?.configured && (
          <a
            href={getMicrosoftOidcLinkUrl("/integrations")}
            className="mt-4 inline-flex h-9 items-center justify-center rounded-lg border border-stone-300 bg-white px-3 text-sm font-medium text-stone-700 transition-colors hover:bg-stone-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
          >
            Link Microsoft account
          </a>
        )}
      </section>

      <section className="mb-10">
        <h2 className="text-lg font-semibold text-stone-900 mb-4">Microsoft Graph connections</h2>
        <p className="mb-4 text-sm text-stone-500">
          Connect Microsoft Graph separately to sync Calendar events and send email from Zabt.
        </p>
        {microsoftGraphErrorMessage && (
          <p className="mb-4 rounded-lg border border-stone-200 bg-stone-50 px-3 py-2 text-sm text-stone-700" role="status">
            {microsoftGraphErrorMessage}
          </p>
        )}
        {microsoftOidcStatus && (
          <div className="mb-4 rounded-lg border border-stone-200 bg-stone-50 px-3 py-2 text-sm text-stone-600">
            <p>
              Graph delegated OAuth: {microsoftOidcStatus.graph_configured ? "Configured" : "Not configured"}
            </p>
            <p>
              Token storage: {microsoftOidcStatus.token_storage_configured ? "Configured" : "Not configured"}
            </p>
            {!microsoftOidcStatus.token_storage_configured && (
              <p className="mt-1 font-medium text-stone-700">
                The deployment must set TOKEN_ENCRYPTION_KEY before connecting Graph.
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
        <h2 className="text-lg font-semibold text-stone-900 mb-4">Upcoming Meetings</h2>
        {loading ? (
          <div className="text-center py-8 text-stone-400">Loading...</div>
        ) : hasAnyConnection ? (
          <CalendarEventList events={events} onEventUpdated={load} />
        ) : (
          <div className="text-center py-12 border border-dashed border-stone-200 rounded-lg text-stone-500">
            <p className="font-medium">No accounts connected</p>
            <p className="text-sm mt-1">Connect Microsoft above to see your upcoming meetings.</p>
          </div>
        )}
      </section>
    </div>
  );
}
