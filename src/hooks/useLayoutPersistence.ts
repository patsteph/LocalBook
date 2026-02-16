import { useState, useCallback, useEffect } from 'react';
import { LayoutNode, makeDefaultLayout } from '../components/canvas/types';

const STORAGE_KEY_PREFIX = 'localbook-layout-';
const DRAWER_KEY = 'localbook-drawers';
const STUDIO_KEY = 'localbook-studio';

export interface DrawerState {
  notebooks: boolean;
  sources: boolean;
  collector: boolean;
  people: boolean;
}

export interface StudioState {
  expanded: boolean;
  activeTab: 'documents' | 'audio' | 'quiz' | 'visual' | 'writing';
}

const DEFAULT_DRAWERS: DrawerState = {
  notebooks: true,
  sources: true,
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
    return loadJSON<LayoutNode>(storageKey, makeDefaultLayout());
  });

  // Reload layout when notebook changes
  useEffect(() => {
    if (!storageKey) {
      setLayoutState(makeDefaultLayout());
      return;
    }
    setLayoutState(loadJSON<LayoutNode>(storageKey, makeDefaultLayout()));
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
