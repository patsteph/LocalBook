import { useState, useEffect, useCallback, useRef } from 'react';
import { API_BASE_URL } from '../services/api';

interface GraphNode {
  id: string;
  label: string;
  type: string;
  color?: string;
  size: number;
  notebook_id?: string;
  metadata: Record<string, any>;
  // Computed for visualization
  x?: number;
  y?: number;
  z?: number;  // For 3D depth
  vx?: number;
  vy?: number;
  vz?: number;
  connections?: number;  // Count of edges
}

interface GraphEdge {
  id: string;
  source: string;
  target: string;
  label: string;
  strength: number;
  color?: string;
  dashed: boolean;
}

interface ConceptCluster {
  id: string;
  name: string;
  description?: string;
  size: number;
  coherence_score: number;
  concept_ids: string[];
  notebook_ids: string[];
}

interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  clusters: ConceptCluster[];
}

interface GraphStats {
  concepts: number;
  links: number;
  clusters: number;
}

interface Props {
  notebookId: string | null;
}

const API_BASE = API_BASE_URL;

const LINK_TYPE_COLORS: Record<string, string> = {
  references: '#3B82F6',
  contradicts: '#EF4444',
  expands: '#10B981',
  example_of: '#F59E0B',
  similar_to: '#8B5CF6',
  precedes: '#06B6D4',
  causes: '#EC4899',
  part_of: '#F97316',
};

