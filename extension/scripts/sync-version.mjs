#!/usr/bin/env node
/**
 * Copy the app's version into extension/package.json before Plasmo builds.
 *
 * Plasmo takes the built manifest's `version` from extension/package.json and
 * nowhere else, so that field is what Chrome shows in chrome://extensions. It
 * was bumped by hand, in release.sh, inside a guard that skipped the whole
 * bump whenever the root package.json had already been bumped first — which is
 * how the v2.2.0 and v2.3.0 extension assets both shipped saying "2.1.1".
 *
 * release.sh is gitignored (it carries signing identities), so its fix does not
 * travel between machines. THIS script does: it runs as `prebuild`, so a build
 * from any checkout re-derives the version and a stale manifest cannot be
 * produced. The root package.json is the single source of truth.
 *
 * Rewrites only the first `"version"` line, so the file's formatting is left
 * exactly as it was.
 */
import { readFileSync, writeFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

const here = dirname(fileURLToPath(import.meta.url))
const extPkgPath = join(here, "..", "package.json")
const rootPkgPath = join(here, "..", "..", "package.json")

const rootVersion = JSON.parse(readFileSync(rootPkgPath, "utf8")).version
if (!rootVersion) {
  console.error("[sync-version] root package.json has no version — refusing to guess")
  process.exit(1)
}

const raw = readFileSync(extPkgPath, "utf8")
const extVersion = JSON.parse(raw).version

if (extVersion === rootVersion) {
  console.log(`[sync-version] extension already at ${rootVersion}`)
  process.exit(0)
}

// First "version" line only. `displayName` / dependency versions are untouched.
const updated = raw.replace(
  /("version"\s*:\s*)"[^"]*"/,
  (_m, prefix) => `${prefix}"${rootVersion}"`
)
if (JSON.parse(updated).version !== rootVersion) {
  console.error("[sync-version] rewrite did not take effect — aborting rather than building a stale manifest")
  process.exit(1)
}

writeFileSync(extPkgPath, updated)
console.log(`[sync-version] extension ${extVersion} → ${rootVersion}`)
