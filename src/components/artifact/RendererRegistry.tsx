/**
 * RendererRegistry — plugin registry mapping artifact type → React renderer.
 *
 * Single source of truth for "how do we draw an artifact of type X."
 * Renderers register themselves at app boot (import side effects in
 * `registerRenderers.ts`). Consumers call `rendererRegistry.resolve(type)`
 * and render the result with `<Component artifact={...} context={...} />`.
 *
 * Wildcard convention: a registration of `'json:*'` is the fallback for any
 * `json:<kind>` artifact whose specific kind isn't registered. Exact-match
 * always wins over wildcard.
 *
 * See `src/types/artifact.ts` for the Artifact + Renderer types.
 */

import React from 'react';
import type { Artifact, ArtifactType, Renderer, RendererProps, RenderContext } from '../../types/artifact';

class RendererRegistryImpl {
  private map = new Map<string, Renderer<any>>();

  register<TPayload>(type: ArtifactType | string, renderer: Renderer<TPayload>): void {
    if (this.map.has(type)) {
      // Replacement allowed — supports hot-reload and tests.
      // eslint-disable-next-line no-console
      console.warn(`[RendererRegistry] replacing renderer for type: ${type}`);
    }
    this.map.set(type, renderer);
  }

  resolve(type: string): Renderer<any> | undefined {
    const exact = this.map.get(type);
    if (exact) return exact;
    // Fallback: json:<kind> → json:*
    if (type.startsWith('json:')) return this.map.get('json:*');
    return undefined;
  }

  has(type: string): boolean {
    return this.resolve(type) !== undefined;
  }

  registered(): string[] {
    return Array.from(this.map.keys()).sort();
  }
}

export const rendererRegistry = new RendererRegistryImpl();

// ─── Convenience component ────────────────────────────────────────────────
// Resolves and renders in one call. Falls back to a small placeholder if no
// renderer is registered for the artifact's type — never throws so a missing
// renderer can't take down the whole canvas.
export const ArtifactRender: React.FC<{
  artifact: Artifact<any>;
  context: RenderContext;
  className?: string;
}> = ({ artifact, context, className }) => {
  const Component = rendererRegistry.resolve(artifact.type);
  if (!Component) {
    return (
      <div className={`p-3 rounded-lg border border-dashed border-gray-300 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/40 ${className || ''}`}>
        <p className="text-[11px] text-gray-500 dark:text-gray-400">
          No renderer registered for artifact type <code className="font-mono">{artifact.type}</code>
        </p>
      </div>
    );
  }
  const props: RendererProps = { artifact, context, className };
  return <Component {...props} />;
};
