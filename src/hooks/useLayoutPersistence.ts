import { useState, useCallback, useEffect } from 'react';
import { LayoutNode, makeDefaultLayout } from '../components/canvas/types';

const STORAGE_KEY_PREFIX = 'localbook-layout-';
const DRAWER_KEY = 'localbook-drawers';
const STUDIO_KEY = 'localbook-studio';

export interface DrawerState {
  notebooks: boolean;
  webResearch: boolean;
  sources: boolean;
  collector: boolean;
  people: boolean;
}

export interface StudioState {
  expanded: boolean;
  activeTab: 'documents' | 'audio' | 'video' | 'quiz' | 'visual' | 'writing';
}

const DEFAULT_DRAWERS: DrawerState = {
  notebooks: true,
  webResearch: false,
  sources: false,
  collector: false,
  people: false,
};

const DEFAULT_STUDIO: StudioState = {
  expanded: false,
  activeTab: 'documents',
};

function loadJSON<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

// Sanitize a persisted layout. Two responsibilities:
// 1. Replace any leaves whose `view` was removed from the PanelView union
//    with the safe default ('chat'). Stops removed views (e.g., the
//    'web-research' canvas panel, 'findings') from being restored.
// 2. Collapse any split nodes back to a single leaf — the multi-panel
//    split layout was deprecated when Library + the unified canvas
//    replaced the side-by-side workflow. Some users have splits from
//    older sessions that come back on every launch and break the
//    "single canvas, no bottom-up disruption" design goal. We pick the
//    first leaf as the survivor since that's what the user originally
//    started in. 2026-06-02.
const REMOVED_VIEWS = new Set(['web-research', 'findings']);

function firstLeaf(node: LayoutNode): LayoutNode {
  return node.type === 'leaf' ? node : firstLeaf(node.children[0]);
}

function sanitizeLayout(node: LayoutNode): LayoutNode {
  if (node.type === 'split') {
    // Collapse the whole subtree to its first leaf, then re-sanitize that
    // leaf in case it had a removed view.
    return sanitizeLayout(firstLeaf(node));
  }
  if (REMOVED_VIEWS.has(node.view as string)) {
    return { ...node, view: 'chat' as any, props: undefined };
  }
  return node;
}

function saveJSON(key: string, value: any): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // localStorage full or unavailable
  }
}

export function useCanvasLayout(notebookId: string | null) {
  const storageKey = notebookId ? `${STORAGE_KEY_PREFIX}${notebookId}` : null;

  const [layout, setLayoutState] = useState<LayoutNode>(() => {
    if (!storageKey) return makeDefaultLayout();
    return sanitizeLayout(loadJSON<LayoutNode>(storageKey, makeDefaultLayout()));
  });

  // Reload layout when notebook changes
  useEffect(() => {
    if (!storageKey) {
      setLayoutState(makeDefaultLayout());
      return;
    }
    setLayoutState(sanitizeLayout(loadJSON<LayoutNode>(storageKey, makeDefaultLayout())));
  }, [storageKey]);

  const setLayout = useCallback((updater: LayoutNode | ((prev: LayoutNode) => LayoutNode)) => {
    setLayoutState(prev => {
      const next = typeof updater === 'function' ? updater(prev) : updater;
      if (storageKey) saveJSON(storageKey, next);
      return next;
    });
  }, [storageKey]);

  const resetLayout = useCallback(() => {
    const def = makeDefaultLayout();
    setLayoutState(def);
    if (storageKey) saveJSON(storageKey, def);
  }, [storageKey]);

  return { layout, setLayout, resetLayout };
}

export function useDrawerState() {
  const [drawers, setDrawersState] = useState<DrawerState>(() =>
    loadJSON(DRAWER_KEY, DEFAULT_DRAWERS)
  );

  const setDrawers = useCallback((updater: DrawerState | ((prev: DrawerState) => DrawerState)) => {
    setDrawersState(prev => {
      const next = typeof updater === 'function' ? updater(prev) : updater;
      saveJSON(DRAWER_KEY, next);
      return next;
    });
  }, []);

  const toggleDrawer = useCallback((drawer: keyof DrawerState) => {
    setDrawers(prev => ({ ...prev, [drawer]: !prev[drawer] }));
  }, [setDrawers]);

  return { drawers, setDrawers, toggleDrawer };
}

export function useStudioState() {
  const [studio, setStudioState] = useState<StudioState>(() =>
    loadJSON(STUDIO_KEY, DEFAULT_STUDIO)
  );

  const setStudio = useCallback((updater: StudioState | ((prev: StudioState) => StudioState)) => {
    setStudioState(prev => {
      const next = typeof updater === 'function' ? updater(prev) : updater;
      saveJSON(STUDIO_KEY, next);
      return next;
    });
  }, []);

  const toggleStudio = useCallback(() => {
    setStudio(prev => ({ ...prev, expanded: !prev.expanded }));
  }, [setStudio]);

  const setStudioTab = useCallback((tab: StudioState['activeTab']) => {
    setStudio(prev => ({ ...prev, activeTab: tab, expanded: true }));
  }, [setStudio]);

  return { studio, setStudio, toggleStudio, setStudioTab };
}
