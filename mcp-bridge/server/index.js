#!/usr/bin/env node
/**
 * LocalBook MCP bridge: stdio <-> Streamable HTTP.
 *
 * LocalBook's MCP server runs inside the Mac app at http://127.0.0.1:8000/mcp
 * (Streamable HTTP, a companion key in `Authorization: Bearer`). Claude Desktop
 * starts local MCP servers as programs that talk over stdin/stdout, and does not
 * accept a URL in its config file. This file is that program. It
 * forwards every JSON-RPC message from stdin to LocalBook and writes every reply
 * (plain JSON or an SSE stream) to stdout. It does no other work.
 *
 * It depends on nothing outside Node itself (18+, which ships `fetch`), so the
 * Claude Desktop extension (manifest.json beside this folder) runs on the Node
 * that Claude Desktop bundles. Nothing has to be installed on the Mac.
 *
 * Environment:
 *   LOCALBOOK_MCP_KEY  the agent key from LocalBook > Settings > Companions (required)
 *   LOCALBOOK_MCP_URL  defaults to http://127.0.0.1:8000/mcp
 *
 * What it handles beyond copying bytes:
 *   - LocalBook restarted: the old session id now gets 404. The bridge replays
 *     the client's own `initialize` once, quietly, and retries the call, so
 *     Claude does not have to be restarted too.
 *   - LocalBook not open: requests get a JSON-RPC error that says so, instead
 *     of hanging.
 *   - Key wrong or missing a scope: LocalBook's own explanation is passed on.
 *
 * Logs go to stderr (Claude Desktop shows them in its MCP log); stdout carries
 * only protocol messages.
 */

'use strict';

const URL_ = process.env.LOCALBOOK_MCP_URL || 'http://127.0.0.1:8000/mcp';
const KEY = (process.env.LOCALBOOK_MCP_KEY || '').trim();
const INIT_WAIT_MS = 10_000;   // how long `initialize` keeps trying while LocalBook starts up

let sessionId = null;
let protocolVersion = null;
let initRequest = null;        // the client's initialize, kept to replay after a restart
let reinitializing = null;     // one replay at a time
let ready = null;              // resolves once the first initialize has been answered
let internalId = 0;

function log(...args) {
    process.stderr.write(`[localbook-bridge] ${args.join(' ')}\n`);
}

function write(message) {
    process.stdout.write(JSON.stringify(message) + '\n');
}

function isRequest(msg) {
    return msg && typeof msg === 'object' && 'method' in msg && 'id' in msg && msg.id !== null;
}

function replyError(msg, code, message) {
    if (isRequest(msg)) write({ jsonrpc: '2.0', id: msg.id, error: { code, message } });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function headers() {
    const h = {
        'Content-Type': 'application/json',
        Accept: 'application/json, text/event-stream',
        Authorization: `Bearer ${KEY}`,
    };
    if (sessionId) h['Mcp-Session-Id'] = sessionId;
    if (protocolVersion) h['MCP-Protocol-Version'] = protocolVersion;
    return h;
}

/** POST one message. Calls `onMessage` for each JSON-RPC message in the reply. */
async function post(body, onMessage) {
    const res = await fetch(URL_, { method: 'POST', headers: headers(), body: JSON.stringify(body) });
    const sid = res.headers.get('mcp-session-id');
    if (sid) sessionId = sid;

    if (res.status === 202 || res.status === 204) return res.status;
    if (!res.ok) {
        let detail = '';
        try {
            const text = await res.text();
            try { detail = JSON.parse(text).detail || JSON.parse(text).error?.message || text; } catch { detail = text; }
        } catch { /* body unreadable */ }
        const err = new Error(String(detail || `HTTP ${res.status}`).slice(0, 500));
        err.status = res.status;
        throw err;
    }

    const type = (res.headers.get('content-type') || '').toLowerCase();
    if (type.includes('text/event-stream')) {
        await readSSE(res.body, onMessage);
    } else {
        const text = await res.text();
        if (text.trim()) {
            const parsed = JSON.parse(text);
            for (const m of Array.isArray(parsed) ? parsed : [parsed]) onMessage(m);
        }
    }
    return res.status;
}

async function readSSE(stream, onMessage) {
    const decoder = new TextDecoder();
    let buffer = '';
    const flush = (block) => {
        let event = 'message';
        const data = [];
        for (const line of block.split('\n')) {
            if (line.startsWith('event:')) event = line.slice(6).trim();
            else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''));
        }
        if (event !== 'message' || data.length === 0) return;
        const text = data.join('\n');
        if (!text.trim()) return;
        try { onMessage(JSON.parse(text)); } catch (e) { log('unreadable event:', e.message); }
    };
    for await (const chunk of stream) {
        buffer += decoder.decode(chunk, { stream: true }).replace(/\r\n/g, '\n');
        let at;
        while ((at = buffer.indexOf('\n\n')) !== -1) {
            flush(buffer.slice(0, at));
            buffer = buffer.slice(at + 2);
        }
    }
    if (buffer.trim()) flush(buffer);
}

