import React from 'react';
import { Panel, Group, Separator } from 'react-resizable-panels';
import { LayoutNode } from './types';
import { CanvasPanel } from './CanvasPanel';

interface CanvasWorkspaceProps {
  layout: LayoutNode;
}

function renderNode(node: LayoutNode, depth: number = 0): React.ReactNode {
  if (node.type === 'leaf') {
    return (
      <CanvasPanel
        key={node.id}
        panelId={node.id}
        view={node.view}
        panelProps={node.props}
      />
    );
  }

  const orientation = node.direction;

  return (
    <Group orientation={orientation} id={`split-${depth}`}>
      <Panel id={`split-${depth}-a`} defaultSize={node.sizes[0]}>
        {renderNode(node.children[0], depth * 2 + 1)}
      </Panel>
      <Separator>
        <div
          className={`group flex items-center justify-center ${
            orientation === 'horizontal'
              ? 'w-1.5 h-full cursor-col-resize hover:bg-blue-400/30'
              : 'h-1.5 w-full cursor-row-resize hover:bg-blue-400/30'
          } transition-colors`}
        >
          <div
            className={`rounded-full bg-gray-300 dark:bg-gray-600 group-hover:bg-blue-500 transition-colors ${
              orientation === 'horizontal' ? 'w-0.5 h-8' : 'h-0.5 w-8'
            }`}
          />
        </div>
      </Separator>
      <Panel id={`split-${depth}-b`} defaultSize={node.sizes[1]}>
        {renderNode(node.children[1], depth * 2 + 2)}
      </Panel>
    </Group>
  );
}

export const CanvasWorkspace: React.FC<CanvasWorkspaceProps> = ({ layout }) => {
  return (
    <div className="h-full w-full overflow-hidden">
      {renderNode(layout)}
    </div>
  );
};
