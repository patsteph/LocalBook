import React from 'react';
import { ChatMessage, Citation as CitationType, InlineVisualData } from '../../types';
import { Citation, CitationList } from '../Citation';
import { MermaidRenderer } from '../shared/MermaidRenderer';
import { InlineVisual } from '../visual';
import { BookmarkButton } from '../shared/BookmarkButton';

interface ChatMessageBubbleProps {
  message: ChatMessage;
  index: number;
  previousMessage?: ChatMessage;
  notebookId: string | null;
  onFollowUp: (question: string) => void;
  onViewSource: (sourceId: string, sourceName: string, searchTerm: string) => void;
  onGenerateVisual: (index: number, content: string, guidance?: string, palette?: string) => void;
  onSaveVisual: (visual: InlineVisualData) => void;
  onOpenInStudio: (content: string) => void;
  onExportVisual: (visual: InlineVisualData, format: 'png' | 'svg') => void;
  onSelectAlternative: (index: number, alt: any) => void;
  onTaglineChange: (index: number, tagline: string) => void;
  onDismissLowConfidence: (message: ChatMessage) => void;
  onOpenWebSearch?: (query?: string) => void;
}

// Render message content with inline clickable citations and mermaid diagrams
const renderMessageWithCitations = (
  content: string,
  citations: CitationType[] | undefined,
  onViewSource: (sourceId: string, sourceName: string, searchTerm: string) => void
) => {
  // First, detect and extract mermaid code blocks
  const mermaidBlockRegex = /```mermaid\s*([\s\S]*?)```/g;
  const segments: { type: 'text' | 'mermaid'; content: string }[] = [];
  let lastIndex = 0;
  let mermaidMatch;

  while ((mermaidMatch = mermaidBlockRegex.exec(content)) !== null) {
    if (mermaidMatch.index > lastIndex) {
      segments.push({ type: 'text', content: content.substring(lastIndex, mermaidMatch.index) });
    }
    segments.push({ type: 'mermaid', content: mermaidMatch[1].trim() });
    lastIndex = mermaidMatch.index + mermaidMatch[0].length;
  }

  if (lastIndex < content.length) {
    segments.push({ type: 'text', content: content.substring(lastIndex) });
  }

  // If no mermaid blocks and no citations, return simple text
  if (segments.length === 1 && segments[0].type === 'text' && (!citations || citations.length === 0)) {
    return <p className="text-sm whitespace-pre-wrap">{content}</p>;
  }

  return (
    <div className="space-y-3">
      {segments.map((segment, segIndex) => {
        if (segment.type === 'mermaid') {
          return (
            <div key={segIndex} className="my-3">
              <MermaidRenderer code={segment.content} className="border border-gray-200 dark:border-gray-600 rounded-lg" />
            </div>
          );
        }

        const textContent = segment.content;
        if (!citations || citations.length === 0) {
          return textContent.trim() ? (
            <p key={segIndex} className="text-sm whitespace-pre-wrap">{textContent}</p>
          ) : null;
        }

        // Parse citations within text
        const parts: (string | React.ReactElement)[] = [];
        let textLastIndex = 0;
        const citationRegex = /\[(\d+)\]/g;
        let match;

        while ((match = citationRegex.exec(textContent)) !== null) {
          const citationNumber = parseInt(match[1], 10);
          const citation = citations.find(c => c.number === citationNumber);

          if (match.index > textLastIndex) {
            parts.push(textContent.substring(textLastIndex, match.index));
          }

          if (citation) {
            parts.push(<Citation key={`cite-${segIndex}-${citationNumber}-${match.index}`} citation={citation} onViewSource={onViewSource} />);
          } else {
            parts.push(match[0]);
          }

          textLastIndex = match.index + match[0].length;
        }

        if (textLastIndex < textContent.length) {
          parts.push(textContent.substring(textLastIndex));
        }

        return parts.length > 0 ? (
          <p key={segIndex} className="text-sm whitespace-pre-wrap">
            {parts.map((part, i) =>
              typeof part === 'string' ? <span key={i}>{part}</span> : part
            )}
          </p>
        ) : null;
      })}
    </div>
  );
};