export function ConstellationPanel({ notebookId }: Props) {
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [stats, setStats] = useState<GraphStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [building, setBuilding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [crossNotebook, setCrossNotebook] = useState(false);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [minStrength, setMinStrength] = useState(0.3);
  const [showClusters, setShowClusters] = useState(true);
  
  // Canvas refs
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animationRef = useRef<number | null>(null);
  const nodesRef = useRef<GraphNode[]>([]);
  const edgesRef = useRef<GraphEdge[]>([]);
  
  // Pan and zoom state
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const isDragging = useRef(false);
  const lastMouse = useRef({ x: 0, y: 0 });
  
  // Auto-refresh interval
  const refreshIntervalRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    loadStats();
    
    // Cleanup on unmount
    return () => {
      if (refreshIntervalRef.current) {
        clearInterval(refreshIntervalRef.current);
      }
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if (notebookId || crossNotebook) {
      loadGraph();
      loadStats();
    }
  }, [notebookId, crossNotebook, minStrength, showClusters]);
  
  // Auto-refresh while building (reduced frequency to save resources)
  useEffect(() => {
    if (building) {
      refreshIntervalRef.current = setInterval(() => {
        loadStats();
        loadGraph();
      }, 20000); // 20s instead of 10s to reduce network/CPU load
    } else {
      if (refreshIntervalRef.current) {
        clearInterval(refreshIntervalRef.current);
        refreshIntervalRef.current = null;
      }
    }
    
    return () => {
      if (refreshIntervalRef.current) {
        clearInterval(refreshIntervalRef.current);
      }
    };
  }, [building]);

  const loadStats = async () => {
    try {
      const params = notebookId ? `?notebook_id=${notebookId}` : '';
      const response = await fetch(`${API_BASE}/graph/stats${params}`);
      if (response.ok) {
        const data = await response.json();
        setStats(data);
      }
    } catch (err) {
      console.error('Failed to load graph stats:', err);
    }
  };

  const loadGraph = async () => {
    setLoading(true);
    setError(null);
    
    try {
      const params = new URLSearchParams({
        include_clusters: showClusters.toString(),
        min_link_strength: minStrength.toString(),
      });
      
      const endpoint = crossNotebook 
        ? `${API_BASE}/graph/all?${params}`
        : `${API_BASE}/graph/notebook/${notebookId}?${params}`;
      
      const response = await fetch(endpoint);
      
      if (response.ok) {
        const data: GraphData = await response.json();
        
        // Count connections per node
        const connectionCounts: Record<string, number> = {};
        for (const edge of data.edges) {
          connectionCounts[edge.source] = (connectionCounts[edge.source] || 0) + 1;
          connectionCounts[edge.target] = (connectionCounts[edge.target] || 0) + 1;
        }
        
        // Initialize node positions with importance-based placement
        const centerX = 400;
        const centerY = 300;
        const maxConnections = Math.max(...Object.values(connectionCounts), 1);
        
        data.nodes = data.nodes.map((node, i) => {
          const connections = connectionCounts[node.id] || 0;
          const importance = connections / maxConnections;
          // More connected nodes closer to center
          const radius = 250 - (importance * 150);
          const angle = (i * 2 * Math.PI / data.nodes.length) + Math.random() * 0.5;
          
          return {
            ...node,
            x: centerX + Math.cos(angle) * radius,
            y: centerY + Math.sin(angle) * radius,
            z: importance, // Depth for 3D effect
            vx: 0,
            vy: 0,
            connections,
          };
        });
        
        setGraphData(data);
        nodesRef.current = data.nodes;
        edgesRef.current = data.edges;
        
        // Start force simulation
        startSimulation();
      } else {
        setError('Failed to load graph');
      }
    } catch (err) {
      setError('Failed to connect to server');
    } finally {
      setLoading(false);
    }
  };

  const startSimulation = useCallback(() => {
    if (animationRef.current) {
      cancelAnimationFrame(animationRef.current);
    }
    
    const simulate = () => {
      const nodes = nodesRef.current;
      const edges = edgesRef.current;
      
      if (nodes.length === 0) return;
      
      // Simple force-directed layout
      const centerX = 400;
      const centerY = 300;
      const repulsion = 5000;
      const attraction = 0.01;
      const damping = 0.9;
      
      // Apply forces
      for (let i = 0; i < nodes.length; i++) {
        const node = nodes[i];
        let fx = 0;
        let fy = 0;
        
        // Repulsion from other nodes
        for (let j = 0; j < nodes.length; j++) {
          if (i === j) continue;
          const other = nodes[j];
          const dx = (node.x || 0) - (other.x || 0);
          const dy = (node.y || 0) - (other.y || 0);
          const dist = Math.sqrt(dx * dx + dy * dy) || 1;
          const force = repulsion / (dist * dist);
          fx += (dx / dist) * force;
          fy += (dy / dist) * force;
        }
        
        // Attraction to center
        fx += (centerX - (node.x || 0)) * 0.001;
        fy += (centerY - (node.y || 0)) * 0.001;
        
        // Attraction along edges
        for (const edge of edges) {
          if (edge.source === node.id || edge.target === node.id) {
            const otherId = edge.source === node.id ? edge.target : edge.source;
            const other = nodes.find(n => n.id === otherId);
            if (other) {
              const dx = (other.x || 0) - (node.x || 0);
              const dy = (other.y || 0) - (node.y || 0);
              fx += dx * attraction * edge.strength;
              fy += dy * attraction * edge.strength;
            }
          }
        }
        
        // Update velocity and position
        node.vx = ((node.vx || 0) + fx) * damping;
        node.vy = ((node.vy || 0) + fy) * damping;
        node.x = (node.x || 0) + (node.vx || 0);
        node.y = (node.y || 0) + (node.vy || 0);
        
        // Keep in bounds
        node.x = Math.max(50, Math.min(750, node.x || 0));
        node.y = Math.max(50, Math.min(550, node.y || 0));
      }
      
      // Draw
      draw();
      
      // Continue simulation
      animationRef.current = requestAnimationFrame(simulate);
    };
    
    simulate();
    
    // Stop after 5 seconds
    setTimeout(() => {
      if (animationRef.current) {
        cancelAnimationFrame(animationRef.current);
      }
    }, 5000);
  }, []);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    
    const nodes = nodesRef.current;
    const edges = edgesRef.current;
    
    // Clear with dark gradient background
    const gradient = ctx.createRadialGradient(400, 300, 0, 400, 300, 500);
    gradient.addColorStop(0, '#1a1a2e');
    gradient.addColorStop(1, '#0f0f1a');
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    
    // Apply pan and zoom transform
    ctx.save();
    ctx.translate(pan.x, pan.y);
    ctx.scale(zoom, zoom);
    
    // Sort nodes by z-depth (back to front)
    const sortedNodes = [...nodes].sort((a, b) => (a.z || 0) - (b.z || 0));
    
    // Draw edges with glow effect
    for (const edge of edges) {
      const source = nodes.find(n => n.id === edge.source);
      const target = nodes.find(n => n.id === edge.target);
      
      if (source && target && source.x && source.y && target.x && target.y) {
        const avgDepth = ((source.z || 0) + (target.z || 0)) / 2;
        const alpha = 0.3 + avgDepth * 0.5;
        
        ctx.beginPath();
        ctx.moveTo(source.x, source.y);
        ctx.lineTo(target.x, target.y);
        
        const color = LINK_TYPE_COLORS[edge.label] || '#4B5563';
        ctx.strokeStyle = color + Math.floor(alpha * 255).toString(16).padStart(2, '0');
        ctx.lineWidth = (edge.strength * 2) * (0.5 + avgDepth * 0.5);
        
        if (edge.dashed) {
          ctx.setLineDash([5, 5]);
        } else {
          ctx.setLineDash([]);
        }
        ctx.stroke();
      }
    }
    
    // Find max connections for scaling
    const maxConn = Math.max(...nodes.map(n => n.connections || 1), 1);
    
    // Draw nodes (back to front for proper layering)
    for (const node of sortedNodes) {
      if (!node.x || !node.y) continue;
      
      const connections = node.connections || 0;
      const importance = connections / maxConn;
      const depth = node.z || 0;
      
      // Size based on connections (more connections = bigger)
      const baseRadius = 6;
      const radius = baseRadius + (importance * 14);
      
      // Opacity based on depth (closer = brighter)
      const alpha = 0.4 + depth * 0.6;
      
      // Glow effect for important nodes
      if (importance > 0.3) {
        ctx.beginPath();
        ctx.arc(node.x, node.y, radius + 8, 0, Math.PI * 2);
        const glowGradient = ctx.createRadialGradient(
          node.x, node.y, radius,
          node.x, node.y, radius + 8
        );
        const baseColor = node.color || '#8B5CF6';
        glowGradient.addColorStop(0, baseColor + '40');
        glowGradient.addColorStop(1, baseColor + '00');
        ctx.fillStyle = glowGradient;
        ctx.fill();
      }
      
      // Node circle
      ctx.beginPath();
      ctx.arc(node.x, node.y, radius, 0, Math.PI * 2);
      const nodeColor = node.color || '#8B5CF6';
      ctx.fillStyle = nodeColor + Math.floor(alpha * 255).toString(16).padStart(2, '0');
      ctx.fill();
      
      // Border
      ctx.strokeStyle = '#ffffff' + Math.floor(alpha * 180).toString(16).padStart(2, '0');
      ctx.lineWidth = selectedNode?.id === node.id ? 3 : 1;
      ctx.stroke();
      
      // Label - size and brightness based on importance
      const fontSize = 9 + (importance * 6);
      const fontWeight = importance > 0.5 ? 'bold' : 'normal';
      const textAlpha = 0.5 + importance * 0.5;
      
      ctx.fillStyle = '#ffffff' + Math.floor(textAlpha * 255).toString(16).padStart(2, '0');
      ctx.font = `${fontWeight} ${fontSize}px sans-serif`;
      ctx.textAlign = 'center';
      
      // Truncate long labels
      const label = node.label.length > 15 ? node.label.slice(0, 12) + '...' : node.label;
      ctx.fillText(label, node.x, node.y + radius + 12);
      
      // Show connection count for important nodes
      if (connections > 2) {
        ctx.font = '8px sans-serif';
        ctx.fillStyle = '#a78bfa80';
        ctx.fillText(`(${connections})`, node.x, node.y + radius + 22);
      }
    }
    
    ctx.restore();
    
    // Draw zoom indicator
    ctx.fillStyle = '#ffffff40';
    ctx.font = '10px sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText(`Zoom: ${(zoom * 100).toFixed(0)}%`, canvas.width - 10, 20);
    
    // Draw building indicator
    if (building) {
      ctx.fillStyle = '#a78bfa';
      ctx.font = '12px sans-serif';
      ctx.textAlign = 'left';
      ctx.fillText('✨ Building... (auto-refreshing)', 10, 20);
    }
  }, [selectedNode, zoom, pan, building]);

  const handleCanvasClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (isDragging.current) return;
    
    const canvas = canvasRef.current;
    if (!canvas) return;
    
    const rect = canvas.getBoundingClientRect();
    // Account for pan and zoom
    const x = (e.clientX - rect.left - pan.x) / zoom;
    const y = (e.clientY - rect.top - pan.y) / zoom;
    
    // Find clicked node
    const nodes = nodesRef.current;
    const maxConn = Math.max(...nodes.map(n => n.connections || 1), 1);
    
    for (const node of nodes) {
      if (!node.x || !node.y) continue;
      const importance = (node.connections || 0) / maxConn;
      const radius = 6 + (importance * 14);
      const dx = x - node.x;
      const dy = y - node.y;
      if (dx * dx + dy * dy < radius * radius) {
        setSelectedNode(node);
        return;
      }
    }
    setSelectedNode(null);
  };
  
  const handleMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    isDragging.current = true;
    lastMouse.current = { x: e.clientX, y: e.clientY };
  };
  
  const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!isDragging.current) return;
    
    const dx = e.clientX - lastMouse.current.x;
    const dy = e.clientY - lastMouse.current.y;
    
    setPan(prev => ({ x: prev.x + dx, y: prev.y + dy }));
    lastMouse.current = { x: e.clientX, y: e.clientY };
  };
  
  const handleMouseUp = () => {
    setTimeout(() => { isDragging.current = false; }, 10);
  };
  
  const handleWheel = (e: React.WheelEvent<HTMLCanvasElement>) => {
    e.preventDefault();
    const delta = e.deltaY > 0 ? 0.9 : 1.1;
    setZoom(prev => Math.max(0.2, Math.min(3, prev * delta)));
  };
  
  const resetView = () => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  };

  const triggerClustering = async () => {
    try {
      await fetch(`${API_BASE}/graph/cluster`, { method: 'POST' });
      // Reload after a delay
      setTimeout(loadGraph, 2000);
      setTimeout(loadStats, 2000);
    } catch (err) {
      console.error('Failed to trigger clustering:', err);
    }
  };

  const buildGraph = async () => {
    if (!notebookId) return;
    
    setBuilding(true);
    try {
      const response = await fetch(`${API_BASE}/graph/build/${notebookId}`, { method: 'POST' });
      if (response.ok) {
        const data = await response.json();
        console.log('Building constellation:', data);
        
        // Stop building after 2 minutes
        setTimeout(() => {
          setBuilding(false);
          loadGraph();
          loadStats();
        }, 120000);
      }
    } catch (err) {
      console.error('Failed to build constellation:', err);
      setBuilding(false);
    }
  };

  return (
    <div className="h-full flex flex-col">
      {/* Controls */}
      <div className="p-4 border-b dark:border-gray-700 bg-gray-50 dark:bg-gray-900">
        <div className="flex items-center gap-4 flex-wrap">
          {/* Cross-notebook toggle */}
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={crossNotebook}
              onChange={(e) => setCrossNotebook(e.target.checked)}
              className="rounded"
            />
            <span className="text-gray-700 dark:text-gray-300">Cross-notebook</span>
          </label>
          
          {/* Show clusters toggle */}
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={showClusters}
              onChange={(e) => setShowClusters(e.target.checked)}
              className="rounded"
            />
            <span className="text-gray-700 dark:text-gray-300">Show clusters</span>
          </label>
          
          {/* Min strength slider */}
          <div className="flex items-center gap-2">
            <span className="text-sm text-gray-600 dark:text-gray-400">Min strength:</span>
            <input
              type="range"
              min="0"
              max="1"
              step="0.1"
              value={minStrength}
              onChange={(e) => setMinStrength(parseFloat(e.target.value))}
              className="w-24"
            />
            <span className="text-sm text-gray-600 dark:text-gray-400">{minStrength}</span>
          </div>
          
          {/* Refresh button */}
          <button
            onClick={loadGraph}
            disabled={loading || (!notebookId && !crossNotebook)}
            className="px-3 py-1 bg-blue-600 hover:bg-blue-700 text-white rounded text-sm disabled:opacity-50"
          >
            {loading ? 'Loading...' : 'Refresh'}
          </button>
          
          {/* Reset view button */}
          <button
            onClick={resetView}
            className="px-3 py-1 bg-gray-600 hover:bg-gray-700 text-white rounded text-sm"
            title="Reset zoom and pan"
          >
            ⟲ Reset View
          </button>
          
          {/* Build Constellation button - combines extraction + clustering */}
          {notebookId && (
            <button
              onClick={async () => {
                await buildGraph();
                // Auto-run clustering after build completes
                setTimeout(triggerClustering, 5000);
              }}
              disabled={loading}
              className="px-3 py-1 bg-purple-600 hover:bg-purple-700 text-white rounded text-sm disabled:opacity-50"
            >
              {loading ? '✨ Building...' : '✨ Build Constellation'}
            </button>
          )}
        </div>
        
        {/* Stats */}
        {stats && (
          <div className="mt-2 flex gap-4 text-xs text-gray-500 dark:text-gray-400">
            <span>📊 {stats.concepts} concepts</span>
            <span>🔗 {stats.links} links</span>
            <span>🎯 {stats.clusters} clusters</span>
          </div>
        )}
      </div>

      {/* Graph Canvas */}
      <div className="flex-1 relative">
        {!notebookId && !crossNotebook ? (
          <div className="absolute inset-0 flex items-center justify-center text-gray-500 dark:text-gray-400">
            <div className="text-center">
              <p className="text-lg mb-2">✨ Constellation</p>
              <p className="text-sm">Select a notebook or enable cross-notebook view</p>
            </div>
          </div>
        ) : loading ? (
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
          </div>
        ) : error ? (
          <div className="absolute inset-0 flex items-center justify-center text-red-500">
            {error}
          </div>
        ) : graphData && graphData.nodes.length === 0 ? (
          <div className="absolute inset-0 flex items-center justify-center text-gray-500 dark:text-gray-400">
            <div className="text-center max-w-md">
              <p className="text-4xl mb-4">✨</p>
              <p className="text-lg font-medium mb-2">No concepts yet</p>
              <p className="text-sm mb-4">
                The Constellation maps concepts and connections across your documents.
                Click "Build Constellation" to extract concepts from your sources.
              </p>
              {notebookId && (
                <button
                  onClick={async () => {
                    await buildGraph();
                    setTimeout(triggerClustering, 5000);
                  }}
                  disabled={loading}
                  className="px-4 py-2 bg-purple-600 hover:bg-purple-700 text-white rounded-lg text-sm disabled:opacity-50"
                >
                  {loading ? '✨ Building...' : '✨ Build Constellation'}
                </button>
              )}
            </div>
          </div>
        ) : (
          <canvas
            ref={canvasRef}
            width={800}
            height={600}
            onClick={handleCanvasClick}
            onMouseDown={handleMouseDown}
            onMouseMove={handleMouseMove}
            onMouseUp={handleMouseUp}
            onMouseLeave={handleMouseUp}
            onWheel={handleWheel}
            className="w-full h-full cursor-grab active:cursor-grabbing"
          />
        )}
      </div>

      {/* Selected Node Info */}
      {selectedNode && (
        <div className="p-4 border-t dark:border-gray-700 bg-white dark:bg-gray-800">
          <div className="flex items-start justify-between">
            <div>
              <h3 className="font-medium text-gray-900 dark:text-white">
                {selectedNode.label}
              </h3>
              <p className="text-sm text-gray-600 dark:text-gray-400">
                {selectedNode.metadata?.description || 'No description'}
              </p>
              <div className="mt-2 flex gap-2 text-xs text-gray-500">
                <span>Frequency: {selectedNode.metadata?.frequency || 0}</span>
                <span>•</span>
                <span>Importance: {((selectedNode.metadata?.importance || 0) * 100).toFixed(0)}%</span>
              </div>
            </div>
            <button
              onClick={() => setSelectedNode(null)}
              className="text-gray-400 hover:text-gray-600"
            >
              ✕
            </button>
          </div>
        </div>
      )}

      {/* Clusters Panel */}
      {showClusters && graphData && graphData.clusters.length > 0 && (
        <div className="p-4 border-t dark:border-gray-700 bg-gray-50 dark:bg-gray-900 max-h-40 overflow-y-auto">
          <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
            Discovered Themes
          </h3>
          <div className="flex flex-wrap gap-2">
            {graphData.clusters.map((cluster) => (
              <div
                key={cluster.id}
                className="px-2 py-1 bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300 rounded text-xs"
                title={`${cluster.size} concepts, ${(cluster.coherence_score * 100).toFixed(0)}% coherence`}
              >
                {cluster.name} ({cluster.size})
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Legend */}
      <div className="p-2 border-t dark:border-gray-700 bg-gray-50 dark:bg-gray-900">
        <div className="flex flex-wrap gap-3 text-xs">
          {Object.entries(LINK_TYPE_COLORS).map(([type, color]) => (
            <div key={type} className="flex items-center gap-1">
              <div className="w-3 h-0.5" style={{ backgroundColor: color }}></div>
              <span className="text-gray-600 dark:text-gray-400">{type}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
