/**
 * MemorySettings Component
 * View and manage what the AI remembers about you
 * "Invisible by default, transparent by design, controllable on demand"
 */

import React, { useState, useEffect } from 'react';
import { memoryService } from '../services/memory';

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

const SOURCE_TYPE_LABELS: Record<string, string> = {
  user_stated: 'You told me',
  ai_inferred: 'I inferred',
  document_extracted: 'From documents',
};

export const MemorySettings: React.FC = () => {
  const [memories, setMemories] = useState<CoreMemoryEntry[]>([]);
  const [stats, setStats] = useState<MemoryStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [memoryEnabled, setMemoryEnabled] = useState(() => {
    return localStorage.getItem('memoryEnabled') !== 'false';
  });
  const [showOnboarding, setShowOnboarding] = useState(() => {
    return !localStorage.getItem('memoryOnboardingSeen');
  });

  useEffect(() => {
    loadMemories();
    loadStats();
  }, []);

  const loadMemories = async () => {
    try {
      const data = await memoryService.getCoreMemories();
      setMemories((data as any).entries || []);
    } catch (err) {
      console.error('Failed to load memories:', err);
    } finally {
      setLoading(false);
    }
  };

  const loadStats = async () => {
    try {
      const data = await memoryService.getStats();
      setStats(data as any);
    } catch (err) {
      console.error('Failed to load memory stats:', err);
    }
  };

  const deleteMemory = async (id: string) => {
    try {
      await memoryService.deleteCoreMemory(id);
      setMemories(memories.filter(m => m.id !== id));
      loadStats();
    } catch (err) {
      setError('Failed to delete memory');
    }
  };

  const clearAllMemories = async () => {
    if (!confirm('Are you sure you want to clear ALL memories? This cannot be undone.')) {
      return;
    }
    
    try {
      // Delete each memory
      for (const memory of memories) {
        await memoryService.deleteCoreMemory(memory.id);
      }
      setMemories([]);
      loadStats();
    } catch (err) {
      setError('Failed to clear memories');
    }
  };

  const toggleMemory = () => {
    const newValue = !memoryEnabled;
    setMemoryEnabled(newValue);
    localStorage.setItem('memoryEnabled', String(newValue));
  };

  const dismissOnboarding = () => {
    setShowOnboarding(false);
    localStorage.setItem('memoryOnboardingSeen', 'true');
  };

  // Group memories by category
  const groupedMemories = memories.reduce((acc, entry) => {
    const cat = entry.category || 'custom';
    if (!acc[cat]) acc[cat] = [];
    acc[cat].push(entry);
    return acc;
  }, {} as Record<string, CoreMemoryEntry[]>);

  return (
    <div className="space-y-4">
      {/* Onboarding Message */}
      {showOnboarding && (
        <div className="p-3 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-700 rounded-lg">
          <div className="flex items-start gap-3">
            <span className="text-lg">🧠</span>
            <div className="flex-1">
              <h4 className="text-sm font-medium text-blue-900 dark:text-blue-100 mb-1">
                How Memory Works
              </h4>
              <p className="text-sm text-blue-800 dark:text-blue-200 mb-3">
                I'll remember key details from our conversations to give better answers over time. 
                This works like a good colleague's memory - I won't constantly remind you what I know, 
                but I'll use context naturally to help you better.
              </p>
              <p className="text-sm text-blue-700 dark:text-blue-300 mb-3">
                <strong>Your privacy matters:</strong> All memories are stored locally on your computer. 
                You can view, edit, or delete anything I remember right here.
              </p>
              <button
                onClick={dismissOnboarding}
                className="text-sm text-blue-600 dark:text-blue-400 hover:underline"
              >
                Got it, don't show again
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Memory Toggle */}
      <div className="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-800 rounded-lg">
        <div>
          <h4 className="text-sm font-medium text-gray-900 dark:text-white">Memory</h4>
          <p className="text-sm text-gray-600 dark:text-gray-400">
            {memoryEnabled 
              ? 'I remember context from our conversations' 
              : 'Memory is disabled - I won\'t remember anything'}
          </p>
        </div>
        <button
          onClick={toggleMemory}
          className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
            memoryEnabled ? 'bg-blue-600' : 'bg-gray-300 dark:bg-gray-600'
          }`}
        >
          <span
            className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
              memoryEnabled ? 'translate-x-6' : 'translate-x-1'
            }`}
          />
        </button>
      </div>

      {/* Stats */}
      {stats && memoryEnabled && (
        <div className="grid grid-cols-3 gap-4">
          <div className="p-3 bg-blue-50 dark:bg-blue-900/20 rounded-lg text-center">
            <div className="text-lg font-bold text-blue-600 dark:text-blue-400">
              {stats.core_memory.entries}
            </div>
            <div className="text-xs text-gray-600 dark:text-gray-400">Core Facts</div>
          </div>
          <div className="p-3 bg-blue-50 dark:bg-blue-900/20 rounded-lg text-center">
            <div className="text-lg font-bold text-blue-600 dark:text-blue-400">
              {stats.recall_memory.entries}
            </div>
            <div className="text-xs text-gray-600 dark:text-gray-400">Conversations</div>
          </div>
          <div className="p-3 bg-green-50 dark:bg-green-900/20 rounded-lg text-center">
            <div className="text-lg font-bold text-green-600 dark:text-green-400">
              {stats.archival_memory.entries}
            </div>
            <div className="text-xs text-gray-600 dark:text-gray-400">Archived</div>
          </div>
        </div>
      )}

      {error && (
        <div className="p-3 bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300 rounded-lg text-sm">
          {error}
        </div>
      )}

      {/* Memory List */}
      {memoryEnabled && (
        <div>
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-base font-semibold text-gray-900 dark:text-white">
              What I Remember About You
            </h3>
            {memories.length > 0 && (
              <button
                onClick={clearAllMemories}
                className="text-sm text-red-600 dark:text-red-400 hover:underline"
              >
                Clear All
              </button>
            )}
          </div>

          {loading ? (
            <div className="flex items-center justify-center py-8">
              <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-blue-600"></div>
            </div>
          ) : memories.length === 0 ? (
            <div className="text-center py-8 text-gray-500 dark:text-gray-400">
              <p className="text-base mb-2">Nothing yet!</p>
              <p className="text-sm">
                As we chat, I'll naturally learn and remember important things about you and your work.
              </p>
            </div>
          ) : (
            <div className="space-y-4">
              {Object.entries(groupedMemories).map(([category, entries]) => (
                <div key={category}>
                  <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                    {CATEGORY_LABELS[category] || category}
                  </h4>
                  <div className="space-y-2">
                    {entries.map((memory) => (
                      <div
                        key={memory.id}
                        className="p-3 bg-white dark:bg-gray-800 border dark:border-gray-700 rounded-lg group"
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 mb-1 flex-wrap">
                              <span className="font-medium text-gray-900 dark:text-white text-sm">
                                {memory.key.replace(/_/g, ' ')}
                              </span>
                              <span className={`px-1.5 py-0.5 rounded text-xs ${IMPORTANCE_COLORS[memory.importance]}`}>
                                {memory.importance}
                              </span>
                            </div>
                            <p className="text-gray-600 dark:text-gray-300 text-sm">
                              {memory.value}
                            </p>
                            <div className="mt-1 text-xs text-gray-400 dark:text-gray-500">
                              {SOURCE_TYPE_LABELS[memory.source_type] || memory.source_type} • 
                              {new Date(memory.updated_at).toLocaleDateString()}
                            </div>
                          </div>
                          <button
                            onClick={() => deleteMemory(memory.id)}
                            className="p-1 opacity-0 group-hover:opacity-100 hover:bg-red-100 dark:hover:bg-red-900/30 rounded transition-opacity"
                            title="Remove this memory"
                          >
                            <svg className="w-4 h-4 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                            </svg>
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Privacy Note */}
      <div className="p-3 bg-gray-50 dark:bg-gray-800 rounded-lg">
        <p className="text-xs text-gray-500 dark:text-gray-400 text-center">
          🔒 All memories are stored locally on your computer and never sent to external servers.
        </p>
      </div>
    </div>
  );
};
