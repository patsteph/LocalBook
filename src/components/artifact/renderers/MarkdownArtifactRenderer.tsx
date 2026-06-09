/**
 * MarkdownArtifactRenderer — canonical markdown renderer for the artifact
 * registry. Payload is the markdown text.
 *
 * Extracted from `CanvasItemCard.tsx` document-type code (the ReactMarkdown
 * + custom code/pre/a handlers for Feynman quiz/audio/knowledge-map blocks,
 * mermaid code-fences, and feynman-quiz nav links).
 *
 * Behavior preserved exactly — chat-response previously used plain
 * ReactMarkdown with no custom components, but the enhanced handlers are
 * inert on non-matching content, so it's safe to use one renderer for
 * both contexts. Context branches only adjust prose classes (size,
 * spacing) per render surface.
 */

import React from 'react';
import ReactMarkdown from 'react-markdown';
import { Target } from 'lucide-react';
import type { RendererProps } from '../../../types/artifact';
import { MermaidRenderer } from '../../shared/MermaidRenderer';
import { SVGRenderer } from '../../shared/SVGRenderer';
import { FeynmanQuizBlock, FeynmanAudioBlock, isFeynmanBlock } from '../../shared/FeynmanBlocks';
import { ArtifactRender } from '../RendererRegistry';

const proseClasses = {
  'canvas-full': 'prose prose-sm dark:prose-invert max-w-none prose-p:my-2 prose-headings:mt-4 prose-headings:mb-1 prose-ul:my-2 prose-li:my-0 prose-hr:my-4',
  'chat-inline': 'prose prose-sm dark:prose-invert max-w-none prose-p:my-2 prose-headings:mt-3 prose-headings:mb-1',
  'source-viewer': 'prose dark:prose-invert max-w-none',
  'export-image': 'prose max-w-none',
  'export-pdf': 'prose max-w-none',
} as const;

// Extra optional props for callers that need to inject markdown component
// overrides on top of the built-in code/pre/a handlers. Used by
// ChatMessageBubble to wrap p/li/td with citation injection without
// duplicating the fence-handling logic. The renderer merges these last so
// caller overrides win on collision (but caller MUST preserve the built-in
// code/pre/a behavior if they override those keys).
export interface MarkdownArtifactRendererProps extends RendererProps<string> {
  componentOverrides?: Record<string, React.FC<any>>;
}

