/**
 * CorrespondentSettings — Phase 6 of v2-information-cortex.
 *
 * Settings panel for the @correspondent IMAP agent. Add an inbox by app
 * password; the backend validates the IMAP login before persisting.
 *
 * Provider-specific app-password URLs are surfaced inline so users don't
 * have to hunt them down.
 */
import React, { useEffect, useState } from 'react';
import { Trash2, RefreshCw, CheckCircle, AlertCircle, Check, X } from 'lucide-react';
import { correspondentService, IMAPAccount, QueueItem, SubscriptionProposal } from '../services/correspondent';
import { notebookService } from '../services/notebooks';
import type { Notebook } from '../types';
import { openUrl } from '@tauri-apps/plugin-opener';

const PROVIDER_HINTS: { domain: string; label: string; host: string; appPwUrl: string }[] = [
  { domain: 'gmail.com', label: 'Gmail', host: 'imap.gmail.com', appPwUrl: 'https://myaccount.google.com/apppasswords' },
  { domain: 'fastmail.com', label: 'Fastmail', host: 'imap.fastmail.com', appPwUrl: 'https://www.fastmail.com/settings/security/tokens' },
  { domain: 'icloud.com', label: 'iCloud+', host: 'imap.mail.me.com', appPwUrl: 'https://account.apple.com/account/manage' },
  { domain: 'outlook.com', label: 'Outlook', host: 'outlook.office365.com', appPwUrl: 'https://account.microsoft.com/security' },
];

function detectProvider(email: string) {
  const dom = (email.split('@')[1] || '').toLowerCase();
  return PROVIDER_HINTS.find((p) => p.domain === dom);
}

