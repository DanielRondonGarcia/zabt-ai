// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
"use client";

import { useEffect, useRef, useState } from "react";
import { AlertTriangle, RefreshCw, Trash2 } from "lucide-react";

import {
  deleteAIProviderConfiguration,
  discoverAIProviderModels,
  getAIProviderConfiguration,
  getApiErrorMessage,
  updateAIProviderConfiguration,
  type AIProviderConfiguration,
  type AIProviderModelCatalog,
  type CustomAIProvider,
} from "@/app/lib/api";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/app/components/ui/alert-dialog";
import { Button } from "@/app/components/ui/button";
import { Input } from "@/app/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/app/components/ui/select";

const PROVIDER_DEFAULTS: Record<CustomAIProvider, { model: string; baseUrl: string }> = {
  openai: { model: "gpt-4o-mini", baseUrl: "https://api.openai.com/v1" },
  anthropic: { model: "claude-3-5-haiku-latest", baseUrl: "https://api.anthropic.com" },
  ollama: { model: "gemma4:31b", baseUrl: "https://ollama.com/v1" },
};

const PROVIDER_LABELS: Record<CustomAIProvider, string> = {
  openai: "OpenAI",
  anthropic: "Claude / Anthropic",
  ollama: "Ollama / OpenAI-compatible",
};

const MODEL_SOURCE_LABELS: Record<AIProviderModelCatalog["source"], string> = {
  "openai-compatible": "OpenAI-compatible API",
  anthropic: "Anthropic model API",
  "ollama-compatible": "Ollama OpenAI-compatible API",
  "ollama-tags": "Ollama native API",
};

function providerOrDefault(value: string): CustomAIProvider | "" {
  return value === "openai" || value === "anthropic" || value === "ollama" ? value : "";
}

function isHttpUrl(value: string): boolean {
  try {
    return new URL(value).protocol === "http:";
  } catch {
    return false;
  }
}

function readError(error: unknown, fallback: string): string {
  return getApiErrorMessage(error) || fallback;
}

