// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
"use client";

import { Moon, Sun } from "lucide-react";
import { useTheme } from "@/app/components/theme-provider";

export function ThemeToggle() {
    const { theme, toggleTheme } = useTheme();
    const nextTheme = theme === "dark" ? "light" : "dark";
    const label = `${nextTheme === "dark" ? "Dark" : "Light"} mode`;
    const Icon = theme === "dark" ? Sun : Moon;

    return (
        <button
            type="button"
            onClick={toggleTheme}
            aria-pressed={theme === "dark"}
            aria-label={`Switch to ${label.toLowerCase()}`}
            title={label}
            className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium text-sidebar-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring focus-visible:ring-offset-2 focus-visible:ring-offset-sidebar motion-reduce:transition-none"
        >
            <Icon className="size-4 shrink-0" aria-hidden="true" />
            <span>{label}</span>
        </button>
    );
}
