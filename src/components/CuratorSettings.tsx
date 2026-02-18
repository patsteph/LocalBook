import React, { useState, useEffect } from 'react';
import { curatorService, CuratorConfig } from '../services/curatorApi';

interface Notebook {
  id: string;
  title: string;
  source_count: number;
}

interface CuratorSettingsProps {
  onClose?: () => void;
}

export const CuratorSettings: React.FC<CuratorSettingsProps> = ({ onClose }) => {
  const [config, setConfig] = useState<CuratorConfig | null>(null);
  const [notebooks, setNotebooks] = useState<Notebook[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const [name, setName] = useState('');
  const [personality, setPersonality] = useState('');
  const [overwatchEnabled, setOverwatchEnabled] = useState(true);
  const [excludedIds, setExcludedIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    try {
      const [cfg, nbs] = await Promise.all([
        curatorService.getConfig(),
        curatorService.getNotebooks(),
      ]);
      setConfig(cfg);
      setNotebooks(nbs);
      setName(cfg.name || '');
      setPersonality(cfg.personality || '');
      setOverwatchEnabled(cfg.oversight?.overwatch_enabled !== false);
      setExcludedIds(new Set(cfg.oversight?.excluded_notebook_ids || []));
    } catch (e) {
      setError('Failed to load curator config');
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    setSuccess(false);
    try {
      await curatorService.updateConfig({
        name: name.trim() || undefined,
        personality: personality.trim() || undefined,
        oversight: {
          overwatch_enabled: overwatchEnabled,
          excluded_notebook_ids: Array.from(excludedIds),
        },
      });
      setSuccess(true);
      setTimeout(() => setSuccess(false), 2000);
    } catch (e) {
      setError('Failed to save curator settings');
    } finally {
      setSaving(false);
    }
  };

  const toggleNotebook = (id: string) => {
    setExcludedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  if (!config) {
    return <div className="p-4 text-sm text-gray-400">Loading curator settings...</div>;
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-900 dark:text-white flex items-center gap-2">
          🧭 Curator Settings
        </h3>
        {onClose && (
          <button onClick={onClose} className="text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-300">✕</button>
        )}
      </div>

      {/* Curator Name */}
      <div>
        <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Curator Name</label>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Atlas"
          className="w-full px-3 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-purple-500"
        />
        <p className="mt-0.5 text-xs text-gray-400">Appears on curator responses in chat</p>
      </div>

      {/* Personality */}
      <div>
        <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Personality</label>
        <textarea
          value={personality}
          onChange={(e) => setPersonality(e.target.value)}
          placeholder="e.g. Analytical, connects dots across topics"
          rows={2}
          className="w-full px-3 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-purple-500 resize-none"
        />
      </div>

      {/* Overwatch Toggle */}
      <div className="flex items-center justify-between py-2 border-t border-gray-100 dark:border-gray-700">
        <div>
          <span className="text-sm font-medium text-gray-900 dark:text-white">Overwatch</span>
          <p className="text-xs text-gray-400">Cross-notebook insights after each chat answer</p>
        </div>
        <button
          onClick={() => setOverwatchEnabled(!overwatchEnabled)}
          className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
            overwatchEnabled ? 'bg-purple-500' : 'bg-gray-300 dark:bg-gray-600'
          }`}
        >
          <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
            overwatchEnabled ? 'translate-x-4' : 'translate-x-0.5'
          }`} />
        </button>
      </div>

      {/* Notebook Scope */}
      <div className="border-t border-gray-100 dark:border-gray-700 pt-3">
        <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-2">Cross-Notebook Search Scope</label>
        <p className="text-xs text-gray-400 mb-2">Select which notebooks the Curator can search with @curator queries.</p>
        <div className="space-y-1.5 max-h-48 overflow-y-auto">
          {notebooks.map(nb => {
            const included = !excludedIds.has(nb.id);
            return (
              <button
                key={nb.id}
                onClick={() => toggleNotebook(nb.id)}
                className={`w-full flex items-center gap-2 px-3 py-1.5 rounded-lg border text-left text-sm transition-colors ${
                  included
                    ? 'border-purple-300 dark:border-purple-700 bg-purple-50 dark:bg-purple-900/20 text-gray-900 dark:text-white'
                    : 'border-gray-200 dark:border-gray-700 text-gray-500 dark:text-gray-400'
                }`}
              >
                <span className={`w-4 h-4 rounded-lg flex items-center justify-center text-xs border ${
                  included ? 'bg-purple-500 border-purple-500 text-white' : 'border-gray-300 dark:border-gray-600'
                }`}>
                  {included && '✓'}
                </span>
                <span className="truncate flex-1">{nb.title}</span>
                <span className="text-xs text-gray-400 shrink-0">{nb.source_count} sources</span>
              </button>
            );
          })}
          {notebooks.length === 0 && <p className="text-xs text-gray-400 py-2">No notebooks found</p>}
        </div>
      </div>

      {/* Error / Success / Save */}
      {error && <p className="text-xs text-red-500">{error}</p>}
      {success && <p className="text-xs text-green-500">Settings saved</p>}

      <button
        onClick={handleSave}
        disabled={saving}
        className="w-full py-2 text-sm font-medium text-white bg-purple-600 hover:bg-purple-700 rounded-lg transition-colors disabled:opacity-50"
      >
        {saving ? 'Saving...' : 'Save Curator Settings'}
      </button>
    </div>
  );
};
