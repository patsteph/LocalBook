import React, { useState, useEffect, useCallback } from 'react';
import { settingsService } from '../services/settings';
import { API_BASE_URL, localFetch } from '../services/api';

interface LLMSelectorProps {
  selectedProvider: string;
  onProviderChange: (provider: string) => void;
}

interface OllamaModel {
  name: string;
  display_name: string;
  size_gb: number;
  ram_required_gb: number;
  context_window: number;
  suggested_role: 'main' | 'fast' | 'vision' | 'embeddings';
  supports_vision: boolean;
  also_vision?: boolean;
  vendor: string;
  origin_country: string;
  parameter_count: string;
  active_as: string | null;
  in_registry: boolean;
  // v1.7.0: backend that serves this model — "ollama" (default) or "llama_server" (sidecar)
  provider?: string;
}

interface ActiveModels {
  main: string;
  fast: string;
  embeddings: string;
  vision: string;
}

interface DefaultCombo {
  main_model: string;
  fast_model: string;
  vision_model: string;
}

interface SavedDefaultResponse {
  has_custom_default: boolean;
  combo: DefaultCombo;
}

type Role = 'main' | 'fast' | 'vision' | 'embeddings';

// What changes when you swap a model — surfaced in the per-column tooltip
// so the user understands a swap fully replaces behavior, not just the name.
const ROLE_FLIP_DETAILS: Record<Role, string> = {
  main:
    'Switching the Main model also swaps:\n' +
    '• stop sequences (per-family from rag_profile)\n' +
    '• RAG temperature, repeat_penalty\n' +
    '• num_ctx_cap (e.g. 16K cap on Gemma)\n' +
    '• /api/chat vs /api/generate endpoint\n' +
    '• JSON-mode strategy (quiz / writing)\n' +
    '• Aggressive vs measured repetition cleanup\n' +
    '• Audio-script word-budget tolerance',
  fast:
    'Switching the Fast model also swaps:\n' +
    '• Follow-up question generator\n' +
    '• OCR cleanup pass model\n' +
    '• Auto-tag generator\n' +
    '• Query decomposer model\n' +
    'Each pulls per-family tuning from this model\'s rag_profile.',
  vision:
    'Switching the Vision model also swaps:\n' +
    '• num_predict / num_ctx / temperature (vision_profile)\n' +
    '• /api/chat vs /api/generate routing\n' +
    '• Cleanup-pass behavior for structured modes',
  embeddings:
    'Switching Embeddings forces re-indexing of every notebook — \n' +
    'older vectors will not match the new model\'s dimension.',
};

const ROLE_META: Record<Role, { label: string; api_role: string; color: string; desc: string }> = {
  main:       { label: 'Main',       api_role: 'main_model',       color: 'blue',   desc: '≥ 5 GB — deep reasoning, synthesis' },
  fast:       { label: 'Fast',       api_role: 'fast_model',       color: 'green',  desc: '< 5 GB — routing, quick tasks' },
  vision:     { label: 'Vision',     api_role: 'vision_model',     color: 'amber',  desc: 'OCR & image analysis' },
  embeddings: { label: 'Embeddings', api_role: 'embedding_model',  color: 'purple', desc: 'Vector search embeddings' },
};

function ActiveBadge() {
  return (
    <span className="px-2 py-0.5 text-xs font-medium rounded-md bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300">
      ● Active
    </span>
  );
}

