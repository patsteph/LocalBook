import { useCallback, useEffect, useRef, useState } from 'react';
import { onEvent } from '../lib/events';
import { api } from '../services/api';
import { EncryptionWizard } from './encryption/EncryptionWizard';

/**
 * The startup prompt for encryption at rest (LB-11).
 *
 * The one road into the setup wizard for every user — the developer first. It
 * never migrates anything itself: the recovery phrase and the backup
 * destination need a person, and D11 turns encryption on one Mac at a time, by
 * choice. The backend decides what (if anything) to show; see
 * `encryption_setup.prompt()`.
 *
 * Owns the wizard (simplified flow): the banner opens it, Settings › Encryption
 * asks for it with `lb:openEncryptionWizard`, and after the switch it opens on
 * its own at the last step, so a Mac with a phrase and a backup folder already
 * set is three clicks end to end.
 */

type Prompt =
    | { show: false }
    | { show: true; kind: 'offer' }
    | { show: true; kind: 'finish'; bytes: number; verified?: { state: string; ok?: boolean } | null }
    | { show: true; kind: 'failed'; error?: string; at?: string };

function mb(bytes: number): string {
    return bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(1)} GB` : `${Math.round(bytes / 1024 ** 2)} MB`;
}

export function EncryptionPrompt() {
    const [prompt, setPrompt] = useState<Prompt | null>(null);
    const [wizardOpen, setWizardOpen] = useState(false);
    const autoOpened = useRef(false);

    const load = useCallback(async () => {
        try {
            const { data } = await api.get<Prompt>('/system/volume/prompt');
            setPrompt(data);
        } catch {
            setPrompt(null);   // an old backend or a transient error — just show nothing
        }
    }, []);

    useEffect(() => { void load(); }, [load]);
    useEffect(() => onEvent('lb:openEncryptionWizard', () => setWizardOpen(true)), []);

    // The switch just happened: land on the last step without a click. Once per launch.
    useEffect(() => {
        if (prompt?.show && prompt.kind === 'finish' && !autoOpened.current) {
            autoOpened.current = true;
            setWizardOpen(true);
        }
    }, [prompt]);

    const closeWizard = () => { setWizardOpen(false); void load(); };
    const wizard = <EncryptionWizard isOpen={wizardOpen} onClose={closeWizard} />;

    const respond = async (action: 'snooze' | 'dismiss' | 'acknowledge_failure') => {
        try {
            const { data } = await api.post<Prompt>('/system/volume/prompt', { action });
            setPrompt(data);
        } catch {
            setPrompt(null);
        }
    };

    if (!prompt || !prompt.show) return wizard;

    const open = () => setWizardOpen(true);
    const button = 'rounded px-2.5 py-1 text-xs font-medium';

    if (prompt.kind === 'failed') {
        return (
            <>{wizard}
            <div className="mx-4 mt-2 mb-1 flex flex-shrink-0 items-center justify-between gap-3 rounded-lg border border-amber-300 bg-amber-50 px-4 py-2.5 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-900/30 dark:text-amber-100">
                <span>
                    <strong>Encryption was not switched on, and your data was not changed.</strong>{' '}
                    {prompt.error}
                </span>
                <div className="flex shrink-0 gap-2">
                    <button onClick={open} className={`${button} bg-amber-600 text-white hover:bg-amber-500`}>Details</button>
                    <button onClick={() => void respond('acknowledge_failure')} className={`${button} opacity-70 hover:opacity-100`}>OK</button>
                </div>
            </div>
            </>
        );
    }

    if (prompt.kind === 'finish') {
        return (
            <>{wizard}
            <div className="mx-4 mt-2 mb-1 flex flex-shrink-0 items-center justify-between gap-3 rounded-lg border border-amber-300 bg-amber-50 px-4 py-2.5 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-900/30 dark:text-amber-100">
                <span>
                    🔒 <strong>Your data is encrypted — one step left.</strong> The unencrypted copy from before
                    ({mb(prompt.bytes)}) is still on this Mac.
                    {prompt.verified?.ok === false && ' The check after the switch found differences, so it is kept.'}
                </span>
                <button onClick={open} className={`${button} shrink-0 bg-amber-600 text-white hover:bg-amber-500`}>
                    {prompt.verified?.ok === false ? 'Details' : 'Remove copy'}
                </button>
            </div>
            </>
        );
    }

    return (
        <>{wizard}
        <div className="mx-4 mt-2 mb-1 flex flex-shrink-0 items-center justify-between gap-3 rounded-lg border border-blue-200 bg-blue-50 px-4 py-2.5 text-xs text-blue-900 dark:border-blue-800/40 dark:bg-blue-900/20 dark:text-blue-100">
            <span>
                🔒 <strong>Encrypt your LocalBook data.</strong> Keeps notebooks, sources and chats unreadable on a
                lost Mac, a backup disk, or any copy that ends up somewhere it shouldn't.
            </span>
            <div className="flex shrink-0 gap-2">
                <button onClick={open} className={`${button} bg-blue-600 text-white hover:bg-blue-500`}>Encrypt</button>
                <button onClick={() => void respond('snooze')} className={`${button} hover:bg-blue-100 dark:hover:bg-blue-900/40`}>Not now</button>
                <button onClick={() => void respond('dismiss')} className={`${button} opacity-60 hover:opacity-100`}>Don't ask again</button>
            </div>
        </div>
        </>
    );
}
