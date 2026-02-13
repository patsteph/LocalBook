"""SVG Template System for Visual Generation

Replaces Mermaid with pure SVG for reliable, beautiful visual output.
Each template maps to one of 8 base patterns, customized with colors and data.

=============================================================================
FLEXIBLE LAYOUT SYSTEM - NO HARDCODED LIMITS
=============================================================================

Templates dynamically scale to handle ANY number of items. Each template
declares its optimal range and overflow strategy. The layout engine
adjusts sizing, spacing, and arrangement based on actual item count.

TEMPLATE_CAPACITIES defines how each template handles variable counts:
- optimal: (min, max) for best visual quality
- overflow: strategy when count exceeds optimal max
  - "scale": shrink items to fit (most templates)
  - "two_column": split into columns (lists, summaries)
  - "wrap": wrap to multiple rows (grids)

=============================================================================
VISUAL DESIGN GUIDELINES (Applied to all templates)
=============================================================================

1. CLARITY & SIMPLICITY
   - Avoid chartjunk - every element should convey information
   - Use whitespace generously for breathing room
   - Limit items per visual (5-6 max) for cognitive load

2. VISUAL HIERARCHY
   - Size encodes importance (larger = more important)
   - Position encodes sequence (top/left = first)
   - Color distinguishes categories, not decorates

3. TEXT READABILITY
   - Titles: 24px, bold, max ~60 chars (word boundary truncation)
   - Labels: 12-14px, max 3 lines, never cut mid-word
   - Left-align text for lists, center for standalone items

4. COLOR PRINCIPLES
   - Qualitative palette for categories (no inherent order)
   - Sequential palette for numeric/ordered data
   - Maintain sufficient contrast (white text on colored backgrounds)

5. PREATTENTIVE ATTRIBUTES
   - Use position and length for precise comparisons
   - Use color hue for categorical distinction
   - Use size for emphasis (center nodes larger than spokes)

=============================================================================
Base Patterns:
1. Hub-Spoke: Central node with radiating connections
   - Templates: key_stats, key_takeaways, concept_map, anatomy, overview_map, overview, stakeholder_map
2. Flow-Horizontal: Left-to-right step progression (max 5 steps)
   - Templates: horizontal_steps, process_flow, timeline, stages_progression
3. Flow-Vertical: Top-to-bottom progression (funnel, ranking)
   - Templates: funnel, ranking, recommendation_stack
4. Two-Column: Side-by-side comparison (pros/cons)
   - Templates: pros_cons, side_by_side, force_field, scope
5. Grid/Matrix: 2x2 or NxN grid layout
   - Templates: quadrant, heatmap, mece
6. Cycle: Circular process flow
   - Templates: cycle_loop, causal_loop
7. Hierarchy: Tree structure (top-down)
   - Templates: system_architecture, decision_tree, argument
8. Chart: Bar/line/pie visualizations
   - Templates: trend_chart, distribution

Benefits over Mermaid:
- Guaranteed text rendering (no broken labels)
- Consistent styling across all templates
- Full control over colors, fonts, spacing
- No external library parsing bugs
- Export-ready (PNG/SVG)
"""

from typing import List, Dict, Any, Tuple
from dataclasses import dataclass
import html
import re
import math


# =============================================================================
# TEMPLATE CAPACITIES - Single source of truth for layout constraints
# =============================================================================
# Each template can handle ANY number of items by scaling dynamically.
# These values guide optimal layout, not hard limits.

TEMPLATE_CAPACITIES = {
    # Hub-spoke patterns: radial layout scales by reducing spoke size
    "hub_spoke": {"optimal": (3, 8), "overflow": "scale"},
    "key_takeaways": {"optimal": (3, 8), "overflow": "scale"},
    "key_stats": {"optimal": (3, 6), "overflow": "scale"},
    "concept_map": {"optimal": (3, 8), "overflow": "scale"},
    "anatomy": {"optimal": (3, 6), "overflow": "scale"},
    "overview_map": {"optimal": (3, 8), "overflow": "scale"},
    "stakeholder_map": {"optimal": (3, 8), "overflow": "scale"},
    
    # Mindmap: horizontal branches scale by reducing branch width
    "mindmap": {"optimal": (3, 10), "overflow": "scale"},
    
    # Executive summary: card list, uses two columns for overflow
    "exec_summary": {"optimal": (3, 12), "overflow": "two_column"},
    
    # Flow patterns: horizontal has tighter limits due to width
    "horizontal_steps": {"optimal": (3, 7), "overflow": "scale"},
    "process_flow": {"optimal": (3, 6), "overflow": "scale"},
    "timeline": {"optimal": (3, 8), "overflow": "scale"},
    "stages_progression": {"optimal": (3, 6), "overflow": "scale"},
    
    # Vertical flow: can handle more items
    "funnel": {"optimal": (3, 8), "overflow": "scale"},
    "ranking": {"optimal": (3, 10), "overflow": "scale"},
    "recommendation_stack": {"optimal": (3, 8), "overflow": "scale"},
    
    # Two-column: inherently balanced
    "pros_cons": {"optimal": (2, 8), "overflow": "scale"},
    "side_by_side": {"optimal": (2, 8), "overflow": "scale"},
    "force_field": {"optimal": (2, 8), "overflow": "scale"},
    "scope": {"optimal": (2, 8), "overflow": "scale"},
    
    # Hierarchy: tree scales by reducing node size
    "mece": {"optimal": (3, 10), "overflow": "scale"},
    "system_architecture": {"optimal": (3, 8), "overflow": "scale"},
    "decision_tree": {"optimal": (3, 8), "overflow": "scale"},
    
    # Cycle: circular, limited by geometry
    "cycle_loop": {"optimal": (3, 8), "overflow": "scale"},
    "causal_loop": {"optimal": (3, 6), "overflow": "scale"},
    
    # Quadrant: fixed 4 quadrants, items distributed
    "quadrant": {"optimal": (4, 8), "overflow": "scale"},
    "heatmap": {"optimal": (4, 12), "overflow": "wrap"},
}


