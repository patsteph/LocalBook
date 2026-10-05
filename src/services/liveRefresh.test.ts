import { describe, it, expect } from 'vitest';
import { refreshScopeFor, NO_REFRESH } from './liveRefresh';

describe('refreshScopeFor', () => {
  it('refreshes the notebook list for a source in ANOTHER notebook', () => {
    // The regression: badges show a count for every notebook, so an event the user is
    // not "looking at" still invalidates what is on screen. This returned nothing
    // before, which is why counts only moved on a reload.
    const scope = refreshScopeFor(
      { type: 'source_updated', data: { notebook_id: 'nb-other' } },
      'nb-selected',
    );
    expect(scope.notebooks).toBe(true);
    expect(scope.selectedNotebook).toBe(false);
  });

  it('refreshes both for a source in the selected notebook', () => {
    const scope = refreshScopeFor(
      { type: 'source_updated', data: { notebook_id: 'nb-selected' } },
      'nb-selected',
    );
    expect(scope).toEqual({ notebooks: true, selectedNotebook: true });
  });

  it('still refreshes the list when the event carries no notebook_id', () => {
    const scope = refreshScopeFor({ type: 'source_updated', data: {} }, 'nb-selected');
    expect(scope.notebooks).toBe(true);
    expect(scope.selectedNotebook).toBe(false);
  });

  it('refreshes the list even with no notebook selected', () => {
    const scope = refreshScopeFor(
      { type: 'source_updated', data: { notebook_id: 'nb-a' } },
      null,
    );
    expect(scope.notebooks).toBe(true);
    expect(scope.selectedNotebook).toBe(false);
  });

  it('ignores unrelated message types', () => {
    expect(refreshScopeFor({ type: 'cluster_progress', data: {} }, 'nb-a')).toEqual(NO_REFRESH);
    expect(refreshScopeFor({ type: 'canvas_item_created' }, 'nb-a')).toEqual(NO_REFRESH);
  });

  it('survives a malformed message', () => {
    expect(refreshScopeFor(null, 'nb-a')).toEqual(NO_REFRESH);
    expect(refreshScopeFor(undefined, 'nb-a')).toEqual(NO_REFRESH);
    expect(refreshScopeFor({}, 'nb-a')).toEqual(NO_REFRESH);
  });

  it('a sync that changed sources and audio refreshes the lists and pulses both', () => {
    // 2026-10-01: after a sync the screens showed the old data until a manual reload.
    const scope = refreshScopeFor(
      { type: 'sync_applied', data: { tables: ['notebooks', 'sources', 'audio_generations', 'highlights'] } } as any,
      'nb-selected',
    );
    expect(scope.notebooks).toBe(true);
    expect(scope.selectedNotebook).toBe(true);
    expect(scope.pulses).toEqual(['sourcesUpdated', 'audioUpdated']);
  });

  it('a sync with nothing selected still refreshes the notebook list', () => {
    const scope = refreshScopeFor({ type: 'sync_applied', data: { tables: ['notebooks'] } } as any, null);
    expect(scope).toEqual({ notebooks: true, selectedNotebook: false, pulses: [] });
  });
});
