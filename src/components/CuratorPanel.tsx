import React, { useState, useEffect, useRef, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import { curatorService } from '../services/curatorApi';

interface CuratorConfig {
  name: string;
  personality: string;
  oversight?: Record<string, any>;
  synthesis?: Record<string, any>;
  voice?: Record<string, any>;
}

interface CuratorMessage {
  role: 'user' | 'curator';
  content: string;
  timestamp: Date;
}

interface BriefStory {
  title: string;
  source_name?: string;
  url?: string;
  summary?: string;
}

interface BriefNotebook {
  notebook_id: string;
  name: string;
  subject?: string;
  items_added: number;
  pending_approval: number;
  flagged_important: number;
  top_finding: string | null;
  recent_stories?: BriefStory[];
  person_changes?: string[];
  upcoming_key_dates?: string[];
  collection_runs?: number;
  collection_items_found?: number;
  unfinished_threads?: string[];
  emerging_topics?: string[];
  one_week_ago_items?: string[];
}

interface MorningBriefData {
  away_duration: string;
  notebooks?: BriefNotebook[];
  cross_notebook_insight?: string | null;
  narrative?: string;
}

interface CuratorPanelProps {
  notebookId: string | null;
  morningBrief?: MorningBriefData | null;
}

export const CuratorPanel: React.FC<CuratorPanelProps> = ({ notebookId, morningBrief }) => {
  const [config, setConfig] = useState<CuratorConfig | null>(null);
  const [messages, setMessages] = useState<CuratorMessage[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [editingName, setEditingName] = useState(false);
  const [nameInput, setNameInput] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Inject morning brief as the curator's opening message when navigated from banner
  const briefConsumedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!morningBrief) return;
    // Only consume each brief once (keyed by generated_at or away_duration)
    const briefKey = morningBrief.away_duration + (morningBrief.notebooks?.length || 0);
    if (briefConsumedRef.current === briefKey) return;
    briefConsumedRef.current = briefKey;

    const greeting = new Date().getHours() < 12 ? 'morning' : new Date().getHours() < 17 ? 'afternoon' : 'evening';

    // Use LLM narrative if available — this is the newsletter-quality brief
    // Guard: skip error strings that leaked through as "narrative"
    const narrative = morningBrief.narrative || '';
    const isValidNarrative = narrative.length > 30 && 
      !narrative.startsWith('Request timed out') && 
      !narrative.startsWith('Error:');
    
    if (isValidNarrative) {
      setMessages([{
        role: 'curator',
        content: `Good ${greeting}! You've been away for ${morningBrief.away_duration}.\n\n${narrative}\n\n---\n*Ask me anything about what happened while you were away.*`,
        timestamp: new Date(),
      }]);
      return;
    }

    // Fallback: build rich structured brief from raw data
    const lines: string[] = [`Good ${greeting}! You've been away for ${morningBrief.away_duration}. Here's what happened:\n`];
    for (const nb of morningBrief.notebooks || []) {
      const label = nb.subject ? `**${nb.name}** (${nb.subject})` : `**${nb.name}**`;
      lines.push(label);

      // Recent stories — specific titles, not just counts
      if (nb.recent_stories && nb.recent_stories.length > 0) {
        for (const story of nb.recent_stories.slice(0, 3)) {
          let storyLine = `  - "${story.title}"`;
          if (story.source_name) storyLine += ` *(${story.source_name})*`;
          if (story.summary) storyLine += ` — ${story.summary.slice(0, 120)}`;
          lines.push(storyLine);
        }
        if (nb.items_added > nb.recent_stories.length) {
          lines.push(`  - ...and ${nb.items_added - nb.recent_stories.length} more`);
        }
      } else if (nb.items_added > 0) {
        lines.push(`  - ${nb.items_added} new items collected`);
      }

      // People updates
      if (nb.person_changes && nb.person_changes.length > 0) {
        for (const pc of nb.person_changes.slice(0, 3)) {
          lines.push(`  - 👤 ${pc}`);
        }
      }

      // Upcoming key dates
      if (nb.upcoming_key_dates && nb.upcoming_key_dates.length > 0) {
        for (const kd of nb.upcoming_key_dates.slice(0, 2)) {
          lines.push(`  - 📅 ${kd}`);
        }
      }

      // Pending approval
      if (nb.pending_approval > 0) {
        lines.push(`  - ⏳ ${nb.pending_approval} items awaiting your review`);
      }

      // Unfinished threads
      if (nb.unfinished_threads && nb.unfinished_threads.length > 0) {
        lines.push(`  - 💬 **Unfinished threads:**`);
        for (const thread of nb.unfinished_threads.slice(0, 2)) {
          lines.push(`    - "${thread}"`);
        }
      }

      // Emerging topics
      if (nb.emerging_topics && nb.emerging_topics.length > 0) {
        lines.push(`  - 🔮 **Emerging interests:** ${nb.emerging_topics.join(', ')} — new this week`);
      }

      // One week ago
      if (nb.one_week_ago_items && nb.one_week_ago_items.length > 0) {
        lines.push(`  - ⏪ **One week ago** you were reading:`);
        for (const item of nb.one_week_ago_items.slice(0, 2)) {
          lines.push(`    - "${item}"`);
        }
      }

      lines.push('');
    }

    if (morningBrief.cross_notebook_insight) {
      lines.push(`💡 **Cross-notebook insight:** ${morningBrief.cross_notebook_insight}`);
    }
    lines.push('---\n*Ask me anything about what happened while you were away.*');

    setMessages([{
      role: 'curator',
      content: lines.join('\n'),
      timestamp: new Date(),
    }]);
  }, [morningBrief]);

  // Load config on mount
  useEffect(() => {
    curatorService.getConfig()
      .then((data: any) => {
        if (data) {
          setConfig(data);
          setNameInput(data.name || 'Curator');
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSendMessage = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || loading) return;

    const userMsg: CuratorMessage = {
      role: 'user',
      content: input.trim(),
      timestamp: new Date(),
    };
    setMessages(prev => [...prev, userMsg]);
    setInput('');
    setLoading(true);

    try {
      const data = await curatorService.chat(userMsg.content, notebookId || undefined);
      setMessages(prev => [...prev, {
        role: 'curator',
        content: data.reply,
        timestamp: new Date(),
      }]);
    } catch {
      setMessages(prev => [...prev, {
        role: 'curator',
        content: 'Connection error. Please try again.',
        timestamp: new Date(),
      }]);
    } finally {
      setLoading(false);
    }
  }, [input, loading, messages, notebookId]);

  const handleSaveName = useCallback(async () => {
    if (!nameInput.trim()) return;
    try {
      const data = await curatorService.updateConfig({ name: nameInput.trim() } as any);
      setConfig(data.config);
      setEditingName(false);
    } catch {
      // ignore
    }
  }, [nameInput]);

  const curatorName = config?.name || 'Curator';

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex-shrink-0 px-4 py-3 border-b border-gray-200 dark:border-gray-700 bg-gradient-to-r from-indigo-50 to-purple-50 dark:from-indigo-900/20 dark:to-purple-900/20">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-full bg-indigo-600 dark:bg-indigo-500 flex items-center justify-center text-white text-sm font-bold">
              {curatorName.charAt(0).toUpperCase()}
            </div>
            {editingName ? (
              <div className="flex items-center gap-1">
                <input
                  type="text"
                  value={nameInput}
                  onChange={e => setNameInput(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && handleSaveName()}
                  className="text-sm font-semibold bg-white dark:bg-gray-800 border border-indigo-300 dark:border-indigo-600 rounded px-2 py-0.5 w-32 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                  autoFocus
                />
                <button
                  onClick={handleSaveName}
                  className="text-xs text-indigo-600 dark:text-indigo-400 hover:text-indigo-800 font-medium"
                >
                  Save
                </button>
                <button
                  onClick={() => { setEditingName(false); setNameInput(curatorName); }}
                  className="text-xs text-gray-400 hover:text-gray-600"
                >
                  Cancel
                </button>
              </div>
            ) : (
              <div className="flex items-center gap-1.5">
                <h3 className="text-sm font-semibold text-indigo-800 dark:text-indigo-200">
                  {curatorName}
                </h3>
                <button
                  onClick={() => { setEditingName(true); setNameInput(curatorName); }}
                  className="text-gray-400 hover:text-indigo-600 dark:hover:text-indigo-400"
                  title="Rename"
                >
                  <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                  </svg>
                </button>
              </div>
            )}
          </div>
          <span className="text-[10px] text-indigo-500 dark:text-indigo-400 bg-indigo-100 dark:bg-indigo-900/40 px-2 py-0.5 rounded-full">
            Cross-Notebook Advisor
          </span>
        </div>
        <p className="text-[10px] text-gray-500 dark:text-gray-400 mt-1 ml-10">
          {config?.personality || 'Your research advisor with cross-notebook awareness'}
        </p>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
        {messages.length === 0 && (
          <div className="text-center mt-8 space-y-3">
            <div className="w-12 h-12 rounded-full bg-indigo-100 dark:bg-indigo-900/40 flex items-center justify-center mx-auto">
              <svg className="w-6 h-6 text-indigo-600 dark:text-indigo-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
              </svg>
            </div>
            <p className="text-sm text-gray-600 dark:text-gray-300 font-medium">
              Chat with {curatorName}
            </p>
            <p className="text-xs text-gray-400 dark:text-gray-500 max-w-xs mx-auto">
              Ask about connections across your notebooks, get a devil's advocate perspective, or discuss research strategy.
            </p>
            <div className="flex flex-wrap justify-center gap-1.5 mt-2">
              {[
                'What patterns do you see across my notebooks?',
                'Play devil\'s advocate on my current research',
                'What gaps should I fill?',
              ].map((suggestion, i) => (
                <button
                  key={i}
                  onClick={() => setInput(suggestion)}
                  className="text-[10px] px-2.5 py-1 bg-indigo-50 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-300 rounded-full hover:bg-indigo-100 dark:hover:bg-indigo-800/40 border border-indigo-200 dark:border-indigo-700 transition-colors"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <div
            key={i}
            className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`max-w-[85%] rounded-lg p-3 ${
                msg.role === 'user'
                  ? 'bg-blue-600 text-white'
                  : 'bg-indigo-50 dark:bg-indigo-900/30 text-indigo-900 dark:text-indigo-100 border border-indigo-200 dark:border-indigo-700/50'
              }`}
            >
              {msg.role === 'curator' && (
                <div className="flex items-center gap-1.5 mb-1">
                  <div className="w-4 h-4 rounded-full bg-indigo-600 dark:bg-indigo-500 flex items-center justify-center text-white text-[8px] font-bold">
                    {curatorName.charAt(0).toUpperCase()}
                  </div>
                  <span className="text-[10px] font-semibold text-indigo-600 dark:text-indigo-400">
                    {curatorName}
                  </span>
                </div>
              )}
              {msg.role === 'curator' ? (
                <div className="text-sm prose prose-sm dark:prose-invert max-w-none prose-p:my-1 prose-headings:mt-4 prose-headings:mb-1 prose-ul:my-1 prose-li:my-0 prose-hr:my-4">
                  <ReactMarkdown>{msg.content}</ReactMarkdown>
                </div>
              ) : (
                <p className="text-sm whitespace-pre-wrap">{msg.content}</p>
              )}
            </div>
          </div>
        ))}

        {loading && (
          <div className="flex justify-start">
            <div className="bg-indigo-50 dark:bg-indigo-900/30 border border-indigo-200 dark:border-indigo-700/50 rounded-lg p-3 max-w-[85%]">
              <div className="flex items-center gap-1.5 mb-1">
                <div className="w-4 h-4 rounded-full bg-indigo-600 dark:bg-indigo-500 flex items-center justify-center text-white text-[8px] font-bold">
                  {curatorName.charAt(0).toUpperCase()}
                </div>
                <span className="text-[10px] font-semibold text-indigo-600 dark:text-indigo-400">
                  {curatorName}
                </span>
              </div>
              <span className="text-sm text-indigo-400 animate-pulse">Thinking...</span>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <form onSubmit={handleSendMessage} className="flex-shrink-0 p-3 border-t border-gray-200 dark:border-gray-700">
        <div className="flex gap-2">
          <input
            type="text"
            value={input}
            onChange={e => setInput(e.target.value)}
            placeholder={`Ask ${curatorName} anything...`}
            className="flex-1 text-sm bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded-lg px-3 py-2 focus:outline-none focus:ring-1 focus:ring-indigo-500 dark:text-gray-100 placeholder-gray-400"
            disabled={loading}
          />
          <button
            type="submit"
            disabled={loading || !input.trim()}
            className="px-3 py-2 bg-indigo-600 text-white text-sm rounded-lg hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
            </svg>
          </button>
        </div>
      </form>
    </div>
  );
};

export default CuratorPanel;
