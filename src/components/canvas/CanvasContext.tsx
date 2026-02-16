import { createContext, useContext } from 'react';
import { LayoutNode, PanelView } from './types';

export interface CanvasContextValue {
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
}

const CanvasContext = createContext<CanvasContextValue | null>(null);

export function useCanvas(): CanvasContextValue {
  const ctx = useContext(CanvasContext);
  if (!ctx) throw new Error('useCanvas must be used within CanvasProvider');
  return ctx;
}

export const CanvasProvider = CanvasContext.Provider;
