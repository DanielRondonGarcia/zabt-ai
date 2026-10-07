// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
"use client";

import {
    createContext,
    useCallback,
    useContext,
    useEffect,
    useMemo,
    useSyncExternalStore,
} from "react";
import type { ReactNode } from "react";

export type Theme = "light" | "dark";

export const THEME_STORAGE_KEY = "zabt-theme";

interface ThemeContextValue {
    theme: Theme;
    setTheme: (theme: Theme) => void;
    toggleTheme: () => void;
}

const ThemeContext = createContext<ThemeContextValue | undefined>(undefined);
const themeListeners = new Set<() => void>();

function isTheme(value: string | null): value is Theme {
    return value === "light" || value === "dark";
}

function getInitialTheme(): Theme {
    if (typeof window === "undefined") return "light";

    let storedTheme: string | null = null;
    try {
        storedTheme = window.localStorage.getItem(THEME_STORAGE_KEY);
    } catch {
        // Storage can be unavailable in privacy-restricted browser contexts.
    }

    if (isTheme(storedTheme)) return storedTheme;

    return window.matchMedia?.("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light";
}

function applyTheme(theme: Theme) {
    const root = document.documentElement;
    root.classList.toggle("dark", theme === "dark");
    root.style.colorScheme = theme;
}

function subscribeToTheme(listener: () => void) {
    themeListeners.add(listener);
    return () => themeListeners.delete(listener);
}

function getThemeSnapshot(): Theme {
    if (typeof document === "undefined") return "light";
    return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

function getServerThemeSnapshot(): Theme {
    return "light";
}

function notifyThemeChange() {
    themeListeners.forEach((listener) => listener());
}

export function ThemeProvider({ children }: { children: ReactNode }) {
    const theme = useSyncExternalStore(
        subscribeToTheme,
        getThemeSnapshot,
        getServerThemeSnapshot
    );

    useEffect(() => {
        applyTheme(getInitialTheme());
        notifyThemeChange();
    }, []);

    const setTheme = useCallback((nextTheme: Theme) => {
        if (typeof document !== "undefined") {
            applyTheme(nextTheme);
            notifyThemeChange();
        }

        if (typeof window !== "undefined") {
            try {
                window.localStorage.setItem(THEME_STORAGE_KEY, nextTheme);
            } catch {
                // Keep the in-memory theme when storage is unavailable.
            }
        }
    }, []);

    const toggleTheme = useCallback(() => {
        setTheme(theme === "dark" ? "light" : "dark");
    }, [setTheme, theme]);

    const value = useMemo(
        () => ({ theme, setTheme, toggleTheme }),
        [setTheme, theme, toggleTheme]
    );

    return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
    const context = useContext(ThemeContext);
    if (!context) {
        throw new Error("useTheme must be used within a ThemeProvider");
    }
    return context;
}
