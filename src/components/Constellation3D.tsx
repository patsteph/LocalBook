import { useEffect, useRef, useState, useCallback } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { API_BASE_URL, WS_BASE_URL } from '../services/api';
import { graphService } from '../services/graph';

interface GraphNode {
  id: string;
  label: string;
  type: string;
  color?: string;
  size: number;
  notebook_id?: string;
  metadata: Record<string, any>;
  connections?: number;
  isCluster?: boolean;  // Is this a cluster super-node?
  clusterId?: string;   // Which cluster does this belong to?
  childIds?: string[];  // Child node IDs if this is a cluster
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
  selectedSourceId?: string | null;  // Filter to show only concepts from this source
  rightSidebarCollapsed?: boolean;  // Whether right sidebar (Studio) is collapsed
}

const API_BASE = API_BASE_URL;

export function Constellation3D({ notebookId, selectedSourceId, rightSidebarCollapsed = false }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const nodesRef = useRef<Map<string, THREE.Mesh>>(new Map());
  const labelsRef = useRef<Map<string, THREE.Sprite>>(new Map());
  const edgesRef = useRef<Map<string, THREE.Line>>(new Map());  // Track edges for visibility control
  const animationRef = useRef<number | null>(null);
  
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [stats, setStats] = useState<GraphStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [building, setBuilding] = useState(false);
  const [enhancing, setEnhancing] = useState(false);  // Background Ollama enhancement
  const [enhanceProgress, setEnhanceProgress] = useState({ current: 0, total: 0 });
  const [sceneReady, setSceneReady] = useState(false);
  const [buildProgress, setBuildProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [, setSelectedNode] = useState<GraphNode | null>(null);
  const [focusedNodeId, setFocusedNodeId] = useState<string | null>(null);
  const [, setConnectedNodeIds] = useState<Set<string>>(new Set());
  // Clusters state removed - using overlay panel instead
  const [navigationHistory, setNavigationHistory] = useState<string[]>([]);
  const [showResetConfirm, setShowResetConfirm] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [insightCount, setInsightCount] = useState(0);
  const [scanningInsights, setScanningInsights] = useState(false);
  const [processingSources, setProcessingSources] = useState<Set<string>>(new Set());  // Track sources being processed
  const wsRef = useRef<WebSocket | null>(null);
  const notebookIdRef = useRef<string | null>(notebookId);
  const autoBuiltNotebooks = useRef<Set<string>>(new Set());  // Track auto-triggered builds to prevent loops
  
  // Keep ref in sync with prop - update synchronously during render
  // This ensures the ref is always current when WebSocket callbacks fire
  notebookIdRef.current = notebookId;
  
  // Simplified - always use current notebook
  const crossNotebook = false;
  
  // Find connected nodes for a given node
  const getConnectedNodes = useCallback((nodeId: string): Set<string> => {
    const connected = new Set<string>();
    if (!graphData) return connected;
    
    for (const edge of graphData.edges) {
      if (edge.source === nodeId) {
        connected.add(edge.target);
      } else if (edge.target === nodeId) {
        connected.add(edge.source);
      }
    }
    return connected;
  }, [graphData]);
  
  // Store original positions for animation
  const originalPositionsRef = useRef<Map<string, THREE.Vector3>>(new Map());
  
  // Animate camera to focus on a node and bring connected nodes closer
  const focusOnNode = useCallback((nodeId: string) => {
    const mesh = nodesRef.current.get(nodeId);
    const camera = cameraRef.current;
    const controls = controlsRef.current;
    const scene = sceneRef.current;
    
    if (!mesh || !camera || !controls || !scene) return;
    
    // Stop auto-rotate when focusing
    controls.autoRotate = false;
    
    // Get connected nodes
    const connected = getConnectedNodes(nodeId);
    console.log(`Node ${nodeId} has ${connected.size} connections:`, Array.from(connected));
    
    // Store original positions if not already stored
    if (originalPositionsRef.current.size === 0) {
      nodesRef.current.forEach((m, id) => {
        originalPositionsRef.current.set(id, m.position.clone());
      });
    }
    
    // Get focused node position
    const focusedPos = mesh.position.clone();
    
    // Adaptive layout based on connection density
    const connCount = connected.size;
    
    // Dynamic camera distance — zoom out more for heavily-connected nodes
    const distance = connCount > 30 ? 70 : connCount > 15 ? 50 : 35;
    const direction = new THREE.Vector3().subVectors(camera.position, controls.target).normalize();
    const newCameraPos = focusedPos.clone().add(direction.multiplyScalar(distance));
    
    // Animate camera AND connected nodes
    const startCameraPos = camera.position.clone();
    const startTarget = controls.target.clone();
    const duration = 800;
    const startTime = Date.now();
    
    // Store start positions for connected node animation
    const nodeStartPositions = new Map<string, THREE.Vector3>();
    const nodeTargetPositions = new Map<string, THREE.Vector3>();
    
    // Sort connected nodes by their connection count (most connected first)
    const connectedArray = Array.from(connected);
    connectedArray.sort((a, b) => {
      const aConn = nodesRef.current.get(a)?.userData.connections || 0;
      const bConn = nodesRef.current.get(b)?.userData.connections || 0;
      return bConn - aConn;
    });
    
    // Dynamic orbit: use concentric rings for many connections
    // Ring 1 (inner): top ~12 nodes, Ring 2 (outer): next ~12, Ring 3: rest
    const baseRadius = Math.min(18 + connCount * 0.4, 50);  // Scale radius, cap at 50
    const nodesPerRing = Math.max(8, Math.ceil(connCount / 3));
    
    nodesRef.current.forEach((m, id) => {
      nodeStartPositions.set(id, m.position.clone());
      
      if (id === nodeId) {
        nodeTargetPositions.set(id, focusedPos.clone());
      } else if (connected.has(id)) {
        const idx = connectedArray.indexOf(id);
        const ring = Math.floor(idx / nodesPerRing);         // 0, 1, 2...
        const posInRing = idx % nodesPerRing;
        const ringTotal = Math.min(nodesPerRing, connCount - ring * nodesPerRing);
        
        // Each ring is progressively wider and slightly offset vertically
        const ringRadius = baseRadius * (1 + ring * 0.6);
        const angle = (posInRing / ringTotal) * Math.PI * 2 + ring * 0.3;  // Offset per ring
        const ySpread = 0.4 - ring * 0.1;   // Inner ring flatter, outer rings more spherical
        
        const offset = new THREE.Vector3(
          Math.cos(angle) * ringRadius,
          Math.sin(angle) * ringRadius * ySpread + ring * 4,  // Vertical stagger per ring
          Math.sin(angle) * ringRadius * 0.25
        );
        nodeTargetPositions.set(id, focusedPos.clone().add(offset));
      } else {
        nodeTargetPositions.set(id, m.position.clone());
      }
    });
    
    const animate = () => {
      const elapsed = Date.now() - startTime;
      const progress = Math.min(elapsed / duration, 1);
      
      // Ease out cubic for smooth deceleration
      const eased = 1 - Math.pow(1 - progress, 3);
      
      // Animate camera
      camera.position.lerpVectors(startCameraPos, newCameraPos, eased);
      controls.target.lerpVectors(startTarget, focusedPos, eased);
      controls.update();
      
      // Animate node positions
      nodesRef.current.forEach((m, id) => {
        const startPos = nodeStartPositions.get(id);
        const targetPos = nodeTargetPositions.get(id);
        if (startPos && targetPos) {
          m.position.lerpVectors(startPos, targetPos, eased);
        }
        
        // Also update label positions
        const label = labelsRef.current.get(id);
        if (label) {
          label.position.copy(m.position);
          label.position.y -= (m.geometry as THREE.SphereGeometry).parameters.radius + 8;
        }
      });
      
      if (progress < 1) {
        requestAnimationFrame(animate);
      }
    };
    
    animate();
    
    // Update state
    setConnectedNodeIds(connected);
    setFocusedNodeId(nodeId);
    
    // Add to navigation history
    setNavigationHistory(prev => {
      const newHistory = [...prev.filter(id => id !== nodeId), nodeId];
      return newHistory.slice(-10);
    });
    
    // Show all connected nodes, but only label the top ones
    const visibleConnected = connected;
    // Only show labels for the top N most-connected nodes to prevent overlap
    const maxLabels = connCount > 30 ? 12 : connCount > 15 ? 15 : connCount;
    const labeledSet = new Set(connectedArray.slice(0, maxLabels));
    console.log(`Showing ${visibleConnected.size} connected nodes (${labeledSet.size} labeled)`);
    
    // Scale factor — shrink nodes more when there are many connections
    const connNodeScale = connCount > 30 ? 0.5 : connCount > 15 ? 0.65 : 0.8;
    
    // Update visual appearance - nodes
    nodesRef.current.forEach((m, id) => {
      const material = m.material as THREE.MeshStandardMaterial;
      const label = labelsRef.current.get(id);
      
      if (id === nodeId) {
        // Focused node - bright, large, prominent label
        material.emissiveIntensity = 1;
        material.opacity = 1;
        const focusedClusterColor = m.userData.clusterColor;
        if (focusedClusterColor) {
          material.color.setHex(focusedClusterColor);
        }
        m.scale.setScalar(1.5);
        if (label) {
          label.visible = true;
          label.scale.set(45, 6, 1);
        }
      } else if (visibleConnected.has(id)) {
        // Connected node — keep cluster color
        const clusterColor = m.userData.clusterColor;
        if (clusterColor) {
          material.color.setHex(clusterColor);
        }
        
        if (labeledSet.has(id)) {
          // Top-N labeled node — full brightness, label visible
          material.emissiveIntensity = 0.8;
          material.opacity = 1;
          m.scale.setScalar(connNodeScale);
          if (label) {
            label.visible = true;
            label.scale.set(40, 5, 1);
          }
        } else {
          // Lower-ranked connected node — visible dot, no label
          material.emissiveIntensity = 0.5;
          material.opacity = 0.7;
          m.scale.setScalar(connNodeScale * 0.7);
          if (label) {
            label.visible = false;
          }
        }
      } else {
        // Other nodes - very faded, almost invisible
        material.emissiveIntensity = 0.02;
        material.opacity = 0.08;
        m.scale.setScalar(0.3);
        if (label) {
          label.visible = false;
        }
      }
    });
    
    // Update edge visibility and positions - only show edges connected to focused node
    // Need to wait for node animation to complete, then update edge positions
    setTimeout(() => {
      edgesRef.current.forEach((line) => {
        const { source, target } = line.userData;
        const material = line.material as THREE.LineBasicMaterial;
        
        // Show edge if it connects focused node to a connected node
        const isRelevant = (source === nodeId && visibleConnected.has(target)) ||
                           (target === nodeId && visibleConnected.has(source));
        
        if (isRelevant) {
          // Update edge geometry to match new node positions
          const sourceNode = nodesRef.current.get(source);
          const targetNode = nodesRef.current.get(target);
          if (sourceNode && targetNode) {
            const positions = line.geometry.attributes.position;
            positions.setXYZ(0, sourceNode.position.x, sourceNode.position.y, sourceNode.position.z);
            positions.setXYZ(1, targetNode.position.x, targetNode.position.y, targetNode.position.z);
            positions.needsUpdate = true;
          }
          
          material.opacity = 0.7;  // Bright, visible
          material.color.setHex(0xA78BFA);  // Purple highlight
          line.visible = true;
        } else {
          line.visible = false;
        }
      });
    }, 850);  // After animation completes (800ms)
    
    // Find and select the node
    const node = graphData?.nodes.find(n => n.id === nodeId);
    if (node) setSelectedNode(node);
  }, [graphData, getConnectedNodes]);
  
  // Navigate back in history
  const navigateBack = useCallback(() => {
    if (navigationHistory.length < 2) {
      // Reset to full view
      resetToFullView();
      return;
    }
    
    const newHistory = [...navigationHistory];
    newHistory.pop();  // Remove current
    const previousId = newHistory[newHistory.length - 1];
    setNavigationHistory(newHistory);
    
    if (previousId) {
      focusOnNode(previousId);
    }
  }, [navigationHistory, focusOnNode]);
  
  // Reset to full view
  const resetToFullView = useCallback(() => {
    const camera = cameraRef.current;
    const controls = controlsRef.current;
    
    if (!camera || !controls) return;
    
    // Animate back to default position
    const startCameraPos = camera.position.clone();
    const startTarget = controls.target.clone();
    const endCameraPos = new THREE.Vector3(0, 0, 200);
    const endTarget = new THREE.Vector3(0, 0, 0);
    const duration = 600;
    const startTime = Date.now();
    
    // Store current node positions for animation
    const nodeStartPositions = new Map<string, THREE.Vector3>();
    nodesRef.current.forEach((m, id) => {
      nodeStartPositions.set(id, m.position.clone());
    });
    
    const animateReset = () => {
      const elapsed = Date.now() - startTime;
      const progress = Math.min(elapsed / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      
      // Animate camera
      camera.position.lerpVectors(startCameraPos, endCameraPos, eased);
      controls.target.lerpVectors(startTarget, endTarget, eased);
      controls.update();
      
      // Animate nodes back to original positions
      nodesRef.current.forEach((m, id) => {
        const startPos = nodeStartPositions.get(id);
        const originalPos = originalPositionsRef.current.get(id);
        if (startPos && originalPos) {
          m.position.lerpVectors(startPos, originalPos, eased);
        }
        
        // Update label positions
        const label = labelsRef.current.get(id);
        if (label) {
          label.position.copy(m.position);
          label.position.y -= (m.geometry as THREE.SphereGeometry).parameters.radius + 8;
        }
      });
      
      if (progress < 1) {
        requestAnimationFrame(animateReset);
      } else {
        controls.autoRotate = true;
      }
    };
    
    animateReset();
    
    // Reset visual appearance - restore top 25 hierarchy
    nodesRef.current.forEach((m, id) => {
      const material = m.material as THREE.MeshStandardMaterial;
      const isTopNode = m.userData.isTopNode;
      const connections = m.userData.connections || 0;
      const importance = connections / 10;  // Rough normalization
      
      if (isTopNode) {
        material.emissiveIntensity = 0.4 + importance * 0.3;
        material.opacity = 0.8 + importance * 0.2;
      } else {
        material.emissiveIntensity = 0.1;
        material.opacity = 0.3;
      }
      m.scale.setScalar(1);
      
      // Only top 25 get labels in full view
      const label = labelsRef.current.get(id);
      if (label) {
        label.visible = isTopNode;
        label.scale.setScalar(1);
      }
      
      // Reset to cluster color
      const clusterColor = m.userData.clusterColor || 0x6366F1;
      material.color.setHex(clusterColor);
    });
    
    // Restore all edges to default visibility
    edgesRef.current.forEach((line) => {
      const material = line.material as THREE.LineBasicMaterial;
      material.color.setHex(0x94A3B8);  // Original color
      material.opacity = 0.1;  // Subtle
      line.visible = true;
    });
    
    setFocusedNodeId(null);
    setConnectedNodeIds(new Set());
    setSelectedNode(null);
    setNavigationHistory([]);
  }, []);
  
  // Filter nodes by selected source - fade out non-matching nodes
  useEffect(() => {
    if (!graphData || !nodesRef.current.size) return;
    
    nodesRef.current.forEach((mesh, nodeId) => {
      const material = mesh.material as THREE.MeshStandardMaterial;
      const label = labelsRef.current.get(nodeId);
      const node = graphData.nodes.find(n => n.id === nodeId);
      
      if (!selectedSourceId) {
        // No filter - restore all nodes to normal
        const clusterColor = mesh.userData.clusterColor || 0x6366F1;
        material.color.setHex(clusterColor);
        material.opacity = mesh.userData.isTopNode ? 0.85 : 0.3;
        material.emissiveIntensity = mesh.userData.isTopNode ? 0.3 : 0.15;
        mesh.scale.setScalar(1);
        if (label) {
          label.visible = mesh.userData.isTopNode;
        }
      } else {
        // Check if node belongs to selected source
        const sourceIds = node?.metadata?.source_ids || [];
        const belongsToSource = sourceIds.includes(selectedSourceId);
        
        if (belongsToSource) {
          // Highlight matching nodes
          const clusterColor = mesh.userData.clusterColor || 0x6366F1;
          material.color.setHex(clusterColor);
          material.opacity = 1;
          material.emissiveIntensity = 0.8;
          mesh.scale.setScalar(1.2);
          if (label) {
            label.visible = true;
          }
        } else {
          // Fade out non-matching nodes
          material.color.setHex(0x666666);
          material.opacity = 0.1;
          material.emissiveIntensity = 0.02;
          mesh.scale.setScalar(0.5);
          if (label) {
            label.visible = false;
          }
        }
      }
    });
    
    // Also filter edges
    edgesRef.current.forEach((line, edgeId) => {
      const material = line.material as THREE.LineBasicMaterial;
      const [sourceId, targetId] = edgeId.split('->');
      
      if (!selectedSourceId) {
        material.opacity = 0.1;
        line.visible = true;
      } else {
        const sourceNode = graphData.nodes.find(n => n.id === sourceId);
        const targetNode = graphData.nodes.find(n => n.id === targetId);
        const sourceMatch = sourceNode?.metadata?.source_ids?.includes(selectedSourceId);
        const targetMatch = targetNode?.metadata?.source_ids?.includes(selectedSourceId);
        
        if (sourceMatch && targetMatch) {
          material.opacity = 0.5;
          line.visible = true;
        } else {
          material.opacity = 0.02;
          line.visible = false;
        }
      }
    });
  }, [selectedSourceId, graphData]);
  
  const refreshIntervalRef = useRef<NodeJS.Timeout | null>(null);

  // Track if scene is initialized
  const sceneInitialized = useRef(false);

  // Detect if dark mode is active
  const isDarkMode = () => document.documentElement.classList.contains('dark');
  
  // Initialize Three.js scene ONCE
  useEffect(() => {
    if (!containerRef.current || sceneInitialized.current) return;
    sceneInitialized.current = true;
    
    const container = containerRef.current;
    const width = container.clientWidth || 800;
    const height = container.clientHeight || 600;
    
    // Scene - use theme-appropriate background
    const scene = new THREE.Scene();
    const darkBg = 0x111827;  // gray-900
    const lightBg = 0xf9fafb; // gray-50
    const bgColor = isDarkMode() ? darkBg : lightBg;
    scene.background = new THREE.Color(bgColor);
    scene.fog = new THREE.Fog(bgColor, 200, 600);
    sceneRef.current = scene;
    
    // Watch for theme changes
    const themeObserver = new MutationObserver(() => {
      const newBg = isDarkMode() ? darkBg : lightBg;
      scene.background = new THREE.Color(newBg);
      scene.fog = new THREE.Fog(newBg, 200, 600);
    });
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
    
    // Camera
    const camera = new THREE.PerspectiveCamera(60, width / height, 0.1, 1000);
    camera.position.set(0, 0, 200);
    cameraRef.current = camera;
    
    // Renderer
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.appendChild(renderer.domElement);
    rendererRef.current = renderer;
    
    // Controls
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.05;
    controls.rotateSpeed = 0.5;
    controls.zoomSpeed = 0.8;
    controls.minDistance = 50;
    controls.maxDistance = 500;
    controls.autoRotate = true;
    controls.autoRotateSpeed = 0.2;
    controlsRef.current = controls;
    
    // Ambient light
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.4);
    scene.add(ambientLight);
    
    // Point light at center
    const pointLight = new THREE.PointLight(0x8B5CF6, 1, 300);
    pointLight.position.set(0, 0, 0);
    scene.add(pointLight);
    
    // Add subtle particle background (works in both light and dark mode)
    const starGeometry = new THREE.BufferGeometry();
    const starCount = 300;
    const starPositions = new Float32Array(starCount * 3);
    for (let i = 0; i < starCount * 3; i += 3) {
      starPositions[i] = (Math.random() - 0.5) * 800;
      starPositions[i + 1] = (Math.random() - 0.5) * 800;
      starPositions[i + 2] = (Math.random() - 0.5) * 800;
    }
    starGeometry.setAttribute('position', new THREE.BufferAttribute(starPositions, 3));
    const starColor = isDarkMode() ? 0xffffff : 0x6366F1;  // White in dark, indigo in light
    const starMaterial = new THREE.PointsMaterial({ 
      color: starColor, 
      size: 0.5, 
      transparent: true, 
      opacity: isDarkMode() ? 0.3 : 0.15 
    });
    const stars = new THREE.Points(starGeometry, starMaterial);
    scene.add(stars);
    
    // Update stars on theme change too
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
    
    // Animation loop - pauses when tab is hidden to save GPU/CPU
    let isTabVisible = true;
    const handleVisibilityChange = () => {
      isTabVisible = !document.hidden;
      if (isTabVisible && !animationRef.current) {
        animate(); // Resume animation when tab becomes visible
      }
    };
    document.addEventListener('visibilitychange', handleVisibilityChange);
    
    const animate = () => {
      if (!isTabVisible) {
        animationRef.current = null;
        return; // Stop animation when tab is hidden
      }
      animationRef.current = requestAnimationFrame(animate);
      controls.update();
      renderer.render(scene, camera);
    };
    animate();
    
    // Handle resize
    const handleResize = () => {
      if (!containerRef.current) return;
      const w = containerRef.current.clientWidth;
      const h = containerRef.current.clientHeight;
      if (w > 0 && h > 0) {
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        renderer.setSize(w, h);
      }
    };
    window.addEventListener('resize', handleResize);
    
    // Use ResizeObserver to detect container size changes (e.g., when Studio panel closes)
    const resizeObserver = new ResizeObserver(() => {
      handleResize();
    });
    resizeObserver.observe(container);
    
    // Also resize after a short delay to catch initial layout
    setTimeout(handleResize, 100);
    
    // Mark scene as ready to trigger graph load
    setSceneReady(true);
    
    // Cleanup
    return () => {
      window.removeEventListener('resize', handleResize);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      resizeObserver.disconnect();
      themeObserver.disconnect();
      if (animationRef.current) cancelAnimationFrame(animationRef.current);
      renderer.dispose();
      if (container.contains(renderer.domElement)) {
        container.removeChild(renderer.domElement);
      }
      sceneInitialized.current = false;
    };
  }, []);
  
  // Handle clicks and cursor changes
  useEffect(() => {
    const container = containerRef.current;
    const camera = cameraRef.current;
    const renderer = rendererRef.current;
    if (!container || !camera || !renderer) return;
    
    const canvas = renderer.domElement;
    const raycaster = new THREE.Raycaster();
    const mouse = new THREE.Vector2();
    let isDragging = false;
    let mouseDownTime = 0;
    let isMouseDown = false;
    
    const getMousePosition = (event: MouseEvent) => {
      // Use the canvas element for accurate coordinates
      const rect = canvas.getBoundingClientRect();
      mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    };
    
    const updateCursor = (event: MouseEvent) => {
      if (isMouseDown) {
        canvas.style.cursor = 'grabbing';
        return;
      }
      
      getMousePosition(event);
      raycaster.setFromCamera(mouse, camera);
      const meshes = Array.from(nodesRef.current.values());
      const intersects = raycaster.intersectObjects(meshes, false);
      
      canvas.style.cursor = intersects.length > 0 ? 'pointer' : 'grab';
    };
    
    const handleMouseDown = () => {
      isDragging = false;
      mouseDownTime = Date.now();
      isMouseDown = true;
      canvas.style.cursor = 'grabbing';
    };
    
    const handleMouseMove = (event: MouseEvent) => {
      if (isMouseDown && Date.now() - mouseDownTime > 200) {
        isDragging = true;
      }
      updateCursor(event);
    };
    
    const handleMouseUp = () => {
      isMouseDown = false;
    };
    
    const handleClick = (event: MouseEvent) => {
      if (isDragging) return;
      
      getMousePosition(event);
      raycaster.setFromCamera(mouse, camera);
      const meshes = Array.from(nodesRef.current.values());
      
      if (meshes.length === 0) return;
      
      const intersects = raycaster.intersectObjects(meshes, false);
      
      if (intersects.length > 0) {
        const mesh = intersects[0].object as THREE.Mesh;
        const nodeId = mesh.userData.nodeId;
        focusOnNode(nodeId);
      }
    };
    
    const handleMouseLeave = () => {
      isMouseDown = false;
      canvas.style.cursor = 'grab';
    };
    
    // Set initial cursor on canvas
    canvas.style.cursor = 'grab';
    
    // Attach events to canvas, not container
    canvas.addEventListener('mousedown', handleMouseDown);
    canvas.addEventListener('mousemove', handleMouseMove);
    canvas.addEventListener('mouseup', handleMouseUp);
    canvas.addEventListener('mouseleave', handleMouseLeave);
    canvas.addEventListener('click', handleClick);
    
    return () => {
      canvas.removeEventListener('mousedown', handleMouseDown);
      canvas.removeEventListener('mousemove', handleMouseMove);
      canvas.removeEventListener('mouseup', handleMouseUp);
      canvas.removeEventListener('mouseleave', handleMouseLeave);
      canvas.removeEventListener('click', handleClick);
    };
  }, [graphData, focusOnNode]);
  
  // Update scene when graph data changes OR when scene becomes available
  useEffect(() => {
    if (graphData && sceneRef.current) {
      updateScene(graphData);
    }
  }, [graphData, sceneReady]);
  
  // Load graph after scene is ready (fixes race condition)
  useEffect(() => {
    if (sceneReady && (notebookId || crossNotebook)) {
      loadGraph();
    }
  }, [sceneReady, notebookId, crossNotebook]);
  

  // Load stats and connect WebSocket
  useEffect(() => {
    loadStats();
    
    // Connect to WebSocket for real-time updates (optional - falls back to polling)
    let wsRetryCount = 0;
    const maxRetries = 3;
    
    const connectWebSocket = () => {
      if (wsRetryCount >= maxRetries) {
        console.log('WebSocket not available, using polling fallback');
        return;
      }
      
      try {
        const ws = new WebSocket(`${WS_BASE_URL}/constellation/ws`);
        
        ws.onopen = () => {
          console.log('Constellation WebSocket connected');
          wsRetryCount = 0;  // Reset on successful connection
        };
        
        ws.onmessage = (event) => {
          try {
            const message = JSON.parse(event.data);
            console.log('WebSocket message:', message.type, message.data);
            
            switch (message.type) {
              case 'connected':
                console.log('WebSocket confirmed connected');
                break;
              case 'concept_added':
                loadGraph();
                loadStats();
                break;
              case 'build_progress':
                console.log('Build progress:', message.data.progress);
                setBuildProgress(prev => Math.max(prev, message.data.progress));
                break;
              case 'build_complete':
                // v0.6.5: BERTopic handles topic discovery automatically
                console.log('Build complete - topics ready');
                setBuilding(false);
                setBuildProgress(100);
                loadGraph();
                loadStats();
                break;
              case 'cluster_progress':
                console.log('Clustering progress:', message.data.phase, message.data.progress);
                break;
              case 'cluster_complete':
                console.log('Clustering complete - refreshing graph with theme colors');
                loadGraph();
                loadStats();
                break;
              case 'enhancement_progress':
                console.log('Enhancement progress:', message.data);
                if (message.data.status === 'starting' || message.data.status === 'enhancing') {
                  setEnhancing(true);
                  setEnhanceProgress({ current: message.data.current, total: message.data.total });
                } else if (message.data.status === 'complete') {
                  setEnhancing(false);
                  setEnhanceProgress({ current: 0, total: 0 });
                  loadGraph();
                  loadStats();
                }
                break;
              case 'source_updated':
                // Track source processing status
                const sourceData = message.data;
                if (sourceData.notebook_id === notebookIdRef.current) {
                  if (sourceData.status === 'processing') {
                    setProcessingSources(prev => new Set(prev).add(sourceData.source_id));
                  } else if (sourceData.status === 'completed' || sourceData.status === 'failed') {
                    setProcessingSources(prev => {
                      const next = new Set(prev);
                      next.delete(sourceData.source_id);
                      return next;
                    });
                    // Refresh graph when source completes
                    if (sourceData.status === 'completed') {
                      loadGraph();
                      loadStats();
                    }
                  }
                }
                break;
            }
          } catch (err) {
            console.error('WebSocket parse error:', err);
          }
        };
        
        ws.onclose = () => {
          wsRetryCount++;
          if (wsRetryCount < maxRetries) {
            setTimeout(connectWebSocket, 5000);
          }
        };
        
        ws.onerror = () => {
          // Silent - will trigger onclose
        };
        
        wsRef.current = ws;
      } catch {
        // WebSocket not supported or blocked
      }
    };
    
    // Try WebSocket but don't block on it
    setTimeout(connectWebSocket, 1000);
    
    return () => {
      if (refreshIntervalRef.current) clearInterval(refreshIntervalRef.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, []);

  // Load stats when notebook changes (graph loading handled by sceneReady effect)
  useEffect(() => {
    if (notebookId || crossNotebook) {
      loadStats();
    }
  }, [notebookId, crossNotebook]);

  // Auto-refresh while building (reduced frequency to save resources)
  useEffect(() => {
    if (building) {
      refreshIntervalRef.current = setInterval(() => {
        loadStats();
        loadGraph();
      }, 20000);  // 20s instead of 10s to reduce network/CPU load
    } else {
      if (refreshIntervalRef.current) {
        clearInterval(refreshIntervalRef.current);
        refreshIntervalRef.current = null;
      }
    }
    return () => {
      if (refreshIntervalRef.current) clearInterval(refreshIntervalRef.current);
    };
  }, [building]);

  const loadStats = async () => {
    try {
      // Use ref to get current notebookId (avoids stale closure in WebSocket callbacks)
      const currentNotebookId = notebookIdRef.current;
      const data = await graphService.getStats(currentNotebookId || undefined);
      setStats(data as any);
    } catch (err) {
      console.error('Failed to load stats:', err);
    }
  };

  const loadGraph = async () => {
    // Use ref to get current notebook ID (avoids stale closure issues)
    const currentNotebookId = notebookIdRef.current;
    
    // Don't load if no notebook selected
    if (!currentNotebookId && !crossNotebook) {
      return;
    }
    
    setLoading(true);
    setError(null);
    
    try {
      const params = new URLSearchParams({
        include_clusters: 'true',
        min_link_strength: '0.3',
      });
      
      const endpoint = crossNotebook 
        ? `${API_BASE}/graph/all?${params}`
        : `${API_BASE}/graph/notebook/${currentNotebookId}?${params}`;
      
      console.log('[Constellation] Loading graph from:', endpoint);
      const response = await fetch(endpoint);
      
      if (response.ok) {
        const data: GraphData = await response.json();
        console.log('[Constellation] Loaded', data.nodes.length, 'nodes,', data.edges.length, 'edges,', data.clusters?.length || 0, 'clusters');
        if (data.clusters && data.clusters.length > 0) {
          console.log('[Constellation] Cluster colors will be applied:', data.clusters.map(c => c.name).join(', '));
        }
        setGraphData(data);
        updateScene(data);
        
        // AUTO-BUILD: If graph is empty and we haven't auto-triggered for this notebook yet,
        // check if sources exist and auto-trigger a topic rebuild
        if (data.nodes.length === 0 && currentNotebookId && !autoBuiltNotebooks.current.has(currentNotebookId) && !building) {
          try {
            const sourcesRes = await fetch(`${API_BASE}/sources/${currentNotebookId}`);
            if (sourcesRes.ok) {
              const sources = await sourcesRes.json();
              if (Array.isArray(sources) && sources.length >= 3) {
                console.log(`[Constellation] Auto-triggering topic build for ${currentNotebookId} (${sources.length} sources, 0 topics)`);
                autoBuiltNotebooks.current.add(currentNotebookId);
                // Small delay so UI renders the "discovering" state first
                setTimeout(() => buildConstellation(), 500);
              }
            }
          } catch (err) {
            console.log('[Constellation] Auto-build check failed:', err);
          }
        }
      } else {
        console.error('[Constellation] Failed to load graph:', response.status);
        setError('Failed to load graph');
      }
    } catch (err) {
      console.error('[Constellation] Error loading graph:', err);
      setError('Failed to connect to server');
    } finally {
      setLoading(false);
    }
  };

  const updateScene = useCallback((data: GraphData) => {
    const scene = sceneRef.current;
    if (!scene) {
      console.log('[Constellation] updateScene called but scene not ready');
      return;
    }
    console.log('[Constellation] updateScene with', data.nodes.length, 'nodes');
    
    // Clear existing tracked objects
    nodesRef.current.forEach(mesh => {
      scene.remove(mesh);
      mesh.geometry.dispose();
      (mesh.material as THREE.Material).dispose();
    });
    labelsRef.current.forEach(sprite => {
      scene.remove(sprite);
      sprite.material.dispose();
    });
    nodesRef.current.clear();
    labelsRef.current.clear();
    originalPositionsRef.current.clear();
    
    // Remove lines (they aren't tracked in refs)
    const linesToRemove = scene.children.filter(child => child instanceof THREE.Line);
    linesToRemove.forEach(line => {
      scene.remove(line);
      (line as THREE.Line).geometry.dispose();
      ((line as THREE.Line).material as THREE.Material).dispose();
    });
    
    if (data.nodes.length === 0) return;
    
    // Count connections and rank nodes
    const connectionCounts: Record<string, number> = {};
    for (const edge of data.edges) {
      connectionCounts[edge.source] = (connectionCounts[edge.source] || 0) + 1;
      connectionCounts[edge.target] = (connectionCounts[edge.target] || 0) + 1;
    }
    
    // Sort nodes by connection count to find top 25 "membrane" nodes
    const sortedByConnections = [...data.nodes].sort((a, b) => 
      (connectionCounts[b.id] || 0) - (connectionCounts[a.id] || 0)
    );
    const top25Ids = new Set(sortedByConnections.slice(0, 25).map(n => n.id));
    const maxConnections = Math.max(...Object.values(connectionCounts), 1);
    
    // Create cluster color mapping - each cluster gets a distinct color
    const clusterColors: Record<string, number> = {};
    const colorPalette = [
      0x6366F1,  // Indigo
      0x8B5CF6,  // Violet
      0xEC4899,  // Pink
      0x14B8A6,  // Teal
      0xF59E0B,  // Amber
      0x10B981,  // Emerald
      0x3B82F6,  // Blue
      0xEF4444,  // Red
      0x06B6D4,  // Cyan
      0x84CC16,  // Lime
      0xA855F7,  // Purple
      0xF97316,  // Orange
    ];
    
    // Map each node to its cluster color
    const nodeClusterColor: Record<string, number> = {};
    if (data.clusters && data.clusters.length > 0) {
      data.clusters.forEach((cluster, idx) => {
        const color = colorPalette[idx % colorPalette.length];
        clusterColors[cluster.id] = color;
        cluster.concept_ids.forEach(conceptId => {
          nodeClusterColor[conceptId] = color;
        });
      });
    }
    
    // Position nodes - CELL METAPHOR
    // Top 25 = outer membrane (visible shell)
    // Minor nodes = inner cytoplasm (hidden until zoom)
    const nodePositions: Record<string, THREE.Vector3> = {};
    
    // First, position top 25 on outer shell
    let topIndex = 0;
    const topNodes = data.nodes.filter(n => top25Ids.has(n.id));
    const minorNodes = data.nodes.filter(n => !top25Ids.has(n.id));
    
    topNodes.forEach((node) => {
      const connections = connectionCounts[node.id] || 0;
      const importance = connections / maxConnections;
      
      // Evenly distribute on outer sphere
      const outerRadius = 90;
      const phi = Math.acos(-1 + (2 * topIndex + 1) / (topNodes.length + 1));
      const theta = Math.PI * (1 + Math.sqrt(5)) * topIndex; // Golden angle
      
      const x = outerRadius * Math.sin(phi) * Math.cos(theta);
      const y = outerRadius * Math.sin(phi) * Math.sin(theta);
      const z = outerRadius * Math.cos(phi);
      
      nodePositions[node.id] = new THREE.Vector3(x, y, z);
      topIndex++;
      
      // Clean, refined nodes
      const nodeSize = 3 + (importance * 3);
      const geometry = new THREE.SphereGeometry(nodeSize, 32, 32);
      
      // Use cluster/theme color for constellation visualization (shows theme relationships)
      const clusterColor = nodeClusterColor[node.id] || 0x6366F1;
      const baseColor = new THREE.Color(clusterColor);
      const material = new THREE.MeshStandardMaterial({
        color: baseColor,
        emissive: baseColor,
        emissiveIntensity: 0.25 + importance * 0.2,
        metalness: 0.3,
        roughness: 0.5,
        transparent: true,
        opacity: 0.75 + importance * 0.15,
      });
      
      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.copy(nodePositions[node.id]);
      mesh.userData = { nodeId: node.id, connections, isTopNode: true, clusterColor };
      scene.add(mesh);
      nodesRef.current.set(node.id, mesh);
      
      // Elegant label - use cluster color, full text with outline
      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d')!;
      canvas.width = 1024;  // Wider for full text
      canvas.height = 128;
      
      const fontSize = 24 + (importance * 6);
      ctx.font = `600 ${fontSize}px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      
      // Draw outline first (contrasting color based on theme)
      const isDark = document.documentElement.classList.contains('dark');
      ctx.strokeStyle = isDark ? 'rgba(0, 0, 0, 0.8)' : 'rgba(255, 255, 255, 0.9)';
      ctx.lineWidth = 4;
      ctx.lineJoin = 'round';
      ctx.strokeText(node.label, 512, 64);
      
      // Then fill with cluster color
      const colorHex = '#' + baseColor.getHexString();
      ctx.fillStyle = colorHex;
      ctx.fillText(node.label, 512, 64);
      
      const texture = new THREE.CanvasTexture(canvas);
      const spriteMaterial = new THREE.SpriteMaterial({ 
        map: texture, 
        transparent: true,
        depthTest: false,
      });
      const sprite = new THREE.Sprite(spriteMaterial);
      sprite.position.copy(nodePositions[node.id]);
      sprite.position.y -= nodeSize + 8;
      sprite.scale.set(60, 8, 1);  // Wider for full text
      sprite.visible = true;
      sprite.raycast = () => {};  // Disable raycasting on labels
      
      scene.add(sprite);
      labelsRef.current.set(node.id, sprite);
    });
    
    // Position minor nodes inside - subtle interior
    minorNodes.forEach((node, i) => {
      const connections = connectionCounts[node.id] || 0;
      const minorImportance = connections / maxConnections;
      
      // Distribute inside, with some variation
      const innerRadius = 25 + Math.random() * 50;
      const phi = Math.acos(-1 + (2 * i) / minorNodes.length);
      const theta = Math.sqrt(minorNodes.length * Math.PI) * phi + Math.random() * 0.3;
      
      const x = innerRadius * Math.sin(phi) * Math.cos(theta);
      const y = innerRadius * Math.sin(phi) * Math.sin(theta);
      const z = innerRadius * Math.cos(phi);
      
      nodePositions[node.id] = new THREE.Vector3(x, y, z);
      
      // Small subtle dots - use cluster/theme color for visualization
      const clusterColor = nodeClusterColor[node.id] || 0x6366F1;
      const nodeSize = 1.5 + minorImportance * 1.5;
      const geometry = new THREE.SphereGeometry(nodeSize, 12, 12);
      const color = new THREE.Color(clusterColor);
      const material = new THREE.MeshStandardMaterial({
        color: color,
        emissive: color,
        emissiveIntensity: 0.15,
        metalness: 0.5,
        roughness: 0.4,
        transparent: true,
        opacity: 0.2 + minorImportance * 0.15,  // Subtle but visible
      });
      
      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.copy(nodePositions[node.id]);
      mesh.userData = { nodeId: node.id, connections, isTopNode: false, clusterColor };
      scene.add(mesh);
      nodesRef.current.set(node.id, mesh);
      
      // Create hidden label (shown on focus) - use cluster color, full text with outline
      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d')!;
      canvas.width = 1024;
      canvas.height = 128;
      ctx.font = '600 24px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      
      // Draw outline first
      const isDark = document.documentElement.classList.contains('dark');
      ctx.strokeStyle = isDark ? 'rgba(0, 0, 0, 0.8)' : 'rgba(255, 255, 255, 0.9)';
      ctx.lineWidth = 3;
      ctx.lineJoin = 'round';
      ctx.strokeText(node.label, 512, 64);
      
      // Then fill with cluster color
      const colorHex = '#' + color.getHexString();
      ctx.fillStyle = colorHex;
      ctx.fillText(node.label, 512, 64);
      
      const texture = new THREE.CanvasTexture(canvas);
      const spriteMaterial = new THREE.SpriteMaterial({ 
        map: texture, 
        transparent: true,
        depthTest: false,
      });
      const sprite = new THREE.Sprite(spriteMaterial);
      sprite.position.copy(nodePositions[node.id]);
      sprite.position.y -= nodeSize + 4;
      sprite.scale.set(50, 6, 1);  // Wider for full text
      sprite.visible = false;  // Hidden until focused
      sprite.raycast = () => {};  // Disable raycasting on labels
      
      scene.add(sprite);
      labelsRef.current.set(node.id, sprite);
    });
    
    // Create edges - ALL thin lines, unified color, gradient opacity
    // Calculate edge importance for gradient
    const edgeImportance: Map<string, number> = new Map();
    for (const edge of data.edges) {
      const sourceConn = connectionCounts[edge.source] || 0;
      const targetConn = connectionCounts[edge.target] || 0;
      const avgConn = (sourceConn + targetConn) / 2;
      edgeImportance.set(`${edge.source}-${edge.target}`, avgConn / maxConnections);
    }
    
    // Clear old edges
    edgesRef.current.forEach(line => {
      scene.remove(line);
      line.geometry.dispose();
      (line.material as THREE.Material).dispose();
    });
    edgesRef.current.clear();
    
    for (const edge of data.edges) {
      const sourcePos = nodePositions[edge.source];
      const targetPos = nodePositions[edge.target];
      
      if (!sourcePos || !targetPos) continue;
      
      const points = [sourcePos, targetPos];
      const geometry = new THREE.BufferGeometry().setFromPoints(points);
      
      // Single unified color - soft blue-gray
      const color = 0x94A3B8;
      
      // Smooth gradient opacity based on connection importance
      const importance = edgeImportance.get(`${edge.source}-${edge.target}`) || 0;
      const opacity = 0.03 + (importance * 0.2);  // Range: 0.03 to 0.23
      
      const material = new THREE.LineBasicMaterial({
        color: color,
        transparent: true,
        opacity: opacity,
      });
      
      const line = new THREE.Line(geometry, material);
      line.userData = { source: edge.source, target: edge.target };
      scene.add(line);
      edgesRef.current.set(`${edge.source}-${edge.target}`, line);
    }
  }, []);

  const buildConstellation = async () => {
    if (!notebookId) return;
    
    setBuilding(true);
    setBuildProgress(0);
    try {
      const response = await graphService.buildGraph(notebookId);
      if (response) {
        console.log('Building constellation - waiting for WebSocket updates...');
        // The WebSocket handler will set building=false when build_complete is received
        // But set a fallback timeout in case WebSocket isn't connected
        setTimeout(() => {
          if (building) {
            console.log('Build timeout - checking results...');
            setBuilding(false);
            setBuildProgress(100);
            loadGraph();
            loadStats();
            // Trigger clustering after build
            triggerClustering();
          }
        }, 180000); // 3 minute fallback
      }
    } catch (err) {
      console.error('Failed to build:', err);
      setBuilding(false);
    }
  };

  const triggerClustering = async () => {
    try {
      console.log('Triggering clustering for color coding...');
      await graphService.clusterGraph();
      // cluster_complete WebSocket event will trigger loadGraph/loadStats automatically
      // No need for setTimeout - the event-driven approach is more reliable
    } catch (err) {
      console.error('Failed to cluster:', err);
    }
  };

  const resetCamera = () => {
    if (cameraRef.current && controlsRef.current) {
      cameraRef.current.position.set(0, 0, 200);
      controlsRef.current.reset();
    }
  };


  return (
    <div className="h-full flex flex-col bg-gray-50 dark:bg-gray-900">
      {/* Controls - Responsive layout */}
      <div className="p-2 border-b border-gray-200 dark:border-gray-700 bg-white/80 dark:bg-gray-800/80 backdrop-blur flex-shrink-0">
        {/* Progress bar - full width when building or processing sources */}
        {(building || processingSources.size > 0) && (
          <div className="mb-2 flex items-center gap-2">
            <div className="flex-1 h-2 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
              {building ? (
                <div 
                  className="h-full bg-purple-500 transition-all duration-300"
                  style={{ width: `${buildProgress}%` }}
                />
              ) : (
                <div className="h-full bg-blue-500 animate-pulse w-full" />
              )}
            </div>
            <span className="text-xs text-purple-600 dark:text-purple-400 font-medium min-w-[3rem]">
              {building ? `${buildProgress.toFixed(0)}%` : `${processingSources.size} source(s)`}
            </span>
          </div>
        )}
        
        <div className={`flex items-center gap-2 ${rightSidebarCollapsed ? 'justify-between' : 'flex-wrap'}`}>
          {/* Left group - Rebuild button and stats */}
          <div className="flex items-center gap-2">
            {notebookId && (
              <button
                onClick={async () => {
                  setBuildProgress(0);
                  await buildConstellation();
                }}
                disabled={building || enhancing || processingSources.size > 0}
                className="px-3 py-1.5 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200 rounded text-sm disabled:opacity-50 font-medium whitespace-nowrap"
                title={processingSources.size > 0 ? `Processing ${processingSources.size} source(s)...` : enhancing ? `Enhancing theme names (${enhanceProgress.current}/${enhanceProgress.total})` : "Rebuild all topics from scratch"}
              >
                {building ? '🔄 Rebuilding...' : processingSources.size > 0 ? `📥 Processing ${processingSources.size} source(s)...` : enhancing ? `✨ Updating ${enhanceProgress.current}/${enhanceProgress.total}` : '🔄 Rebuild Topics'}
              </button>
            )}
            
            {stats && stats.concepts > 0 && (
              <span className="text-xs text-gray-500 dark:text-gray-400 whitespace-nowrap">
                {stats.concepts} themes • {stats.links} connections
              </span>
            )}
            
            {/* Insights Badge */}
            {notebookId && (
              <button
                onClick={async () => {
                  if (scanningInsights) return;
                  setScanningInsights(true);
                  try {
                    const data = await graphService.scanContradictions(notebookId);
                    setInsightCount(data.contradictions?.length || 0);
                  } catch (err) {
                    console.error('Failed to scan for insights:', err);
                  } finally {
                    setScanningInsights(false);
                  }
                }}
                disabled={scanningInsights}
                className={`px-2 py-1 rounded text-xs whitespace-nowrap flex items-center gap-1 ${
                  insightCount > 0 
                    ? 'bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400' 
                    : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-600'
                }`}
                title={scanningInsights ? 'Scanning...' : insightCount > 0 ? `${insightCount} contradictions found` : 'Scan for contradictions'}
              >
                {scanningInsights ? (
                  <span className="animate-spin">⟳</span>
                ) : insightCount > 0 ? (
                  <>⚠️ {insightCount}</>
                ) : (
                  <>🔍 Insights</>
                )}
              </button>
            )}
          </div>
          
          {/* Right group - Refresh and Reset (spread out when sidebar collapsed) */}
          <div className="flex items-center gap-2">
            <button
              onClick={() => {
                resetCamera();
                loadGraph();
                loadStats();
              }}
              disabled={loading}
              className="px-2 py-1 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-white text-xs whitespace-nowrap"
              title="Refresh and reset view"
            >
              ⟲ Refresh
            </button>
            
            {/* Reset Knowledge Graph - with confirmation */}
            {stats && stats.concepts > 0 && (
              <>
                {showResetConfirm ? (
                  <div className="flex items-center gap-1 bg-red-50 dark:bg-red-900/30 px-2 py-1 rounded">
                    <span className="text-xs text-red-600 dark:text-red-400">Clear?</span>
                    <button
                      onClick={async () => {
                        if (!notebookId) return;
                        setResetting(true);
                        try {
                          await graphService.resetGraph(notebookId);
                          setGraphData(null);
                          setStats(null);
                          loadGraph();
                        } catch (err) {
                          console.error('Failed to reset graph:', err);
                        } finally {
                          setResetting(false);
                          setShowResetConfirm(false);
                        }
                      }}
                      disabled={resetting}
                      className="text-xs px-1.5 py-0.5 bg-red-600 text-white rounded hover:bg-red-700 disabled:opacity-50"
                    >
                      {resetting ? '...' : 'Yes'}
                    </button>
                    <button
                      onClick={() => setShowResetConfirm(false)}
                      className="text-xs px-1.5 py-0.5 text-gray-600 dark:text-gray-400 hover:text-gray-800"
                    >
                      No
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={() => setShowResetConfirm(true)}
                    className="px-2 py-1 text-gray-400 hover:text-red-500 dark:text-gray-500 dark:hover:text-red-400 text-xs whitespace-nowrap"
                    title="Reset knowledge graph"
                  >
                    🗑️ Reset
                  </button>
                )}
              </>
            )}
          </div>
        </div>
      </div>

      {/* Main content - responsive split (canvas takes most space, info panel is fixed width) */}
      <div className="flex-1 flex overflow-hidden">
        {/* 3D Canvas - flexible, takes remaining space */}
        <div className="flex-1 min-w-0 relative">
          {!notebookId ? (
            <div className="absolute inset-0 flex items-center justify-center text-gray-400">
              <div className="text-center">
                <p className="text-4xl mb-4">✨</p>
                <p className="text-lg mb-2">Constellation</p>
                <p className="text-sm">Select a notebook to view its knowledge map</p>
              </div>
            </div>
          ) : loading && !graphData ? (
            <div className="absolute inset-0 flex items-center justify-center">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-purple-600"></div>
            </div>
          ) : error ? (
            <div className="absolute inset-0 flex items-center justify-center text-red-500">
              {error}
            </div>
          ) : graphData && graphData.nodes.length === 0 ? (
            <div className="absolute inset-0 flex items-center justify-center text-gray-400">
              <div className="text-center max-w-md">
                {building ? (
                  <>
                    <p className="text-4xl mb-4 animate-pulse">✨</p>
                    <p className="text-base font-medium mb-2">Discovering themes...</p>
                    <p className="text-sm mb-4 text-gray-500">
                      Analyzing your sources to find connections and patterns.
                    </p>
                    <div className="w-48 h-2 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden mx-auto">
                      <div 
                        className="h-full bg-purple-500 transition-all duration-300"
                        style={{ width: `${buildProgress}%` }}
                      />
                    </div>
                  </>
                ) : (
                  <>
                    <p className="text-4xl mb-4">✨</p>
                    <p className="text-base font-medium mb-2">No topics yet</p>
                    <p className="text-sm mb-4">
                      Add at least 3 sources, then topics will be discovered automatically.
                    </p>
                    {notebookId && (
                      <button
                        onClick={async () => {
                          await buildConstellation();
                        }}
                        disabled={building}
                        className="px-4 py-2 bg-gray-200 hover:bg-gray-300 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200 rounded-lg text-sm"
                      >
                        🔄 Rebuild Topics
                      </button>
                    )}
                  </>
                )}
              </div>
            </div>
          ) : null}
          
          <div 
            ref={containerRef} 
            className="w-full h-full"
            style={{ minHeight: '400px' }}
          />
          
          {/* Navigation controls overlay */}
          {focusedNodeId && (
            <div className="absolute top-4 left-4 flex gap-2">
              <button
                onClick={navigateBack}
                className="px-3 py-1.5 bg-white/90 dark:bg-gray-800/90 hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-700 dark:text-white rounded text-sm flex items-center gap-1 shadow-sm"
              >
                ← Back
              </button>
              <button
                onClick={resetToFullView}
                className="px-3 py-1.5 bg-white/90 dark:bg-gray-800/90 hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-700 dark:text-white rounded text-sm shadow-sm"
              >
                ⟲ Full View
              </button>
            </div>
          )}
          
          {/* Navigation hints - bottom left */}
          <div className="absolute bottom-4 left-4 text-xs text-gray-600 dark:text-gray-400 bg-white/70 dark:bg-gray-800/70 px-3 py-2 rounded backdrop-blur-sm shadow-sm">
            🖱️ Click node to focus • Drag to rotate • Scroll to zoom
          </div>
        </div>
      </div>
    </div>
  );
}
