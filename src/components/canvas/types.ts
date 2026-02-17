export type PanelView =
  | 'chat'
  | 'constellation'
  | 'timeline'
  | 'findings'
  | 'curator'
  | 'settings'
  | 'llm-selector'
  | 'embedding-selector'
  | 'web-research'
  | 'content-viewer'
  | 'quiz-viewer'
  | 'visual-viewer';

export interface LeafNode {
  type: 'leaf';
  id: string;
  view: PanelView;
  props?: Record<string, any>;
}

export interface SplitNode {
  type: 'split';
  direction: 'horizontal' | 'vertical';
  sizes: [number, number];
  children: [LayoutNode, LayoutNode];
}

export type LayoutNode = SplitNode | LeafNode;

export const VIEW_LABELS: Record<PanelView, string> = {
  'chat': 'Chat',
  'constellation': 'Constellation',
  'timeline': 'Timeline',
  'findings': 'Findings',
  'curator': 'Curator',
  'settings': 'Settings',
  'llm-selector': 'AI Brain',
  'embedding-selector': 'Embedding Model',
  'web-research': 'Web Research',
  'content-viewer': 'Document',
  'quiz-viewer': 'Quiz',
  'visual-viewer': 'Visual',
};

export const VIEW_ICONS: Record<PanelView, string> = {
  'chat': '💬',
  'constellation': '✨',
  'timeline': '📅',
  'findings': '🔖',
  'curator': '💡',
  'settings': '⚙️',
  'llm-selector': '🧠',
  'embedding-selector': '📊',
  'web-research': '🌐',
  'content-viewer': '📄',
  'quiz-viewer': '🎯',
  'visual-viewer': '🎨',
};

export function countLeaves(node: LayoutNode): number {
  if (node.type === 'leaf') return 1;
  return countLeaves(node.children[0]) + countLeaves(node.children[1]);
}

export function findLeaf(node: LayoutNode, id: string): LeafNode | null {
  if (node.type === 'leaf') return node.id === id ? node : null;
  return findLeaf(node.children[0], id) || findLeaf(node.children[1], id);
}

export function replaceLeaf(node: LayoutNode, id: string, replacement: LayoutNode): LayoutNode {
  if (node.type === 'leaf') return node.id === id ? replacement : node;
  return {
    ...node,
    children: [
      replaceLeaf(node.children[0], id, replacement),
      replaceLeaf(node.children[1], id, replacement),
    ] as [LayoutNode, LayoutNode],
  };
}

export function removeLeaf(node: LayoutNode, id: string): LayoutNode | null {
  if (node.type === 'leaf') return node.id === id ? null : node;
  const left = removeLeaf(node.children[0], id);
  const right = removeLeaf(node.children[1], id);
  if (!left) return right;
  if (!right) return left;
  return { ...node, children: [left, right] as [LayoutNode, LayoutNode] };
}

export function findFirstLeafId(node: LayoutNode): string {
  if (node.type === 'leaf') return node.id;
  return findFirstLeafId(node.children[0]);
}

export function makeDefaultLayout(): LayoutNode {
  return { type: 'leaf', id: 'main', view: 'chat' };
}
