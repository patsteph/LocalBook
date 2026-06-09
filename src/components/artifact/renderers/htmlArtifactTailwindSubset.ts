/**
 * htmlArtifactTailwindSubset — precompiled CSS string injected into the
 * Shadow DOM root of every `'html'` artifact. Hand-curated, not a Tailwind
 * build; covers the utilities a hand-authored or LLM-generated card needs
 * to look presentable without leaking the host page's global cascade.
 *
 * Every selector is scoped under `.lb-html-artifact` so the shadow root
 * styles never reach further than the artifact wrapper even if the host
 * page ever mounted this CSS without Shadow DOM (defense in depth).
 *
 * Extending: when a new use case (Phase 4 Studio HTML / Phase 9 newsletter
 * source viewer) needs a utility that's not here, ADD IT — keep the file
 * small and curated rather than wiring in a full Tailwind build.
 *
 * Dark mode is handled via `@media (prefers-color-scheme: dark)` which
 * matches the rest of LocalBook (Tailwind `dark:` is also class-based, but
 * the shadow root has no access to the host's dark class, so we use the
 * media query directly).
 */

export const HTML_ARTIFACT_TAILWIND_SUBSET = `
:host, :root { color-scheme: light dark; }

.lb-html-artifact {
  /* Base typography + box-model reset. Matches the host page so nothing
     looks foreign when shadow root mounts. K2 (2026-06-09): explicit
     background-color so we always have correct contrast — defaults
     ride the OS prefers-color-scheme which drifts from the app theme. */
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  font-size: 14px;
  line-height: 1.5;
  color: #111827;
  background-color: #ffffff;
  box-sizing: border-box;
}
.lb-html-artifact *,
.lb-html-artifact *::before,
.lb-html-artifact *::after { box-sizing: border-box; }

/* Headings */
.lb-html-artifact h1,
.lb-html-artifact h2,
.lb-html-artifact h3,
.lb-html-artifact h4 { font-weight: 600; margin: 0 0 0.5rem 0; line-height: 1.25; }
.lb-html-artifact h1 { font-size: 1.5rem; }
.lb-html-artifact h2 { font-size: 1.25rem; }
.lb-html-artifact h3 { font-size: 1.125rem; }
.lb-html-artifact h4 { font-size: 1rem; }

/* Block elements */
.lb-html-artifact p { margin: 0 0 0.75rem 0; }
.lb-html-artifact ul,
.lb-html-artifact ol { margin: 0 0 0.75rem 0; padding-left: 1.25rem; }
.lb-html-artifact li { margin: 0.125rem 0; }
.lb-html-artifact hr { border: none; border-top: 1px solid #e5e7eb; margin: 1rem 0; }
.lb-html-artifact blockquote { margin: 0 0 0.75rem 0; padding-left: 0.75rem; border-left: 3px solid #d1d5db; color: #4b5563; }
.lb-html-artifact code { font-family: ui-monospace, SFMono-Regular, monospace; font-size: 0.875em; background: rgba(0,0,0,0.06); padding: 0.125rem 0.25rem; border-radius: 0.25rem; }
.lb-html-artifact pre { background: rgba(0,0,0,0.06); padding: 0.75rem; border-radius: 0.5rem; overflow-x: auto; margin: 0 0 0.75rem 0; }
.lb-html-artifact pre code { background: transparent; padding: 0; }

/* Tables */
.lb-html-artifact table { width: 100%; border-collapse: collapse; margin: 0 0 0.75rem 0; }
.lb-html-artifact th,
.lb-html-artifact td { padding: 0.5rem 0.75rem; border-bottom: 1px solid #e5e7eb; text-align: left; }
.lb-html-artifact th { font-weight: 600; background: rgba(0,0,0,0.03); }

/* Anchors — sanitization neutralizes javascript: hrefs; we just style. */
.lb-html-artifact a { color: #2563eb; text-decoration: underline; }
.lb-html-artifact a:hover { color: #1d4ed8; }

/* ────────────────────────────────────────────────────────────────────────
   Utility subset — typography
   ──────────────────────────────────────────────────────────────────────── */
.lb-html-artifact .text-xs { font-size: 0.75rem; line-height: 1rem; }
.lb-html-artifact .text-sm { font-size: 0.875rem; line-height: 1.25rem; }
.lb-html-artifact .text-base { font-size: 1rem; line-height: 1.5rem; }
.lb-html-artifact .text-lg { font-size: 1.125rem; line-height: 1.75rem; }
.lb-html-artifact .text-xl { font-size: 1.25rem; line-height: 1.75rem; }
.lb-html-artifact .text-2xl { font-size: 1.5rem; line-height: 2rem; }

.lb-html-artifact .font-normal { font-weight: 400; }
.lb-html-artifact .font-medium { font-weight: 500; }
.lb-html-artifact .font-semibold { font-weight: 600; }
.lb-html-artifact .font-bold { font-weight: 700; }

.lb-html-artifact .uppercase { text-transform: uppercase; }
.lb-html-artifact .tracking-wide { letter-spacing: 0.025em; }
.lb-html-artifact .leading-tight { line-height: 1.25; }
.lb-html-artifact .leading-relaxed { line-height: 1.625; }

.lb-html-artifact .text-left { text-align: left; }
.lb-html-artifact .text-center { text-align: center; }
.lb-html-artifact .text-right { text-align: right; }

/* Text colors */
.lb-html-artifact .text-gray-400 { color: #9ca3af; }
.lb-html-artifact .text-gray-500 { color: #6b7280; }
.lb-html-artifact .text-gray-600 { color: #4b5563; }
.lb-html-artifact .text-gray-700 { color: #374151; }
.lb-html-artifact .text-gray-800 { color: #1f2937; }
.lb-html-artifact .text-gray-900 { color: #111827; }
.lb-html-artifact .text-blue-600 { color: #2563eb; }
.lb-html-artifact .text-blue-700 { color: #1d4ed8; }
.lb-html-artifact .text-green-600 { color: #16a34a; }
.lb-html-artifact .text-green-700 { color: #15803d; }
.lb-html-artifact .text-red-600 { color: #dc2626; }
.lb-html-artifact .text-red-700 { color: #b91c1c; }
.lb-html-artifact .text-amber-600 { color: #d97706; }
.lb-html-artifact .text-amber-700 { color: #b45309; }
.lb-html-artifact .text-emerald-600 { color: #059669; }
.lb-html-artifact .text-emerald-700 { color: #047857; }

/* ────────────────────────────────────────────────────────────────────────
   Utility subset — layout
   ──────────────────────────────────────────────────────────────────────── */
.lb-html-artifact .flex { display: flex; }
.lb-html-artifact .inline-flex { display: inline-flex; }
.lb-html-artifact .grid { display: grid; }
.lb-html-artifact .block { display: block; }
.lb-html-artifact .inline-block { display: inline-block; }
.lb-html-artifact .hidden { display: none; }

.lb-html-artifact .flex-col { flex-direction: column; }
.lb-html-artifact .flex-row { flex-direction: row; }
.lb-html-artifact .flex-wrap { flex-wrap: wrap; }
.lb-html-artifact .flex-1 { flex: 1 1 0%; }

.lb-html-artifact .items-start { align-items: flex-start; }
.lb-html-artifact .items-center { align-items: center; }
.lb-html-artifact .items-end { align-items: flex-end; }
.lb-html-artifact .justify-start { justify-content: flex-start; }
.lb-html-artifact .justify-center { justify-content: center; }
.lb-html-artifact .justify-between { justify-content: space-between; }
.lb-html-artifact .justify-end { justify-content: flex-end; }

.lb-html-artifact .gap-1 { gap: 0.25rem; }
.lb-html-artifact .gap-2 { gap: 0.5rem; }
.lb-html-artifact .gap-3 { gap: 0.75rem; }
.lb-html-artifact .gap-4 { gap: 1rem; }
.lb-html-artifact .gap-6 { gap: 1.5rem; }

.lb-html-artifact .grid-cols-2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.lb-html-artifact .grid-cols-3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.lb-html-artifact .grid-cols-4 { grid-template-columns: repeat(4, minmax(0, 1fr)); }

/* ────────────────────────────────────────────────────────────────────────
   Utility subset — spacing
   ──────────────────────────────────────────────────────────────────────── */
.lb-html-artifact .p-0 { padding: 0; }
.lb-html-artifact .p-1 { padding: 0.25rem; }
.lb-html-artifact .p-2 { padding: 0.5rem; }
.lb-html-artifact .p-3 { padding: 0.75rem; }
.lb-html-artifact .p-4 { padding: 1rem; }
.lb-html-artifact .p-6 { padding: 1.5rem; }
.lb-html-artifact .px-2 { padding-left: 0.5rem; padding-right: 0.5rem; }
.lb-html-artifact .px-3 { padding-left: 0.75rem; padding-right: 0.75rem; }
.lb-html-artifact .px-4 { padding-left: 1rem; padding-right: 1rem; }
.lb-html-artifact .py-1 { padding-top: 0.25rem; padding-bottom: 0.25rem; }
.lb-html-artifact .py-2 { padding-top: 0.5rem; padding-bottom: 0.5rem; }
.lb-html-artifact .py-3 { padding-top: 0.75rem; padding-bottom: 0.75rem; }

.lb-html-artifact .m-0 { margin: 0; }
.lb-html-artifact .mt-1 { margin-top: 0.25rem; }
.lb-html-artifact .mt-2 { margin-top: 0.5rem; }
.lb-html-artifact .mt-4 { margin-top: 1rem; }
.lb-html-artifact .mb-1 { margin-bottom: 0.25rem; }
.lb-html-artifact .mb-2 { margin-bottom: 0.5rem; }
.lb-html-artifact .mb-4 { margin-bottom: 1rem; }
.lb-html-artifact .mr-2 { margin-right: 0.5rem; }
.lb-html-artifact .ml-2 { margin-left: 0.5rem; }

/* ────────────────────────────────────────────────────────────────────────
   Utility subset — chrome
   ──────────────────────────────────────────────────────────────────────── */
.lb-html-artifact .rounded { border-radius: 0.25rem; }
.lb-html-artifact .rounded-md { border-radius: 0.375rem; }
.lb-html-artifact .rounded-lg { border-radius: 0.5rem; }
.lb-html-artifact .rounded-xl { border-radius: 0.75rem; }
.lb-html-artifact .rounded-full { border-radius: 9999px; }

.lb-html-artifact .border { border-width: 1px; border-style: solid; border-color: #e5e7eb; }
.lb-html-artifact .border-2 { border-width: 2px; border-style: solid; border-color: #e5e7eb; }
.lb-html-artifact .border-gray-200 { border-color: #e5e7eb; }
.lb-html-artifact .border-gray-300 { border-color: #d1d5db; }
.lb-html-artifact .border-blue-200 { border-color: #bfdbfe; }
.lb-html-artifact .border-blue-300 { border-color: #93c5fd; }

.lb-html-artifact .bg-white { background-color: #ffffff; }
.lb-html-artifact .bg-gray-50 { background-color: #f9fafb; }
.lb-html-artifact .bg-gray-100 { background-color: #f3f4f6; }
.lb-html-artifact .bg-blue-50 { background-color: #eff6ff; }
.lb-html-artifact .bg-blue-100 { background-color: #dbeafe; }
.lb-html-artifact .bg-green-50 { background-color: #f0fdf4; }
.lb-html-artifact .bg-amber-50 { background-color: #fffbeb; }
.lb-html-artifact .bg-emerald-50 { background-color: #ecfdf5; }
.lb-html-artifact .bg-red-50 { background-color: #fef2f2; }

.lb-html-artifact .shadow-sm { box-shadow: 0 1px 2px 0 rgba(0,0,0,0.05); }
.lb-html-artifact .shadow { box-shadow: 0 1px 3px 0 rgba(0,0,0,0.1), 0 1px 2px 0 rgba(0,0,0,0.06); }
.lb-html-artifact .shadow-md { box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1), 0 2px 4px -1px rgba(0,0,0,0.06); }

.lb-html-artifact .w-full { width: 100%; }
.lb-html-artifact .w-6 { width: 1.5rem; }
.lb-html-artifact .w-24 { width: 6rem; }
/* Fractional widths — added 2026-06-08 for inline consensus-cluster bar
   charts. Bucketed twelfths cover any pct → class mapping cleanly. */
.lb-html-artifact .w-1\\/12 { width: 8.333333%; }
.lb-html-artifact .w-2\\/12 { width: 16.666667%; }
.lb-html-artifact .w-3\\/12 { width: 25%; }
.lb-html-artifact .w-4\\/12 { width: 33.333333%; }
.lb-html-artifact .w-5\\/12 { width: 41.666667%; }
.lb-html-artifact .w-6\\/12 { width: 50%; }
.lb-html-artifact .w-7\\/12 { width: 58.333333%; }
.lb-html-artifact .w-8\\/12 { width: 66.666667%; }
.lb-html-artifact .w-9\\/12 { width: 75%; }
.lb-html-artifact .w-10\\/12 { width: 83.333333%; }
.lb-html-artifact .w-11\\/12 { width: 91.666667%; }
.lb-html-artifact .h-2 { height: 0.5rem; }
.lb-html-artifact .overflow-hidden { overflow: hidden; }
.lb-html-artifact .truncate { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lb-html-artifact .flex { display: flex; }
.lb-html-artifact .flex-1 { flex: 1 1 0%; }
.lb-html-artifact .items-center { align-items: center; }
.lb-html-artifact .gap-2 { gap: 0.5rem; }
.lb-html-artifact .text-right { text-align: right; }
.lb-html-artifact .bg-blue-100 { background-color: #dbeafe; }
.lb-html-artifact .bg-blue-500 { background-color: #3b82f6; }
.lb-html-artifact .max-w-md { max-width: 28rem; }
.lb-html-artifact .max-w-lg { max-width: 32rem; }
.lb-html-artifact .max-w-xl { max-width: 36rem; }

.lb-html-artifact img { max-width: 100%; height: auto; }

/* ────────────────────────────────────────────────────────────────────────
   Dark mode — K2 (2026-06-09): driven by the explicit .lb-dark class
   the host sets at mount time based on the app html.dark state, NOT
   by OS prefers-color-scheme. The OS-based variant drifted from the
   app theme and left users staring at unreadable text.
   ──────────────────────────────────────────────────────────────────────── */
.lb-html-artifact.lb-dark {
  color: #e5e7eb;
  background-color: #1f2937;
}
.lb-html-artifact.lb-dark code,
.lb-html-artifact.lb-dark pre { background: rgba(255,255,255,0.08); }
.lb-html-artifact.lb-dark th,
.lb-html-artifact.lb-dark td { border-bottom-color: #374151; }
.lb-html-artifact.lb-dark th { background: rgba(255,255,255,0.04); }
.lb-html-artifact.lb-dark hr { border-top-color: #374151; }
.lb-html-artifact.lb-dark blockquote { border-left-color: #4b5563; color: #9ca3af; }

.lb-html-artifact.lb-dark .text-gray-400 { color: #6b7280; }
.lb-html-artifact.lb-dark .text-gray-500 { color: #9ca3af; }
.lb-html-artifact.lb-dark .text-gray-600 { color: #d1d5db; }
.lb-html-artifact.lb-dark .text-gray-700 { color: #e5e7eb; }
.lb-html-artifact.lb-dark .text-gray-800 { color: #f3f4f6; }
.lb-html-artifact.lb-dark .text-gray-900 { color: #f9fafb; }

.lb-html-artifact.lb-dark .border,
.lb-html-artifact.lb-dark .border-gray-200,
.lb-html-artifact.lb-dark .border-gray-300 { border-color: #374151; }

.lb-html-artifact.lb-dark .bg-white { background-color: #1f2937; }
.lb-html-artifact.lb-dark .bg-gray-50 { background-color: #111827; }
.lb-html-artifact.lb-dark .bg-gray-100 { background-color: #1f2937; }
.lb-html-artifact.lb-dark .bg-blue-50 { background-color: #1e3a8a; }
.lb-html-artifact.lb-dark .bg-blue-100 { background-color: #1e40af; }
.lb-html-artifact.lb-dark .bg-green-50 { background-color: #064e3b; }
.lb-html-artifact.lb-dark .bg-amber-50 { background-color: #78350f; }
.lb-html-artifact.lb-dark .bg-emerald-50 { background-color: #064e3b; }
.lb-html-artifact.lb-dark .bg-red-50 { background-color: #7f1d1d; }
`;
