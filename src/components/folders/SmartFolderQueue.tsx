/**
 * The Smart Folder review queue — where a human decides where a recording goes.
 *
 * The card is built around one idea: the user is being asked to confirm a
 * GUESS, so it has to show them what the guess is made of. Who we think was in
 * the recording, HOW we know that (frontmatter is not the same kind of claim as
 * a filename), what it seems to be about, and how sure we are. A card that just
 * said "file this in Sarah 1:1?" would be asking for trust it hasn't earned.
 *
 * Three actions, per the agreed design:
 *
 *   Approve                    — this one, this time.
 *   Choose another…            — the correction. Teaches the suggestion.
 *   Approve + always route…    — the user writes a RULE, choosing its scope.
 *
 * That third button is the whole safety model made clickable. Auto-routing is
 * never unlocked by a confidence threshold; it is granted by the user, scoped
 * to a pattern they picked and can read back later in Settings.
 */
import { useCallback, useEffect, useState } from 'react';
import {
  approvePending,
  dismissPending,
  listPending,
  type PendingItem,
  type PendingList,
  type RuleScope,
} from '../../services/folders';

interface Props {
  notebookId?: string;
  onResolved?: () => void;
}

function confidenceLabel(c: number): { text: string; tone: string } {
  if (c >= 0.8) return { text: 'strong match', tone: 'text-green-600 dark:text-green-400' };
  if (c >= 0.6) return { text: 'likely match', tone: 'text-blue-600 dark:text-blue-400' };
  if (c > 0) return { text: 'weak match', tone: 'text-amber-600 dark:text-amber-400' };
  return { text: 'no match', tone: 'text-gray-500 dark:text-gray-400' };
}

