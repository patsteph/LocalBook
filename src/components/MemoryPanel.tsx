import { useState, useEffect } from 'react';

interface CoreMemoryEntry {
  id: string;
  key: string;
  value: string;
  category: string;
  importance: string;
  source_type: string;
  confidence: number;
  created_at: string;
  updated_at: string;
  access_count: number;
}

interface CoreMemory {
  entries: CoreMemoryEntry[];
  max_tokens: number;
}

interface MemoryStats {
  core_memory: {
    entries: number;
    tokens: number;
    max_tokens: number;
    usage_percent: number;
  };
  recall_memory: {
    entries: number;
  };
  archival_memory: {
    entries: number;
  };
}

interface ArchivalSearchResult {
  id: string;
  content: string;
  content_type: string;
  topics: string[];
  similarity_score: number;
  recency_score: number;
  combined_score: number;
  created_at: string;
}

const API_BASE = 'http://localhost:8000';

const CATEGORY_LABELS: Record<string, string> = {
  user_preference: '⚙️ Preferences',
  user_fact: '👤 About You',
  project_context: '📁 Projects',
  key_decision: '✅ Decisions',
  recurring_theme: '🔄 Themes',
  important_date: '📅 Dates',
  relationship: '👥 People',
  custom: '📝 Other',
};

const IMPORTANCE_COLORS: Record<string, string> = {
  critical: 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200',
  high: 'bg-orange-100 text-orange-800 dark:bg-orange-900 dark:text-orange-200',
  medium: 'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200',
  low: 'bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-200',
};

const SOURCE_TYPE_ICONS: Record<string, string> = {
  user_stated: '💬',
  ai_inferred: '🤖',
  document_extracted: '📄',
};