export const MarkdownArtifactRenderer: React.FC<MarkdownArtifactRendererProps> = ({
  artifact,
  context,
  className = '',
  componentOverrides,
}) => {
  const text = typeof artifact.payload === 'string' ? artifact.payload : '';
  if (!text) {
    return null;
  }

  const docTitle = artifact.title;

  return (
    <div className={`${proseClasses[context]} ${className}`.trim()}>
      <ReactMarkdown
        components={{
          a: ({ href, children, ...props }) => {
            // Intercept Feynman quiz links: #feynman-quiz:difficulty:label
            if (href?.startsWith('#feynman-quiz:')) {
              const parts = href.replace('#feynman-quiz:', '').split(':');
              const difficulty = parts[0] || 'medium';
              const label = parts.slice(1).join(':') || 'Quiz';
              return (
                <button
                  onClick={(e) => {
                    e.preventDefault();
                    const topic = (docTitle || '').replace(/^Document:\s*/i, '').replace(/^Feynman.*?:\s*/i, '');
                    window.dispatchEvent(new CustomEvent('feynmanQuizNav', {
                      detail: { topic: `${label}: ${topic}`.trim(), difficulty },
                    }));
                  }}
                  className="inline-flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-lg no-underline cursor-pointer transition-colors bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300 hover:bg-purple-200 dark:hover:bg-purple-800/50 border border-purple-300 dark:border-purple-700"
                >
                  <Target className="w-4 h-4" />
                  {children}
                </button>
              );
            }
            return <a href={href} {...props}>{children}</a>;
          },
          code: ({ className: codeClass, children, ...props }) => {
            const raw = String(children).replace(/\n$/, '');
            if (/language-mermaid/.test(codeClass || '')) {
              return (
                <div className="not-prose my-4">
                  <MermaidRenderer code={raw} className="border border-gray-200 dark:border-gray-600 rounded-lg" />
                </div>
              );
            }
            if (/language-feynman-quiz/.test(codeClass || '')) {
              return <FeynmanQuizBlock json={raw} docTitle={docTitle} />;
            }
            if (/language-feynman-audio/.test(codeClass || '')) {
              return <FeynmanAudioBlock json={raw} />;
            }
            if (/language-feynman-knowledge-map/.test(codeClass || '')) {
              return (
                <div className="not-prose my-4">
                  <SVGRenderer svg={raw} className="border border-gray-200 dark:border-gray-600 rounded-lg" />
                </div>
              );
            }
            // Phase 4 mixed-medium fence languages — dispatch via the
            // artifact registry so the same payload types work whether
            // they're standalone canvas items or inline doc fences.
            if (/language-svg/.test(codeClass || '')) {
              return (
                <div className="not-prose my-4">
                  <ArtifactRender
                    artifact={{ id: `inline-svg-${raw.length}`, type: 'svg', payload: raw }}
                    context={context}
                    className="border border-gray-200 dark:border-gray-600 rounded-lg"
                  />
                </div>
              );
            }
            if (/language-klein/.test(codeClass || '')) {
              return (
                <div className="not-prose my-4">
                  <ArtifactRender
                    artifact={{ id: `inline-klein-${raw.length}`, type: 'klein', payload: raw }}
                    context={context}
                    className="border border-gray-200 dark:border-gray-600 rounded-lg"
                  />
                </div>
              );
            }
            if (/language-json-chart/.test(codeClass || '')) {
              try {
                const payload = JSON.parse(raw);
                return (
                  <div className="not-prose my-4">
                    <ArtifactRender
                      artifact={{ id: `inline-chart-${raw.length}`, type: 'json:chart', payload }}
                      context={context}
                      className="border border-gray-200 dark:border-gray-600 rounded-lg"
                    />
                  </div>
                );
              } catch {
                // Malformed chart JSON — drop quietly. Doc text still reads.
                return null;
              }
            }
            // Phase I/J (2026-06-09) — interactive Correspondent cards.
            // ```json-correspondent-queue and ```json-correspondent-subscriptions
            // dispatch to the React renderers that mirror the Settings UI.
            if (/language-json-correspondent-queue/.test(codeClass || '')) {
              try {
                const payload = JSON.parse(raw);
                return (
                  <ArtifactRender
                    artifact={{ id: `inline-corq-${raw.length}`, type: 'json:correspondent-queue', payload }}
                    context={context}
                  />
                );
              } catch {
                return null;
              }
            }
            if (/language-json-correspondent-subscriptions/.test(codeClass || '')) {
              try {
                const payload = JSON.parse(raw);
                return (
                  <ArtifactRender
                    artifact={{ id: `inline-corsub-${raw.length}`, type: 'json:correspondent-subscriptions', payload }}
                    context={context}
                  />
                );
              } catch {
                return null;
              }
            }
            // Phase 14 (2026-06-08) — `html` code-fence routes to the
            // HtmlArtifactRenderer (Shadow DOM + DOMPurify strict). Lets
            // Curator asides, anticipatory drafts, and any markdown surface
            // embed dashboard / perspectives / deep-dive HTML inline without
            // requiring a separate artifact envelope. The fence content is
            // the raw HTML string.
            if (/language-html\b/.test(codeClass || '')) {
              return (
                <div className="not-prose my-4">
                  <ArtifactRender
                    artifact={{ id: `inline-html-${raw.length}`, type: 'html', payload: raw }}
                    context={context}
                    className="border border-gray-200 dark:border-gray-600 rounded-lg"
                  />
                </div>
              );
            }
            return <code className={codeClass} {...props}>{children}</code>;
          },
          pre: ({ children, ...props }) => {
            const child = children as any;
            const cls = child?.props?.className || '';
            if (
              cls && (
                isFeynmanBlock(cls) ||
                /language-mermaid/.test(cls) ||
                /language-feynman-knowledge-map/.test(cls) ||
                /language-svg/.test(cls) ||
                /language-klein/.test(cls) ||
                /language-json-chart/.test(cls) ||
                /language-json-correspondent-queue/.test(cls) ||
                /language-json-correspondent-subscriptions/.test(cls) ||
                /language-html\b/.test(cls)
              )
            ) {
              return <>{children}</>;
            }
            return <pre {...props}>{children}</pre>;
          },
          // Caller overrides merged last — primarily for citation injection
          // wrappers (chat) on p/li/td. Caller is responsible for not
          // clobbering code/pre/a unless they intend to.
          ...(componentOverrides || {}),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
};

export default MarkdownArtifactRenderer;