export function AIProviderSettings() {
  const [configuration, setConfiguration] = useState<AIProviderConfiguration | null>(null);
  const [provider, setProvider] = useState<CustomAIProvider | "">("");
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [useForSummary, setUseForSummary] = useState(true);
  const [useForChat, setUseForChat] = useState(true);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [pendingClear, setPendingClear] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");
  const [error, setError] = useState("");
  const [modelCatalog, setModelCatalog] = useState<AIProviderModelCatalog | null>(null);
  const [modelCatalogLoading, setModelCatalogLoading] = useState(false);
  const [modelCatalogError, setModelCatalogError] = useState("");
  const modelDiscoveryRequest = useRef(0);
  const autoLoadModelCatalog = useRef(false);

  function resetModelCatalog() {
    modelDiscoveryRequest.current += 1;
    setModelCatalog(null);
    setModelCatalogError("");
    setModelCatalogLoading(false);
  }

  function applyConfiguration(next: AIProviderConfiguration) {
    const nextProvider = next.provider ?? "";
    setConfiguration(next);
    setProvider(nextProvider);
    setModel(next.model ?? (nextProvider ? PROVIDER_DEFAULTS[nextProvider].model : ""));
    setBaseUrl(next.base_url ?? (nextProvider ? PROVIDER_DEFAULTS[nextProvider].baseUrl : ""));
    setApiKey("");
    setEnabled(next.provider ? next.enabled : true);
    setUseForSummary(next.provider ? next.use_for_summary : true);
    setUseForChat(next.provider ? next.use_for_chat : true);
    resetModelCatalog();
  }

  useEffect(() => {
    getAIProviderConfiguration()
      .then(applyConfiguration)
      .catch((loadError) => setError(readError(loadError, "AI provider settings could not be loaded. Refresh the page and try again.")))
      .finally(() => setLoading(false));
  }, []);

  function handleProviderChange(value: string) {
    const nextProvider = providerOrDefault(value);
    setProvider(nextProvider);
    setStatusMessage("");
    setError("");
    setApiKey("");
    resetModelCatalog();
    autoLoadModelCatalog.current = false;
    if (nextProvider) {
      setModel(PROVIDER_DEFAULTS[nextProvider].model);
      setBaseUrl(PROVIDER_DEFAULTS[nextProvider].baseUrl);
    } else {
      setModel("");
      setBaseUrl("");
    }
  }

  async function loadModels() {
    if (!provider) return;

    const requestId = ++modelDiscoveryRequest.current;
    const enteredApiKey = apiKey.trim();
    setModelCatalogLoading(true);
    setModelCatalogError("");
    try {
      const catalog = await discoverAIProviderModels({
        provider,
        base_url: baseUrl.trim() || PROVIDER_DEFAULTS[provider].baseUrl,
        ...(enteredApiKey ? { api_key: enteredApiKey } : {}),
      });
      if (requestId !== modelDiscoveryRequest.current) return;
      setModelCatalog(catalog);
      if (catalog.models.length === 0) {
        setModelCatalogError(
          "The provider returned no models. Enter a model ID manually or check the provider.",
        );
      }
    } catch (loadError) {
      if (requestId !== modelDiscoveryRequest.current) return;
      setModelCatalog(null);
      setModelCatalogError(
        readError(
          loadError,
          "Models could not be loaded. Check the provider URL and credentials, then try again.",
        ),
      );
    } finally {
      if (requestId === modelDiscoveryRequest.current) {
        setModelCatalogLoading(false);
      }
    }
  }

  useEffect(() => {
    if (loading || !provider || autoLoadModelCatalog.current) return;
    autoLoadModelCatalog.current = true;
    void loadModels();
  }, [loading, provider]);

  async function save() {
    setSaving(true);
    setError("");
    setStatusMessage("");
    try {
      if (!provider) {
        await deleteAIProviderConfiguration();
        const next: AIProviderConfiguration = {
          provider: null,
          model: null,
          base_url: null,
          api_key_configured: false,
          enabled: false,
          use_for_summary: false,
          use_for_chat: false,
        };
        applyConfiguration(next);
        setStatusMessage("Application defaults are now selected.");
        return;
      }

      const selectedModel = model.trim() || PROVIDER_DEFAULTS[provider].model;
      const selectedBaseUrl = baseUrl.trim() || PROVIDER_DEFAULTS[provider].baseUrl;
      const enteredApiKey = apiKey.trim();
      const ollamaHttpEndpoint = provider === "ollama" && isHttpUrl(selectedBaseUrl);
      if (ollamaHttpEndpoint && enteredApiKey) {
        setError("Ollama API keys require HTTPS. Leave the API key blank for local HTTP Ollama, or use an HTTPS endpoint.");
        return;
      }
      const shouldClearStoredOllamaKey =
        ollamaHttpEndpoint &&
        !enteredApiKey &&
        configuration?.provider === "ollama" &&
        configuration.api_key_configured;
      const payload = {
        provider,
        model: selectedModel,
        base_url: selectedBaseUrl,
        enabled,
        use_for_summary: useForSummary,
        use_for_chat: useForChat,
        ...(enteredApiKey
          ? { api_key: apiKey }
          : shouldClearStoredOllamaKey
            ? { api_key: "" }
            : {}),
      };
      const next = await updateAIProviderConfiguration(payload);
      applyConfiguration(next);
      setStatusMessage("AI provider settings saved.");
    } catch (saveError) {
      setError(readError(saveError, "AI provider settings could not be saved. Check the fields and try again."));
    } finally {
      setSaving(false);
    }
  }

  async function clearConfiguration() {
    setSaving(true);
    setError("");
    setStatusMessage("");
    try {
      await deleteAIProviderConfiguration();
      applyConfiguration({
        provider: null,
        model: null,
        base_url: null,
        api_key_configured: false,
        enabled: false,
        use_for_summary: false,
        use_for_chat: false,
      });
      setPendingClear(false);
      setStatusMessage("Custom AI provider removed. Application defaults are now active.");
    } catch (clearError) {
      setError(readError(clearError, "The custom AI provider could not be removed. Try again."));
    } finally {
      setSaving(false);
    }
  }

  const hasCustomProvider = provider !== "";
  const ollamaHttpEndpoint =
    provider === "ollama" &&
    isHttpUrl(baseUrl.trim() || PROVIDER_DEFAULTS.ollama.baseUrl);
  const keyHint = ollamaHttpEndpoint
    ? "Keyless HTTP Ollama is limited to localhost development. Use HTTPS before entering an API key."
    : configuration?.provider === provider && configuration.api_key_configured
      ? "A key is already stored securely. Leave this blank to keep it."
      : "Required for OpenAI, Claude, and cloud Ollama; optional only for local keyless Ollama.";

  return (
    <section className="min-w-0 space-y-4" aria-labelledby="ai-provider-heading">
      <div className="min-w-0 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <h2 id="ai-provider-heading" className="text-lg font-semibold text-foreground">
            AI provider
          </h2>
          <span className="rounded-4xl border border-border px-2 py-0.5 text-xs font-medium text-muted-foreground">
            {configuration?.provider ? "Configured" : "Application default"}
          </span>
        </div>
        <p className="break-words text-sm text-muted-foreground">
          Use your own provider for meeting summaries and AI Chat. Credentials are encrypted on the server and never returned to the browser. Cloud Ollama uses HTTPS and an API key; keyless HTTP Ollama is supported only for localhost development. Disabled or unselected purposes use the application default.
        </p>
      </div>

      {loading ? (
        <p className="text-sm text-muted-foreground" aria-live="polite">Loading…</p>
      ) : (
        <div className="min-w-0 space-y-4 rounded-lg border border-border bg-card p-4">
          <div className="space-y-1.5">
            <label htmlFor="ai-provider" className="text-sm font-medium text-card-foreground">
              Provider
            </label>
            <Select
              value={provider || null}
              onValueChange={(value) => handleProviderChange(value ?? "")}
              disabled={saving}
              name="ai-provider"
            >
              <SelectTrigger id="ai-provider">
                <SelectValue placeholder="Application default" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={null}>Application default</SelectItem>
                {(Object.keys(PROVIDER_LABELS) as CustomAIProvider[]).map((value) => (
                  <SelectItem key={value} value={value}>{PROVIDER_LABELS[value]}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {hasCustomProvider && (
            <>
              <div className="grid min-w-0 gap-3 sm:grid-cols-2">
                <div className="min-w-0 space-y-1.5">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <label htmlFor="ai-provider-model" className="text-sm font-medium text-card-foreground">
                      Model
                    </label>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={loadModels}
                      loading={modelCatalogLoading}
                      disabled={saving || modelCatalogLoading}
                    >
                      {!modelCatalogLoading && <RefreshCw className="size-3.5" aria-hidden="true" />}
                      {modelCatalog ? "Refresh models" : "Load models"}
                    </Button>
                  </div>
                  {modelCatalog && modelCatalog.models.length > 0 ? (
                    <Select
                      value={model || null}
                      onValueChange={(value) => setModel(value ?? "")}
                      disabled={saving || modelCatalogLoading}
                      name="ai-provider-model"
                    >
                      <SelectTrigger id="ai-provider-model" aria-describedby="ai-provider-model-status">
                        <SelectValue placeholder={PROVIDER_DEFAULTS[provider].model} />
                      </SelectTrigger>
                      <SelectContent>
                        {model && !modelCatalog.models.includes(model) && (
                          <SelectItem value={model} className="min-w-0 break-all">
                            {model} (current)
                          </SelectItem>
                        )}
                        {modelCatalog.models.map((value) => (
                          <SelectItem key={value} value={value} className="min-w-0 break-all">
                            {value}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  ) : (
                    <Input
                      id="ai-provider-model"
                      name="ai-provider-model"
                      value={model}
                      onChange={(event) => setModel(event.target.value)}
                      placeholder={PROVIDER_DEFAULTS[provider].model}
                      autoComplete="off"
                      maxLength={128}
                      disabled={saving}
                      aria-describedby="ai-provider-model-status"
                    />
                  )}
                  <p
                    id="ai-provider-model-status"
                    className={`break-words text-xs ${modelCatalogError ? "text-destructive" : "text-muted-foreground"}`}
                    role={modelCatalogError ? "alert" : "status"}
                    aria-live="polite"
                  >
                    {modelCatalogLoading
                      ? "Loading models…"
                      : modelCatalog && modelCatalog.models.length > 0
                        ? `${modelCatalog.models.length} models loaded from ${MODEL_SOURCE_LABELS[modelCatalog.source]}.`
                        : modelCatalogError || "Load models to choose from the provider catalog. Manual model IDs are supported if discovery is unavailable."}
                  </p>
                </div>
                <div className="min-w-0 space-y-1.5">
                  <label htmlFor="ai-provider-base-url" className="text-sm font-medium text-card-foreground">
                    Base URL
                  </label>
                  <Input
                    id="ai-provider-base-url"
                    name="ai-provider-base-url"
                    type="url"
                    value={baseUrl}
                    onChange={(event) => {
                      setBaseUrl(event.target.value);
                      resetModelCatalog();
                    }}
                    placeholder={PROVIDER_DEFAULTS[provider].baseUrl}
                    autoComplete="url"
                    maxLength={2048}
                    disabled={saving}
                  />
                  <p className="break-words text-xs text-muted-foreground">Cloud Ollama uses https://ollama.com/v1 and an API key. Other public Ollama endpoints require HTTPS; keyless HTTP is limited to localhost development.</p>
                </div>
              </div>

              <div className="min-w-0 space-y-1.5">
                <label htmlFor="ai-provider-api-key" className="text-sm font-medium text-card-foreground">
                  API key
                </label>
                <Input
                  id="ai-provider-api-key"
                  name="ai-provider-api-key"
                  type="password"
                  value={apiKey}
                  onChange={(event) => {
                    setApiKey(event.target.value);
                    resetModelCatalog();
                  }}
                  placeholder="Enter a new API key…"
                  autoComplete="new-password"
                  maxLength={4096}
                  spellCheck={false}
                  disabled={saving}
                />
                <p className="break-words text-xs text-muted-foreground">{keyHint}</p>
              </div>

              <div className="space-y-2 border-t border-border pt-3">
                <label className="flex min-h-8 cursor-pointer items-center gap-3 text-sm text-card-foreground">
                  <input
                    type="checkbox"
                    name="ai-provider-enabled"
                    checked={enabled}
                    onChange={(event) => setEnabled(event.target.checked)}
                    disabled={saving}
                    className="size-4 accent-primary focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
                  />
                  <span>Enable this provider</span>
                </label>
                <label className="flex min-h-8 cursor-pointer items-center gap-3 text-sm text-card-foreground">
                  <input
                    type="checkbox"
                    name="ai-provider-summaries"
                    checked={useForSummary}
                    onChange={(event) => setUseForSummary(event.target.checked)}
                    disabled={saving}
                    className="size-4 accent-primary focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
                  />
                  <span>Use for meeting summaries</span>
                </label>
                <label className="flex min-h-8 cursor-pointer items-center gap-3 text-sm text-card-foreground">
                  <input
                    type="checkbox"
                    name="ai-provider-chat"
                    checked={useForChat}
                    onChange={(event) => setUseForChat(event.target.checked)}
                    disabled={saving}
                    className="size-4 accent-primary focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
                  />
                  <span>Use for AI Chat</span>
                </label>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <Button type="button" onClick={save} loading={saving} disabled={saving}>
                  Save provider settings
                </Button>
                {configuration?.provider && (
                  <Button type="button" variant="destructive" onClick={() => setPendingClear(true)} disabled={saving}>
                    <Trash2 className="size-3.5" aria-hidden="true" />
                    Remove custom provider
                  </Button>
                )}
              </div>
            </>
          )}

          {!hasCustomProvider && (
            <div className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-dashed border-border px-3 py-3 text-sm text-muted-foreground">
              <div className="flex min-w-0 items-start gap-3">
                <AlertTriangle className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden="true" />
                <p className="break-words">Application defaults remain active until you select and save a custom provider.</p>
              </div>
              {configuration?.provider && (
                <Button type="button" variant="outline" onClick={save} loading={saving} disabled={saving}>
                  Use application defaults
                </Button>
              )}
            </div>
          )}
        </div>
      )}

      {(error || statusMessage) && (
        <p
          className={`min-h-5 break-words text-sm ${error ? "text-destructive" : "text-primary"}`}
          role={error ? "alert" : "status"}
          aria-live="polite"
        >
          {error || statusMessage}
        </p>
      )}
      <AlertDialog open={pendingClear} onOpenChange={(open) => !open && setPendingClear(false)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove this custom AI provider?</AlertDialogTitle>
            <AlertDialogDescription>
              The encrypted credential and custom settings will be deleted. Summaries and AI Chat will use application defaults.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={saving}>Keep provider</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={clearConfiguration} loading={saving}>
              Remove provider
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