export function MemoryPanel() {
  const [activeSection, setActiveSection] = useState<'core' | 'archival' | 'stats'>('core');
  const [coreMemory, setCoreMemory] = useState<CoreMemory | null>(null);
  const [stats, setStats] = useState<MemoryStats | null>(null);
  const [archivalResults, setArchivalResults] = useState<ArchivalSearchResult[]>([]);
  const [archivalQuery, setArchivalQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  
  // Edit state
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');
  
  // New memory form
  const [showNewForm, setShowNewForm] = useState(false);
  const [newKey, setNewKey] = useState('');
  const [newValue, setNewValue] = useState('');
  const [newCategory, setNewCategory] = useState('user_fact');
  const [newImportance, setNewImportance] = useState('medium');

  useEffect(() => {
    loadCoreMemory();
    loadStats();
  }, []);

  const loadCoreMemory = async () => {
    try {
      const response = await fetch(`${API_BASE}/memory/core`);
      if (response.ok) {
        const data = await response.json();
        setCoreMemory(data);
      }
    } catch (err) {
      console.error('Failed to load core memory:', err);
    }
  };

  const loadStats = async () => {
    try {
      const response = await fetch(`${API_BASE}/memory/stats`);
      if (response.ok) {
        const data = await response.json();
        setStats(data);
      }
    } catch (err) {
      console.error('Failed to load memory stats:', err);
    }
  };

  const searchArchival = async () => {
    if (!archivalQuery.trim()) return;
    
    setLoading(true);
    setError(null);
    
    try {
      const response = await fetch(`${API_BASE}/memory/archival/search`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: archivalQuery, max_results: 10 }),
      });
      
      if (response.ok) {
        const data = await response.json();
        setArchivalResults(data);
      } else {
        setError('Search failed');
      }
    } catch (err) {
      setError('Failed to search archival memory');
    } finally {
      setLoading(false);
    }
  };

  const updateMemory = async (id: string, newValue: string) => {
    try {
      const response = await fetch(`${API_BASE}/memory/core/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value: newValue }),
      });
      
      if (response.ok) {
        setEditingId(null);
        loadCoreMemory();
        loadStats();
      }
    } catch (err) {
      console.error('Failed to update memory:', err);
    }
  };

  const deleteMemory = async (id: string) => {
    if (!confirm('Delete this memory?')) return;
    
    try {
      const response = await fetch(`${API_BASE}/memory/core/${id}`, {
        method: 'DELETE',
      });
      
      if (response.ok) {
        loadCoreMemory();
        loadStats();
      }
    } catch (err) {
      console.error('Failed to delete memory:', err);
    }
  };

  const createMemory = async () => {
    if (!newKey.trim() || !newValue.trim()) return;
    
    try {
      const response = await fetch(`${API_BASE}/memory/core`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          key: newKey,
          value: newValue,
          category: newCategory,
          importance: newImportance,
        }),
      });
      
      if (response.ok) {
        setShowNewForm(false);
        setNewKey('');
        setNewValue('');
        loadCoreMemory();
        loadStats();
      }
    } catch (err) {
      console.error('Failed to create memory:', err);
    }
  };

  const triggerCompression = async () => {
    setLoading(true);
    try {
      const response = await fetch(`${API_BASE}/memory/compress`, {
        method: 'POST',
      });
      
      if (response.ok) {
        loadCoreMemory();
        loadStats();
      }
    } catch (err) {
      console.error('Failed to compress memories:', err);
    } finally {
      setLoading(false);
    }
  };

  // Group core memories by category
  const groupedMemories = coreMemory?.entries.reduce((acc, entry) => {
    const cat = entry.category || 'custom';
    if (!acc[cat]) acc[cat] = [];
    acc[cat].push(entry);
    return acc;
  }, {} as Record<string, CoreMemoryEntry[]>) || {};

  return (
    <div className="h-full flex flex-col">
      {/* Section Tabs */}
      <div className="flex border-b dark:border-gray-700 bg-gray-50 dark:bg-gray-900 px-2">
        <button
          onClick={() => setActiveSection('core')}
          className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 ${
            activeSection === 'core'
              ? 'border-purple-600 text-purple-600 dark:text-purple-400'
              : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900'
          }`}
        >
          🧠 Core Memory
        </button>
        <button
          onClick={() => setActiveSection('archival')}
          className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 ${
            activeSection === 'archival'
              ? 'border-purple-600 text-purple-600 dark:text-purple-400'
              : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900'
          }`}
        >
          📚 Long-term
        </button>
        <button
          onClick={() => setActiveSection('stats')}
          className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 ${
            activeSection === 'stats'
              ? 'border-purple-600 text-purple-600 dark:text-purple-400'
              : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900'
          }`}
        >
          📊 Stats
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-4">
        {/* Core Memory Section */}
        {activeSection === 'core' && (
          <div className="space-y-4">
            {/* Token usage bar */}
            {stats && (
              <div className="bg-gray-100 dark:bg-gray-700 rounded-lg p-3">
                <div className="flex justify-between text-sm mb-1">
                  <span className="text-gray-600 dark:text-gray-300">Memory Usage</span>
                  <span className="text-gray-600 dark:text-gray-300">
                    {stats.core_memory.tokens} / {stats.core_memory.max_tokens} tokens
                  </span>
                </div>
                <div className="w-full bg-gray-200 dark:bg-gray-600 rounded-full h-2">
                  <div
                    className={`h-2 rounded-full transition-all ${
                      stats.core_memory.usage_percent > 80
                        ? 'bg-red-500'
                        : stats.core_memory.usage_percent > 60
                        ? 'bg-yellow-500'
                        : 'bg-green-500'
                    }`}
                    style={{ width: `${Math.min(100, stats.core_memory.usage_percent)}%` }}
                  />
                </div>
              </div>
            )}

            {/* Add new memory button */}
            <button
              onClick={() => setShowNewForm(!showNewForm)}
              className="w-full flex items-center justify-center gap-2 px-3 py-2 bg-purple-600 hover:bg-purple-700 text-white rounded-lg text-sm font-medium transition-colors"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
              </svg>
              Add Memory
            </button>

            {/* New memory form */}
            {showNewForm && (
              <div className="bg-purple-50 dark:bg-purple-900/20 rounded-lg p-4 space-y-3">
                <input
                  type="text"
                  placeholder="Key (e.g., 'preferred_name')"
                  value={newKey}
                  onChange={(e) => setNewKey(e.target.value)}
                  className="w-full px-3 py-2 border dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-sm"
                />
                <textarea
                  placeholder="Value (e.g., 'Patrick')"
                  value={newValue}
                  onChange={(e) => setNewValue(e.target.value)}
                  className="w-full px-3 py-2 border dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-sm resize-none"
                  rows={2}
                />
                <div className="flex gap-2">
                  <select
                    value={newCategory}
                    onChange={(e) => setNewCategory(e.target.value)}
                    className="flex-1 px-3 py-2 border dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-sm"
                  >
                    {Object.entries(CATEGORY_LABELS).map(([value, label]) => (
                      <option key={value} value={value}>{label}</option>
                    ))}
                  </select>
                  <select
                    value={newImportance}
                    onChange={(e) => setNewImportance(e.target.value)}
                    className="flex-1 px-3 py-2 border dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-sm"
                  >
                    <option value="critical">🔴 Critical</option>
                    <option value="high">🟠 High</option>
                    <option value="medium">🔵 Medium</option>
                    <option value="low">⚪ Low</option>
                  </select>
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={createMemory}
                    className="flex-1 px-3 py-2 bg-purple-600 hover:bg-purple-700 text-white rounded-lg text-sm font-medium"
                  >
                    Save
                  </button>
                  <button
                    onClick={() => setShowNewForm(false)}
                    className="px-3 py-2 bg-gray-200 dark:bg-gray-700 hover:bg-gray-300 dark:hover:bg-gray-600 rounded-lg text-sm"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {/* Grouped memories */}
            {Object.entries(groupedMemories).map(([category, entries]) => (
              <div key={category} className="space-y-2">
                <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300">
                  {CATEGORY_LABELS[category] || category}
                </h3>
                {entries.map((entry) => (
                  <div
                    key={entry.id}
                    className="bg-white dark:bg-gray-800 rounded-lg border dark:border-gray-700 p-3"
                  >
                    {editingId === entry.id ? (
                      <div className="space-y-2">
                        <textarea
                          value={editValue}
                          onChange={(e) => setEditValue(e.target.value)}
                          className="w-full px-2 py-1 border dark:border-gray-600 rounded bg-gray-50 dark:bg-gray-700 text-sm resize-none"
                          rows={2}
                        />
                        <div className="flex gap-2">
                          <button
                            onClick={() => updateMemory(entry.id, editValue)}
                            className="px-2 py-1 bg-green-600 text-white rounded text-xs"
                          >
                            Save
                          </button>
                          <button
                            onClick={() => setEditingId(null)}
                            className="px-2 py-1 bg-gray-300 dark:bg-gray-600 rounded text-xs"
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    ) : (
                      <>
                        <div className="flex items-start justify-between gap-2">
                          <div className="flex-1">
                            <div className="flex items-center gap-2 mb-1">
                              <span className="font-medium text-gray-900 dark:text-white text-sm">
                                {entry.key}
                              </span>
                              <span className={`px-1.5 py-0.5 rounded text-xs ${IMPORTANCE_COLORS[entry.importance]}`}>
                                {entry.importance}
                              </span>
                              <span title={entry.source_type}>
                                {SOURCE_TYPE_ICONS[entry.source_type] || '❓'}
                              </span>
                            </div>
                            <p className="text-gray-600 dark:text-gray-300 text-sm">
                              {entry.value}
                            </p>
                          </div>
                          <div className="flex gap-1">
                            <button
                              onClick={() => {
                                setEditingId(entry.id);
                                setEditValue(entry.value);
                              }}
                              className="p-1 hover:bg-gray-100 dark:hover:bg-gray-700 rounded"
                              title="Edit"
                            >
                              <svg className="w-4 h-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                              </svg>
                            </button>
                            <button
                              onClick={() => deleteMemory(entry.id)}
                              className="p-1 hover:bg-red-100 dark:hover:bg-red-900 rounded"
                              title="Delete"
                            >
                              <svg className="w-4 h-4 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                              </svg>
                            </button>
                          </div>
                        </div>
                        <div className="mt-2 text-xs text-gray-400">
                          Accessed {entry.access_count} times • Updated {new Date(entry.updated_at).toLocaleDateString()}
                        </div>
                      </>
                    )}
                  </div>
                ))}
              </div>
            ))}

            {Object.keys(groupedMemories).length === 0 && (
              <div className="text-center py-8 text-gray-500 dark:text-gray-400">
                <p className="text-lg mb-2">🧠 No memories yet</p>
                <p className="text-sm">
                  As you chat, I'll learn and remember important things about you.
                </p>
              </div>
            )}
          </div>
        )}

        {/* Archival Memory Section */}
        {activeSection === 'archival' && (
          <div className="space-y-4">
            {/* Search */}
            <div className="flex gap-2">
              <input
                type="text"
                placeholder="Search long-term memory..."
                value={archivalQuery}
                onChange={(e) => setArchivalQuery(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && searchArchival()}
                className="flex-1 px-3 py-2 border dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-sm"
              />
              <button
                onClick={searchArchival}
                disabled={loading}
                className="px-4 py-2 bg-purple-600 hover:bg-purple-700 text-white rounded-lg text-sm font-medium disabled:opacity-50"
              >
                {loading ? '...' : 'Search'}
              </button>
            </div>

            {error && (
              <div className="p-3 bg-red-100 dark:bg-red-900/20 text-red-700 dark:text-red-300 rounded-lg text-sm">
                {error}
              </div>
            )}

            {/* Results */}
            {archivalResults.length > 0 ? (
              <div className="space-y-3">
                {archivalResults.map((result) => (
                  <div
                    key={result.id}
                    className="bg-white dark:bg-gray-800 rounded-lg border dark:border-gray-700 p-3"
                  >
                    <div className="flex items-start justify-between gap-2 mb-2">
                      <span className="text-xs px-2 py-0.5 bg-gray-100 dark:bg-gray-700 rounded">
                        {result.content_type}
                      </span>
                      <span className="text-xs text-gray-500">
                        {Math.round(result.combined_score * 100)}% match
                      </span>
                    </div>
                    <p className="text-sm text-gray-700 dark:text-gray-300">
                      {result.content}
                    </p>
                    {result.topics.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1">
                        {result.topics.map((topic, i) => (
                          <span
                            key={i}
                            className="text-xs px-1.5 py-0.5 bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300 rounded"
                          >
                            {topic}
                          </span>
                        ))}
                      </div>
                    )}
                    <div className="mt-2 text-xs text-gray-400">
                      {new Date(result.created_at).toLocaleDateString()}
                    </div>
                  </div>
                ))}
              </div>
            ) : archivalQuery && !loading ? (
              <div className="text-center py-8 text-gray-500 dark:text-gray-400">
                <p>No memories found for "{archivalQuery}"</p>
              </div>
            ) : (
              <div className="text-center py-8 text-gray-500 dark:text-gray-400">
                <p className="text-lg mb-2">📚 Long-term Memory</p>
                <p className="text-sm">
                  Search through conversation summaries and extracted facts.
                </p>
              </div>
            )}
          </div>
        )}

        {/* Stats Section */}
        {activeSection === 'stats' && stats && (
          <div className="space-y-4">
            {/* Memory Tiers */}
            <div className="grid grid-cols-3 gap-3">
              <div className="bg-purple-50 dark:bg-purple-900/20 rounded-lg p-4 text-center">
                <div className="text-2xl font-bold text-purple-600 dark:text-purple-400">
                  {stats.core_memory.entries}
                </div>
                <div className="text-xs text-gray-600 dark:text-gray-400">Core Memories</div>
              </div>
              <div className="bg-blue-50 dark:bg-blue-900/20 rounded-lg p-4 text-center">
                <div className="text-2xl font-bold text-blue-600 dark:text-blue-400">
                  {stats.recall_memory.entries}
                </div>
                <div className="text-xs text-gray-600 dark:text-gray-400">Conversations</div>
              </div>
              <div className="bg-green-50 dark:bg-green-900/20 rounded-lg p-4 text-center">
                <div className="text-2xl font-bold text-green-600 dark:text-green-400">
                  {stats.archival_memory.entries}
                </div>
                <div className="text-xs text-gray-600 dark:text-gray-400">Archived</div>
              </div>
            </div>

            {/* Token Usage */}
            <div className="bg-white dark:bg-gray-800 rounded-lg border dark:border-gray-700 p-4">
              <h3 className="font-medium text-gray-900 dark:text-white mb-3">Core Memory Usage</h3>
              <div className="space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="text-gray-600 dark:text-gray-400">Tokens Used</span>
                  <span className="font-medium">{stats.core_memory.tokens}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-gray-600 dark:text-gray-400">Max Tokens</span>
                  <span className="font-medium">{stats.core_memory.max_tokens}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-gray-600 dark:text-gray-400">Usage</span>
                  <span className="font-medium">{stats.core_memory.usage_percent.toFixed(1)}%</span>
                </div>
              </div>
            </div>

            {/* Actions */}
            <div className="space-y-2">
              <button
                onClick={triggerCompression}
                disabled={loading}
                className="w-full px-4 py-2 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
              >
                {loading ? 'Compressing...' : '🗜️ Compress Old Memories'}
              </button>
              <p className="text-xs text-gray-500 dark:text-gray-400 text-center">
                Moves old conversations to long-term storage
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