def get_layout_params(template_id: str, item_count: int) -> dict:
    """Calculate dynamic layout parameters based on item count.
    
    Returns scaling factors and layout hints for the template.
    """
    capacity = TEMPLATE_CAPACITIES.get(template_id, {"optimal": (3, 8), "overflow": "scale"})
    opt_min, opt_max = capacity["optimal"]
    overflow = capacity["overflow"]
    
    # Calculate how much we need to scale
    if item_count <= opt_max:
        scale_factor = 1.0
        use_overflow = False
    else:
        # Scale down proportionally
        scale_factor = opt_max / item_count
        use_overflow = True
    
    # Determine layout strategy
    if use_overflow and overflow == "two_column" and item_count > 5:
        layout_mode = "two_column"
        columns = 2
    elif use_overflow and overflow == "wrap":
        layout_mode = "wrap"
        columns = min(4, (item_count + 1) // 2)
    else:
        layout_mode = "single"
        columns = 1
    
    return {
        "scale_factor": max(0.5, scale_factor),  # Never scale below 50%
        "layout_mode": layout_mode,
        "columns": columns,
        "item_count": item_count,
        "is_overflow": use_overflow,
    }


@dataclass
class SVGConfig:
    """Configuration for SVG generation."""
    width: int = 800
    height: int = 600
    padding: int = 40
    font_family: str = "ui-sans-serif, system-ui, -apple-system, sans-serif"
    font_size_title: int = 24
    font_size_label: int = 14
    font_size_small: int = 12
    corner_radius: int = 8
    node_padding: int = 16


# =============================================================================
# LAYOUT ENGINE: Collision Detection & Resolution
# =============================================================================

@dataclass
class LayoutNode:
    """A node with position and bounding box for collision detection."""
    id: str
    x: float
    y: float
    width: float
    height: float
    fixed: bool = False  # If True, node won't be moved during resolution
    
    @property
    def left(self) -> float:
        return self.x - self.width / 2
    
    @property
    def right(self) -> float:
        return self.x + self.width / 2
    
    @property
    def top(self) -> float:
        return self.y - self.height / 2
    
    @property
    def bottom(self) -> float:
        return self.y + self.height / 2
    
    def overlaps(self, other: 'LayoutNode', padding: float = 5) -> bool:
        """Check if this node overlaps with another (with optional padding)."""
        return not (
            self.right + padding < other.left or
            self.left - padding > other.right or
            self.bottom + padding < other.top or
            self.top - padding > other.bottom
        )
    
    def overlap_vector(self, other: 'LayoutNode') -> Tuple[float, float]:
        """Calculate vector to push nodes apart if overlapping."""
        dx = self.x - other.x
        dy = self.y - other.y
        
        # Calculate overlap amounts
        overlap_x = (self.width + other.width) / 2 - abs(dx)
        overlap_y = (self.height + other.height) / 2 - abs(dy)
        
        if overlap_x <= 0 or overlap_y <= 0:
            return (0, 0)
        
        # Push in direction of least overlap
        if overlap_x < overlap_y:
            return (math.copysign(overlap_x + 10, dx), 0)
        else:
            return (0, math.copysign(overlap_y + 10, dy))


def resolve_collisions(
    nodes: List[LayoutNode],
    canvas_width: float = 800,
    canvas_height: float = 600,
    padding: float = 40,
    max_iterations: int = 50
) -> List[LayoutNode]:
    """
    Resolve overlapping nodes by pushing them apart iteratively.
    
    Uses a simple force-based approach:
    1. Detect overlapping pairs
    2. Push overlapping nodes apart
    3. Keep nodes within canvas bounds
    4. Repeat until no overlaps or max iterations
    
    Args:
        nodes: List of LayoutNode objects to resolve
        canvas_width: Width of the canvas
        canvas_height: Height of the canvas
        padding: Minimum padding from canvas edges
        max_iterations: Maximum resolution iterations
        
    Returns:
        List of LayoutNode objects with resolved positions
    """
    if not nodes:
        return nodes
    
    for iteration in range(max_iterations):
        moved = False
        
        # Check all pairs for collisions
        for i, node_a in enumerate(nodes):
            if node_a.fixed:
                continue
                
            for node_b in nodes[i + 1:]:
                if node_a.overlaps(node_b):
                    # Calculate push vector
                    vx, vy = node_a.overlap_vector(node_b)
                    
                    # Apply push (split between both nodes unless one is fixed)
                    if node_b.fixed:
                        node_a.x += vx
                        node_a.y += vy
                    else:
                        node_a.x += vx / 2
                        node_a.y += vy / 2
                        node_b.x -= vx / 2
                        node_b.y -= vy / 2
                    
                    moved = True
        
        # Keep nodes within canvas bounds
        for node in nodes:
            if node.fixed:
                continue
            node.x = max(padding + node.width / 2, min(canvas_width - padding - node.width / 2, node.x))
            node.y = max(padding + node.height / 2, min(canvas_height - padding - node.height / 2, node.y))
        
        # Early exit if no movement
        if not moved:
            break
    
    return nodes


def calculate_text_bbox(text: str, font_size: int = 14, padding: int = 16) -> Tuple[float, float]:
    """Estimate bounding box for text (width, height)."""
    # Approximate character width based on font size
    char_width = font_size * 0.55
    line_height = font_size * 1.4
    
    lines = text.split('\n') if '\n' in text else [text]
    max_line_len = max(len(line) for line in lines)
    
    width = max_line_len * char_width + padding * 2
    height = len(lines) * line_height + padding
    
    return (width, height)


# Color Themes - synced with frontend VisualToolbar.tsx palettes
COLOR_THEMES = {
    "auto": ["#ef4444", "#f97316", "#eab308", "#22c55e", "#3b82f6", "#8b5cf6"],
    "vibrant": ["#ef4444", "#f97316", "#eab308", "#22c55e", "#3b82f6", "#8b5cf6"],
    "ocean": ["#0ea5e9", "#06b6d4", "#14b8a6", "#0d9488", "#0891b2", "#0284c7"],
    "sunset": ["#f97316", "#fb923c", "#fbbf24", "#f59e0b", "#dc2626", "#facc15"],
    "forest": ["#22c55e", "#16a34a", "#15803d", "#84cc16", "#65a30d", "#10b981"],
    "monochrome": ["#1f2937", "#374151", "#4b5563", "#6b7280", "#9ca3af", "#d1d5db"],
    "pastel": ["#fecaca", "#fed7aa", "#fef08a", "#bbf7d0", "#bfdbfe", "#ddd6fe"],
}

# Dark mode background colors
DARK_BG = "#1e293b"
DARK_TEXT = "#f1f5f9"
DARK_MUTED = "#94a3b8"

# Light mode colors
LIGHT_BG = "#ffffff"
LIGHT_TEXT = "#1e293b"
LIGHT_MUTED = "#64748b"


def clean_label_text(text: str) -> str:
    """Remove citation artifacts and clean up label text for visuals.
    
    Strips patterns like:
    - ([1], [2], [3], [4]):
    - (Cited in [1], [2], [5])
    - (, , , ): - empty after citation removal
    - [1], [2]
    - **bold markers**
    - Leading "And ", "Or "
    - Empty parentheses (), trailing &
    - Trailing colons :
    """
    if not text:
        return text
    
    # Remove citation brackets FIRST [1], [2], etc.
    cleaned = re.sub(r'\s*\[\d+\]', '', text)
    # Remove "(Cited in ...)" patterns
    cleaned = re.sub(r'\s*\(Cited in[^)]*\)', '', cleaned)
    # Remove parentheses that now only contain commas/spaces: (, , , ) or ( ):
    cleaned = re.sub(r'\s*\(\s*[,\s]*\s*\)\s*:?', '', cleaned)
    # Remove parentheses with just ellipsis: (...)
    cleaned = re.sub(r'\s*\(\s*\.{2,}\s*\)', '', cleaned)
    # Remove INCOMPLETE citation brackets at end: [1, [1,2, etc.
    cleaned = re.sub(r'\s*\[\d[\d,\s]*$', '', cleaned)
    # Remove markdown bold markers
    cleaned = re.sub(r'\*\*', '', cleaned)
    # Remove empty parentheses ()
    cleaned = re.sub(r'\s*\(\s*\)', '', cleaned)
    # Remove incomplete parentheses at end like "(e" or "(e.g."
    cleaned = re.sub(r'\s*\([^)]{0,10}$', '', cleaned)
    # Remove leading "And " or "Or " (garbage from extraction)
    cleaned = re.sub(r'^(And|Or)\s+', '', cleaned, flags=re.IGNORECASE)
    # Remove trailing " &" or " and" (incomplete conjunctions)
    cleaned = re.sub(r'\s+(&|and)\s*$', '', cleaned, flags=re.IGNORECASE)
    # Remove trailing colons
    cleaned = re.sub(r'\s*:\s*$', '', cleaned)
    # Clean up extra whitespace
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    # CRITICAL: Strip trailing ellipsis - NEVER show "..."
    while cleaned.endswith('...'):
        cleaned = cleaned[:-3].strip()
    while cleaned.endswith('..'):
        cleaned = cleaned[:-2].strip()
    return cleaned


def smart_shorten_label(text: str, max_len: int = 500) -> str:
    """Clean a label - NO TRUNCATION. Let CSS handle overflow with word-wrap.
    
    This function ONLY cleans text. It does NOT add '...' or truncate.
    All text overflow is handled by CSS word-wrap in foreignObject elements.
    """
    if not text:
        return text
    
    # Just clean the text - NO TRUNCATION
    return clean_label_text(text)


def escape_svg_text(text: str, max_len: int = 500) -> str:
    """Escape text for safe SVG rendering - NO TRUNCATION."""
    if not text:
        return ""
    # Just clean and escape - no truncation
    cleaned = clean_label_text(str(text))
    return html.escape(cleaned)


def escape_title(text: str, max_len: int = 200) -> str:
    """Escape title text - NO TRUNCATION."""
    if not text:
        return ""
    # Just clean and escape - no truncation
    cleaned = clean_label_text(str(text))
    return html.escape(cleaned)


def wrap_text(text: str, max_chars: int = 20, max_lines: int = 3) -> List[str]:
    """Wrap text into multiple lines for node labels. Never cuts mid-word."""
    if not text:
        return [""]
    # Smart shorten first to remove filler words, then wrap
    # Calculate total chars available across all lines
    total_chars = max_chars * max_lines
    text = smart_shorten_label(str(text).strip(), total_chars)
    words = text.split()
    
    if not words:
        return [""]
    
    lines = []
    current_line = ""
    
    for word in words:
        # If word alone is longer than max, keep it whole - CSS handles overflow
        pass  # Don't truncate individual words
        
        # Check if adding this word exceeds limit
        test_line = current_line + (" " if current_line else "") + word
        
        if len(test_line) <= max_chars:
            current_line = test_line
        else:
            # Start new line
            if current_line:
                lines.append(current_line)
            current_line = word
            
            # Stop if we hit max lines
            if len(lines) >= max_lines - 1:
                break
    
    # Add final line - NO TRUNCATION, let CSS handle overflow
    if current_line:
        lines.append(current_line)
    
    return lines if lines else [""]


class SVGTemplateBuilder:
    """Builds SVG visualizations from structured data."""
    
    def __init__(self, dark_mode: bool = True):
        self.dark_mode = dark_mode
        self.config = SVGConfig()
        self.bg_color = DARK_BG if dark_mode else LIGHT_BG
        self.text_color = DARK_TEXT if dark_mode else LIGHT_TEXT
        self.muted_color = DARK_MUTED if dark_mode else LIGHT_MUTED
    
    def _svg_header(self, width: int = None, height: int = None) -> str:
        """Generate SVG header with viewBox for scaling."""
        w = width or self.config.width
        h = height or self.config.height
        return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="100%" height="100%">
  <defs>
    <filter id="shadow" x="-20%" y="-20%" width="140%" height="140%">
      <feDropShadow dx="0" dy="2" stdDeviation="3" flood-opacity="0.15"/>
    </filter>
    <linearGradient id="grad1" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#6366f1;stop-opacity:1" />
      <stop offset="100%" style="stop-color:#8b5cf6;stop-opacity:1" />
    </linearGradient>
  </defs>
  <rect width="100%" height="100%" fill="{self.bg_color}"/>
'''
    
    def _svg_footer(self) -> str:
        return "</svg>"
    
    def _text_element(self, x: int, y: int, text: str, font_size: int = 14, 
                      color: str = None, anchor: str = "middle", bold: bool = False,
                      max_len: int = 40) -> str:
        """Generate a text element."""
        color = color or self.text_color
        weight = "600" if bold else "400"
        escaped = escape_svg_text(text, max_len)
        return f'  <text x="{x}" y="{y}" font-family="{self.config.font_family}" font-size="{font_size}" fill="{color}" text-anchor="{anchor}" font-weight="{weight}">{escaped}</text>\n'
    
    def _title_element(self, x: int, y: int, title: str) -> str:
        """Generate a title text element with proper truncation."""
        escaped = escape_title(title, 50)
        return f'  <text x="{x}" y="{y}" font-family="{self.config.font_family}" font-size="{self.config.font_size_title}" fill="{self.text_color}" text-anchor="middle" font-weight="600">{escaped}</text>\n'
    
    def _multiline_text(self, x: int, y: int, lines: List[str], font_size: int = 14,
                        color: str = None, anchor: str = "middle", line_height: int = 20,
                        max_len: int = 40) -> str:
        """Generate multiline text."""
        color = color or self.text_color
        result = ""
        for i, line in enumerate(lines):
            result += self._text_element(x, y + i * line_height, line, font_size, color, anchor, max_len=max_len)
        return result
    
    def _text_in_rect(self, x: int, y: int, width: int, height: int, text: str,
                      font_size: int = 12, color: str = "#ffffff", 
                      align: str = "center", padding: int = 8) -> str:
        """Generate text that wraps within rectangle boundaries using foreignObject.
        
        Uses HTML/CSS inside SVG for proper text wrapping.
        Dynamically adjusts font size if text is long.
        """
        escaped = escape_svg_text(text, max_len=200)
        inner_w = width - padding * 2
        inner_h = height - padding * 2
        
        # Dynamic font sizing based on text length and box size
        text_len = len(text)
        if text_len > 60 or inner_w < 100:
            font_size = max(9, font_size - 2)
        elif text_len > 40:
            font_size = max(10, font_size - 1)
        
        return f'''  <foreignObject x="{x + padding}" y="{y + padding}" width="{inner_w}" height="{inner_h}">
    <div xmlns="http://www.w3.org/1999/xhtml" style="
      width: 100%; height: 100%;
      display: flex; align-items: center; justify-content: {align};
      text-align: {align};
      font-family: {self.config.font_family};
      font-size: {font_size}px;
      color: {color};
      line-height: 1.2;
      word-wrap: break-word;
      overflow-wrap: break-word;
      word-break: break-word;
      overflow: hidden;
    ">{escaped}</div>
  </foreignObject>
'''
    
    def _text_in_circle(self, cx: int, cy: int, r: int, text: str,
                        font_size: int = 11, color: str = "#ffffff") -> str:
        """Generate text that wraps within circle boundaries using foreignObject.
        
        Creates a centered square inscribed in the circle for text area.
        Dynamically adjusts font size based on text length and circle size.
        """
        # Inscribed square in circle: side = r * sqrt(2) ≈ r * 1.41
        side = int(r * 1.4)  # Slightly larger for better text fit
        x = cx - side // 2
        y = cy - side // 2
        escaped = escape_svg_text(text, max_len=150)
        
        # Dynamic font sizing based on text length and circle size
        text_len = len(text)
        if r < 45 or text_len > 50:
            font_size = max(8, font_size - 3)
        elif r < 55 or text_len > 35:
            font_size = max(9, font_size - 2)
        elif text_len > 25:
            font_size = max(10, font_size - 1)
        
        return f'''  <foreignObject x="{x}" y="{y}" width="{side}" height="{side}">
    <div xmlns="http://www.w3.org/1999/xhtml" style="
      width: 100%; height: 100%;
      display: flex; align-items: center; justify-content: center;
      text-align: center;
      font-family: {self.config.font_family};
      font-size: {font_size}px;
      color: {color};
      line-height: 1.15;
      word-wrap: break-word;
      overflow-wrap: break-word;
      word-break: break-word;
      overflow: hidden;
    ">{escaped}</div>
  </foreignObject>
'''
    
    def _insight_text(self, cx: int, y: int, width: int, text: str, 
                       font_size: int = 11, color: str = None) -> str:
        """Generate insight/tagline text at bottom with word wrapping.
        
        Uses foreignObject for proper multi-line text wrapping.
        NO TRUNCATION - let CSS handle wrapping naturally.
        """
        color = color or self.muted_color
        # Clean the text
        clean_text = clean_label_text(text) if text else ""
        if not clean_text:
            return ""
        
        # NO TRUNCATION - CSS handles overflow with word-wrap
        
        # Calculate box dimensions - leave margins on sides
        box_width = min(width - 40, 900)  # Wider for longer text
        box_x = cx - box_width // 2
        box_height = 70  # Allow 3-4 lines for full tagline
        
        # NO max_len - use full text, CSS handles wrapping
        escaped = html.escape(clean_text)
        
        return f'''  <foreignObject x="{box_x}" y="{y - 10}" width="{box_width}" height="{box_height}">
    <div xmlns="http://www.w3.org/1999/xhtml" style="
      width: 100%; height: 100%;
      display: flex; align-items: flex-start; justify-content: center;
      text-align: center;
      font-family: {self.config.font_family};
      font-size: {font_size}px;
      color: {color};
      line-height: 1.4;
      word-wrap: break-word;
      overflow-wrap: break-word;
    ">{escaped}</div>
  </foreignObject>
'''
    
    def _rounded_rect(self, x: int, y: int, width: int, height: int, 
                      fill: str, stroke: str = None, rx: int = None) -> str:
        """Generate a rounded rectangle."""
        rx = rx or self.config.corner_radius
        stroke_attr = f'stroke="{stroke}" stroke-width="2"' if stroke else ""
        return f'  <rect x="{x}" y="{y}" width="{width}" height="{height}" rx="{rx}" fill="{fill}" {stroke_attr} filter="url(#shadow)"/>\n'
    
    def _circle(self, cx: int, cy: int, r: int, fill: str, stroke: str = None) -> str:
        """Generate a circle."""
        stroke_attr = f'stroke="{stroke}" stroke-width="2"' if stroke else ""
        return f'  <circle cx="{cx}" cy="{cy}" r="{r}" fill="{fill}" {stroke_attr} filter="url(#shadow)"/>\n'
    
    def _line(self, x1: int, y1: int, x2: int, y2: int, color: str, width: int = 2, opacity: float = 1.0) -> str:
        """Generate a line with optional opacity."""
        opacity_attr = f' opacity="{opacity}"' if opacity < 1.0 else ""
        return f'  <line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{width}"{opacity_attr}/>\n'
    
    def _resolve_node_layout(
        self, 
        nodes: List[Dict[str, Any]], 
        fixed_nodes: List[Dict[str, Any]] = None,
        canvas_width: int = 800, 
        canvas_height: int = 600
    ) -> List[Dict[str, Any]]:
        """
        Resolve collisions between nodes and return updated positions.
        
        Args:
            nodes: List of dicts with {id, x, y, width, height, ...}
            fixed_nodes: List of fixed nodes that won't move but others avoid
            canvas_width/height: Canvas bounds
            
        Returns:
            List of dicts with resolved {id, x, y} positions
        """
        layout_nodes = []
        
        # Add fixed nodes first
        if fixed_nodes:
            for fn in fixed_nodes:
                layout_nodes.append(LayoutNode(
                    id=fn.get("id", "fixed"),
                    x=fn["x"], y=fn["y"],
                    width=fn.get("width", 50), height=fn.get("height", 50),
                    fixed=True
                ))
        
        # Add movable nodes
        for node in nodes:
            layout_nodes.append(LayoutNode(
                id=node.get("id", "node"),
                x=node["x"], y=node["y"],
                width=node.get("width", 50), height=node.get("height", 30),
                fixed=False
            ))
        
        # Resolve collisions
        resolved = resolve_collisions(layout_nodes, canvas_width, canvas_height)
        
        # Return as dicts
        return [{"id": n.id, "x": n.x, "y": n.y} for n in resolved if not n.fixed]
    
    def _sub_item_elements(self, bx: int, by: int, sx: int, sy: int, text: str, color: str) -> str:
        """Generate sub-item elements for mindmap: line + pill background + text."""
        
        # NO TRUNCATION - use full text
        sub_text = clean_label_text(text)
        text_width = min(len(sub_text) * 5.2 + 16, 350)  # Wider pills
        
        # Determine anchor and rect position based on sub-item position relative to branch
        if sx > bx + 20:  # Right of branch
            rect_x = sx
            anchor = "start"
        elif sx < bx - 20:  # Left of branch
            rect_x = sx - text_width
            anchor = "end"
        else:  # Roughly centered (top/bottom)
            rect_x = sx - text_width // 2
            anchor = "middle"
        
        result = ""
        # Connection line
        result += f'  <line x1="{bx}" y1="{by}" x2="{sx}" y2="{sy}" stroke="{color}" stroke-width="2" opacity="0.5"/>\n'
        # Pill background
        result += f'  <rect x="{rect_x}" y="{sy - 10}" width="{text_width}" height="20" rx="10" fill="{color}" opacity="0.25"/>\n'
        # Text
        result += f'  <text x="{sx}" y="{sy + 4}" font-family="{self.config.font_family}" font-size="9" fill="#e0e0e0" text-anchor="{anchor}">{html.escape(sub_text)}</text>\n'
        
        return result
    
    def _arrow(self, x1: int, y1: int, x2: int, y2: int, color: str) -> str:
        """Generate an arrow line."""
        # Calculate arrow head
        import math
        angle = math.atan2(y2 - y1, x2 - x1)
        arrow_len = 10
        arrow_angle = math.pi / 6
        
        ax1 = x2 - arrow_len * math.cos(angle - arrow_angle)
        ay1 = y2 - arrow_len * math.sin(angle - arrow_angle)
        ax2 = x2 - arrow_len * math.cos(angle + arrow_angle)
        ay2 = y2 - arrow_len * math.sin(angle + arrow_angle)
        
        return f'''  <line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="2"/>
  <polygon points="{x2},{y2} {ax1},{ay1} {ax2},{ay2}" fill="{color}"/>
'''

    # =========================================================================
    # PATTERN 1: HUB-SPOKE (Central node with radiating connections)
    # Templates: key_stats, key_takeaways, concept_map, anatomy, overview_map, stakeholder_map
    #
    # Design principles:
    # - Center node is visually dominant (larger, primary color)
    # - Spokes are clearly connected but subordinate
    # - Even spacing for visual balance
    # - Text is readable and properly wrapped
    # =========================================================================
    
    def build_hub_spoke(self, title: str, center_label: str, spokes: List[str], 
                        colors: List[str], sub_items: Dict[str, List[str]] = None,
                        insight: str = None) -> str:
        """Build a hub-spoke diagram with central node, radiating items, and optional sub-labels.
        
        FLEXIBLE: Dynamically scales to handle any number of spokes.
        """
        import math
        
        # FLEXIBLE: Use all spokes provided, scale layout dynamically
        num_spokes = len(spokes)
        _has_subs = sub_items and len(sub_items) > 0
        
        # Dynamic sizing based on spoke count - PPT slide friendly
        # Half-landscape slide: ~640x480, Full landscape: ~1280x720
        layout = get_layout_params("hub_spoke", num_spokes)
        _scale = layout["scale_factor"]
        
        # Canvas grows significantly with more items to prevent overlap
        if num_spokes <= 5:
            width, height = 900, 650
        elif num_spokes <= 8:
            width, height = 1100, 750
        else:
            # 9+ items: use larger canvas for breathing room
            width, height = 1400, 900
        
        cx, cy = width // 2, (height // 2) + 20
        
        # Dynamic spoke sizing - LARGER center and orbit for many items
        if num_spokes <= 5:
            center_radius = 75
            spoke_radius = 55
            orbit_radius = 180
        elif num_spokes <= 8:
            center_radius = 85
            spoke_radius = 50
            orbit_radius = 240  # More space between spokes
        else:
            # 9+ items: much larger orbit to prevent overlap
            center_radius = 95
            spoke_radius = 48
            orbit_radius = 320  # Big orbit for many items
        
        svg = self._svg_header(width, height)
        
        # Title
        svg += self._title_element(width // 2, 38, title)
        
        # Draw connection lines first (behind nodes)
        for i in range(num_spokes):
            angle = (2 * math.pi * i / num_spokes) - math.pi / 2
            sx = cx + orbit_radius * math.cos(angle)
            sy = cy + orbit_radius * math.sin(angle)
            svg += self._line(cx, cy, int(sx), int(sy), colors[i % len(colors)], 3)
        
        # Center node
        svg += self._circle(cx, cy, center_radius, colors[0])
        svg += self._text_in_circle(cx, cy, center_radius, clean_label_text(center_label), font_size=13)
        
        # Spoke nodes - use foreignObject for proper text wrapping in circles
        for i, spoke in enumerate(spokes[:num_spokes]):
            angle = (2 * math.pi * i / num_spokes) - math.pi / 2
            sx = cx + orbit_radius * math.cos(angle)
            sy = cy + orbit_radius * math.sin(angle)
            
            color = colors[(i + 1) % len(colors)]
            svg += self._circle(int(sx), int(sy), spoke_radius, color)
            
            # Clean label and use foreignObject for proper text wrapping
            svg += self._text_in_circle(int(sx), int(sy), spoke_radius, clean_label_text(spoke), font_size=10)
        
        # MAGICAL: Insight synthesis line at bottom (with word wrap)
        if insight:
            svg += self._insight_text(width // 2, height - 45, width, insight, 10)
        
        svg += self._svg_footer()
        return svg

    # =========================================================================
    # PATTERN 1B: MINDMAP (Hierarchical branching from center)
    # Templates: mindmap, concept_breakdown
    #
    # Design principles:
    # - Center topic with radiating branches
    # - Each branch has sub-branches for detail
    # - Clean hierarchical visualization
    # - Better than hub-spoke when sub-points exist
    # =========================================================================
    
    def build_mindmap(self, title: str, center_label: str, branches: List[str], 
                      colors: List[str], sub_items: Dict[str, List[str]] = None,
                      insight: str = None) -> str:
        """Build a TRUE mindmap with horizontal branches and rectangular nodes.
        
        FLEXIBLE: Dynamically scales to handle any number of branches.
        """
        # FLEXIBLE: Use ALL branches, scale layout dynamically
        num_branches = len(branches)
        has_subs = sub_items and len(sub_items) > 0
        layout = get_layout_params("mindmap", num_branches)
        scale = layout["scale_factor"]
        
        # Dynamic canvas sizing - grows with content
        # Wider canvas to accommodate sub-items on left/right edges
        has_subs = sub_items and len(sub_items) > 0
        base_width = 1400 if has_subs else 1200  # Wider when sub-items exist
        base_height = 700
        if num_branches > 8:
            # Grow canvas for many branches
            height = int(base_height + (num_branches - 8) * 60)
        else:
            height = base_height
        width = base_width
        cx, cy = width // 2, height // 2
        
        svg = self._svg_header(width, height)
        
        # Title
        svg += self._title_element(width // 2, 35, title)
        
        # Dynamic center node sizing
        center_w = int(160 * scale)
        center_h = int(55 * scale)
        center_w = max(120, center_w)
        center_h = max(45, center_h)
        svg += self._rounded_rect(cx - center_w // 2, cy - center_h // 2, center_w, center_h, colors[0])
        center_lines = wrap_text(clean_label_text(center_label), 16, max_lines=2)
        center_y_text = cy - (len(center_lines) - 1) * 8 + 5
        svg += self._multiline_text(cx, center_y_text, center_lines, 14, "#ffffff", line_height=16)
        
        # Split branches: left side and right side for horizontal layout
        left_branches = branches[:num_branches // 2 + num_branches % 2]
        right_branches = branches[num_branches // 2 + num_branches % 2:num_branches]
        
        # Dynamic branch sizing based on count
        if num_branches <= 6:
            branch_w, branch_h = 200, 50
            v_spacing = 100
        elif num_branches <= 10:
            branch_w, branch_h = 180, 45
            v_spacing = 80
        else:
            branch_w, branch_h = 160, 40
            v_spacing = 65
        h_offset = 280
        
        def draw_branch_with_subs(bx, by, branch_text, color, is_left, branch_idx):
            """Draw a branch node with curved connector and sub-items."""
            nonlocal svg
            
            # Curved connector from center to branch (bezier curve)
            if is_left:
                # Left side: curve goes left
                ctrl_x = cx - h_offset // 2
                svg += f'  <path d="M {cx - center_w // 2} {cy} Q {ctrl_x} {cy} {bx + branch_w // 2} {by}" stroke="{color}" stroke-width="3" fill="none"/>\n'
            else:
                # Right side: curve goes right
                ctrl_x = cx + h_offset // 2
                svg += f'  <path d="M {cx + center_w // 2} {cy} Q {ctrl_x} {cy} {bx - branch_w // 2} {by}" stroke="{color}" stroke-width="3" fill="none"/>\n'
            
            # Branch node - rounded rectangle
            svg += self._rounded_rect(bx - branch_w // 2, by - branch_h // 2, branch_w, branch_h, color)
            
            # Branch label - clean but don't over-shorten (branches have space for 2 lines)
            clean_branch = clean_label_text(branch_text)
            # Replace "and" with "&" to save space, but don't truncate comma lists
            clean_branch = re.sub(r'\band\b', '&', clean_branch, flags=re.IGNORECASE)
            # Manual word wrap without aggressive shortening
            words = clean_branch.split()
            branch_lines = []
            current_line = ""
            for word in words:
                test = current_line + (" " if current_line else "") + word
                if len(test) <= 28:
                    current_line = test
                else:
                    if current_line:
                        branch_lines.append(current_line)
                    current_line = word
                    if len(branch_lines) >= 2:  # Max 2 lines (was 1, cutting off text)
                        break
            if current_line:
                branch_lines.append(current_line)
            if not branch_lines:
                branch_lines = [clean_branch[:28]]
            text_y = by - (len(branch_lines) - 1) * 8 + 5
            svg += self._multiline_text(bx, text_y, branch_lines, 12, "#ffffff", line_height=16)
            
            # Sub-items as small pills extending outward
            if sub_items:
                branch_subs = self._find_sub_items(branch_text, sub_items)
                if branch_subs:
                    for j, sub in enumerate(branch_subs[:2]):  # Max 2 sub-items
                        sub_y = by - 20 + j * 28
                        clean_sub = clean_label_text(sub)
                        
                        # NO TRUNCATION - use full text
                        sub_text = clean_sub
                        
                        sub_w = min(len(sub_text) * 5.2 + 20, 350)  # Much wider pills
                        
                        # Position pills to stay on canvas - CLAMP to available space
                        if is_left:
                            # Calculate available space on left - use more of the canvas
                            available_space = bx - branch_w // 2 - 20  # Smaller margin
                            if sub_w > available_space:
                                # Clamp width to available space - text will be smaller font
                                sub_w = max(available_space, 120)
                            
                            pill_x = max(15, bx - branch_w // 2 - sub_w - 15)
                            svg += self._line(bx - branch_w // 2, by, pill_x + sub_w, sub_y, color, 2, opacity=0.5)
                            svg += f'  <rect x="{pill_x}" y="{sub_y - 10}" width="{sub_w}" height="20" rx="10" fill="{color}" opacity="0.3"/>\n'
                            svg += self._text_element(pill_x + sub_w // 2, sub_y + 4, sub_text, 9, "#e0e0e0", "middle", max_len=200)
                        else:
                            pill_x = bx + branch_w // 2 + 20
                            svg += self._line(bx + branch_w // 2, by, pill_x, sub_y, color, 2, opacity=0.5)
                            svg += f'  <rect x="{pill_x}" y="{sub_y - 10}" width="{sub_w}" height="20" rx="10" fill="{color}" opacity="0.3"/>\n'
                            svg += self._text_element(pill_x + sub_w // 2, sub_y + 4, sub_text, 9, "#e0e0e0", "middle", max_len=200)
        
        # Draw left branches (top to bottom)
        left_start_y = cy - (len(left_branches) - 1) * v_spacing // 2
        for i, branch in enumerate(left_branches):
            bx = cx - h_offset
            by = left_start_y + i * v_spacing
            color = colors[(i + 1) % len(colors)]
            draw_branch_with_subs(bx, by, branch, color, True, i)
        
        # Draw right branches (top to bottom)
        right_start_y = cy - (len(right_branches) - 1) * v_spacing // 2
        for i, branch in enumerate(right_branches):
            bx = cx + h_offset
            by = right_start_y + i * v_spacing
            color = colors[(len(left_branches) + i + 1) % len(colors)]
            draw_branch_with_subs(bx, by, branch, color, False, len(left_branches) + i)
        
        # Insight at bottom (with word wrap)
        if insight:
            svg += self._insight_text(width // 2, height - 45, width, insight, 11)
        
        svg += self._svg_footer()
        return svg
    
    def _find_sub_items(self, branch_text: str, sub_items: Dict[str, List[str]]) -> List[str]:
        """Find sub-items for a branch using fuzzy matching."""
        if not sub_items:
            return []
        
        # Try exact match first
        if branch_text in sub_items:
            return sub_items[branch_text]
        
        # Try fuzzy match (80% overlap)
        branch_lower = branch_text.lower().strip()
        for key in sub_items:
            key_lower = key.lower().strip()
            if len(branch_lower) >= 10 and len(key_lower) >= 10:
                if branch_lower.startswith(key_lower[:15]) or key_lower.startswith(branch_lower[:15]):
                    min_len = min(len(branch_lower), len(key_lower))
                    if min_len >= 0.7 * max(len(branch_lower), len(key_lower)):
                        return sub_items[key]
        
        return []
    
    # =========================================================================
    # PATTERN 1C: SUMMARY CARDS (Executive Summary - distinct from hub-spoke)
    # Templates: exec_summary
    #
    # Design principles:
    # - Vertical card layout (NOT radial)
    # - Numbered cards with clear hierarchy
    # - Each card is a distinct rectangle with content
    # =========================================================================
    
    def build_summary_cards(self, title: str, items: List[str], colors: List[str],
                            insight: str = None) -> str:
        """Build a card-based summary layout - DISTINCT from hub-spoke.
        
        FLEXIBLE: Handles any number of items with dynamic layout.
        - 1-5 items: single column
        - 6-12 items: two columns
        - 13+ items: two columns with smaller cards
        """
        # FLEXIBLE: Use ALL items, no arbitrary limit
        num_items = len(items)
        _layout = get_layout_params("exec_summary", num_items)
        
        # Dynamic column decision
        use_two_columns = num_items > 5
        
        # Dynamic card sizing based on count
        if num_items <= 5:
            card_height = 65
            card_gap = 15
        elif num_items <= 8:
            card_height = 55
            card_gap = 12
        elif num_items <= 12:
            card_height = 48
            card_gap = 10
        else:
            card_height = 42
            card_gap = 8
        
        # Canvas size - grows with content
        width = 900
        if use_two_columns:
            rows = (num_items + 1) // 2
            total_card_height = rows * card_height + (rows - 1) * card_gap
            height = max(400, total_card_height + 140)
        else:
            total_card_height = num_items * card_height + (num_items - 1) * card_gap
            height = max(400, total_card_height + 140)
        
        svg = self._svg_header(width, height)
        
        # Title
        svg += self._title_element(width // 2, 35, title)
        
        # Cards layout
        if use_two_columns:
            # Two-column layout: side by side for slide-ready output
            card_width = 400
            col_gap = 30
            left_x = (width - 2 * card_width - col_gap) // 2
            right_x = left_x + card_width + col_gap
            start_y = 75
            
            for i, item in enumerate(items[:num_items]):
                col = i % 2  # 0 = left, 1 = right
                row = i // 2
                x = left_x if col == 0 else right_x
                y = start_y + row * (card_height + card_gap)
                color = colors[i % len(colors)]
                
                svg += self._rounded_rect(x, y, card_width, card_height, color)
                
                # Number badge
                badge_x = x + 28
                badge_y = y + card_height // 2
                svg += f'  <circle cx="{badge_x}" cy="{badge_y}" r="16" fill="#ffffff" opacity="0.2"/>\n'
                svg += self._text_element(badge_x, badge_y + 5, str(i + 1), 13, "#ffffff", "middle", bold=True)
                
                # Card text - use foreignObject for word wrap instead of truncation
                text_x = x + 55
                clean_item = clean_label_text(item)
                # Use _text_in_rect for proper word wrap instead of truncation
                svg += self._text_in_rect(text_x, y + 2, card_width - 60, card_height - 4, clean_item, 11, "#ffffff", "left")
        else:
            # Single column layout
            card_width = 600
            start_x = (width - card_width) // 2
            start_y = 80
            
            for i, item in enumerate(items[:num_items]):
                y = start_y + i * (card_height + card_gap)
                color = colors[i % len(colors)]
                
                svg += self._rounded_rect(start_x, y, card_width, card_height, color)
                
                # Number badge on left
                badge_x = start_x + 35
                badge_y = y + card_height // 2
                svg += f'  <circle cx="{badge_x}" cy="{badge_y}" r="20" fill="#ffffff" opacity="0.2"/>\n'
                svg += self._text_element(badge_x, badge_y + 5, str(i + 1), 16, "#ffffff", "middle", bold=True)
                
                # Card text - use foreignObject for word wrap instead of truncation
                text_x = start_x + 80
                clean_item = clean_label_text(item)
                svg += self._text_in_rect(text_x, y + 2, card_width - 90, card_height - 4, clean_item, 13, "#ffffff", "left")
        
        # Insight at bottom (with word wrap)
        if insight:
            svg += self._insight_text(width // 2, height - 45, width, insight, 11)
        
        svg += self._svg_footer()
        return svg

    # =========================================================================
    # PATTERN 2: FLOW-HORIZONTAL (Left-to-right progression)
    # Templates: horizontal_steps, process_flow, timeline, stages_progression
    #
    # Design principles:
    # - Left-to-right reading order (natural for Western audiences)
    # - Clear directional arrows showing flow
    # - Numbered badges for sequence clarity
    # - Adequate spacing between steps
    # =========================================================================
    
    def build_flow_horizontal(self, title: str, steps: List[str], colors: List[str],
                               show_numbers: bool = True, insight: str = None) -> str:
        """Build a horizontal flow diagram with clear step progression.
        
        FLEXIBLE: Dynamically scales to handle any number of steps.
        """
        # FLEXIBLE: Use ALL steps, scale layout dynamically
        num_steps = len(steps)
        _layout = get_layout_params("horizontal_steps", num_steps)
        
        # Dynamic sizing based on step count
        if num_steps <= 4:
            step_width = 150
            gap = 50
        elif num_steps <= 6:
            step_width = 120
            gap = 40
        elif num_steps <= 8:
            step_width = 100
            gap = 30
        else:
            step_width = 85
            gap = 25
        
        total_width = num_steps * step_width + (num_steps - 1) * gap
        width = max(800, total_width + 100)
        height = 320
        
        svg = self._svg_header(width, height)
        
        # Title with breathing room
        svg += self._title_element(width // 2, 40, title)
        
        # Center the flow
        start_x = (width - total_width) // 2
        y = height // 2 + 10
        
        for i, step in enumerate(steps[:num_steps]):
            x = start_x + i * (step_width + gap)
            color = colors[i % len(colors)]
            
            # Step box - rounded rectangle
            svg += self._rounded_rect(x, y - 45, step_width, 90, color)
            
            # Number badge in top-left corner
            if show_numbers:
                svg += self._circle(x + 18, y - 28, 14, self.bg_color)
                svg += self._text_element(x + 18, y - 24, str(i + 1), 12, color, bold=True)
            
            # Step text - generous wrapping for readability
            step_lines = wrap_text(step, 14, max_lines=3)
            text_y = y - (len(step_lines) - 1) * 7
            svg += self._multiline_text(x + step_width // 2, text_y, step_lines, 12, "#ffffff", line_height=15)
            
            # Arrow to next step
            if i < num_steps - 1:
                arrow_start = x + step_width + 5
                arrow_end = x + step_width + gap - 5
                svg += self._arrow(arrow_start, y, arrow_end, y, self.muted_color)
        
        # Insight at bottom
        if insight:
            svg += self._insight_text(width // 2, height - 45, width, insight, 11)
        
        svg += self._svg_footer()
        return svg

    # =========================================================================
    # PATTERN 3: FLOW-VERTICAL (Top-to-bottom - funnel, ranking)
    # Templates: funnel, ranking, recommendation_stack
    # =========================================================================
    
    def build_flow_vertical(self, title: str, items: List[str], colors: List[str],
                            values: List[Any] = None, is_funnel: bool = False,
                            sub_items: Dict[str, List[str]] = None,
                            insight: str = None) -> str:
        """Build a vertical flow diagram (funnel or ranking).
        
        FLEXIBLE: Dynamically scales to handle any number of items.
        """
        # FLEXIBLE: Use ALL items, scale layout dynamically
        num_items = len(items)
        _layout = get_layout_params("funnel" if is_funnel else "ranking", num_items)
        
        # Dynamic sizing based on item count
        if num_items <= 5:
            item_height = 55
            gap = 15
        elif num_items <= 8:
            item_height = 45
            gap = 12
        elif num_items <= 10:
            item_height = 38
            gap = 10
        else:
            item_height = 32
            gap = 8
        
        width = 700
        start_y = 90
        height = max(480, start_y + num_items * (item_height + gap) + 40)
        
        svg = self._svg_header(width, height)
        
        # Title with more breathing room
        svg += self._title_element(width // 2, 45, title)
        
        for i, item in enumerate(items):  # Use ALL items
            y = start_y + i * (item_height + gap)
            color = colors[i % len(colors)]
            
            if is_funnel:
                # Funnel: progressively narrower - visual metaphor for filtering
                width_factor = 1 - (i * 0.10)
                item_width = int(500 * width_factor)
                x = (width - item_width) // 2
                
                # Funnel box with trapezoid effect via gradient opacity
                svg += self._rounded_rect(x, y, item_width, item_height, color)
                
                # Centered text for funnel
                text_x = width // 2
                if values and i < len(values):
                    svg += self._text_element(text_x, y + 22, item, 14, "#ffffff", bold=True, max_len=40)
                    svg += self._text_element(text_x, y + 40, str(values[i]), 12, "#ffffff", max_len=30)
                else:
                    svg += self._text_element(text_x, y + 32, item, 14, "#ffffff", bold=True, max_len=45)
            else:
                # Ranking: horizontal bar chart style - length encodes importance
                # First item gets full width, subsequent items slightly shorter
                max_width = 520
                item_width = max_width - (i * 15)  # Gradual decrease shows ranking
                x = 90  # Left-aligned for easier reading
                
                # Rank number in circle badge
                badge_x = 50
                badge_y = y + item_height // 2
                svg += self._circle(badge_x, badge_y, 18, color)
                svg += self._text_element(badge_x, badge_y + 5, str(i + 1), 14, "#ffffff", bold=True)
                
                # Item bar - left aligned for readability
                svg += self._rounded_rect(x, y, item_width, item_height, color)
                
                # Left-aligned text (easier to scan)
                text_x = x + 20
                
                # P0: Check if we have sub-points for this theme
                theme_subpoints = None
                if sub_items:
                    # Try exact match first, then partial match
                    theme_subpoints = sub_items.get(item)
                    if not theme_subpoints:
                        # Try matching by first few words
                        for key in sub_items:
                            if key.lower().startswith(item.lower()[:20]) or item.lower().startswith(key.lower()[:20]):
                                theme_subpoints = sub_items[key]
                                break
                
                if theme_subpoints:
                    # Show title + sub-points for learning value
                    svg += self._text_element(text_x, y + 20, item, 13, "#ffffff", "start", bold=True, max_len=55)
                    # Show sub-points in smaller, muted text
                    subpoint_text = " • ".join(theme_subpoints[:2])  # Max 2 subpoints
                    svg += self._text_element(text_x, y + 40, subpoint_text, 10, "#e0e0e0", "start", max_len=60)
                elif values and i < len(values):
                    svg += self._text_element(text_x, y + 22, item, 13, "#ffffff", "start", bold=True, max_len=35)
                    svg += self._text_element(text_x, y + 40, str(values[i]), 11, "#ffffff", "start", max_len=30)
                else:
                    svg += self._text_element(text_x, y + 34, item, 14, "#ffffff", "start", bold=True, max_len=45)
        
        # MAGICAL: Insight synthesis line at bottom (with word wrap)
        if insight:
            svg += self._insight_text(width // 2, height - 40, width, insight, 10)
        
        svg += self._svg_footer()
        return svg

    # =========================================================================
    # PATTERN 4: TWO-COLUMN (Side-by-side comparison)
    # Templates: pros_cons, side_by_side, force_field, scope
    # =========================================================================
    
    def build_two_column(self, title: str, left_title: str, right_title: str,
                         left_items: List[str], right_items: List[str],
                         left_color: str, right_color: str,
                         left_icon: str = "✓", right_icon: str = "✗",
                         insight: str = None) -> str:
        """Build a two-column comparison diagram."""
        width = 800
        max_items = max(len(left_items), len(right_items))
        height = max(450, max_items * 50 + 200)  # Increased for insight
        
        svg = self._svg_header(width, height)
        
        # Title
        svg += self._title_element(width // 2, 40, title)
        
        col_width = 340
        left_x = 60
        right_x = width - 60 - col_width
        header_y = 80
        
        # Left column header
        svg += self._rounded_rect(left_x, header_y, col_width, 50, left_color)
        svg += self._text_element(left_x + col_width // 2, header_y + 32, f"{left_icon} {left_title}", 16, "#ffffff", bold=True)
        
        # Right column header
        svg += self._rounded_rect(right_x, header_y, col_width, 50, right_color)
        svg += self._text_element(right_x + col_width // 2, header_y + 32, f"{right_icon} {right_title}", 16, "#ffffff", bold=True)
        
        # Left items
        for i, item in enumerate(left_items[:6]):
            y = header_y + 70 + i * 45
            svg += self._rounded_rect(left_x, y, col_width, 38, self.bg_color, left_color)
            svg += self._text_element(left_x + 20, y + 24, f"{left_icon}", 14, left_color, "start")
            svg += self._text_element(left_x + 45, y + 24, item, 12, self.text_color, "start", max_len=38)
        
        # Right items
        for i, item in enumerate(right_items[:6]):
            y = header_y + 70 + i * 45
            svg += self._rounded_rect(right_x, y, col_width, 38, self.bg_color, right_color)
            svg += self._text_element(right_x + 20, y + 24, f"{right_icon}", 14, right_color, "start")
            svg += self._text_element(right_x + 45, y + 24, item, 12, self.text_color, "start", max_len=38)
        
        # Insight at bottom
        if insight:
            svg += self._insight_text(width // 2, height - 45, width, insight, 11)
        
        svg += self._svg_footer()
        return svg

    # =========================================================================
    # PATTERN 5: GRID/MATRIX (2x2 quadrant or NxN grid)
    # Templates: quadrant, heatmap, mece
    # =========================================================================
    
    def build_quadrant(self, title: str, x_axis: str, y_axis: str,
                       quadrant_labels: List[str], items: List[Tuple[str, float, float]],
                       colors: List[str], insight: str = None) -> str:
        """Build a 2x2 quadrant matrix."""
        width, height = 780, 700  # Wider for better text fit
        
        svg = self._svg_header(width, height)
        
        # Title
        svg += self._title_element(width // 2, 35, title)
        
        # Grid area
        grid_x, grid_y = 110, 80
        grid_size = 500
        
        # Quadrant backgrounds
        quad_colors = [colors[0] + "40", colors[1] + "40", colors[2] + "40", colors[3] + "40"]
        half = grid_size // 2
        
        # Q1 (top-right), Q2 (top-left), Q3 (bottom-left), Q4 (bottom-right)
        svg += self._rounded_rect(grid_x + half, grid_y, half, half, quad_colors[0], rx=0)
        svg += self._rounded_rect(grid_x, grid_y, half, half, quad_colors[1], rx=0)
        svg += self._rounded_rect(grid_x, grid_y + half, half, half, quad_colors[2], rx=0)
        svg += self._rounded_rect(grid_x + half, grid_y + half, half, half, quad_colors[3], rx=0)
        
        # Quadrant labels
        if len(quadrant_labels) >= 4:
            svg += self._text_element(grid_x + half + half // 2, grid_y + 25, quadrant_labels[0], 12, colors[0], bold=True)
            svg += self._text_element(grid_x + half // 2, grid_y + 25, quadrant_labels[1], 12, colors[1], bold=True)
            svg += self._text_element(grid_x + half // 2, grid_y + half + 25, quadrant_labels[2], 12, colors[2], bold=True)
            svg += self._text_element(grid_x + half + half // 2, grid_y + half + 25, quadrant_labels[3], 12, colors[3], bold=True)
        
        # Axes
        svg += self._line(grid_x, grid_y + half, grid_x + grid_size, grid_y + half, self.muted_color)
        svg += self._line(grid_x + half, grid_y, grid_x + half, grid_y + grid_size, self.muted_color)
        
        # Axis labels
        svg += self._text_element(grid_x + grid_size // 2, grid_y + grid_size + 30, x_axis, 12, self.muted_color)
        svg += f'  <text x="{grid_x - 30}" y="{grid_y + grid_size // 2}" font-family="{self.config.font_family}" font-size="12" fill="{self.muted_color}" text-anchor="middle" transform="rotate(-90, {grid_x - 30}, {grid_y + grid_size // 2})">{escape_svg_text(y_axis)}</text>\n'
        
        # Plot items - use larger circles with foreignObject for proper text wrapping
        for i, (label, x_val, y_val) in enumerate(items[:8]):
            px = grid_x + int(x_val * grid_size)
            py = grid_y + grid_size - int(y_val * grid_size)
            color = colors[i % len(colors)]
            
            # Larger circle with foreignObject text
            svg += self._circle(px, py, 48, color)
            svg += self._text_in_circle(px, py, 48, label, font_size=9)
        
        # Insight at bottom
        if insight:
            svg += self._insight_text(width // 2, height - 45, width, insight, 11)
        
        svg += self._svg_footer()
        return svg

    # =========================================================================
    # PATTERN 6: CYCLE (Circular process flow)
    # Templates: cycle_loop, causal_loop
    # =========================================================================
    
    def build_cycle(self, title: str, steps: List[str], colors: List[str],
                     insight: str = None) -> str:
        """Build a circular cycle diagram.
        
        FLEXIBLE: Dynamically scales to handle any number of steps.
        """
        import math
        
        # FLEXIBLE: Use ALL steps, scale layout dynamically
        num_steps = len(steps)
        layout = get_layout_params("cycle_loop", num_steps)
        _scale = layout["scale_factor"]
        
        # Dynamic sizing based on step count
        if num_steps <= 4:
            node_radius = 55
            radius = 160
        elif num_steps <= 6:
            node_radius = 50
            radius = 180
        elif num_steps <= 8:
            node_radius = 42
            radius = 200
        else:
            node_radius = 35
            radius = 220
        
        width = max(600, 2 * (radius + node_radius + 50))
        height = width
        cx, cy = width // 2, height // 2 + 20
        
        svg = self._svg_header(width, height)
        
        # Title
        svg += self._title_element(width // 2, 35, title)
        
        # Draw curved arrows first
        for i in range(num_steps):
            angle1 = (2 * math.pi * i / num_steps) - math.pi / 2
            angle2 = (2 * math.pi * ((i + 1) % num_steps) / num_steps) - math.pi / 2
            
            # Arrow from edge of one node to edge of next
            x1 = cx + (radius - node_radius - 10) * math.cos(angle1 + 0.3)
            y1 = cy + (radius - node_radius - 10) * math.sin(angle1 + 0.3)
            x2 = cx + (radius - node_radius - 10) * math.cos(angle2 - 0.3)
            y2 = cy + (radius - node_radius - 10) * math.sin(angle2 - 0.3)
            
            svg += self._arrow(int(x1), int(y1), int(x2), int(y2), self.muted_color)
        
        # Draw nodes - use ALL steps
        for i, step in enumerate(steps):
            angle = (2 * math.pi * i / num_steps) - math.pi / 2
            nx = cx + radius * math.cos(angle)
            ny = cy + radius * math.sin(angle)
            
            color = colors[i % len(colors)]
            svg += self._circle(int(nx), int(ny), node_radius, color)
            
            step_lines = wrap_text(step, 10)
            svg += self._multiline_text(int(nx), int(ny) - 5, step_lines, 11, "#ffffff", line_height=14)
        
        # Insight at bottom
        if insight:
            svg += self._insight_text(width // 2, height - 45, width, insight, 11)
        
        svg += self._svg_footer()
        return svg

    # =========================================================================
    # PATTERN 7: HIERARCHY (Tree structure)
    # Templates: system_architecture, decision_tree, argument, mece
    # =========================================================================
    
    def build_hierarchy(self, title: str, root: str, children: List[Dict[str, Any]], 
                        colors: List[str], insight: str = None) -> str:
        """Build a hierarchical tree diagram.
        
        FLEXIBLE: Dynamically scales to handle any number of children.
        """
        # FLEXIBLE: Use ALL children, scale layout dynamically
        num_children = len(children)
        layout = get_layout_params("mece", num_children)
        _scale = layout["scale_factor"]
        
        # Dynamic canvas - grows with more children
        width = max(800, 100 + num_children * 110)
        width = min(width, 1400)  # Cap at reasonable size
        height = 500
        
        svg = self._svg_header(width, height)
        
        # Title
        svg += self._title_element(width // 2, 35, title)
        
        # Root node
        root_x, root_y = width // 2, 90
        root_width, root_height = 180, 55
        svg += self._rounded_rect(root_x - root_width // 2, root_y, root_width, root_height, colors[0])
        # Use foreignObject for proper text wrapping within boundaries
        svg += self._text_in_rect(root_x - root_width // 2, root_y, root_width, root_height, root, font_size=13)
        
        # Level 1 children - FLEXIBLE: use ALL children, scale dynamically
        if num_children > 0:
            # Dynamic sizing based on number of children
            if num_children <= 4:
                child_width = 160
                child_gap = 30
            elif num_children <= 6:
                child_width = 130
                child_gap = 20
            elif num_children <= 8:
                child_width = 110
                child_gap = 15
            elif num_children <= 10:
                child_width = 95
                child_gap = 12
            else:
                child_width = 80
                child_gap = 10
            child_height = 65
            total_width = num_children * child_width + (num_children - 1) * child_gap
            start_x = (width - total_width) // 2
            child_y = 200
            
            for i, child in enumerate(children):  # Use ALL children
                cx = start_x + i * (child_width + child_gap) + child_width // 2
                
                # Line from root to child
                svg += self._line(root_x, root_y + root_height, cx, child_y, self.muted_color)
                
                # Child node
                child_label = child if isinstance(child, str) else child.get("name", f"Item {i+1}")
                color = colors[(i + 1) % len(colors)]
                svg += self._rounded_rect(cx - child_width // 2, child_y, child_width, child_height, color)
                # Use foreignObject for proper text wrapping within boundaries
                svg += self._text_in_rect(cx - child_width // 2, child_y, child_width, child_height, child_label, font_size=11)
                
                # Grandchildren if available
                if isinstance(child, dict) and "children" in child:
                    grandchildren = child["children"][:2]
                    gc_y = child_y + 100
                    gc_width = 100
                    gc_start = cx - (len(grandchildren) * gc_width + (len(grandchildren) - 1) * 20) // 2
                    
                    for j, gc in enumerate(grandchildren):
                        gc_x = gc_start + j * (gc_width + 20) + gc_width // 2
                        svg += self._line(cx, child_y + 50, gc_x, gc_y, self.muted_color)
                        svg += self._rounded_rect(gc_x - gc_width // 2, gc_y, gc_width, 40, self.bg_color, color)
                        # Wrap grandchild labels
                        gc_lines = wrap_text(gc, 14, max_lines=2)
                        gc_text_y = gc_y + 16 if len(gc_lines) > 1 else gc_y + 24
                        svg += self._multiline_text(gc_x, gc_text_y, gc_lines, 9, self.text_color, line_height=12)
        
        # Insight at bottom (with word wrap)
        if insight:
            svg += self._insight_text(width // 2, height - 45, width, insight, 11)
        
        svg += self._svg_footer()
        return svg

    # =========================================================================
    # PATTERN 8: CHART (Bar/trend visualization)
    # Templates: trend_chart, distribution
    # =========================================================================
    
    def build_bar_chart(self, title: str, labels: List[str], values: List[float], 
                        colors: List[str], y_label: str = "", insight: str = None) -> str:
        """Build a bar chart."""
        width = 700
        height = 450  # Increased for insight
        
        svg = self._svg_header(width, height)
        
        # Title
        svg += self._title_element(width // 2, 35, title)
        
        # Chart area
        chart_x, chart_y = 80, 70
        chart_width, chart_height = 550, 250
        
        # Y-axis
        svg += self._line(chart_x, chart_y, chart_x, chart_y + chart_height, self.muted_color)
        # X-axis
        svg += self._line(chart_x, chart_y + chart_height, chart_x + chart_width, chart_y + chart_height, self.muted_color)
        
        # Bars
        num_bars = min(len(labels), len(values), 8)
        if num_bars > 0 and max(values[:num_bars]) > 0:
            bar_width = min(60, (chart_width - 40) // num_bars - 10)
            max_val = max(values[:num_bars])
            
            for i in range(num_bars):
                bar_height = int((values[i] / max_val) * (chart_height - 20))
                bx = chart_x + 30 + i * (bar_width + 15)
                by = chart_y + chart_height - bar_height
                
                color = colors[i % len(colors)]
                svg += self._rounded_rect(bx, by, bar_width, bar_height, color, rx=4)
                
                # Value label
                svg += self._text_element(bx + bar_width // 2, by - 8, str(int(values[i])), 11, self.text_color)
                
                # X-axis label
                svg += self._text_element(bx + bar_width // 2, chart_y + chart_height + 20, labels[i][:10], 10, self.muted_color)
        
        # Insight at bottom
        if insight:
            svg += self._insight_text(width // 2, height - 45, width, insight, 11)
        
        svg += self._svg_footer()
        return svg
    
    def build_pie_chart(self, title: str, labels: List[str], values: List[float],
                        colors: List[str], insight: str = None) -> str:
        """Build a pie chart."""
        import math
        
        width, height = 600, 550  # Increased for insight
        cx, cy = 250, 280
        radius = 150
        
        svg = self._svg_header(width, height)
        
        # Title
        svg += self._title_element(width // 2, 35, title)
        
        total = sum(values) if values else 1
        start_angle = -math.pi / 2  # Start from top
        
        num_slices = min(len(labels), len(values), 6)
        
        for i in range(num_slices):
            pct = values[i] / total if total > 0 else 0
            end_angle = start_angle + 2 * math.pi * pct
            
            # SVG arc
            large_arc = 1 if pct > 0.5 else 0
            x1 = cx + radius * math.cos(start_angle)
            y1 = cy + radius * math.sin(start_angle)
            x2 = cx + radius * math.cos(end_angle)
            y2 = cy + radius * math.sin(end_angle)
            
            color = colors[i % len(colors)]
            svg += f'  <path d="M {cx} {cy} L {x1} {y1} A {radius} {radius} 0 {large_arc} 1 {x2} {y2} Z" fill="{color}" filter="url(#shadow)"/>\n'
            
            start_angle = end_angle
        
        # Legend
        legend_x = 450
        legend_y = 120
        for i in range(num_slices):
            ly = legend_y + i * 35
            color = colors[i % len(colors)]
            pct = (values[i] / total * 100) if total > 0 else 0
            
            svg += self._rounded_rect(legend_x, ly, 20, 20, color, rx=4)
            svg += self._text_element(legend_x + 30, ly + 15, f"{labels[i][:15]} ({pct:.0f}%)", 12, self.text_color, "start")
        
        # Insight at bottom
        if insight:
            svg += self._insight_text(width // 2, height - 45, width, insight, 11)
        
        svg += self._svg_footer()
        return svg


# =========================================================================
# TEMPLATE BUILDER - Maps template IDs to SVG patterns
# =========================================================================

class SVGVisualBuilder:
    """High-level builder that routes templates to appropriate SVG patterns."""
    
    def __init__(self, dark_mode: bool = True):
        self.builder = SVGTemplateBuilder(dark_mode)
        self.dark_mode = dark_mode
    
    def build(self, template_id: str, structure: Dict[str, Any], 
              colors: List[str], title: str = "") -> str:
        """Build SVG for a given template using extracted structure data."""
        
        # Get data from structure
        themes = structure.get("themes", [])
        entities = structure.get("entities", [])
        pros = structure.get("pros", [])
        cons = structure.get("cons", [])
        tensions = structure.get("tensions", [])  # NEW: for force_field template
        gaps = structure.get("gaps", [])  # NEW: for force_field template
        sequence = structure.get("sequence", [])
        numbers = structure.get("numbers", [])
        recommendations = structure.get("recommendations", [])
        components = structure.get("components", [])
        rankings = structure.get("rankings", [])
        _comparisons = structure.get("comparisons", [])
        dates_events = structure.get("dates_events", [])
        subpoints = structure.get("subpoints", {})  # P0: Theme -> [subpoints] for learning value
        
        # CRITICAL: Ensure we have SOMETHING to display - prevents blank visuals
        # If all main data sources are empty, create fallback from title
        if not themes and not entities and not sequence and not pros:
            print(f"[SVG Builder] ⚠️ ALL data sources empty for {template_id}, using title fallback")
            if title and len(title) > 5:
                # Split title into fake themes
                themes = [title]
            else:
                themes = ["No data extracted", "Try asking a more detailed question"]
        
        # Route to appropriate pattern based on template_id
        
        # === EXECUTIVE SUMMARY (card-based layout, distinct from hub-spoke) ===
        if template_id == "exec_summary":
            # FLEXIBLE: Pass ALL themes, layout handles any count
            items = themes if themes else ["Key Point 1", "Key Point 2", "Key Point 3"]
            insight = structure.get("insight")
            return self.builder.build_summary_cards(title, items, colors, insight=insight)
        
        # === MINDMAP TEMPLATE (horizontal branches, rectangles) ===
        if template_id == "mindmap":
            # FLEXIBLE: Pass ALL themes, layout handles any count
            items = themes if themes else entities if entities else ["Topic 1", "Topic 2", "Topic 3"]
            center = "Themes"
            if title:
                import re
                match = re.search(r'\d+\s+(Key\s+)?(\w+)', title)
                if match:
                    center = match.group(2).title()
            insight = structure.get("insight")
            return self.builder.build_mindmap(title, center, items, colors,
                                               sub_items=subpoints, insight=insight)
        
        # === HUB-SPOKE TEMPLATES (simpler, no sub-branches) ===
        if template_id in ["key_stats", "key_takeaways", "concept_map", "anatomy", 
                          "overview_map", "overview", "stakeholder_map"]:
            # FLEXIBLE: Pass ALL themes, layout handles any count
            items = themes if themes else entities if entities else ["Point 1", "Point 2", "Point 3"]
            
            # Derive center label from title
            center = None
            if title:
                import re
                match = re.search(r'\d+\s+(Key\s+)?(\w+)', title)
                if match:
                    center = match.group(2).title()
                elif len(title) < 20:
                    center = title
            
            if not center:
                center_labels = {
                    "key_stats": "Key Stats",
                    "key_takeaways": "Themes",
                    "concept_map": "Concepts",
                    "anatomy": "Components",
                    "overview_map": "Overview",
                    "overview": "Overview",
                    "stakeholder_map": "Stakeholders"
                }
                center = center_labels.get(template_id, "Overview")
            
            # Hub-spoke can show sub-labels for depth when available
            insight = structure.get("insight")
            return self.builder.build_hub_spoke(title, center, items, colors, 
                                                 sub_items=subpoints, insight=insight)
        
        # === HORIZONTAL FLOW TEMPLATES ===
        elif template_id in ["horizontal_steps", "process_flow", "timeline", "stages_progression"]:
            # FLEXIBLE: Pass ALL items, layout handles dynamic scaling
            steps = sequence if sequence else themes if themes else ["Step 1", "Step 2", "Step 3"]
            insight = structure.get("insight")
            return self.builder.build_flow_horizontal(title, steps, colors, insight=insight)
        
        # === VERTICAL FLOW TEMPLATES ===
        elif template_id in ["funnel"]:
            # FLEXIBLE: Pass ALL items
            items = sequence if sequence else themes
            vals = numbers if numbers else None
            insight = structure.get("insight")
            return self.builder.build_flow_vertical(title, items, colors, vals, is_funnel=True, insight=insight)
        
        elif template_id in ["ranking", "recommendation_stack"]:
            # FLEXIBLE: Pass ALL items
            items = rankings if rankings else recommendations if recommendations else themes
            insight = structure.get("insight")  # Synthesis line for magical bottom text
            return self.builder.build_flow_vertical(title, items, colors, is_funnel=False, 
                                                     sub_items=subpoints, insight=insight)
        
        # === TWO-COLUMN TEMPLATES ===
        elif template_id in ["pros_cons"]:
            # FLEXIBLE: Pass ALL items
            p = pros if pros else themes
            c = cons if cons else []
            insight = structure.get("insight")
            return self.builder.build_two_column(title, "Advantages", "Challenges", 
                                                  p, c, colors[1], colors[3], "✓", "✗", insight=insight)
        
        elif template_id in ["side_by_side"]:
            # FLEXIBLE: Pass ALL items
            left = pros if pros else themes
            right = cons if cons else []
            insight = structure.get("insight")
            return self.builder.build_two_column(title, "Option A", "Option B",
                                                  left, right, colors[0], colors[1], "→", "→", insight=insight)
        
        elif template_id in ["force_field"]:
            # Force field shows tensions/conflicts - driving vs restraining forces
            # tensions and gaps already extracted above
            # Use tensions if available, otherwise fall back to pros/cons
            if tensions:
                left = [t[0] if isinstance(t, list) else t for t in tensions]
                right = [t[2] if isinstance(t, list) and len(t) > 2 else "Challenge" for t in tensions]
            else:
                # FLEXIBLE: Pass ALL items
                left = pros if pros else themes
                right = cons if cons else gaps if gaps else []
            insight = structure.get("insight")
            return self.builder.build_two_column(title, "Driving Forces", "Restraining Forces",
                                                  left, right, colors[1], colors[3], "→", "←", insight=insight)
        
        elif template_id in ["scope"]:
            # FLEXIBLE: Pass ALL items
            in_scope = themes if themes else ["Item 1"]
            out_scope = cons if cons else ["Excluded"]
            insight = structure.get("insight")
            return self.builder.build_two_column(title, "In Scope", "Out of Scope",
                                                  in_scope, out_scope, colors[1], colors[3], "✓", "✗", insight=insight)
        
        # === QUADRANT/GRID TEMPLATES ===
        elif template_id in ["quadrant", "heatmap"]:
            quad_labels = ["Do First", "Schedule", "Delegate", "Eliminate"]
            theme_items = themes[:8] if themes else ["Item 1", "Item 2", "Item 3", "Item 4"]
            
            # Check if structure already has positioned quadrant_items
            quadrant_items = structure.get("quadrant_items")
            if quadrant_items:
                items = [(qi["name"], qi["x"], qi["y"]) for qi in quadrant_items[:8]]
            else:
                # Distribute items across quadrants so each quadrant gets representation
                # Q1 Do First (top-right): high x, high y
                # Q2 Schedule (top-left): low x, high y
                # Q3 Delegate (bottom-left): low x, low y
                # Q4 Eliminate (bottom-right): high x, low y
                quadrant_positions = [
                    (0.75, 0.80),  # Q1 - Do First
                    (0.25, 0.75),  # Q2 - Schedule
                    (0.20, 0.25),  # Q3 - Delegate
                    (0.70, 0.20),  # Q4 - Eliminate
                    (0.85, 0.65),  # Q1 overflow
                    (0.35, 0.85),  # Q2 overflow
                    (0.15, 0.35),  # Q3 overflow
                    (0.80, 0.35),  # Q4 overflow
                ]
                items = [
                    (t, quadrant_positions[i][0], quadrant_positions[i][1])
                    for i, t in enumerate(theme_items)
                ]
            
            insight = structure.get("insight")
            return self.builder.build_quadrant(title, "Effort →", "Impact →", quad_labels, items, colors, insight=insight)
        
        elif template_id in ["mece"]:
            root = title or "Total"
            # FLEXIBLE: Pass ALL themes
            theme_items = themes if themes else ["Category 1", "Category 2", "Category 3"]
            children = [{"name": t, "children": []} for t in theme_items]
            insight = structure.get("insight")
            return self.builder.build_hierarchy(title, root, children, colors, insight=insight)
        
        # === CYCLE TEMPLATES ===
        elif template_id in ["cycle_loop", "causal_loop"]:
            # FLEXIBLE: Pass ALL items
            steps = sequence if sequence else themes if themes else ["Plan", "Do", "Check", "Act"]
            insight = structure.get("insight")
            return self.builder.build_cycle(title, steps, colors, insight=insight)
        
        # === HIERARCHY TEMPLATES ===
        elif template_id in ["system_architecture", "decision_tree", "argument"]:
            root = themes[0] if themes else title or "Root"
            # FLEXIBLE: Pass ALL remaining themes as children
            children = themes[1:] if len(themes) > 1 else components if components else ["Child 1", "Child 2"]
            insight = structure.get("insight")
            return self.builder.build_hierarchy(title, root, children, colors, insight=insight)
        
        # === CHART TEMPLATES ===
        elif template_id in ["trend_chart"]:
            labels = [d.split(":")[0][:10] if ":" in str(d) else str(d)[:10] for d in (dates_events or themes)[:6]]
            vals = [float(re.sub(r'[^\d.]', '', str(n)) or (i + 1) * 10) for i, n in enumerate(numbers[:6])] if numbers else list(range(10, 70, 10))
            insight = structure.get("insight")
            return self.builder.build_bar_chart(title, labels, vals, colors, insight=insight)
        
        elif template_id in ["distribution"]:
            labels = themes[:6] if themes else ["Category A", "Category B", "Category C"]
            vals = [float(re.sub(r'[^\d.]', '', str(n)) or 25) for n in numbers[:6]] if numbers else [35, 28, 20, 17]
            insight = structure.get("insight")
            return self.builder.build_pie_chart(title, labels, vals, colors, insight=insight)
        
        # === DEFAULT: Hub-spoke ===
        else:
            items = themes[:6] if themes else entities[:6] if entities else ["Point 1", "Point 2", "Point 3"]
            center = title or "Overview"
            return self.builder.build_hub_spoke(title, center, items, colors)


# Singleton instance
svg_builder = SVGVisualBuilder(dark_mode=True)


def build_svg_visual(template_id: str, structure: Dict[str, Any], 
                     colors: List[str], title: str = "", dark_mode: bool = True) -> str:
    """Build an SVG visual from template and extracted structure.
    
    This is the main entry point for visual generation.
    Replaces Mermaid code generation entirely.
    """
    builder = SVGVisualBuilder(dark_mode=dark_mode)
    return builder.build(template_id, structure, colors, title)
