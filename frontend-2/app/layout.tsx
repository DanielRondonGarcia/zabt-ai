// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import { Suspense } from "react";
import { PHProvider } from "@/app/providers/posthog-provider";
import { PostHogPageView } from "@/app/components/posthog-pageview";
import { ThemeProvider } from "@/app/components/theme-provider";
import "./globals.css";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains-mono",
  subsets: ["latin"],
});

const themeBootstrapScript = `
(() => {
  const storageKey = "zabt-theme";
  let storedTheme = null;
  try {
    storedTheme = window.localStorage.getItem(storageKey);
  } catch {
    // Storage can be unavailable in privacy-restricted browser contexts.
  }
  const theme = storedTheme === "light" || storedTheme === "dark"
    ? storedTheme
    : window.matchMedia?.("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  const root = document.documentElement;
  root.classList.toggle("dark", theme === "dark");
  root.style.colorScheme = theme;
})();
`;

export const metadata: Metadata = {
  title: "Zabt AI",
  description: "AI Meeting Notes — transcribe and summarize your meetings automatically",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeBootstrapScript }} />
      </head>
      <body className={`${inter.variable} ${jetbrainsMono.variable} font-sans antialiased`}>
        <ThemeProvider>
          <PHProvider>
            <Suspense fallback={null}>
              <PostHogPageView />
            </Suspense>
            {children}
          </PHProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
