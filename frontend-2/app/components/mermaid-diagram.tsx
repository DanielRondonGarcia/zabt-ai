// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2025-2026 Afeef Janjua
"use client";

import { useEffect, useId, useState } from "react";
import { Loader2 } from "lucide-react";

type MermaidApi = typeof import("mermaid").default;

let mermaidLoader: Promise<MermaidApi> | null = null;
let renderAttempt = 0;

/** Loads and configures Mermaid once per page; every diagram shares the same instance. */
const loadMermaid = (): Promise<MermaidApi> => {
  if (!mermaidLoader) {
    mermaidLoader = import("mermaid").then((module) => {
      const mermaid = module.default;
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        // Keep parse failures out of `document.body`; the component renders its own fallback.
        suppressErrorRendering: true,
        theme: "neutral",
        fontFamily: "var(--font-inter), Inter, ui-sans-serif, system-ui, sans-serif",
      });
      return mermaid;
    });
  }
  return mermaidLoader;
};

/** First meaningful line of the diagram source, used as the accessible description. */
const describeDiagram = (code: string): string => {
  const firstLine = code
    .split("\n")
    .map((line) => line.trim())
    .find((line) => line.length > 0 && !line.startsWith("%%"));
  return firstLine ? `Diagram: ${firstLine}` : "Diagram";
};

type RenderResult =
  | { status: "ready"; svg: string }
  | { status: "error"; message: string };

/** Result tagged with the source it was rendered from, so a changed `code` prop shows as loading. */
type RenderState = RenderResult & { source: string };

interface MermaidDiagramProps {
  code: string;
}

export function MermaidDiagram({ code }: MermaidDiagramProps) {
  const descriptionId = useId();
  const [result, setResult] = useState<RenderState | null>(null);

  useEffect(() => {
    let active = true;
    // Mermaid uses the id as a DOM id and CSS selector: one unique id per render attempt so a
    // Strict Mode double-invocation or a fast re-render never clobbers another attempt's output.
    renderAttempt += 1;
    const diagramId = `mermaid-${Date.now().toString(36)}-${renderAttempt}`;

    loadMermaid()
      .then((mermaid) => mermaid.render(diagramId, code.trim()))
      .then(({ svg }) => {
        if (active) setResult({ status: "ready", svg, source: code });
      })
      .catch((error: unknown) => {
        if (!active) return;
        const message = error instanceof Error ? error.message : "Unknown rendering error.";
        setResult({ status: "error", message: message.split("\n")[0] ?? message, source: code });
      });

    return () => {
      active = false;
    };
  }, [code]);

  const state: RenderResult | { status: "loading" } =
    result && result.source === code ? result : { status: "loading" };

  if (state.status === "loading") {
    return (
      <div
        role="status"
        className="not-prose my-4 flex items-center gap-2 rounded-lg border border-stone-200 bg-stone-50 p-4 text-sm text-stone-500"
      >
        <Loader2 aria-hidden="true" className="size-4 animate-spin text-primary motion-reduce:animate-none" />
        Rendering diagram…
      </div>
    );
  }

  if (state.status === "error") {
    return (
      <figure className="not-prose my-4 overflow-hidden rounded-lg border border-stone-200 bg-stone-50">
        <pre className="overflow-x-auto p-4 font-mono text-xs leading-5 text-stone-700">
          <code>{code}</code>
        </pre>
        <figcaption className="border-t border-stone-200 px-4 py-2 text-xs text-stone-500">
          The diagram could not be rendered, so the source is shown instead. {state.message}
        </figcaption>
      </figure>
    );
  }

  return (
    <figure className="not-prose my-4 overflow-hidden rounded-lg border border-stone-200 bg-white">
      <p id={descriptionId} className="sr-only">
        {describeDiagram(code)}
      </p>
      <div
        role="img"
        aria-labelledby={descriptionId}
        className="overflow-x-auto p-4 [&_svg]:mx-auto [&_svg]:h-auto [&_svg]:max-w-full"
        // Mermaid sanitizes its output with DOMPurify under `securityLevel: "strict"`.
        dangerouslySetInnerHTML={{ __html: state.svg }}
      />
      <details className="border-t border-stone-200">
        <summary className="cursor-pointer px-4 py-2 text-xs font-medium text-stone-600 hover:text-stone-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50">
          Show diagram source
        </summary>
        <pre className="overflow-x-auto border-t border-stone-200 bg-stone-50 p-4 font-mono text-xs leading-5 text-stone-700">
          <code>{code}</code>
        </pre>
      </details>
    </figure>
  );
}
