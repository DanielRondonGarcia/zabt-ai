// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
"use client";

import { ArrowRight, Sparkles } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

interface AiQueryBarProps {
    onSubmit?: (query: string) => void;
}

export function AiQueryBar({ onSubmit }: AiQueryBarProps) {
    const router = useRouter();
    const [query, setQuery] = useState("");
    const [advanced, setAdvanced] = useState(false);

    const handleSubmit = () => {
        const trimmed = query.trim();
        if (!trimmed) return;
        if (onSubmit) {
            onSubmit(trimmed);
        } else {
            router.push(`/ai-chat?q=${encodeURIComponent(trimmed)}`);
        }
    };

    const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
        if (e.key === "Enter") handleSubmit();
    };

    return (
        <div className="bg-white border border-stone-200 rounded-lg flex items-center gap-2 px-4 py-3">
            <Sparkles aria-hidden="true" className="w-4 h-4 text-primary/60 flex-shrink-0" />

            {/* Input */}
            <input
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Ask Zabt anything about your meetings…"
                className="flex-1 text-sm text-stone-700 placeholder-stone-400 bg-transparent focus:outline-none"
            />

            {/* Advanced toggle */}
            <button
                onClick={() => setAdvanced((v) => !v)}
                className={`text-xs font-medium transition-colors px-2 py-1 rounded-lg ${advanced
                        ? "text-primary bg-primary/10"
                        : "text-stone-400 hover:text-stone-500"
                    }`}
            >
                Advanced
            </button>

            {/* Submit */}
            <button
                onClick={handleSubmit}
                disabled={!query.trim()}
                className="flex items-center justify-center w-7 h-7 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:bg-stone-200 disabled:text-stone-400 transition-colors flex-shrink-0"
                aria-label="Submit"
            >
                <ArrowRight aria-hidden="true" className="w-3.5 h-3.5" />
            </button>
        </div>
    );
}
