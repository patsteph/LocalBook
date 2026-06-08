/**
 * ComparisonArtifactRenderer — Phase 4 of v2-information-cortex.
 *
 * Renders a `json:comparison` artifact as a side-by-side HTML card:
 *   ┌─────────────────────────────────────────────────────────┐
 *   │  Source A                  Source B                     │
 *   ├────────────────────┬────────────────────────────────────┤
 *   │  Unique to A       │  Unique to B                       │
 *   │  ...               │  ...                               │
 *   ├────────────────────┴────────────────────────────────────┤
 *   │  Similarities                                            │
 *   │  ...                                                     │
 *   ├──────────────────────────────────────────────────────────┤
 *   │  Differences                                             │
 *   │  ...                                                     │
 *   ├──────────────────────────────────────────────────────────┤
 *   │  Synthesis                                               │
 *   │  ...                                                     │
 *   └──────────────────────────────────────────────────────────┘
 *
 * Uses plain Tailwind classes (no Shadow DOM) — this is structured-data
 * React, not raw LLM HTML. Pure render; chrome lives canvas-level.
 */
import React from 'react';
import type { RendererProps } from '../../../types/artifact';

interface ComparisonPayload {
  source_a?: { id?: string; title?: string };
  source_b?: { id?: string; title?: string };
  similarities?: string[];
  differences?: string[];
  unique_to_a?: string[];
  unique_to_b?: string[];
  synthesis?: string;
}

const Section: React.FC<{ title: string; children: React.ReactNode; emphasis?: 'a' | 'b' | 'shared' }> = ({
  title,
  children,
  emphasis,
}) => {
  const accent =
    emphasis === 'a'
      ? 'border-blue-200 dark:border-blue-800 bg-blue-50 dark:bg-blue-950/30'
      : emphasis === 'b'
      ? 'border-purple-200 dark:border-purple-800 bg-purple-50 dark:bg-purple-950/30'
      : 'border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/40';
  return (
    <div className={`rounded-lg border ${accent} p-3`}>
      <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-400 mb-2">{title}</h4>
      <div className="text-sm text-gray-800 dark:text-gray-200">{children}</div>
    </div>
  );
};

const BulletList: React.FC<{ items?: string[] }> = ({ items }) => {
  if (!items || items.length === 0) {
    return <p className="text-xs italic text-gray-500 dark:text-gray-400">(none)</p>;
  }
  return (
    <ul className="space-y-1 list-disc pl-4">
      {items.map((item, i) => (
        <li key={i}>{item}</li>
      ))}
    </ul>
  );
};

export const ComparisonArtifactRenderer: React.FC<RendererProps<ComparisonPayload>> = ({ artifact, className = '' }) => {
  const payload = (artifact.payload ?? {}) as ComparisonPayload;
  const titleA = payload.source_a?.title || 'Source A';
  const titleB = payload.source_b?.title || 'Source B';

  return (
    <div className={`flex flex-col gap-3 ${className}`.trim()}>
      <div className="grid grid-cols-2 gap-3">
        <Section title={`Unique to ${titleA}`} emphasis="a">
          <BulletList items={payload.unique_to_a} />
        </Section>
        <Section title={`Unique to ${titleB}`} emphasis="b">
          <BulletList items={payload.unique_to_b} />
        </Section>
      </div>
      <Section title="Similarities" emphasis="shared">
        <BulletList items={payload.similarities} />
      </Section>
      <Section title="Differences" emphasis="shared">
        <BulletList items={payload.differences} />
      </Section>
      {payload.synthesis && (
        <Section title="Synthesis" emphasis="shared">
          <p>{payload.synthesis}</p>
        </Section>
      )}
    </div>
  );
};

export default ComparisonArtifactRenderer;