export const CorrespondentSettings: React.FC = () => {
  const [accounts, setAccounts] = useState<IMAPAccount[]>([]);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [subscriptions, setSubscriptions] = useState<SubscriptionProposal[]>([]);
  const [notebooks, setNotebooks] = useState<Notebook[]>([]);
  const [loading, setLoading] = useState(false);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [busyItem, setBusyItem] = useState<string | null>(null);
  // F5a (2026-06-08) — per-queue-item override notebook so users can
  // redirect a mis-routed item before approving. Keyed by item_id.
  const [overrideNotebook, setOverrideNotebook] = useState<Record<string, string>>({});

  // Add-account form state.
  const [email, setEmail] = useState('');
  const [appPassword, setAppPassword] = useState('');
  const [host, setHost] = useState('');
  const [port, setPort] = useState(993);
  // Phase 8 — SMTP + forward-confirmation prefs.
  const [smtpHost, setSmtpHost] = useState('');
  const [smtpPort, setSmtpPort] = useState(465);
  const [sendConfirmations, setSendConfirmations] = useState(true);

  const provider = detectProvider(email);

  useEffect(() => {
    if (provider && !host) setHost(provider.host);
  }, [provider]); // eslint-disable-line react-hooks/exhaustive-deps

  // Phase 8 — fetch SMTP defaults from the backend when an email is typed.
  useEffect(() => {
    if (!email || smtpHost) return;
    let cancelled = false;
    correspondentService.smtpHint(email).then((hint) => {
      if (cancelled || !hint.found) return;
      if (hint.host) setSmtpHost(hint.host);
      if (typeof hint.port === 'number') setSmtpPort(hint.port);
    }).catch(() => { /* non-fatal */ });
    return () => { cancelled = true; };
  }, [email]); // eslint-disable-line react-hooks/exhaustive-deps

  const load = async () => {
    setLoading(true);
    try {
      const [list, q, subs, nbs] = await Promise.all([
        correspondentService.listAccounts(),
        correspondentService.listQueue().catch(() => []),
        correspondentService.listSubscriptionQueue().catch(() => []),
        notebookService.list().catch(() => []),
      ]);
      setAccounts(list);
      setQueue(q);
      setSubscriptions(subs);
      setNotebooks(nbs);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load accounts');
    } finally {
      setLoading(false);
    }
  };

  const handleSubscribeApprove = async (item: SubscriptionProposal) => {
    setBusyItem(item.id);
    try {
      await correspondentService.approveSubscription(item.id);
      setSuccess(`Subscribed to ${item.title}.`);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Subscribe failed');
    } finally {
      setBusyItem(null);
    }
  };

  const handleSubscribeDismiss = async (item: SubscriptionProposal) => {
    setBusyItem(item.id);
    try {
      await correspondentService.dismissSubscription(item.id);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Dismiss failed');
    } finally {
      setBusyItem(null);
    }
  };

  const handleApprove = async (item: QueueItem) => {
    setBusyItem(item.item_id);
    try {
      // F5a (2026-06-08) — pass override notebook if user picked one.
      // Backend's approve_queued accepts an optional notebook_id; falls
      // back to top_candidate when not supplied. Backend also records
      // the sender → notebook signal so future emails from same sender
      // bias toward this choice (F5b).
      const override = overrideNotebook[item.item_id];
      await correspondentService.approveQueueItem(item.item_id, override || undefined);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Approve failed');
    } finally {
      setBusyItem(null);
    }
  };

  const handleDismiss = async (item: QueueItem) => {
    setBusyItem(item.item_id);
    try {
      await correspondentService.dismissQueueItem(item.item_id);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Dismiss failed');
    } finally {
      setBusyItem(null);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const handleAdd = async () => {
    setError(null);
    setSuccess(null);
    if (!email || !appPassword) {
      setError('Email and app password are required.');
      return;
    }
    setAdding(true);
    try {
      const finalHost = host || (provider ? provider.host : '');
      if (!finalHost) {
        setError('IMAP host is required (we could not auto-detect from the email domain).');
        return;
      }
      await correspondentService.addAccount({
        email,
        imap_host: finalHost,
        imap_port: port,
        imap_password: appPassword,
        use_ssl: true,
        smtp_host: smtpHost || undefined,
        smtp_port: smtpPort,
        smtp_use_tls: true,
        send_confirmations: sendConfirmations,
      });
      setSuccess(`${email} connected.`);
      setEmail('');
      setAppPassword('');
      setHost('');
      setPort(993);
      setSmtpHost('');
      setSmtpPort(465);
      setSendConfirmations(true);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Add failed');
    } finally {
      setAdding(false);
    }
  };

  const handleDelete = async (acc: IMAPAccount) => {
    if (!window.confirm(`Remove ${acc.email}? Correspondent will stop polling this inbox.`)) return;
    try {
      await correspondentService.deleteAccount(acc.email);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed');
    }
  };

  const handleSync = async () => {
    setError(null);
    setSuccess(null);
    try {
      const { summary } = await correspondentService.sync();
      const t = summary.totals;
      setSuccess(`Synced: ${t.ingested} ingested, ${t.queued} queued, ${t.personal} personal, ${t.transactional} transactional.`);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Sync failed');
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-base font-semibold text-gray-800 dark:text-gray-100 flex items-center gap-2">
          📬 Correspondent — Email ingestion
        </h3>
        <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
          Forward newsletters to a dedicated inbox (or use your real one — Correspondent only ingests messages it classifies as newsletters; personal email is moved to a <code>LocalBook/Personal</code> folder and never read by the LLM).
        </p>
      </div>

      {error && (
        <div className="p-3 rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/20 text-sm text-red-700 dark:text-red-400">
          {error}
        </div>
      )}
      {success && (
        <div className="p-3 rounded-lg border border-green-200 dark:border-green-800 bg-green-50 dark:bg-green-900/20 text-sm text-green-700 dark:text-green-400 flex items-center gap-2">
          <CheckCircle className="w-4 h-4" /> {success}
        </div>
      )}

      {/* Add-account form */}
      <div className="rounded-xl border border-gray-200 dark:border-gray-700 p-4 space-y-3 bg-white dark:bg-gray-800">
        <h4 className="text-sm font-semibold text-gray-800 dark:text-gray-200">Add an inbox</h4>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="block text-[10px] uppercase tracking-wide text-gray-500 mb-1">Email</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              className="w-full px-2 py-1 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100"
            />
          </div>
          <div>
            <label className="block text-[10px] uppercase tracking-wide text-gray-500 mb-1">App password</label>
            <input
              type="password"
              value={appPassword}
              onChange={(e) => setAppPassword(e.target.value)}
              placeholder="provider-issued app password"
              className="w-full px-2 py-1 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100"
            />
          </div>
          <div>
            <label className="block text-[10px] uppercase tracking-wide text-gray-500 mb-1">IMAP host</label>
            <input
              type="text"
              value={host}
              onChange={(e) => setHost(e.target.value)}
              placeholder={provider ? provider.host : 'imap.example.com'}
              className="w-full px-2 py-1 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100"
            />
          </div>
          <div>
            <label className="block text-[10px] uppercase tracking-wide text-gray-500 mb-1">Port</label>
            <input
              type="number"
              value={port}
              onChange={(e) => setPort(parseInt(e.target.value) || 993)}
              className="w-full px-2 py-1 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100"
            />
          </div>
        </div>
        {provider && (
          <p className="text-xs text-gray-500 dark:text-gray-400 flex items-center gap-1">
            <span>{provider.label} app password →</span>
            <button
              onClick={() => openUrl(provider.appPwUrl).catch(() => window.open(provider.appPwUrl, '_blank'))}
              className="underline text-blue-600 dark:text-blue-400"
            >
              {provider.appPwUrl}
            </button>
          </p>
        )}

        {/* Phase 8 — SMTP outbound + forward confirmation reply settings */}
        <details className="text-xs">
          <summary className="cursor-pointer text-gray-600 dark:text-gray-300">Outbound mail (for forward confirmations)</summary>
          <div className="grid grid-cols-2 gap-2 mt-2">
            <div>
              <label className="block text-[10px] uppercase tracking-wide text-gray-500 mb-1">SMTP host</label>
              <input
                type="text"
                value={smtpHost}
                onChange={(e) => setSmtpHost(e.target.value)}
                placeholder="smtp.example.com"
                className="w-full px-2 py-1 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100"
              />
            </div>
            <div>
              <label className="block text-[10px] uppercase tracking-wide text-gray-500 mb-1">SMTP port</label>
              <input
                type="number"
                value={smtpPort}
                onChange={(e) => setSmtpPort(parseInt(e.target.value) || 465)}
                className="w-full px-2 py-1 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100"
              />
            </div>
          </div>
          <label className="inline-flex items-center gap-1.5 mt-2 text-[11px] text-gray-700 dark:text-gray-300 cursor-pointer">
            <input
              type="checkbox"
              checked={sendConfirmations}
              onChange={(e) => setSendConfirmations(e.target.checked)}
              className="rounded border-gray-300 dark:border-gray-600"
            />
            Send a confirmation reply when a forwarded email is ingested
          </label>
        </details>

        <button
          onClick={handleAdd}
          disabled={adding}
          className="px-3 py-1.5 text-sm rounded-lg bg-amber-600 hover:bg-amber-700 text-white disabled:opacity-50"
        >
          {adding ? 'Validating…' : 'Connect'}
        </button>
      </div>

      {/* Existing accounts */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <h4 className="text-sm font-semibold text-gray-800 dark:text-gray-200">Connected inboxes</h4>
          <button
            onClick={handleSync}
            disabled={loading || accounts.length === 0}
            className="text-xs flex items-center gap-1 px-2 py-1 rounded-lg border border-gray-200 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50"
          >
            <RefreshCw className="w-3 h-3" /> Sync now
          </button>
        </div>
        {accounts.length === 0 ? (
          <p className="text-xs italic text-gray-500 dark:text-gray-400">No inboxes connected yet.</p>
        ) : (
          <div className="space-y-2">
            {accounts.map((a) => (
              <div key={a.email} className="rounded-lg border border-gray-200 dark:border-gray-700 p-3 bg-white dark:bg-gray-800">
                <div className="flex items-center justify-between">
                  <div className="flex flex-col">
                    <span className="text-sm font-medium text-gray-800 dark:text-gray-100">{a.email}</span>
                    <span className="text-xs text-gray-500 dark:text-gray-400">
                      {a.imap_host}:{a.imap_port} · last polled: {a.last_polled_at || '(never)'}
                    </span>
                  </div>
                  <button
                    onClick={() => handleDelete(a)}
                    className="text-gray-400 hover:text-red-600 dark:hover:text-red-400"
                    title="Remove inbox"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
                {/* Phase 13 — weekly auto-journal toggle. Backend scheduler
                    composes a Sunday evening "what you learned this week"
                    HTML email per enabled account. Default on; user opts
                    out per-inbox here. */}
                <label className="mt-3 flex items-center gap-2 text-xs text-gray-700 dark:text-gray-300 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={a.weekly_journal_enabled !== false}
                    onChange={async (e) => {
                      const next = e.target.checked;
                      try {
                        await correspondentService.updateAccount(a.email, { weekly_journal_enabled: next });
                        await load();
                      } catch (err) {
                        console.error('[CorrespondentSettings] toggle weekly journal failed:', err);
                      }
                    }}
                    className="rounded border-gray-300 dark:border-gray-600 text-amber-600 focus:ring-amber-500"
                  />
                  <span>Send me a weekly journal of what I learned</span>
                </label>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Approval queue */}
      <div>
        <h4 className="text-sm font-semibold text-gray-800 dark:text-gray-200 mb-2">Pending approvals</h4>
        {queue.length === 0 ? (
          <p className="text-xs italic text-gray-500 dark:text-gray-400">
            No items waiting. Newsletters whose best-fit notebook scores below the routing threshold land here.
          </p>
        ) : (
          <div className="space-y-2">
            {queue.map((q) => (
              <div key={q.item_id} className="rounded-lg border border-gray-200 dark:border-gray-700 p-3 bg-white dark:bg-gray-800">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      {q.kind === 'forward' && (
                        <span className="text-[10px] uppercase tracking-wide text-amber-700 bg-amber-100 dark:bg-amber-900/40 rounded px-1.5 py-0.5">
                          forward
                        </span>
                      )}
                      <p className="text-sm font-medium text-gray-800 dark:text-gray-100 truncate">{q.subject}</p>
                    </div>
                    <p className="text-xs text-gray-500 dark:text-gray-400 truncate">from {q.sender}</p>
                    {q.summary && (
                      <p className="text-xs text-gray-600 dark:text-gray-300 mt-1 italic">{q.summary}</p>
                    )}
                    {q.top_candidate && (
                      <p className="text-xs text-gray-500 dark:text-gray-400 mt-2">
                        Best match: <span className="font-medium">{q.top_candidate.notebook_name}</span>{' '}
                        <span className="text-gray-400">({(q.top_candidate.confidence * 100).toFixed(0)}%)</span>
                        {q.decision_reason && <span className="text-gray-400"> · {q.decision_reason}</span>}
                      </p>
                    )}
                    {/* F5a (2026-06-08) — override picker so the user can
                        redirect to any notebook. Defaults to the top_candidate.
                        Approval below uses overrideNotebook[item_id] when set. */}
                    {notebooks.length > 0 && (
                      <div className="mt-2 flex items-center gap-2">
                        <label className="text-[10px] uppercase tracking-wide text-gray-500 dark:text-gray-400">
                          Route to
                        </label>
                        <select
                          value={overrideNotebook[q.item_id] ?? (q.top_candidate?.notebook_id || '')}
                          onChange={(e) =>
                            setOverrideNotebook((prev) => ({ ...prev, [q.item_id]: e.target.value }))
                          }
                          className="text-xs rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 px-2 py-1 text-gray-800 dark:text-gray-200 flex-1 min-w-0"
                        >
                          {!q.top_candidate && <option value="">— pick a notebook —</option>}
                          {notebooks.map((nb) => (
                            <option key={nb.id} value={nb.id}>
                              {nb.title}
                              {q.top_candidate?.notebook_id === nb.id ? ' (best match)' : ''}
                            </option>
                          ))}
                        </select>
                      </div>
                    )}
                  </div>
                  <div className="flex flex-col gap-1">
                    <button
                      onClick={() => handleApprove(q)}
                      disabled={
                        busyItem === q.item_id
                        || (!q.top_candidate && !overrideNotebook[q.item_id])
                      }
                      className="px-2 py-1 text-xs rounded-lg bg-green-600 hover:bg-green-700 text-white disabled:opacity-50 flex items-center gap-1"
                      title="Ingest into the selected notebook (Correspondent learns from this choice)"
                    >
                      <Check className="w-3 h-3" /> Approve
                    </button>
                    <button
                      onClick={() => handleDismiss(q)}
                      disabled={busyItem === q.item_id}
                      className="px-2 py-1 text-xs rounded-lg bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-300 disabled:opacity-50 flex items-center gap-1"
                    >
                      <X className="w-3 h-3" /> Dismiss
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Subscription proposals (Phase 7) */}
      <div>
        <h4 className="text-sm font-semibold text-gray-800 dark:text-gray-200 mb-2">Suggested subscriptions</h4>
        {subscriptions.length === 0 ? (
          <p className="text-xs italic text-gray-500 dark:text-gray-400">
            Newsletters mentioned inside ingested newsletters will surface here for one-click subscription.
          </p>
        ) : (
          <div className="space-y-2">
            {subscriptions.map((s) => {
              const isEntity = s.kind === 'entity';
              return (
                <div
                  key={s.id}
                  className={`rounded-lg border p-3 ${
                    isEntity
                      ? 'border-purple-200 dark:border-purple-800 bg-purple-50/40 dark:bg-purple-900/10'
                      : 'border-amber-200 dark:border-amber-800 bg-amber-50/40 dark:bg-amber-900/10'
                  }`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        {isEntity ? (
                          <>
                            <span className="text-[10px] uppercase tracking-wide text-purple-700 bg-purple-100 dark:bg-purple-900/40 rounded px-1.5 py-0.5">
                              entity watch
                            </span>
                            <span className="text-[10px] uppercase tracking-wide text-gray-500 bg-gray-100 dark:bg-gray-800 rounded px-1.5 py-0.5">
                              {s.entity_type}
                            </span>
                          </>
                        ) : (
                          <>
                            <span className="text-[10px] uppercase tracking-wide text-amber-700 bg-amber-100 dark:bg-amber-900/40 rounded px-1.5 py-0.5">
                              {s.kind_label}
                            </span>
                            <span className="text-[10px] uppercase tracking-wide text-gray-500 bg-gray-100 dark:bg-gray-800 rounded px-1.5 py-0.5">
                              {s.source_type}
                            </span>
                          </>
                        )}
                      </div>
                      <p className="text-sm font-medium text-gray-800 dark:text-gray-100 truncate mt-1">{s.title}</p>
                      {isEntity ? (
                        <>
                          {s.source_email?.summary && (
                            <p className="text-xs text-gray-600 dark:text-gray-300 mt-1 italic line-clamp-2">
                              {s.source_email.summary}
                            </p>
                          )}
                          {s.source_email?.sender && (
                            <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                              mentioned by {s.source_email.sender}
                            </p>
                          )}
                        </>
                      ) : (
                        <>
                          <p className="text-xs text-gray-500 dark:text-gray-400 truncate">
                            {s.feed_url || s.url}
                          </p>
                          {s.source_email?.subject && (
                            <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                              mentioned in <em>{s.source_email.subject}</em>
                              {s.source_email.sender && <span> · from {s.source_email.sender}</span>}
                            </p>
                          )}
                        </>
                      )}
                    </div>
                    <div className="flex flex-col gap-1">
                      <button
                        onClick={() => handleSubscribeApprove(s)}
                        disabled={busyItem === s.id}
                        className={`px-2 py-1 text-xs rounded-lg text-white disabled:opacity-50 flex items-center gap-1 ${
                          isEntity ? 'bg-purple-600 hover:bg-purple-700' : 'bg-amber-600 hover:bg-amber-700'
                        }`}
                        title={isEntity ? 'Track this entity — adds a watch source to the notebook' : 'Subscribe — adds this feed to the suggested notebook\'s Collector'}
                      >
                        <Check className="w-3 h-3" /> {isEntity ? 'Track' : 'Subscribe'}
                      </button>
                      <button
                        onClick={() => handleSubscribeDismiss(s)}
                        disabled={busyItem === s.id}
                        className="px-2 py-1 text-xs rounded-lg bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-300 disabled:opacity-50 flex items-center gap-1"
                      >
                        <X className="w-3 h-3" /> Dismiss
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div className="p-3 rounded-lg bg-gray-50 dark:bg-gray-900/40 border border-gray-200 dark:border-gray-700 text-xs text-gray-600 dark:text-gray-300">
        <AlertCircle className="w-3 h-3 inline mr-1 text-amber-600" />
        Correspondent uses only app passwords (no OAuth in v1). All credentials are encrypted at rest in your local credential locker; nothing is sent to a cloud server.
      </div>
    </div>
  );
};

export default CorrespondentSettings;
