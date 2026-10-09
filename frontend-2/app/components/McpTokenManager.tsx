// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
"use client";

import { useEffect, useState, type FormEvent } from "react";
import { AlertTriangle, Check, Copy, KeyRound, Trash2 } from "lucide-react";

import {
  createMcpToken,
  getMcpStatus,
  getMcpTokens,
  revokeMcpToken,
  type McpStatus,
  type McpTokenCreated,
  type McpTokenMetadata,
} from "@/app/lib/api";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/app/components/ui/alert-dialog";
import { Button } from "@/app/components/ui/button";
import { Input } from "@/app/components/ui/input";

const dateFormatter = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

function formatDate(value: string): string {
  return dateFormatter.format(new Date(value));
}

function buildMcpConnectionExample(endpoint: string): string {
  return JSON.stringify(
    {
      mcpServers: {
        zabt: {
          type: "streamable-http",
          url: endpoint,
          headers: {
            Authorization: "Bearer ${ZABT_MCP_TOKEN}",
          },
        },
      },
    },
    null,
    2,
  );
}

export function McpTokenManager() {
  const [status, setStatus] = useState<McpStatus | null>(null);
  const [tokens, setTokens] = useState<McpTokenMetadata[]>([]);
  const [label, setLabel] = useState("");
  const [expiresInDays, setExpiresInDays] = useState("30");
  const [newToken, setNewToken] = useState<McpTokenCreated | null>(null);
  const [pendingRevoke, setPendingRevoke] = useState<McpTokenMetadata | null>(null);
  const [copiedToken, setCopiedToken] = useState(false);
  const [copiedExample, setCopiedExample] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [revoking, setRevoking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");

  async function refresh() {
    setError(null);
    try {
      const [nextStatus, nextTokens] = await Promise.all([getMcpStatus(), getMcpTokens()]);
      setStatus(nextStatus);
      setTokens(nextTokens);
    } catch {
      setError("MCP settings could not be loaded. Refresh the page and try again.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!label.trim()) {
      setError("Enter a label for this token.");
      return;
    }

    setSaving(true);
    setError(null);
    setCopiedToken(false);
    try {
      const created = await createMcpToken(label, Number(expiresInDays));
      setNewToken(created);
      setLabel("");
      setAnnouncement(`Token ${created.label} created. Copy it now; it will not be shown again.`);
      await refresh();
    } catch {
      setError("The token could not be created. Check the label and expiry, then try again.");
    } finally {
      setSaving(false);
    }
  }

  async function copyToken() {
    if (!newToken) return;
    try {
      await navigator.clipboard.writeText(newToken.token);
      setCopiedToken(true);
      setAnnouncement("Token copied. Keep it private; it will not be shown after leaving this page.");
    } catch {
      setError("Copying failed. Select the token manually and store it securely.");
    }
  }

  async function copyConnectionExample() {
    if (!status) return;
    try {
      await navigator.clipboard.writeText(buildMcpConnectionExample(status.endpoint));
      setCopiedExample(true);
      setAnnouncement("MCP connection example copied.");
    } catch {
      setError("Copying the connection example failed. Select the JSON manually and try again.");
    }
  }

  async function confirmRevoke() {
    if (!pendingRevoke) return;
    setRevoking(true);
    setError(null);
    try {
      await revokeMcpToken(pendingRevoke.id);
      setAnnouncement(`Token ${pendingRevoke.label} revoked.`);
      setPendingRevoke(null);
      await refresh();
    } catch {
      setError("The token could not be revoked. Refresh the page and try again.");
    } finally {
      setRevoking(false);
    }
  }

  return (
    <section className="min-w-0 space-y-4" aria-labelledby="mcp-tokens-heading">
      <div className="space-y-1">
        <h2 id="mcp-tokens-heading" className="text-lg font-semibold text-foreground">
          Read-only MCP access
        </h2>
        <p className="text-sm text-muted-foreground">
          Create bearer tokens for external MCP clients to read your groups and meeting context.
          Tokens are shown only once and are never stored in the browser.
        </p>
      </div>

      {loading ? (
        <p className="text-sm text-muted-foreground" aria-live="polite">Loading…</p>
      ) : (
        <>
          <div className="rounded-lg border border-border bg-card p-4 text-sm">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="font-medium text-card-foreground">Endpoint</p>
                <code className="break-all text-xs text-muted-foreground" translate="no">
                  {status?.endpoint ?? "Unavailable"}
                </code>
              </div>
              <div>
                <p className="font-medium text-card-foreground">Authentication</p>
                <p className="text-muted-foreground">Bearer token</p>
              </div>
            </div>
            <p className="mt-3 text-xs text-muted-foreground">
              OAuth 2.1 metadata and PKCE can be added later. Do not use your Microsoft or OIDC token here.
            </p>
          </div>

          {status && (
            <div className="min-w-0 space-y-3 overflow-hidden rounded-lg border border-border bg-card p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 space-y-1">
                  <h3 className="font-medium text-card-foreground">Connection example</h3>
                  <p className="text-sm text-muted-foreground">
                    Define <code className="font-mono text-xs" translate="no">ZABT_MCP_TOKEN</code> in the external client with the one-time token value.
                    If it has a “Bearer token environment variable” field, enter only <code className="font-mono text-xs" translate="no">ZABT_MCP_TOKEN</code>, not the raw token or <code className="font-mono text-xs" translate="no">Bearer …</code>.
                  </p>
                </div>
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  onClick={copyConnectionExample}
                  aria-label={copiedExample ? "MCP connection example copied" : "Copy MCP connection example"}
                >
                  {copiedExample ? <Check className="size-4" aria-hidden="true" /> : <Copy className="size-4" aria-hidden="true" />}
                </Button>
              </div>
              <pre className="max-w-full overflow-x-auto rounded-md border border-border bg-background p-3 font-mono text-xs leading-relaxed text-foreground" translate="no">
                <code>{buildMcpConnectionExample(status.endpoint)}</code>
              </pre>
              <p className="text-xs text-muted-foreground">
                Use Streamable HTTP, not STDIO. Reconnect or reopen the chat after saving so the client discovers the tools.
              </p>
            </div>
          )}

          <form className="space-y-3 rounded-lg border border-border bg-card p-4" onSubmit={handleCreate}>
            <div className="flex items-center gap-2">
              <KeyRound className="size-4 text-primary" aria-hidden="true" />
              <h3 className="font-medium text-card-foreground">Create a token</h3>
            </div>
            <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_10rem_auto] sm:items-end">
              <div className="space-y-1.5">
                <label htmlFor="mcp-token-label" className="text-sm font-medium text-card-foreground">
                  Label
                </label>
                <Input
                  id="mcp-token-label"
                  name="mcp-token-label"
                  value={label}
                  onChange={(event) => setLabel(event.target.value)}
                  placeholder="Claude Desktop…"
                  autoComplete="off"
                  maxLength={100}
                  disabled={saving}
                />
              </div>
              <div className="space-y-1.5">
                <label htmlFor="mcp-token-expiry" className="text-sm font-medium text-card-foreground">
                  Expires in
                </label>
                <select
                  id="mcp-token-expiry"
                  name="mcp-token-expiry"
                  value={expiresInDays}
                  onChange={(event) => setExpiresInDays(event.target.value)}
                  className="h-8 w-full rounded-lg border border-input bg-background px-2.5 text-sm text-foreground outline-none transition-colors focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
                  disabled={saving}
                >
                  <option value="7">7 days</option>
                  <option value="30">30 days</option>
                  <option value="90">90 days</option>
                  <option value="365">365 days</option>
                </select>
              </div>
              <Button type="submit" loading={saving} disabled={saving}>
                Create token
              </Button>
            </div>
          </form>

          {newToken && (
            <div className="space-y-3 rounded-lg border border-primary/40 bg-primary/5 p-4" role="status" aria-live="polite">
              <div className="flex items-start gap-3">
                <AlertTriangle className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden="true" />
                <div className="min-w-0 space-y-1">
                  <p className="font-medium text-card-foreground">Copy this token now</p>
                  <p className="text-sm text-muted-foreground">
                    This is the only time Zabt will show the raw token. Anyone with it can read your MCP data until it expires or is revoked.
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <code className="min-w-0 flex-1 break-all rounded-md border border-border bg-background px-3 py-2 font-mono text-xs text-foreground" translate="no">
                  {newToken.token}
                </code>
                <Button type="button" variant="outline" size="icon" onClick={copyToken} aria-label={copiedToken ? "MCP token copied" : "Copy MCP token"}>
                  {copiedToken ? <Check className="size-4" aria-hidden="true" /> : <Copy className="size-4" aria-hidden="true" />}
                </Button>
              </div>
            </div>
          )}

          <div className="space-y-2">
            <h3 className="font-medium text-foreground">Your tokens</h3>
            {tokens.length === 0 ? (
              <p className="rounded-lg border border-dashed border-border px-4 py-5 text-sm text-muted-foreground">
                No MCP tokens yet.
              </p>
            ) : (
              <ul className="space-y-2" aria-label="MCP tokens">
                {tokens.map((token) => {
                  const revoked = token.revoked_at !== null;
                  const expired = new Date(token.expires_at).getTime() <= Date.now();
                  return (
                    <li key={token.id} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-card p-3">
                      <div className="min-w-0 space-y-1">
                        <p className="truncate font-medium text-card-foreground">{token.label}</p>
                        <p className="font-mono text-xs text-muted-foreground" translate="no">
                          {token.token_prefix}… · expires {formatDate(token.expires_at)}
                        </p>
                        <p className="text-xs text-muted-foreground">
                          {revoked ? "Revoked" : expired ? "Expired" : token.last_used_at ? `Last used ${formatDate(token.last_used_at)}` : "Not used yet"}
                        </p>
                      </div>
                      {!revoked && (
                        <Button type="button" variant="destructive" size="sm" onClick={() => setPendingRevoke(token)}>
                          <Trash2 className="size-3.5" aria-hidden="true" />
                          Revoke
                        </Button>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </>
      )}

      {error && <p className="text-sm text-destructive" role="alert">{error}</p>}
      <p className="sr-only" aria-live="polite">{announcement}</p>

      <AlertDialog open={pendingRevoke !== null} onOpenChange={(open) => !open && setPendingRevoke(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Revoke this MCP token?</AlertDialogTitle>
            <AlertDialogDescription>
              {pendingRevoke ? `“${pendingRevoke.label}” will stop working immediately. This cannot be undone.` : "This token will stop working immediately."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={revoking}>Keep token</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={confirmRevoke} loading={revoking}>
              Revoke token
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
