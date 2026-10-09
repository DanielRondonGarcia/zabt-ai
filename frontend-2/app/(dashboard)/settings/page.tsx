// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
import { LanguagePreferencesEditor } from "@/app/components/LanguagePreferencesEditor";
import { McpTokenManager } from "@/app/components/McpTokenManager";
import { AIProviderSettings } from "@/app/components/AIProviderSettings";

export default function SettingsPage() {
  return (
    <div className="mx-auto max-w-2xl space-y-8 overflow-x-hidden p-8">
      <h1 className="text-2xl font-semibold text-foreground">Settings</h1>
      <section className="space-y-3">
        <h2 className="text-lg font-semibold text-foreground">Transcription languages</h2>
        <LanguagePreferencesEditor />
      </section>
      <AIProviderSettings />
      <McpTokenManager />
    </div>
  );
}
