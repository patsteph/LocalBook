import { createContext, useContext, useMemo } from 'react';
import { LayoutNode, PanelView, CanvasItem } from './types';

// ---------------------------------------------------------------------------
// Canvas Items context — high-frequency updates (streaming tokens, edits)
// ---------------------------------------------------------------------------
export interface CanvasItemsContextValue {
  canvasItems: CanvasItem[];
  addCanvasItem: (item: Omit<CanvasItem, 'id' | 'timestamp'> & { id?: string }) => void;
  removeCanvasItem: (id: string) => void;
  updateCanvasItem: (id: string, updates: Partial<Pick<CanvasItem, 'title' | 'content' | 'status' | 'metadata'>>) => void;
  toggleCanvasItemCollapse: (id: string) => void;
  clearCanvas: () => void;
}

const CanvasItemsCtx = createContext<CanvasItemsContextValue | null>(null);
export const CanvasItemsProvider = CanvasItemsCtx.Provider;

/** Focused hook — only re-renders when canvas items change */
export function useCanvasItems(): CanvasItemsContextValue {
  const ctx = useContext(CanvasItemsCtx);
  if (!ctx) throw new Error('useCanvasItems must be used within CanvasItemsProvider');
  return ctx;
}

// ---------------------------------------------------------------------------
// App Shell context — low-frequency updates (notebook, layout, dark mode…)
// ---------------------------------------------------------------------------
export interface AppShellContextValue {
  // Notebook state
  selectedNotebookId: string | null;
  selectedNotebookName: string;
  selectedSourceId: string | null;
  setSelectedSourceId: React.Dispatch<React.SetStateAction<string | null>>;

  // LLM state
  selectedLLMProvider: string;
  setSelectedLLMProvider: (provider: string) => void;

  // Refresh triggers
  refreshSources: number;
  triggerSourcesRefresh: () => void;
  refreshNotebooks: number;
  triggerNotebooksRefresh: () => void;
  collectorRefreshKey: number;

  // Toast
  addToast: (toast: { type: 'success' | 'error' | 'info' | 'warning'; title: string; message?: string; duration?: number }) => void;

  // Canvas operations
  openPanel: (view: PanelView, props?: Record<string, any>) => void;
  closePanel: (panelId: string) => void;
  splitPanel: (panelId: string, direction: 'horizontal' | 'vertical', newView: PanelView) => void;
  changePanelView: (panelId: string, view: PanelView) => void;
  layout: LayoutNode;

  // Navigation helpers
  openWebResearch: (query?: string) => void;
  openSettings: () => void;
  openLLMSelector: () => void;
  openEmbeddingSelector: () => void;
  chatPrefillQuery: string;
  setChatPrefillQuery: (query: string) => void;
  navigateToChat: () => void;

  // Dark mode
  darkMode: boolean;
  toggleDarkMode: () => void;

  // Morning brief
  morningBrief: any;
  setMorningBrief: (brief: any) => void;
  curatorBriefData: any;
  setCuratorBriefData: (data: any) => void;

  // Generation activity status (drives rainbow line animation)
  generationStatus: 'idle' | 'generating' | 'complete' | 'error';
  setGenerationStatus: (status: 'idle' | 'generating' | 'complete' | 'error') => void;

  // Chat context for "From Chat" mode — recent conversation summary
  chatContext: string;
  setChatContext: (context: string) => void;
}

const AppShellCtx = createContext<AppShellContextValue | null>(null);
export const AppShellProvider = AppShellCtx.Provider;

/** Focused hook — only re-renders on shell changes (notebook, layout, dark mode) */
export function useAppShell(): AppShellContextValue {
  const ctx = useContext(AppShellCtx);
  if (!ctx) throw new Error('useAppShell must be used within AppShellProvider');
  return ctx;
}

// ---------------------------------------------------------------------------
// Composite — backward-compatible useCanvas() that merges both contexts
// ---------------------------------------------------------------------------
export type CanvasContextValue = AppShellContextValue & CanvasItemsContextValue;

/** Legacy composite hook — reads from both contexts. Prefer useAppShell() or useCanvasItems() for performance. */
export function useCanvas(): CanvasContextValue {
  const shell = useContext(AppShellCtx);
  const items = useContext(CanvasItemsCtx);
  if (!shell || !items) throw new Error('useCanvas must be used within CanvasProvider');
  return useMemo(() => ({ ...shell, ...items }), [shell, items]);
}
