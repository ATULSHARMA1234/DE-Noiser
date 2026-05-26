'use client';

import React, { useState, useEffect, useRef } from 'react';
import { Zap, Loader2, AlertTriangle, RefreshCw, FileText, Network, Maximize2, Minimize2, Activity, ArrowRight, Clock } from 'lucide-react';
import { apiFetch, runAnalysis as runAnalysisJob } from '@/lib/api';

interface Node {
  id: string;
  label: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  fx?: number | null;
  fy?: number | null;
  size: number;
  color: string;
  clusterCount: number;
}

interface Link {
  id: string;
  source: string;
  target: string;
  confidence: number;
  avgDelayMs: number;
  occurrences: number;
  sourceTemplate: string;
  targetTemplate: string;
  directionLabel: string;
  narrative?: string;
}

export default function ServiceTopology() {
  const [sources, setSources] = useState<any[]>([]);
  const [selectedSource, setSelectedSource] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [elapsedTime, setElapsedTime] = useState(0);

  // Graph Data State
  const [nodes, setNodes] = useState<Node[]>([]);
  const [links, setLinks] = useState<Link[]>([]);
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);
  const [selectedLink, setSelectedLink] = useState<Link | null>(null);

  // View Controls
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [isDraggingCanvas, setIsDraggingCanvas] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
  const [minConfidence, setMinConfidence] = useState(0.3);
  const [enablePhysics, setEnablePhysics] = useState(true);

  // Physics Drag State
  const [draggedNodeId, setDraggedNodeId] = useState<string | null>(null);

  const svgRef = useRef<SVGSVGElement | null>(null);
  const requestRef = useRef<number | null>(null);

  // Mock initial topology to instantly WOW the user on landing page
  const loadMockTopology = () => {
    const mockNodes: Node[] = [
      { id: 'gateway_service', label: 'gateway_service', x: 200, y: 150, vx: 0, vy: 0, size: 28, color: '#3b82f6', clusterCount: 4 },
      { id: 'auth_service', label: 'auth_service', x: 100, y: 300, vx: 0, vy: 0, size: 24, color: '#10b981', clusterCount: 2 },
      { id: 'payment_service', label: 'payment_service', x: 300, y: 300, vx: 0, vy: 0, size: 26, color: '#d946ef', clusterCount: 5 },
      { id: 'order_service', label: 'order_service', x: 500, y: 300, vx: 0, vy: 0, size: 24, color: '#f59e0b', clusterCount: 3 },
      { id: 'database_replica', label: 'database_replica', x: 300, y: 450, vx: 0, vy: 0, size: 30, color: '#ef4444', clusterCount: 8 }
    ];

    const mockLinks: Link[] = [
      {
        id: 'link_1',
        source: 'gateway_service',
        target: 'auth_service',
        confidence: 0.85,
        avgDelayMs: 12.5,
        occurrences: 14,
        sourceTemplate: 'GET /api/v1/checkout/session - 500 Internal Error',
        targetTemplate: 'JWT Token verification failed: expired signature',
        directionLabel: 'gateway_service -> auth_service'
      },
      {
        id: 'link_2',
        source: 'gateway_service',
        target: 'payment_service',
        confidence: 0.92,
        avgDelayMs: 45.2,
        occurrences: 28,
        sourceTemplate: 'POST /api/v1/payment/charge - 504 Gateway Timeout',
        targetTemplate: 'Failed to charge card for user_<ID>: timeout from Stripe gateway',
        directionLabel: 'gateway_service -> payment_service'
      },
      {
        id: 'link_3',
        source: 'payment_service',
        target: 'database_replica',
        confidence: 0.96,
        avgDelayMs: 110.8,
        occurrences: 45,
        sourceTemplate: 'Database transaction failed: rollback initiated',
        targetTemplate: 'SQL Error: PG::ConnectionBad: PQconsumeInput() connection closed',
        directionLabel: 'payment_service -> database_replica'
      },
      {
        id: 'link_4',
        source: 'order_service',
        target: 'database_replica',
        confidence: 0.74,
        avgDelayMs: 154.1,
        occurrences: 19,
        sourceTemplate: 'WARN Order confirmation delay detected for order_<ID>',
        targetTemplate: 'SQL Error: PG::ConnectionBad: PQconsumeInput() connection closed',
        directionLabel: 'order_service -> database_replica'
      }
    ];

    setNodes(mockNodes);
    setLinks(mockLinks);
    // Auto-select the strongest link to showcase drill-down sidebars
    setSelectedLink(mockLinks[2]);
  };

  // 1. Fetch available sources on mount
  useEffect(() => {
    apiFetch('/sources')
      .then((data) => {
        setSources(data);
        if (data.length > 0) setSelectedSource(data[0].path);
      })
      .catch(console.error);

    loadMockTopology();
  }, []);

  // 2. Timer for loading state
  useEffect(() => {
    let interval: any;
    if (loading) {
      setElapsedTime(0);
      interval = setInterval(() => setElapsedTime(t => t + 1), 1000);
    }
    return () => clearInterval(interval);
  }, [loading]);

  // 3. Trigger Real Log Analysis
  const runAnalysis = async () => {
    if (!selectedSource) return;

    setLoading(true);
    setError(null);
    setSelectedNode(null);
    setSelectedLink(null);

    try {
      const result = await runAnalysisJob({ source: selectedSource, intelligence: true });
      
      if (!result.causal_links || result.causal_links.length === 0) {
        setError("Analysis complete, but no cross-service causal co-occurrences were found in these logs. Reverted to interactive demo data.");
        loadMockTopology();
        return;
      }

      // Dynamic Node & Edge derivation from causal links
      const derivedNodesMap: Record<string, Node> = {};
      const derivedLinks: Link[] = [];

      const colors = ['#3b82f6', '#d946ef', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899'];
      let colorIdx = 0;

      result.causal_links.forEach((link: any, idx: number) => {
        const srcId = link.source_service;
        const tgtId = link.target_service;

        // Initialize nodes if not exists
        if (!derivedNodesMap[srcId]) {
          derivedNodesMap[srcId] = {
            id: srcId,
            label: srcId,
            x: 150 + Math.random() * 300,
            y: 150 + Math.random() * 300,
            vx: 0,
            vy: 0,
            size: 24,
            color: colors[colorIdx++ % colors.length],
            clusterCount: 1
          };
        }
        if (!derivedNodesMap[tgtId]) {
          derivedNodesMap[tgtId] = {
            id: tgtId,
            label: tgtId,
            x: 150 + Math.random() * 300,
            y: 150 + Math.random() * 300,
            vx: 0,
            vy: 0,
            size: 24,
            color: colors[colorIdx++ % colors.length],
            clusterCount: 1
          };
        }

        derivedNodesMap[srcId].clusterCount += 1;
        derivedNodesMap[tgtId].clusterCount += 1;

        derivedLinks.push({
          id: `link_${idx}`,
          source: srcId,
          target: tgtId,
          confidence: link.confidence,
          avgDelayMs: link.avg_delay_ms,
          occurrences: link.occurrences,
          sourceTemplate: link.source_template,
          targetTemplate: link.target_template,
          directionLabel: link.direction,
          narrative: link.narrative
        });
      });

      // Update node sizing based on cluster counts
      const derivedNodesList = Object.values(derivedNodesMap).map(node => ({
        ...node,
        size: Math.min(40, Math.max(20, 20 + node.clusterCount * 1.5))
      }));

      setNodes(derivedNodesList);
      setLinks(derivedLinks);
      if (derivedLinks.length > 0) {
        setSelectedLink(derivedLinks[0]);
      }

    } catch (err: any) {
      setError(err.message || 'API connection failed. Reverted to interactive demo data.');
      loadMockTopology();
    } finally {
      setLoading(false);
    }
  };

  // 4. Interactive Physics Verlet Integration Loop
  useEffect(() => {
    if (!enablePhysics || nodes.length === 0) return;

    const tick = () => {
      setNodes(prevNodes => {
        // Create working copy of node positions
        const nextNodes = prevNodes.map(n => ({ ...n }));

        const width = 800;
        const height = 500;
        const kSpring = 0.005; // Spring attraction force
        const dRest = 200;    // Rest length of links
        const kRepel = 600;   // Coulomb repulsion force
        const kGravity = 0.005; // Force pulling nodes to center

        // Force calculations
        // A. Coulomb Repulsion between every pair of nodes
        for (let i = 0; i < nextNodes.length; i++) {
          const n1 = nextNodes[i];
          for (let j = i + 1; j < nextNodes.length; j++) {
            const n2 = nextNodes[j];

            const dx = n2.x - n1.x;
            const dy = n2.y - n1.y;
            const d = Math.sqrt(dx * dx + dy * dy) || 1.0;

            if (d < 400) {
              const f = kRepel / (d * d);
              const fx = (dx / d) * f;
              const fy = (dy / d) * f;

              // Apply repulsion (equal and opposite)
              n1.vx -= fx;
              n1.vy -= fy;
              n2.vx += fx;
              n2.vy += fy;
            }
          }
        }

        // B. Spring Attraction between connected nodes
        links.forEach(link => {
          const nSrc = nextNodes.find(n => n.id === link.source);
          const nTgt = nextNodes.find(n => n.id === link.target);

          if (nSrc && nTgt) {
            const dx = nTgt.x - nSrc.x;
            const dy = nTgt.y - nSrc.y;
            const d = Math.sqrt(dx * dx + dy * dy) || 1.0;

            const f = kSpring * (d - dRest);
            const fx = (dx / d) * f;
            const fy = (dy / d) * f;

            nSrc.vx += fx;
            nSrc.vy += fy;
            nTgt.vx -= fx;
            nTgt.vy -= fy;
          }
        });

        // C. Center Gravity & Integration
        nextNodes.forEach(node => {
          // Pull to center
          const dx = width / 2 - node.x;
          const dy = height / 2 - node.y;
          node.vx += dx * kGravity;
          node.vy += dy * kGravity;

          // Apply velocity and drag friction
          if (node.id === draggedNodeId) {
            // Keep fixed if dragged
            node.vx = 0;
            node.vy = 0;
          } else {
            node.x += node.vx;
            node.y += node.vy;
            node.vx *= 0.85; // High friction for stabilization
            node.vy *= 0.85;
          }

          // Boundary box containment
          node.x = Math.max(40, Math.min(width - 40, node.x));
          node.y = Math.max(40, Math.min(height - 40, node.y));
        });

        return nextNodes;
      });

      requestRef.current = requestAnimationFrame(tick);
    };

    requestRef.current = requestAnimationFrame(tick);
    return () => {
      if (requestRef.current) cancelAnimationFrame(requestRef.current);
    };
  }, [links, enablePhysics, draggedNodeId, nodes.length]);

  // 5. Canvas Dragging / Pan Implementation
  const handleMouseDown = (e: React.MouseEvent) => {
    if (draggedNodeId) return; // Prioritize node drag
    setIsDraggingCanvas(true);
    setDragStart({ x: e.clientX - pan.x, y: e.clientY - pan.y });
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (isDraggingCanvas) {
      setPan({
        x: e.clientX - dragStart.x,
        y: e.clientY - dragStart.y
      });
    } else if (draggedNodeId && svgRef.current) {
      const rect = svgRef.current.getBoundingClientRect();
      // Translate raw screen coordinate to zoomed & panned SVG coordinate space
      const svgX = (e.clientX - rect.left - pan.x) / zoom;
      const svgY = (e.clientY - rect.top - pan.y) / zoom;

      setNodes(prev =>
        prev.map(node =>
          node.id === draggedNodeId ? { ...node, x: svgX, y: svgY, vx: 0, vy: 0 } : node
        )
      );
    }
  };

  const handleMouseUp = () => {
    setIsDraggingCanvas(false);
    setDraggedNodeId(null);
  };

  const handleZoom = (factor: number) => {
    setZoom(z => Math.max(0.4, Math.min(3, z * factor)));
  };

  const resetView = () => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
    // Reset positions
    setNodes(prev => prev.map((n, i) => ({
      ...n,
      x: 150 + (i % 3) * 200,
      y: 150 + Math.floor(i / 3) * 150,
      vx: 0, vy: 0
    })));
  };

  // Filter links by slider value
  const filteredLinks = links.filter(l => l.confidence >= minConfidence);

  return (
    <div className="space-y-6 max-w-[1600px] mx-auto pb-10">
      
      {/* Topology Header Controls */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold text-white flex items-center gap-2">
            <Network className="text-fuchsia-500" size={24} /> Service Causal Topology
          </h1>
          <p className="text-xs text-zinc-500 mt-1">
            Real-time visual dependency graph displaying neural co-occurrences and time-decay causality propagation.
          </p>
        </div>

        {/* Source selector */}
        <div className="flex items-center gap-3 bg-[#121214] border border-white/5 rounded-xl px-4 py-2">
          <FileText size={16} className="text-zinc-500 shrink-0" />
          <select 
            value={selectedSource}
            onChange={(e) => setSelectedSource(e.target.value)}
            className="bg-[#141416] border border-white/10 text-zinc-300 text-xs rounded-lg px-4 py-2 outline-none w-52 appearance-none cursor-pointer"
          >
            {sources.map((src) => (
              <option key={src.path} value={src.path}>
                {src.name}
              </option>
            ))}
          </select>
          <button
            onClick={runAnalysis}
            disabled={loading || !selectedSource}
            className="bg-fuchsia-600 hover:bg-fuchsia-500 disabled:opacity-50 text-white font-bold rounded-lg px-4 py-2 text-xs flex items-center gap-1.5 transition-colors cursor-pointer border-none"
          >
            {loading ? (
              <><Loader2 size={13} className="animate-spin" /> Analyzing ({elapsedTime}s)</>
            ) : (
              <><RefreshCw size={13} /> Run Forensic Graph</>
            )}
          </button>
        </div>
      </div>

      {/* Main Grid View */}
      <div className="grid grid-cols-3 gap-6">
        
        {/* GRAPH VIEWPORT PANEL */}
        <div className="col-span-2 bg-[#121214] border border-white/5 rounded-2xl overflow-hidden shadow-2xl flex flex-col h-[580px] relative select-none">
          
          {/* Controls Overlay */}
          <div className="absolute top-4 left-4 z-20 flex items-center gap-2 bg-[#18181b]/80 backdrop-blur-md border border-white/10 px-3 py-2 rounded-xl">
            <button onClick={() => handleZoom(1.2)} title="Zoom In" className="w-8 h-8 rounded-lg hover:bg-white/10 text-zinc-300 flex items-center justify-center border-none cursor-pointer transition-colors">
              <Maximize2 size={15} />
            </button>
            <button onClick={() => handleZoom(0.8)} title="Zoom Out" className="w-8 h-8 rounded-lg hover:bg-white/10 text-zinc-300 flex items-center justify-center border-none cursor-pointer transition-colors">
              <Minimize2 size={15} />
            </button>
            <button onClick={resetView} title="Reset Canvas" className="w-8 h-8 rounded-lg hover:bg-white/10 text-zinc-300 flex items-center justify-center border-none cursor-pointer transition-colors">
              <Activity size={15} />
            </button>
            <div className="h-4 w-[1px] bg-white/10 mx-1"></div>
            <label className="flex items-center gap-2 text-[10px] font-bold text-zinc-400 cursor-pointer">
              <input 
                type="checkbox" 
                checked={enablePhysics} 
                onChange={(e) => setEnablePhysics(e.target.checked)}
                className="accent-fuchsia-500 rounded"
              />
              PHYSICS
            </label>
          </div>

          <div className="absolute top-4 right-4 z-20 bg-[#18181b]/80 backdrop-blur-md border border-white/10 px-4 py-2 rounded-xl flex items-center gap-3">
            <span className="text-[10px] font-bold text-zinc-400">MIN CONFIDENCE:</span>
            <input 
              type="range" 
              min="0.1" 
              max="0.9" 
              step="0.05" 
              value={minConfidence} 
              onChange={(e) => setMinConfidence(parseFloat(e.target.value))}
              className="accent-fuchsia-500 w-24 h-1 bg-zinc-700 rounded-lg cursor-pointer"
            />
            <span className="text-xs font-mono font-bold text-fuchsia-400 w-8">{minConfidence.toFixed(2)}</span>
          </div>

          {/* SVG Canvas */}
          <div 
            className="flex-1 w-full h-full cursor-grab active:cursor-grabbing overflow-hidden bg-black/30"
            onMouseDown={handleMouseDown}
            onMouseMove={handleMouseMove}
            onMouseUp={handleMouseUp}
            onMouseLeave={handleMouseUp}
          >
            <svg 
              ref={svgRef} 
              width="100%" 
              height="100%" 
              className="w-full h-full"
            >
              {/* SVG Glowing Drop Shadows */}
              <defs>
                <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
                  <feGaussianBlur stdDeviation="6" result="blur" />
                  <feMerge>
                    <feMergeNode in="blur" />
                    <feMergeNode in="SourceGraphic" />
                  </feMerge>
                </filter>
              </defs>

              {/* View Group with Zoom & Pan */}
              <g transform={`translate(${pan.x}, ${pan.y}) scale(${zoom})`}>
                
                {/* DRAW EDGES (Causal Links) */}
                {filteredLinks.map((link) => {
                  const srcNode = nodes.find(n => n.id === link.source);
                  const tgtNode = nodes.find(n => n.id === link.target);

                  if (!srcNode || !tgtNode) return null;

                  const isSelected = selectedLink?.id === link.id;

                  // Compute path offset for curved directed lines
                  const dx = tgtNode.x - srcNode.x;
                  const dy = tgtNode.y - srcNode.y;
                  const d = Math.sqrt(dx * dx + dy * dy) || 1.0;

                  // Target boundary node radius clearance
                  const targetRadiusOffset = tgtNode.size + 12; 
                  const targetX = tgtNode.x - (dx / d) * targetRadiusOffset;
                  const targetY = tgtNode.y - (dy / d) * targetRadiusOffset;

                  return (
                    <g key={link.id} className="group">
                      {/* Active Clickable Outer Wide Path (Gives easy hover/click margin) */}
                      <path 
                        d={`M ${srcNode.x} ${srcNode.y} L ${targetX} ${targetY}`}
                        stroke="transparent"
                        strokeWidth="16"
                        className="cursor-pointer"
                        onClick={(e) => {
                          e.stopPropagation();
                          setSelectedLink(link);
                          setSelectedNode(null);
                        }}
                      />

                      {/* Displayed Directed Line */}
                      <path 
                        d={`M ${srcNode.x} ${srcNode.y} L ${targetX} ${targetY}`}
                        stroke={isSelected ? '#d946ef' : 'rgba(255,255,255,0.15)'}
                        strokeWidth={isSelected ? 3.5 : 2}
                        strokeDasharray={isSelected ? '6,4' : link.confidence > 0.8 ? '4,4' : 'none'}
                        className="transition-all duration-300"
                        markerEnd={`url(#arrow-${link.id})`}
                      />

                      {/* Custom Arrowheads */}
                      <defs>
                        <marker 
                          id={`arrow-${link.id}`} 
                          viewBox="0 0 10 10" 
                          refX="6" 
                          refY="5" 
                          markerWidth="6" 
                          markerHeight="6" 
                          orient="auto-start-reverse"
                        >
                          <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill={isSelected ? '#d946ef' : 'rgba(255,255,255,0.3)'} />
                        </marker>
                      </defs>

                      {/* Moving Glowing Causal Event Packet (Flowing speed based on confidence) */}
                      <circle r="4" fill="#d946ef" filter="url(#glow)">
                        <animateMotion 
                          path={`M ${srcNode.x} ${srcNode.y} L ${targetX} ${targetY}`} 
                          dur={`${Math.max(1, 4 - link.confidence * 3)}s`} 
                          repeatCount="indefinite" 
                        />
                      </circle>

                      {/* Delay Tag overlay along line */}
                      <g transform={`translate(${(srcNode.x + tgtNode.x) / 2}, ${(srcNode.y + tgtNode.y) / 2})`}>
                        <rect 
                          x="-28" 
                          y="-9" 
                          width="56" 
                          height="18" 
                          rx="4" 
                          fill="#18181b" 
                          stroke={isSelected ? '#d946ef' : 'rgba(255,255,255,0.1)'} 
                          strokeWidth="1"
                        />
                        <text 
                          fill={isSelected ? '#d946ef' : '#71717a'} 
                          fontSize="9" 
                          fontWeight="bold" 
                          fontFamily="monospace"
                          textAnchor="middle" 
                          y="3"
                        >
                          {link.avgDelayMs.toFixed(0)}ms
                        </text>
                      </g>
                    </g>
                  );
                })}

                {/* DRAW NODES (Services) */}
                {nodes.map((node) => {
                  const isSelected = selectedNode?.id === node.id;
                  const isCausalNeighbor = selectedLink && (selectedLink.source === node.id || selectedLink.target === node.id);

                  return (
                    <g 
                      key={node.id} 
                      transform={`translate(${node.x}, ${node.y})`}
                      className="cursor-grab active:cursor-grabbing"
                      onMouseDown={(e) => {
                        e.stopPropagation();
                        setDraggedNodeId(node.id);
                        setSelectedNode(node);
                        setSelectedLink(null);
                      }}
                    >
                      {/* Selection Neon Halo */}
                      {(isSelected || isCausalNeighbor) && (
                        <circle 
                          r={node.size + 10} 
                          fill="none" 
                          stroke={isSelected ? '#d946ef' : '#3b82f6'} 
                          strokeWidth="2" 
                          strokeDasharray="4,4"
                          className="animate-spin"
                          style={{ animationDuration: '15s' }}
                        />
                      )}

                      {/* Node Glowing Circle */}
                      <circle 
                        r={node.size} 
                        fill="#121214" 
                        stroke={node.color} 
                        strokeWidth={isSelected ? 4 : 2}
                        filter={isSelected ? 'url(#glow)' : ''}
                        className="transition-all duration-300"
                      />

                      {/* Floating glowing dot indicating health */}
                      <circle 
                        cx={node.size * 0.7} 
                        cy={-node.size * 0.7} 
                        r="5" 
                        fill={node.color} 
                        filter="url(#glow)"
                        className="animate-pulse"
                      />

                      {/* Service Initials Label */}
                      <text 
                        fill="#ffffff" 
                        fontSize="10" 
                        fontWeight="bold" 
                        fontFamily="sans-serif"
                        textAnchor="middle" 
                        y="4"
                      >
                        {node.label.split('_').map(w => w[0]).join('').toUpperCase().substring(0, 3)}
                      </text>

                      {/* Service Full Text Header (displays below node) */}
                      <text 
                        fill="#a1a1aa" 
                        fontSize="11" 
                        fontWeight="600" 
                        fontFamily="sans-serif"
                        textAnchor="middle" 
                        y={node.size + 18}
                        className="bg-black/80 px-2 py-0.5 rounded pointer-events-none select-none"
                      >
                        {node.label}
                      </text>
                    </g>
                  );
                })}

              </g>
            </svg>
          </div>

          {/* Graph Legend overlay */}
          <div className="absolute bottom-4 left-4 bg-[#18181b]/80 backdrop-blur-md border border-white/5 px-4 py-3 rounded-xl space-y-1.5 text-[10px] text-zinc-500 font-bold z-20">
            <div className="flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full bg-blue-500" /> Gateway & Routing
            </div>
            <div className="flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full bg-d946ef bg-fuchsia-500" /> Business logic & Payments
            </div>
            <div className="flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full bg-emerald-500" /> Security & Auth
            </div>
            <div className="flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full bg-red-500" /> Database & Storage
            </div>
            <div className="flex items-center gap-2 mt-1 pt-1 border-t border-white/5">
              <span className="text-zinc-500 border border-zinc-500/30 px-1 rounded font-mono font-normal">50ms</span> Avg Delay Label
            </div>
          </div>
        </div>

        {/* SIDEBAR DETAILED INSPECTION DRAWER */}
        <div className="col-span-1 bg-[#121214] border border-white/5 rounded-2xl p-6 h-[580px] flex flex-col shadow-2xl overflow-y-auto">
          
          {/* Handle Error State */}
          {error && (
            <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl flex items-start gap-2.5 text-xs mb-4">
              <AlertTriangle size={16} className="shrink-0 mt-0.5" />
              <div>
                <p className="font-bold">Correlation Notice</p>
                <p className="mt-1 leading-relaxed text-zinc-400">{error}</p>
              </div>
            </div>
          )}

          {/* ════ CAUSAL LINK DETAILS ════ */}
          {selectedLink && (
            <div className="space-y-6 flex-1 flex flex-col">
              <div className="flex items-start justify-between">
                <div>
                  <span className="text-[10px] font-bold tracking-widest text-fuchsia-400 bg-fuchsia-500/10 border border-fuchsia-500/20 px-2.5 py-1 rounded-sm uppercase">
                    Causal Link Inspected
                  </span>
                  <h2 className="text-base font-bold text-white mt-3 flex items-center gap-2">
                    {selectedLink.source} <ArrowRight size={14} className="text-fuchsia-500" /> {selectedLink.target}
                  </h2>
                </div>
              </div>

              {/* Statistics grid */}
              <div className="grid grid-cols-3 gap-3">
                <div className="bg-[#18181b] border border-white/5 rounded-lg p-3 text-center">
                  <p className="text-[9px] font-bold text-zinc-500 uppercase">Confidence</p>
                  <p className="text-lg font-bold text-fuchsia-400 mt-1">{(selectedLink.confidence * 100).toFixed(0)}%</p>
                </div>
                <div className="bg-[#18181b] border border-white/5 rounded-lg p-3 text-center">
                  <p className="text-[9px] font-bold text-zinc-500 uppercase">Avg Delay</p>
                  <p className="text-lg font-bold text-white mt-1 flex items-center justify-center gap-1">
                    <Clock size={12} className="text-zinc-500" /> {selectedLink.avgDelayMs.toFixed(1)}<span className="text-[10px] text-zinc-500 font-normal">ms</span>
                  </p>
                </div>
                <div className="bg-[#18181b] border border-white/5 rounded-lg p-3 text-center">
                  <p className="text-[9px] font-bold text-zinc-500 uppercase">Co-occurrences</p>
                  <p className="text-lg font-bold text-zinc-300 mt-1">{selectedLink.occurrences}</p>
                </div>
              </div>

              {/* LLM Plain-English Causal Narrative */}
              {selectedLink.narrative && (
                <div className="bg-fuchsia-500/5 border border-fuchsia-500/10 rounded-xl p-4 text-xs leading-relaxed text-zinc-300">
                  <p className="text-[10px] font-bold text-fuchsia-400 uppercase tracking-widest mb-1.5 flex items-center gap-1.5">
                    <Zap size={10} className="text-fuchsia-500 animate-pulse" /> AI Causal Narration
                  </p>
                  {selectedLink.narrative}
                </div>
              )}

              {/* Forensics Split Terminals */}
              <div className="space-y-3 flex-1 flex flex-col min-h-0">
                <p className="text-[10px] font-bold text-zinc-500 uppercase tracking-widest">Co-occurring Log Templates</p>
                
                {/* Source service template */}
                <div className="flex-1 bg-black/50 border border-white/5 rounded-lg p-4 font-mono text-[10px] overflow-y-auto flex flex-col relative">
                  <span className="absolute top-2 right-2 text-[8px] font-bold px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-400 border border-blue-500/20">
                    {selectedLink.source} (CAUSE)
                  </span>
                  <div className="text-zinc-500 mt-2 font-bold select-none">{'// Log Pattern:'}</div>
                  <div className="text-zinc-300 leading-relaxed mt-1">{selectedLink.sourceTemplate}</div>
                </div>

                {/* Delay Indicator separator */}
                <div className="flex items-center justify-center gap-3">
                  <div className="h-[1px] bg-white/5 flex-1"></div>
                  <span className="text-[9px] font-bold text-fuchsia-400 font-mono bg-fuchsia-500/10 px-2 py-0.5 rounded-full border border-fuchsia-500/20 flex items-center gap-1 animate-pulse">
                    + {selectedLink.avgDelayMs.toFixed(1)}ms delay propagation
                  </span>
                  <div className="h-[1px] bg-white/5 flex-1"></div>
                </div>

                {/* Target service template */}
                <div className="flex-1 bg-black/50 border border-white/5 rounded-lg p-4 font-mono text-[10px] overflow-y-auto flex flex-col relative">
                  <span className="absolute top-2 right-2 text-[8px] font-bold px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400">
                    {selectedLink.target} (EFFECT)
                  </span>
                  <div className="text-zinc-500 mt-2 font-bold select-none">{'// Log Pattern:'}</div>
                  <div className="text-zinc-300 leading-relaxed mt-1">{selectedLink.targetTemplate}</div>
                </div>
              </div>
            </div>
          )}

          {/* ════ SERVICE (NODE) DETAILS ════ */}
          {selectedNode && (
            <div className="space-y-6 flex-1 flex flex-col">
              <div>
                <span className="text-[10px] font-bold tracking-widest text-blue-400 bg-blue-500/10 border border-blue-500/20 px-2.5 py-1 rounded-sm uppercase">
                  Service Node Inspected
                </span>
                <h2 className="text-lg font-bold text-white mt-3 flex items-center gap-2">
                  <span className="w-3 h-3 rounded-full" style={{ backgroundColor: selectedNode.color }} />
                  {selectedNode.label}
                </h2>
              </div>

              {/* Node statistics */}
              <div className="grid grid-cols-2 gap-3">
                <div className="bg-[#18181b] border border-white/5 rounded-lg p-4 text-center">
                  <p className="text-[9px] font-bold text-zinc-500 uppercase">Causal Incidents</p>
                  <p className="text-2xl font-bold text-white mt-1">{selectedNode.clusterCount}</p>
                </div>
                <div className="bg-[#18181b] border border-white/5 rounded-lg p-4 text-center">
                  <p className="text-[9px] font-bold text-zinc-500 uppercase">Network Role</p>
                  <p className="text-xs font-bold text-zinc-400 mt-2 truncate" style={{ color: selectedNode.color }}>
                    {selectedNode.color === '#ef4444' ? 'Database/Storage' :
                     selectedNode.color === '#10b981' ? 'Auth/Security' :
                     selectedNode.color === '#d946ef' ? 'Transaction/Core' : 'Inbound Gateway'}
                  </p>
                </div>
              </div>

              {/* Node active dependencies list */}
              <div className="space-y-3 flex-1 overflow-y-auto">
                <p className="text-[10px] font-bold text-zinc-500 uppercase tracking-widest">Active Causal Links</p>
                <div className="space-y-2">
                  {links.filter(l => l.source === selectedNode.id || l.target === selectedNode.id).map(link => {
                    const isSource = link.source === selectedNode.id;
                    const counterpart = isSource ? link.target : link.source;

                    return (
                      <div 
                        key={link.id}
                        onClick={() => setSelectedLink(link)}
                        className="bg-[#18181b] hover:bg-white/5 border border-white/5 rounded-xl p-3.5 flex items-center justify-between cursor-pointer transition-colors"
                      >
                        <div className="flex items-center gap-2">
                          <span className={`text-[9px] font-bold px-2 py-0.5 rounded ${
                            isSource ? 'bg-red-500/10 text-red-400' : 'bg-emerald-500/10 text-emerald-400'
                          }`}>
                            {isSource ? 'OUTBOUND' : 'INBOUND'}
                          </span>
                          <span className="text-xs font-medium text-white truncate max-w-[120px]">{counterpart}</span>
                        </div>
                        <div className="text-right">
                          <span className="text-xs font-bold text-fuchsia-400">{(link.confidence * 100).toFixed(0)}%</span>
                          <p className="text-[9px] text-zinc-500">{link.avgDelayMs.toFixed(0)}ms latency</p>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          )}

          {/* ════ DEFAULT IDLE VIEW ════ */}
          {!selectedLink && !selectedNode && (
            <div className="flex-1 flex flex-col items-center justify-center text-center p-6 space-y-4">
              <Network className="text-zinc-600 animate-pulse" size={48} />
              <div>
                <h3 className="text-sm font-bold text-white">Select a Link or Node</h3>
                <p className="text-xs text-zinc-500 mt-1 max-w-[200px] mx-auto leading-relaxed">
                  Click on an edge to inspect co-occurring templates side-by-side, or click a node to see its internal service statistics.
                </p>
              </div>
            </div>
          )}
        </div>

      </div>

    </div>
  );
}