function Card({ item, notebooks, onDone }: {
  item: PendingItem;
  notebooks: Array<{ id: string; title: string }>;
  onDone: () => void;
}) {
  const [choosing, setChoosing] = useState(false);
  const [ruleMenu, setRuleMenu] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [target, setTarget] = useState(item.suggested_id || '');

  const conf = confidenceLabel(item.confidence);
  const people = item.participants;

  const act = async (notebookId: string | null, ruleScope: RuleScope | null) => {
    setBusy(true);
    setError(null);
    try {
      await approvePending(item.id, { notebook_id: notebookId, rule_scope: ruleScope });
      onDone();
    } catch (e: any) {
      setError(e?.message || 'Could not file this recording.');
      setBusy(false);
    }
  };

  const drop = async () => {
    setBusy(true);
    try {
      await dismissPending(item.id);
      onDone();
    } catch (e: any) {
      setError(e?.message || 'Failed');
      setBusy(false);
    }
  };

  const ruleOptions: Array<{ scope: RuleScope; label: string }> = [];
  if (people.length) {
    ruleOptions.push({
      scope: 'participants',
      label: `recordings with ${people.join(' and ')}`,
    });
  }
  if (item.topics.length) {
    ruleOptions.push({
      scope: 'topics',
      label: `recordings about ${item.topics.slice(0, 3).join(', ')}`,
    });
  }
  if (people.length && item.topics.length) {
    ruleOptions.push({
      scope: 'both',
      label: `recordings with ${people.join(' and ')} about ${item.topics.slice(0, 2).join(', ')}`,
    });
  }

  return (
    <div className="rounded-lg border border-gray-200 dark:border-gray-700 px-4 py-3">
      <div className="flex items-start gap-2">
        <span className="text-base leading-none mt-0.5">📄</span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate"
             title={item.abs_path}>
            {item.filename}
          </p>

          {/* What the guess is made of. */}
          <p className="mt-1 text-xs text-gray-600 dark:text-gray-300">
            {people.length > 0
              ? <><span className="font-medium">{people.join(', ')}</span></>
              : <span className="text-gray-400">No participants identified</span>}
            {item.summary && <> · {item.summary}</>}
          </p>
          {item.topics.length > 0 && (
            <div className="mt-1.5 flex gap-1 flex-wrap">
              {item.topics.slice(0, 5).map((t) => (
                <span key={t}
                      className="px-1.5 py-0.5 text-[10px] rounded bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-400">
                  {t}
                </span>
              ))}
            </div>
          )}

          <p className="mt-2 text-xs">
            {item.suggested_name ? (
              <>
                <span className="text-gray-500 dark:text-gray-400">Suggested: </span>
                <span className="font-medium text-gray-900 dark:text-gray-100">
                  {item.suggested_name}
                </span>
                <span className={`ml-1.5 ${conf.tone}`}>({conf.text})</span>
              </>
            ) : (
              <span className="text-amber-700 dark:text-amber-400">
                No notebook looks like a fit — pick one, or create a notebook first.
              </span>
            )}
          </p>

          {error && (
            <p className="mt-1.5 text-xs text-red-600 dark:text-red-400">{error}</p>
          )}

          {/* Chooser — the correction path. */}
          {choosing && (
            <div className="mt-2 flex items-center gap-2">
              <select
                value={target}
                onChange={(e) => setTarget(e.target.value)}
                className="flex-1 px-2 py-1 text-xs rounded border dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
              >
                <option value="">Choose a notebook…</option>
                {notebooks.map((n) => (
                  <option key={n.id} value={n.id}>{n.title}</option>
                ))}
              </select>
              <button
                disabled={!target || busy}
                onClick={() => act(target, null)}
                className="px-2.5 py-1 text-xs rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40"
              >
                File here
              </button>
              <button onClick={() => setChoosing(false)}
                      className="px-2 py-1 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">
                Cancel
              </button>
            </div>
          )}

          {/* The rule menu — the user granting standing permission, scoped. */}
          {ruleMenu && (
            <div className="mt-2 rounded border dark:border-gray-700 divide-y dark:divide-gray-700">
              <p className="px-2.5 py-1.5 text-[11px] text-gray-500 dark:text-gray-400">
                File this one, and from now on file…
              </p>
              {ruleOptions.map((o) => (
                <button
                  key={o.scope}
                  disabled={busy}
                  onClick={() => act(target || item.suggested_id, o.scope)}
                  className="w-full text-left px-2.5 py-1.5 text-xs text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-40"
                >
                  {o.label}
                </button>
              ))}
              <button onClick={() => setRuleMenu(false)}
                      className="w-full text-left px-2.5 py-1.5 text-[11px] text-gray-500 hover:bg-gray-50 dark:hover:bg-gray-800">
                Cancel
              </button>
            </div>
          )}

          {/* Actions */}
          {!choosing && !ruleMenu && (
            <div className="mt-2.5 flex items-center gap-2 flex-wrap">
              <button
                disabled={busy || !item.suggested_id}
                onClick={() => act(item.suggested_id, null)}
                className="px-2.5 py-1 text-xs font-medium rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40"
              >
                {busy ? 'Filing…' : 'Approve'}
              </button>
              <button
                disabled={busy}
                onClick={() => setChoosing(true)}
                className="px-2.5 py-1 text-xs rounded border dark:border-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800"
              >
                Choose another…
              </button>
              {ruleOptions.length > 0 && item.suggested_id && (
                <button
                  disabled={busy}
                  onClick={() => setRuleMenu(true)}
                  className="px-2.5 py-1 text-xs rounded border border-purple-300 dark:border-purple-700 text-purple-700 dark:text-purple-300 hover:bg-purple-50 dark:hover:bg-purple-900/20"
                  title="File this one and grant standing permission for matching recordings"
                >
                  Approve + always route these ▾
                </button>
              )}
              <button
                disabled={busy}
                onClick={drop}
                className="ml-auto px-2 py-1 text-xs text-gray-400 hover:text-red-600"
                title="Leave this recording out. The file itself is untouched."
              >
                Not this one
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export function SmartFolderQueue({ notebookId, onResolved }: Props) {
  const [data, setData] = useState<PendingList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      setData(await listPending(notebookId));
      setError(null);
    } catch (e: any) {
      setError(e?.message || 'Could not load the review queue.');
    } finally {
      setLoading(false);
    }
  }, [notebookId]);

  useEffect(() => { void load(); }, [load]);

  const done = () => { void load(); onResolved?.(); };

  if (loading) return <p className="text-sm text-gray-500">Loading…</p>;
  if (error) {
    return (
      <div className="rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 px-3 py-2 text-sm text-red-700 dark:text-red-300">
        {error}
      </div>
    );
  }
  if (!data || data.items.length === 0) {
    return (
      <div className="rounded-lg border border-dashed dark:border-gray-700 px-4 py-6 text-center">
        <p className="text-sm text-gray-600 dark:text-gray-300">Nothing waiting for review.</p>
        <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
          Recordings from a Smart Folder appear here so you can say where they belong.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {/* The system noticing a relationship the user hasn't filed yet. */}
      {data.suggested_notebooks.length > 0 && (
        <div className="rounded-lg bg-purple-50 dark:bg-purple-900/15 border border-purple-200 dark:border-purple-800 px-3 py-2">
          {data.suggested_notebooks.slice(0, 2).map((s) => (
            <p key={s.participant} className="text-xs text-purple-800 dark:text-purple-300">
              <span className="font-medium">{s.count} unfiled recordings with {s.participant}.</span>{' '}
              A notebook for them would give these somewhere to go.
            </p>
          ))}
        </div>
      )}

      {data.items.map((item) => (
        <Card key={item.id} item={item} notebooks={data.notebooks} onDone={done} />
      ))}
    </div>
  );
}
