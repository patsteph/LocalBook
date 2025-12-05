"""Export API endpoints for notebook export"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Optional, List, Any
from storage.notebook_store import notebook_store
from storage.source_store import source_store
from storage.highlights_store import highlights_store
from datetime import datetime

router = APIRouter()


class ChatHistoryItem(BaseModel):
    """Chat history item for export"""
    question: str
    answer: str
    citations: Optional[List[Any]] = None
    timestamp: Optional[str] = None


class ExportRequest(BaseModel):
    """Export request - matches frontend ExportOptions"""
    notebook_id: str
    format: str  # 'markdown', 'html', 'pdf'
    include_sources_content: Optional[bool] = False
    chat_history: Optional[List[ChatHistoryItem]] = None


class ExportFormat(BaseModel):
    """Export format definition"""
    id: str
    name: str
    extension: str
    description: str


@router.get("/formats")
async def get_export_formats():
    """Get available export formats"""
    formats = [
        {
            "id": "markdown",
            "name": "Markdown",
            "extension": "md",
            "description": "Plain text with formatting, great for notes and documentation"
        },
        {
            "id": "html",
            "name": "HTML",
            "extension": "html",
            "description": "Web page format, viewable in any browser"
        },
        {
            "id": "pdf",
            "name": "PDF",
            "extension": "pdf",
            "description": "Portable document format, best for sharing and printing"
        }
    ]
    return {"formats": formats}


@router.post("/notebook")
async def export_notebook(request: ExportRequest):
    """Export a notebook to the specified format"""
    notebook = await notebook_store.get(request.notebook_id)
    if not notebook:
        raise HTTPException(status_code=404, detail="Notebook not found")
    
    sources = await source_store.list(request.notebook_id)
    
    # Get highlights for each source
    all_highlights = []
    for source in sources:
        highlights = await highlights_store.list(request.notebook_id, source["id"])
        for h in highlights:
            h["source_filename"] = source.get("filename", "Unknown")
        all_highlights.extend(highlights)
    
    if request.format == "markdown":
        content = _generate_markdown(notebook, sources, all_highlights, request.chat_history, request.include_sources_content)
        return Response(
            content=content,
            media_type="text/markdown",
            headers={"Content-Disposition": f"attachment; filename={notebook['title']}.md"}
        )
    
    elif request.format == "html":
        content = _generate_html(notebook, sources, all_highlights, request.chat_history, request.include_sources_content)
        return Response(
            content=content,
            media_type="text/html",
            headers={"Content-Disposition": f"attachment; filename={notebook['title']}.html"}
        )
    
    elif request.format == "pdf":
        # For PDF, we return HTML and let the frontend handle PDF generation
        # (using jsPDF as shown in the frontend code)
        content = _generate_html(notebook, sources, all_highlights, request.chat_history, request.include_sources_content)
        return Response(
            content=content,
            media_type="text/html",
            headers={"Content-Disposition": f"attachment; filename={notebook['title']}.html"}
        )
    
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported format: {request.format}")


def _generate_markdown(notebook: dict, sources: list, highlights: list, chat_history: list, include_content: bool) -> str:
    """Generate markdown export"""
    lines = []
    
    # Title
    lines.append(f"# {notebook['title']}")
    lines.append("")
    
    if notebook.get('description'):
        lines.append(f"_{notebook['description']}_")
        lines.append("")
    
    lines.append(f"Exported: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    
    # Sources section
    lines.append("## Sources")
    lines.append("")
    
    if sources:
        for i, source in enumerate(sources, 1):
            lines.append(f"### {i}. {source.get('filename', 'Unknown')}")
            lines.append(f"- **Format:** {source.get('format', 'unknown').upper()}")
            lines.append(f"- **Chunks:** {source.get('chunks', 0)}")
            lines.append(f"- **Characters:** {source.get('characters', 0):,}")
            lines.append(f"- **Status:** {source.get('status', 'unknown')}")
            
            if source.get('url'):
                lines.append(f"- **URL:** {source['url']}")
            
            lines.append("")
            
            if include_content and source.get('content'):
                lines.append("#### Content")
                lines.append("```")
                lines.append(source['content'][:5000])  # Limit content length
                if len(source.get('content', '')) > 5000:
                    lines.append("... (truncated)")
                lines.append("```")
                lines.append("")
    else:
        lines.append("_No sources in this notebook_")
        lines.append("")
    
    # Highlights section
    if highlights:
        lines.append("## Highlights & Annotations")
        lines.append("")
        
        for h in highlights:
            lines.append(f"### From: {h.get('source_filename', 'Unknown')}")
            lines.append(f"> {h.get('highlighted_text', '')}")
            if h.get('annotation'):
                lines.append(f"**Note:** {h['annotation']}")
            lines.append("")
    
    # Chat history section
    if chat_history:
        lines.append("## Q&A History")
        lines.append("")
        
        for i, exchange in enumerate(chat_history, 1):
            lines.append(f"### Q{i}: {exchange.question}")
            if exchange.timestamp:
                lines.append(f"_Asked: {exchange.timestamp}_")
            lines.append("")
            lines.append(f"**Answer:** {exchange.answer}")
            lines.append("")
            
            if exchange.citations:
                lines.append("**Citations:**")
                for citation in exchange.citations:
                    lines.append(f"- [{citation.get('number', '?')}] {citation.get('filename', 'Unknown')}: {citation.get('snippet', '')[:100]}...")
                lines.append("")
    
    # Footer
    lines.append("---")
    lines.append("_Generated by LocalBook_")
    
    return "\n".join(lines)


def _generate_html(notebook: dict, sources: list, highlights: list, chat_history: list, include_content: bool) -> str:
    """Generate HTML export"""
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{notebook['title']} - LocalBook Export</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            max-width: 800px;
            margin: 0 auto;
            padding: 2rem;
            line-height: 1.6;
            color: #333;
        }}
        h1 {{ color: #1a1a1a; border-bottom: 2px solid #3b82f6; padding-bottom: 0.5rem; }}
        h2 {{ color: #374151; margin-top: 2rem; }}
        h3 {{ color: #4b5563; }}
        .meta {{ color: #6b7280; font-size: 0.9rem; }}
        .source {{ background: #f9fafb; padding: 1rem; border-radius: 8px; margin: 1rem 0; }}
        .highlight {{ background: #fef3c7; padding: 1rem; border-left: 4px solid #f59e0b; margin: 1rem 0; }}
        .qa {{ background: #eff6ff; padding: 1rem; border-radius: 8px; margin: 1rem 0; }}
        .question {{ font-weight: bold; color: #1e40af; }}
        .answer {{ margin-top: 0.5rem; }}
        .citation {{ font-size: 0.85rem; color: #6b7280; margin-top: 0.5rem; }}
        blockquote {{ border-left: 4px solid #d1d5db; padding-left: 1rem; margin: 1rem 0; color: #4b5563; }}
        code {{ background: #f3f4f6; padding: 0.2rem 0.4rem; border-radius: 4px; }}
        pre {{ background: #1f2937; color: #f9fafb; padding: 1rem; border-radius: 8px; overflow-x: auto; }}
        .footer {{ margin-top: 3rem; padding-top: 1rem; border-top: 1px solid #e5e7eb; color: #9ca3af; font-size: 0.85rem; }}
    </style>
</head>
<body>
    <h1>{notebook['title']}</h1>
"""
    
    if notebook.get('description'):
        html += f"    <p class='meta'><em>{notebook['description']}</em></p>\n"
    
    html += f"    <p class='meta'>Exported: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>\n"
    
    # Sources
    html += "    <h2>📚 Sources</h2>\n"
    
    if sources:
        for i, source in enumerate(sources, 1):
            html += f"""    <div class='source'>
        <h3>{i}. {source.get('filename', 'Unknown')}</h3>
        <p><strong>Format:</strong> {source.get('format', 'unknown').upper()} | 
           <strong>Chunks:</strong> {source.get('chunks', 0)} | 
           <strong>Characters:</strong> {source.get('characters', 0):,}</p>
"""
            if source.get('url'):
                html += f"        <p><strong>URL:</strong> <a href='{source['url']}'>{source['url']}</a></p>\n"
            html += "    </div>\n"
    else:
        html += "    <p><em>No sources in this notebook</em></p>\n"
    
    # Highlights
    if highlights:
        html += "    <h2>🖍️ Highlights & Annotations</h2>\n"
        for h in highlights:
            html += f"""    <div class='highlight'>
        <p><strong>From:</strong> {h.get('source_filename', 'Unknown')}</p>
        <blockquote>{h.get('highlighted_text', '')}</blockquote>
"""
            if h.get('annotation'):
                html += f"        <p><strong>Note:</strong> {h['annotation']}</p>\n"
            html += "    </div>\n"
    
    # Chat history
    if chat_history:
        html += "    <h2>💬 Q&A History</h2>\n"
        for i, exchange in enumerate(chat_history, 1):
            html += f"""    <div class='qa'>
        <p class='question'>Q{i}: {exchange.question}</p>
"""
            if exchange.timestamp:
                html += f"        <p class='meta'>Asked: {exchange.timestamp}</p>\n"
            html += f"        <div class='answer'>{exchange.answer}</div>\n"
            
            if exchange.citations:
                html += "        <div class='citation'><strong>Citations:</strong><ul>\n"
                for citation in exchange.citations:
                    html += f"            <li>[{citation.get('number', '?')}] {citation.get('filename', 'Unknown')}</li>\n"
                html += "        </ul></div>\n"
            html += "    </div>\n"
    
    html += """    <div class='footer'>
        <p>Generated by LocalBook</p>
    </div>
</body>
</html>"""
    
    return html
