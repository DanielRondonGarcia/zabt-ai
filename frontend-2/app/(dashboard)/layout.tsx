// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { usePostHog } from "posthog-js/react";
import { fetchCurrentUser } from "@/app/lib/api";
import { AppShell } from "@/app/components/app-shell";
import { TooltipProvider } from "@/app/components/ui/tooltip";
import { Spinner } from "@/app/components/ui/spinner";

export default function DashboardLayout({
    children,
}: {
    children: React.ReactNode;
}) {
    const router = useRouter();
    const posthog = usePostHog();
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let mounted = true;
        fetchCurrentUser()
            .then((user) => {
                if (!mounted) return;
                posthog?.identify(user.email, {
                    email: user.email,
                    full_name: user.full_name,
                });
                setLoading(false);
            })
            .catch(() => {
                if (mounted) {
                    router.replace("/login");
                }
            });

        return () => {
            mounted = false;
        };
    }, [posthog, router]);

    if (loading) {
        return (
            <div className="flex items-center justify-center min-h-screen bg-stone-50">
                <div className="flex flex-col items-center gap-3">
                    <Spinner className="size-6 text-muted-foreground" />
                    <p className="text-sm text-stone-400 font-medium">Authenticating...</p>
                </div>
            </div>
        );
    }

    return (
        <TooltipProvider>
            <AppShell>{children}</AppShell>
        </TooltipProvider>
    );
}