export const LLMSelector: React.FC<LLMSelectorProps> = ({ selectedProvider, onProviderChange }) => {
  const [mode, setMode] = useState<'local' | 'cloud'>('local');
  const [models, setModels] = useState<OllamaModel[]>([]);
  const [active, setActive] = useState<ActiveModels>({ main: '', fast: '', embeddings: '', vision: '' });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [switching, setSwitching] = useState<string | null>(null);
  const [switchMsg, setSwitchMsg] = useState<{ text: string; ok: boolean } | null>(null);
  const [availableProviders, setAvailableProviders] = useState<{ [key: string]: boolean }>({});
  const [savedDefault, setSavedDefault] = useState<SavedDefaultResponse | null>(null);
  const [savingDefault, setSavingDefault] = useState(false);

  useEffect(() => {
    if (selectedProvider !== 'ollama') setMode('cloud');
    else setMode('local');
  }, [selectedProvider]);

  useEffect(() => {
    loadModels();
    loadProviders();
    loadSavedDefault();
  }, []);

  const loadSavedDefault = async () => {
    try {
      const res = await localFetch(`${API_BASE_URL}/evaluator/save-default`);
      if (!res.ok) return;
      const data: SavedDefaultResponse = await res.json();
      setSavedDefault(data);
    } catch {
      /* non-fatal */
    }
  };

  const saveCurrentAsDefault = useCallback(async () => {
    setSavingDefault(true);
    setSwitchMsg(null);
    try {
      const res = await localFetch(`${API_BASE_URL}/evaluator/save-default`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          main_model: active.main,
          fast_model: active.fast,
          vision_model: active.vision,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setSwitchMsg({ text: data.detail ?? 'Save failed', ok: false });
      } else {
        setSwitchMsg({ text: data.message ?? 'Default saved.', ok: true });
        await loadSavedDefault();
      }
    } catch (e: any) {
      setSwitchMsg({ text: e.message ?? 'Network error', ok: false });
    } finally {
      setSavingDefault(false);
    }
  }, [active]);

  const resetDefault = useCallback(async () => {
    setSavingDefault(true);
    setSwitchMsg(null);
    try {
      const res = await localFetch(`${API_BASE_URL}/evaluator/save-default`, {
        method: 'DELETE',
      });
      const data = await res.json();
      if (!res.ok) {
        setSwitchMsg({ text: data.detail ?? 'Reset failed', ok: false });
      } else {
        setSwitchMsg({ text: data.message ?? 'Reset to built-in defaults.', ok: true });
        await loadSavedDefault();
      }
    } catch (e: any) {
      setSwitchMsg({ text: e.message ?? 'Network error', ok: false });
    } finally {
      setSavingDefault(false);
    }
  }, []);

  // Compare current active combo to the saved default — if they match, the
  // Save button has nothing to do, so we disable it.
  const currentMatchesSaved = !!savedDefault && (
    savedDefault.combo.main_model === active.main &&
    savedDefault.combo.fast_model === active.fast &&
    savedDefault.combo.vision_model === active.vision
  );

  const loadModels = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await localFetch(`${API_BASE_URL}/settings/ollama/models`);
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setModels(data.models ?? []);
      setActive(data.active ?? {});
    } catch (e: any) {
      setError(e.message ?? 'Could not reach Ollama');
    } finally {
      setLoading(false);
    }
  };

  const loadProviders = async () => {
    try {
      const status = await settingsService.getAPIKeysStatus();
      setAvailableProviders(status.configured);
    } catch { /* non-fatal */ }
  };

  const handleSwitch = useCallback(async (modelName: string, role: Role) => {
    setSwitching(`${modelName}:${role}`);
    setSwitchMsg(null);
    try {
      const res = await localFetch(`${API_BASE_URL}/evaluator/swap`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_model: modelName, role: ROLE_META[role].api_role }),
      });
      const data = await res.json();
      if (!res.ok) {
        setSwitchMsg({ text: data.detail ?? 'Switch failed', ok: false });
      } else {
        setSwitchMsg({ text: data.message ?? `Switched ${role} → ${modelName}`, ok: true });
        await loadModels();
        if (role !== 'embeddings' && role !== 'vision') onProviderChange('ollama');
      }
    } catch (e: any) {
      setSwitchMsg({ text: e.message ?? 'Network error', ok: false });
    } finally {
      setSwitching(null);
    }
  }, [onProviderChange]);

  const cloudProviders = [
    { id: 'custom_llm',  name: 'Custom LLM',  subtitle: 'Company Internal',  available: availableProviders.custom_llm,  special: true },
    { id: 'openai',      name: 'OpenAI',       subtitle: 'GPT-4o',             available: availableProviders.openai },
    { id: 'anthropic',   name: 'Anthropic',    subtitle: 'Claude 3.5 Sonnet',  available: availableProviders.anthropic },
    { id: 'google_ai',   name: 'Google AI',    subtitle: 'Gemini 1.5 Flash',   available: availableProviders.google_ai },
  ];

  const modelsForRole = (role: Role) => {
    if (role === 'vision') {
      return models.filter(m => m.suggested_role === 'vision' || m.also_vision);
    }
    return models.filter(m => m.suggested_role === role);
  };

  const renderModelRow = (m: OllamaModel, role: Role) => {
    const key = `${m.name}:${role}`;
    const isSwitching = switching === key;
    const isActive = active[role] === m.name;
    const isSidecar = m.provider === 'llama_server';
    // Phase 2 (v1.8.0): sidecar models are fully selectable. The backend
    // auto-starts llama-server when the swap endpoint receives a
    // llama_server-provider target.

    return (
      <div
        key={key}
        className={`flex items-center gap-3 px-3 py-2.5 rounded-lg border transition-all ${
          isActive
            ? 'border-emerald-400 dark:border-emerald-600 bg-emerald-50 dark:bg-emerald-900/10'
            : 'border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 hover:border-gray-300 dark:hover:border-gray-600'
        }`}
      >
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-1.5 mb-0.5">
            <span className="font-medium text-sm text-gray-900 dark:text-white truncate">
              {m.display_name}
            </span>
            {isActive && <ActiveBadge />}
            {isSidecar && (
              <span
                title="Served by a llama-server sidecar (experimental — Phase 1 evaluator-only)"
                className="px-1.5 py-0.5 text-xs rounded bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300"
              >
                ⚗ Sidecar
              </span>
            )}
            {m.supports_vision && (
              <span className="px-1.5 py-0.5 text-xs rounded bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300">
                👁 Vision
              </span>
            )}
            {!m.in_registry && (
              <span className="px-1.5 py-0.5 text-xs rounded bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400">
                Community
              </span>
            )}
          </div>
          <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-gray-500 dark:text-gray-400">
            <span>{m.size_gb} GB disk</span>
            <span>~{m.ram_required_gb} GB RAM</span>
            {m.context_window > 0 && <span>{(m.context_window / 1000).toFixed(0)}K ctx</span>}
            {m.origin_country && <span>{m.origin_country}</span>}
          </div>
        </div>
        <button
          onClick={() => !isActive && handleSwitch(m.name, role)}
          disabled={isActive || isSwitching}
          title={isSidecar ? 'Switching to this model will auto-start the llama-server sidecar (may take 10–20 s on first use).' : undefined}
          className={`shrink-0 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
            isActive
              ? 'bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300 cursor-default'
              : isSwitching
              ? 'bg-gray-200 dark:bg-gray-700 text-gray-400 cursor-wait'
              : 'bg-blue-600 hover:bg-blue-700 text-white'
          }`}
        >
          {isActive ? 'Active' : isSwitching ? '…' : 'Use'}
        </button>
      </div>
    );
  };

  const renderRoleColumn = (role: Role) => {
    const meta = ROLE_META[role];
    const roleModels = modelsForRole(role);
    const colorBorder: Record<string, string> = {
      blue:   'border-blue-200 dark:border-blue-800',
      green:  'border-green-200 dark:border-green-800',
      amber:  'border-amber-200 dark:border-amber-800',
      purple: 'border-purple-200 dark:border-purple-800',
    };
    const colorHeader: Record<string, string> = {
      blue:   'bg-blue-50 dark:bg-blue-900/20 text-blue-800 dark:text-blue-300',
      green:  'bg-green-50 dark:bg-green-900/20 text-green-800 dark:text-green-300',
      amber:  'bg-amber-50 dark:bg-amber-900/20 text-amber-800 dark:text-amber-300',
      purple: 'bg-purple-50 dark:bg-purple-900/20 text-purple-800 dark:text-purple-300',
    };
    return (
      <div key={role} className={`flex-1 rounded-xl border ${colorBorder[meta.color]} overflow-hidden`}>
        <div className={`px-3 py-2 ${colorHeader[meta.color]}`}>
          <div className="flex items-center justify-between">
            <div className="font-semibold text-sm">{meta.label}</div>
            <span
              title={ROLE_FLIP_DETAILS[role]}
              className="cursor-help text-xs opacity-60 hover:opacity-100 select-none"
              aria-label={`What changes when switching ${meta.label}`}
            >
              ⓘ
            </span>
          </div>
          <div className="text-xs opacity-75 mt-0.5">{meta.desc}</div>
        </div>
        <div className="p-2 space-y-1.5">
          {roleModels.length === 0 ? (
            <p className="text-xs text-gray-400 dark:text-gray-500 text-center py-3">
              No {meta.label.toLowerCase()} models pulled
            </p>
          ) : (
            roleModels.map(m => renderModelRow(m, role))
          )}
        </div>
      </div>
    );
  };

  return (
    <div className="p-4 space-y-4">
      {/* Mode Toggle */}
      <div className="flex items-center justify-center">
        <div className="relative inline-flex items-center bg-gray-200 dark:bg-gray-700 rounded-full p-1">
          <button
            onClick={() => { setMode('local'); onProviderChange('ollama'); }}
            className={`px-6 py-2 rounded-full text-sm font-medium transition-all ${
              mode === 'local'
                ? 'bg-blue-600 text-white shadow-lg'
                : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white'
            }`}
          >
            🏠 Local
          </button>
          <button
            onClick={() => setMode('cloud')}
            className={`px-6 py-2 rounded-full text-sm font-medium transition-all ${
              mode === 'cloud'
                ? 'bg-blue-600 text-white shadow-lg'
                : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white'
            }`}
          >
            ☁️ Cloud
          </button>
        </div>
      </div>

      {/* Local — Dynamic Ollama Model Table */}
      {mode === 'local' && (
        <div className="space-y-3">
          {loading ? (
            <div className="flex items-center justify-center py-10">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
            </div>
          ) : error ? (
            <div className="rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/10 p-4 text-center space-y-2">
              <p className="text-sm font-medium text-red-700 dark:text-red-400">Ollama unreachable</p>
              <p className="text-xs text-red-600 dark:text-red-500">{error}</p>
              <button
                onClick={loadModels}
                className="px-3 py-1.5 text-xs font-medium bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300 rounded-lg hover:bg-red-200 dark:hover:bg-red-900/50"
              >
                Retry
              </button>
            </div>
          ) : models.length === 0 ? (
            <div className="text-center py-8 space-y-2">
              <p className="text-sm text-gray-500 dark:text-gray-400">No models found in Ollama</p>
              <p className="text-xs text-gray-400 dark:text-gray-500">Run <code className="px-1 bg-gray-100 dark:bg-gray-700 rounded">ollama pull &lt;model&gt;</code> to add one</p>
            </div>
          ) : (
            <>
              {savedDefault && (
                <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 px-3 py-2 text-xs flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className={`shrink-0 px-2 py-0.5 rounded font-medium ${
                      savedDefault.has_custom_default
                        ? 'bg-blue-100 dark:bg-blue-900/40 text-blue-700 dark:text-blue-300'
                        : 'bg-gray-200 dark:bg-gray-700 text-gray-600 dark:text-gray-300'
                    }`}>
                      {savedDefault.has_custom_default ? 'Saved default' : 'Built-in default'}
                    </span>
                    <span className="text-gray-600 dark:text-gray-400 truncate">
                      {savedDefault.combo.main_model} · {savedDefault.combo.fast_model} · {savedDefault.combo.vision_model}
                    </span>
                  </div>
                </div>
              )}
              {switchMsg && (
                <div className={`rounded-lg px-3 py-2 text-sm ${
                  switchMsg.ok
                    ? 'bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800'
                    : 'bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400 border border-red-200 dark:border-red-800'
                }`}>
                  {switchMsg.text}
                </div>
              )}
              <div className="flex gap-3">
                {(['main', 'fast', 'vision', 'embeddings'] as Role[]).map(renderRoleColumn)}
              </div>
              <div className="flex items-center justify-between pt-1 gap-3 flex-wrap">
                <p className="text-xs text-gray-400 dark:text-gray-500">
                  {models.length} model{models.length !== 1 ? 's' : ''} installed · roles auto-classified by size
                </p>
                <div className="flex items-center gap-2">
                  <button
                    onClick={saveCurrentAsDefault}
                    disabled={savingDefault || currentMatchesSaved || !active.main}
                    title={
                      currentMatchesSaved
                        ? 'Current combo already saved as your default.'
                        : 'Persist the current Main / Fast / Vision combo as the default that loads on every app launch.'
                    }
                    className={`px-3 py-1.5 text-xs font-medium rounded-lg transition-all ${
                      currentMatchesSaved
                        ? 'bg-gray-100 dark:bg-gray-800 text-gray-400 dark:text-gray-500 cursor-default'
                        : savingDefault
                        ? 'bg-gray-200 dark:bg-gray-700 text-gray-400 cursor-wait'
                        : 'bg-blue-600 hover:bg-blue-700 text-white'
                    }`}
                  >
                    {savingDefault ? '…' : 'Save as my default'}
                  </button>
                  <button
                    onClick={resetDefault}
                    disabled={savingDefault || (savedDefault !== null && !savedDefault.has_custom_default)}
                    title="Clear your saved default. Next launch will use built-in OLMo + Phi4."
                    className={`px-3 py-1.5 text-xs font-medium rounded-lg transition-all ${
                      savedDefault && !savedDefault.has_custom_default
                        ? 'bg-gray-100 dark:bg-gray-800 text-gray-400 dark:text-gray-500 cursor-default'
                        : savingDefault
                        ? 'bg-gray-200 dark:bg-gray-700 text-gray-400 cursor-wait'
                        : 'border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800'
                    }`}
                  >
                    Reset to built-in
                  </button>
                  <button
                    onClick={loadModels}
                    className="text-xs text-blue-600 dark:text-blue-400 hover:underline"
                  >
                    Refresh
                  </button>
                </div>
              </div>
            </>
          )}
        </div>
      )}

      {/* Cloud Providers */}
      {mode === 'cloud' && (
        <div className="space-y-3">
          {cloudProviders.map((provider) => (
            <button
              key={provider.id}
              onClick={() => provider.available && onProviderChange(provider.id)}
              disabled={!provider.available}
              className={`w-full p-4 rounded-lg border-2 transition-all ${
                selectedProvider === provider.id
                  ? provider.special
                    ? 'border-purple-600 bg-purple-50 dark:bg-purple-900/20'
                    : 'border-blue-600 bg-blue-50 dark:bg-blue-900/20'
                  : provider.available
                  ? provider.special
                    ? 'border-purple-300 dark:border-purple-700 hover:border-purple-400 bg-white dark:bg-gray-800'
                    : 'border-gray-300 dark:border-gray-600 hover:border-blue-400 bg-white dark:bg-gray-800'
                  : 'border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 opacity-50 cursor-not-allowed'
              }`}
            >
              <div className="flex items-center gap-3">
                <div className="text-2xl">
                  {provider.id === 'custom_llm' ? '🔐' :
                   provider.id === 'openai' ? '🤖' :
                   provider.id === 'anthropic' ? '🧠' : '✨'}
                </div>
                <div className="flex-1 text-left">
                  <div className="flex items-center gap-2">
                    <div className={`font-semibold ${provider.available ? 'text-gray-900 dark:text-white' : 'text-gray-400 dark:text-gray-500'}`}>
                      {provider.name}
                    </div>
                    {provider.special && provider.available && (
                      <span className="px-2 py-0.5 text-xs font-medium bg-purple-200 dark:bg-purple-800 text-purple-800 dark:text-purple-200 rounded-lg">
                        Secure
                      </span>
                    )}
                  </div>
                  <div className={`text-sm ${provider.available ? 'text-gray-600 dark:text-gray-400' : 'text-gray-400 dark:text-gray-500'}`}>
                    {provider.available ? provider.subtitle : 'Not configured'}
                  </div>
                </div>
                {selectedProvider === provider.id && provider.available && (
                  <svg className={`w-6 h-6 ${provider.special ? 'text-purple-600' : 'text-blue-600'}`} fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                  </svg>
                )}
              </div>
            </button>
          ))}
          {!cloudProviders.some(p => p.available) && (
            <div className="text-center py-4 space-y-1">
              <p className="text-sm text-gray-500 dark:text-gray-400">No cloud providers configured</p>
              <p className="text-xs text-gray-400 dark:text-gray-500">Configure API keys in Settings to use cloud LLMs</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
