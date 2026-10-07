// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
"use client";

import { Sidebar } from "@/app/components/sidebar";
import { useState } from "react";

interface AppShellProps {
    children: React.ReactNode;
}

export function AppShell({ children }: AppShellProps) {
    const [sidebarOpen, setSidebarOpen] = useState(false);

    return (
        <div className="flex h-screen overflow-hidden bg-background text-foreground">
            {/* Mobile backdrop */}
            {sidebarOpen && (
                <div
                    className="fixed inset-0 z-40 bg-stone-900/30 md:hidden"
                    onClick={() => setSidebarOpen(false)}
                />
            )}

            {/* Sidebar — fixed on mobile, static on md+ */}
            <div
                className={`
          fixed inset-y-0 left-0 z-50 transition-transform duration-200 ease-in-out motion-reduce:transition-none
          md:relative md:translate-x-0 md:z-auto
          ${sidebarOpen ? "translate-x-0" : "-translate-x-full"}
        `}
            >
                <Sidebar onNavClick={() => setSidebarOpen(false)} />
            </div>

            {/* Main content */}
            <div className="flex flex-1 flex-col min-w-0 overflow-hidden">
                {/* Mobile top bar */}
                <header className="flex items-center gap-3 px-4 py-3 bg-background border-b border-border md:hidden">
                    <button
                        onClick={() => setSidebarOpen(true)}
                        className="p-1.5 rounded-lg text-muted-foreground hover:bg-accent hover:text-accent-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        aria-label="Open menu"
                    >
                        <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />
                        </svg>
                    </button>
                    <span className="text-sm font-bold text-foreground">Zabt AI</span>
                </header>

                {/* Page content */}
                <main className="flex-1 overflow-y-auto">
                    {children}
                </main>
            </div>
        </div>
    );
}