export const ChatMessageBubble: React.FC<ChatMessageBubbleProps> = ({
  message,
  index,
  previousMessage,
  notebookId,
  onFollowUp,
  onViewSource,
  onGenerateVisual,
  onSaveVisual,
  onOpenInStudio,
  onExportVisual,
  onSelectAlternative,
  onTaglineChange,
  onDismissLowConfidence,
  onOpenWebSearch,
}) => {
  return (
    <div className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-3xl rounded-lg p-3 ${
          message.role === 'user'
            ? 'bg-blue-600 text-white'
            : 'bg-gray-100 dark:bg-gray-800 text-gray-900 dark:text-gray-100'
        }`}
      >
        {message.role === 'user' ? (
          <p className="text-sm whitespace-pre-wrap">{message.content}</p>
        ) : (
          <>
            {/* Status message - shown while processing (Phase 1.2) */}
            {message.statusMessage && !message.content && (
              <div className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300">
                <span className="animate-pulse">{message.statusMessage}</span>
              </div>
            )}
            
            {/* Memory indicator - subtle tooltip when memory is used */}
            {message.memoryUsed && message.memoryUsed.length > 0 && (
              <div className="mb-2 flex items-center gap-1.5 group relative">
                <span className="text-xs text-purple-500 dark:text-purple-400 flex items-center gap-1 cursor-help">
                  <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
                    <path d="M10 2a8 8 0 100 16 8 8 0 000-16zm0 14a6 6 0 110-12 6 6 0 010 12zm-1-5h2v2H9v-2zm0-6h2v4H9V5z"/>
                  </svg>
                  Using context from our conversations
                </span>
                {message.memoryContextSummary && (
                  <div className="absolute left-0 top-full mt-1 p-2 bg-gray-900 text-white text-xs rounded shadow-lg opacity-0 group-hover:opacity-100 transition-opacity z-10 max-w-xs whitespace-normal">
                    {message.memoryContextSummary}
                  </div>
                )}
              </div>
            )}
            
            {/* Answer */}
            {message.content && (
              renderMessageWithCitations(message.content, message.citations, onViewSource)
            )}
            
            {message.citations && message.citations.length > 0 && (
              <CitationList citations={message.citations} onViewSource={onViewSource} />
            )}
            {message.web_sources && message.web_sources.length > 0 && (
              <div className="mt-2 pt-2 border-t border-gray-200 dark:border-gray-700">
                <p className="text-xs font-semibold text-gray-600 dark:text-gray-400 mb-1.5">Web Sources:</p>
                <div className="space-y-2">
                  {message.web_sources.map((source, idx) => (
                    <a
                      key={idx}
                      href={source.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="block p-2 bg-blue-50 dark:bg-blue-900/20 rounded text-xs hover:bg-blue-100 dark:hover:bg-blue-900/30 transition-colors"
                    >
                      <div className="flex items-start gap-2">
                        <span className="text-base">🌐</span>
                        <div className="flex-1">
                          <p className="font-medium text-blue-700 dark:text-blue-400">{source.title}</p>
                          <p className="text-gray-600 dark:text-gray-400 mt-1">{source.snippet}</p>
                          <p className="text-gray-500 dark:text-gray-400 mt-1 truncate">{source.url}</p>
                        </div>
                      </div>
                    </a>
                  ))}
                </div>
              </div>
            )}
            {message.follow_up_questions && message.follow_up_questions.length > 0 && (
              <div className="mt-2 pt-2 border-t border-gray-200 dark:border-gray-700">
                <p className="text-xs font-semibold text-gray-600 dark:text-gray-400 mb-1.5">💡 Follow-up Questions:</p>
                <div className="flex flex-wrap gap-1.5">
                  {message.follow_up_questions.map((question, idx) => (
                    <button
                      key={idx}
                      onClick={() => onFollowUp(question)}
                      className="text-xs px-2.5 py-1 bg-purple-100 dark:bg-purple-800/40 text-purple-800 dark:text-purple-200 rounded-full hover:bg-purple-200 dark:hover:bg-purple-700/50 transition-colors border border-purple-300 dark:border-purple-600"
                    >
                      {question}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {/* Inline Visual - Canvas feature */}
            {(message.inlineVisual || message.visualLoading) && (
              <InlineVisual
                visual={message.inlineVisual ? {
                  id: message.inlineVisual.id,
                  type: message.inlineVisual.type,
                  code: message.inlineVisual.code,
                  title: message.inlineVisual.title,
                  template_id: message.inlineVisual.template_id,
                  pattern: message.inlineVisual.pattern,
                  tagline: message.inlineVisual.tagline,
                } : null}
                alternatives={(message.alternativeVisuals || []).map(alt => ({
                  id: alt.id,
                  type: alt.type,
                  code: alt.code,
                  title: alt.title,
                  template_id: alt.template_id,
                  pattern: alt.pattern,
                }))}
                loading={message.visualLoading}
                loadingMessage={message.visualLoadingMessage || 'Creating visual...'}
                onSaveToFindings={message.inlineVisual ? () => onSaveVisual(message.inlineVisual!) : undefined}
                onOpenInStudio={() => onOpenInStudio(message.content)}
                onExport={message.inlineVisual ? (format) => onExportVisual(message.inlineVisual!, format) : undefined}
                onRegenerate={() => onGenerateVisual(index, message.content)}
                onRegenerateWithGuidance={(guidance) => onGenerateVisual(index, message.content, guidance)}
                onRegenerateWithPalette={(palette) => onGenerateVisual(index, message.content, undefined, palette)}
                onSelectAlternative={(alt) => onSelectAlternative(index, alt)}
                onTaglineChange={(newTagline) => onTaglineChange(index, newTagline)}
              />
            )}
            
            {/* Action buttons row - Create Visual & Bookmark */}
            {message.content && message.content.length > 50 && (
              <div className="mt-2 pt-2 border-t border-gray-200 dark:border-gray-700 flex items-center gap-2">
                {/* Create Visual button - shows when no inline visual exists */}
                {message.content.length > 100 && !message.inlineVisual && !message.visualLoading && (
                  <button
                    onClick={() => onGenerateVisual(index, message.content)}
                    className="text-xs px-2.5 py-1 bg-green-100 dark:bg-green-800/40 text-green-800 dark:text-green-200 rounded-full hover:bg-green-200 dark:hover:bg-green-700/50 transition-colors border border-green-300 dark:border-green-600 flex items-center gap-1"
                  >
                    🎨 Create Visual
                  </button>
                )}
                {/* Bookmark answer to Findings */}
                {notebookId && (
                  <BookmarkButton
                    notebookId={notebookId}
                    type="answer"
                    title={message.content.substring(0, 60) + (message.content.length > 60 ? '...' : '')}
                    content={{
                      question: previousMessage?.content || 'Research question',
                      answer: message.content,
                      citations: message.citations,
                    }}
                  />
                )}
              </div>
            )}
            {/* Curator Overwatch Aside */}
            {message.curatorAside && (
              <div className="mt-3 p-2.5 rounded-lg bg-indigo-50 dark:bg-indigo-900/20 border-l-3 border-indigo-500 dark:border-indigo-400">
                <div className="flex items-center gap-1.5 mb-1">
                  <div className="w-4 h-4 rounded-full bg-indigo-600 dark:bg-indigo-500 flex items-center justify-center text-white text-[7px] font-bold flex-shrink-0">
                    {(message.curatorName || 'C').charAt(0).toUpperCase()}
                  </div>
                  <span className="text-[10px] font-semibold text-indigo-600 dark:text-indigo-400 uppercase tracking-wide">
                    {message.curatorName || 'Curator'}
                  </span>
                </div>
                <p className="text-xs text-indigo-800 dark:text-indigo-200 leading-relaxed">
                  {message.curatorAside}
                </p>
              </div>
            )}
            {message.lowConfidenceQuery && (
              <div className="mt-3 pt-2 border-t border-gray-200 dark:border-gray-700">
                <div className="flex gap-2">
                  <button
                    onClick={() => {
                      if (onOpenWebSearch) {
                        onOpenWebSearch(message.lowConfidenceQuery);
                      }
                    }}
                    className="px-3 py-1.5 bg-blue-600 text-white text-xs rounded-lg hover:bg-blue-700 transition-colors font-medium"
                  >
                    Yes, search the web
                  </button>
                  <button
                    onClick={() => onDismissLowConfidence(message)}
                    className="px-3 py-1.5 bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300 text-xs rounded-lg hover:bg-gray-300 dark:hover:bg-gray-600 transition-colors font-medium"
                  >
                    No, continue
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
};
