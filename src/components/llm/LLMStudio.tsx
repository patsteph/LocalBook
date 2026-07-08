import { useState } from 'react';
import { LLMSelector } from '../LLMSelector';
import { EvaluatorPanel } from './EvaluatorPanel';
import { EvalHistoryPanel } from './EvalHistoryPanel';

type StudioTab = 'locker' | 'evaluator' | 'history';

const TABS: { id: StudioTab; label: string }[] = [
  { id: 'locker',    label: '🧠 Locker' },
  { id: 'evaluator', label: '🧪 Evaluator' },
  { id: 'history',   label: '📊 History' },
];

interface LLMStudioProps {
  selectedProvider: string;
  onProviderChange: (provider: string) => void;
}

// The unified LLM management surface (rendered inside the App's <Modal>): pick a
// brain (Locker), benchmark it (Evaluator), and compare past runs (History) —
// one place, one flow. Replaces the old split of an in-app Locker modal + a
// browser-only evaluator page.
export function LLMStudio({ selectedProvider, onProviderChange }: LLMStudioProps) {
  const [tab, setTab] = useState<StudioTab>('locker');

  return (
    <div>
      <div className="sticky top-0 z-10 flex gap-1 px-4 pt-3 bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${
              tab === t.id
                ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'locker' && (
        <LLMSelector
          selectedProvider={selectedProvider}
          onProviderChange={onProviderChange}
          onTestCombo={() => setTab('evaluator')}
        />
      )}
      {tab === 'evaluator' && <EvaluatorPanel />}
      {tab === 'history' && <EvalHistoryPanel />}
    </div>
  );
}