function unreachable(err) {
    return err && !err.status;   // fetch failed before any HTTP status: nothing is listening
}

function explain(err) {
    if (unreachable(err)) {
        return 'LocalBook is not running on this Mac. Open LocalBook and try again.';
    }
    if (err.status === 401) return `LocalBook did not accept the agent key: ${err.message}`;
    if (err.status === 403) return `LocalBook refused this connection: ${err.message}`;
    return `LocalBook returned an error: ${err.message}`;
}

async function initialize(msg) {
    initRequest = msg;
    const deadline = Date.now() + INIT_WAIT_MS;
    for (;;) {
        try {
            sessionId = null;
            protocolVersion = null;
            await post(msg, (reply) => {
                if (reply.id === msg.id && reply.result) protocolVersion = reply.result.protocolVersion || null;
                write(reply);
            });
            return;
        } catch (err) {
            if (unreachable(err) && Date.now() < deadline) {
                await sleep(1000);
                continue;
            }
            log('initialize failed:', err.message);
            replyError(msg, -32000, explain(err));
            return;
        }
    }
}

/** LocalBook restarted and forgot the session: start a new one, silently. */
async function reinitialize() {
    if (!initRequest) throw new Error('no session to resume');
    if (!reinitializing) {
        reinitializing = (async () => {
            sessionId = null;
            protocolVersion = null;
            const replay = { ...initRequest, id: `localbook-bridge-${++internalId}` };
            await post(replay, (reply) => {
                if (reply.id === replay.id && reply.result) protocolVersion = reply.result.protocolVersion || null;
                // swallowed: the client already has its initialize result
            });
            await post({ jsonrpc: '2.0', method: 'notifications/initialized' }, () => {});
            log('LocalBook restarted; resumed with a new session');
        })().finally(() => { reinitializing = null; });
    }
    return reinitializing;
}

async function forward(msg) {
    for (let attempt = 0; attempt < 2; attempt++) {
        try {
            if (reinitializing) await reinitializing;
            await post(msg, write);
            return;
        } catch (err) {
            if (attempt === 0 && (err.status === 404 || (err.status === 400 && /session/i.test(err.message))) && initRequest) {
                try { await reinitialize(); continue; } catch (e) { err = e; }
            }
            log(`${msg.method || 'reply'} failed:`, err.message);
            replyError(msg, -32000, explain(err));
            return;
        }
    }
}

function handle(msg) {
    if (msg && msg.method === 'initialize') {
        if (!KEY) {
            replyError(msg, -32000,
                'No LocalBook agent key is set. Issue one in LocalBook > Settings > Companions > Agent keys ' +
                '(tick "mcp"), then paste it into this extension\'s settings.');
            return;
        }
        ready = initialize(msg);
        return;
    }
    // Everything after initialize waits for it, then runs concurrently, so a long
    // ask_notebook does not hold up a cancel or a quick list_notebooks.
    Promise.resolve(ready).then(() => forward(msg));
}

let pending = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => {
    pending += chunk;
    let at;
    while ((at = pending.indexOf('\n')) !== -1) {
        const line = pending.slice(0, at).trim();
        pending = pending.slice(at + 1);
        if (!line) continue;
        let msg;
        try { msg = JSON.parse(line); } catch {
            write({ jsonrpc: '2.0', id: null, error: { code: -32700, message: 'Parse error' } });
            continue;
        }
        handle(msg);
    }
});

process.stdin.on('end', async () => {
    // Tell LocalBook the session is over (the spec's DELETE), then leave.
    if (sessionId) {
        try { await fetch(URL_, { method: 'DELETE', headers: headers() }); } catch { /* already gone */ }
    }
    process.exit(0);
});

log(`forwarding to ${URL_}`);
