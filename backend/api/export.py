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
        },
        {
            "id": "pptx",
            "name": "PowerPoint",
            "extension": "pptx",
            "description": "Presentation slides, great for meetings and sharing insights"
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
    
    elif request.format == "pptx":
        content = _generate_pptx(notebook, sources, all_highlights, request.chat_history)
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            headers={"Content-Disposition": f"attachment; filename={notebook['title']}.pptx"}
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


def _generate_pptx(notebook: dict, sources: list, highlights: list, chat_history: list) -> bytes:
    """Generate PowerPoint presentation export"""
    from pptx import Presentation
    from pptx.util import Inches, Pt
    from pptx.dml.color import RgbColor
    from pptx.enum.text import PP_ALIGN
    import io
    
    prs = Presentation()
    prs.slide_width = Inches(13.333)  # 16:9 aspect ratio
    prs.slide_height = Inches(7.5)
    
    # Title slide
    title_slide_layout = prs.slide_layouts[6]  # Blank layout
    slide = prs.slides.add_slide(title_slide_layout)
    
    # Add title
    title_box = slide.shapes.add_textbox(Inches(0.5), Inches(2.5), Inches(12.333), Inches(1.5))
    title_frame = title_box.text_frame
    title_para = title_frame.paragraphs[0]
    title_para.text = notebook['title']
    title_para.font.size = Pt(44)
    title_para.font.bold = True
    title_para.alignment = PP_ALIGN.CENTER
    
    # Add subtitle with description or date
    subtitle_box = slide.shapes.add_textbox(Inches(0.5), Inches(4.2), Inches(12.333), Inches(0.8))
    subtitle_frame = subtitle_box.text_frame
    subtitle_para = subtitle_frame.paragraphs[0]
    if notebook.get('description'):
        subtitle_para.text = notebook['description']
    else:
        subtitle_para.text = f"Exported: {datetime.now().strftime('%Y-%m-%d')}"
    subtitle_para.font.size = Pt(24)
    subtitle_para.font.color.rgb = RgbColor(100, 100, 100)
    subtitle_para.alignment = PP_ALIGN.CENTER
    
    # Add "Generated by LocalBook" footer
    footer_box = slide.shapes.add_textbox(Inches(0.5), Inches(6.8), Inches(12.333), Inches(0.4))
    footer_frame = footer_box.text_frame
    footer_para = footer_frame.paragraphs[0]
    footer_para.text = "Generated by LocalBook"
    footer_para.font.size = Pt(12)
    footer_para.font.color.rgb = RgbColor(150, 150, 150)
    footer_para.alignment = PP_ALIGN.CENTER
    
    # Sources overview slide
    if sources:
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        
        # Title
        title_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(12.333), Inches(0.8))
        title_frame = title_box.text_frame
        title_para = title_frame.paragraphs[0]
        title_para.text = "📚 Sources"
        title_para.font.size = Pt(36)
        title_para.font.bold = True
        
        # Sources list
        content_box = slide.shapes.add_textbox(Inches(0.5), Inches(1.3), Inches(12.333), Inches(5.5))
        content_frame = content_box.text_frame
        content_frame.word_wrap = True
        
        for i, source in enumerate(sources[:10], 1):  # Limit to 10 sources per slide
            if i > 1:
                para = content_frame.add_paragraph()
            else:
                para = content_frame.paragraphs[0]
            
            para.text = f"{i}. {source.get('filename', 'Unknown')} ({source.get('format', 'unknown').upper()})"
            para.font.size = Pt(18)
            para.space_after = Pt(12)
    
    # Key Highlights slide (if any)
    if highlights:
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        
        # Title
        title_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(12.333), Inches(0.8))
        title_frame = title_box.text_frame
        title_para = title_frame.paragraphs[0]
        title_para.text = "🖍️ Key Highlights"
        title_para.font.size = Pt(36)
        title_para.font.bold = True
        
        # Highlights (limit to fit on slide)
        content_box = slide.shapes.add_textbox(Inches(0.5), Inches(1.3), Inches(12.333), Inches(5.5))
        content_frame = content_box.text_frame
        content_frame.word_wrap = True
        
        for i, h in enumerate(highlights[:5], 1):  # Limit to 5 highlights
            if i > 1:
                para = content_frame.add_paragraph()
            else:
                para = content_frame.paragraphs[0]
            
            text = h.get('highlighted_text', '')[:150]
            if len(h.get('highlighted_text', '')) > 150:
                text += "..."
            para.text = f'"{text}"'
            para.font.size = Pt(16)
            para.font.italic = True
            para.space_after = Pt(8)
            
            # Source attribution
            source_para = content_frame.add_paragraph()
            source_para.text = f"— {h.get('source_filename', 'Unknown')}"
            source_para.font.size = Pt(14)
            source_para.font.color.rgb = RgbColor(100, 100, 100)
            source_para.space_after = Pt(16)
    
    # Q&A slides (one per exchange, limited)
    if chat_history:
        for i, exchange in enumerate(chat_history[:5], 1):  # Limit to 5 Q&A pairs
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            
            # Question as title
            title_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(12.333), Inches(1.2))
            title_frame = title_box.text_frame
            title_frame.word_wrap = True
            title_para = title_frame.paragraphs[0]
            q_text = exchange.question[:100]
            if len(exchange.question) > 100:
                q_text += "..."
            title_para.text = f"Q{i}: {q_text}"
            title_para.font.size = Pt(28)
            title_para.font.bold = True
            
            # Answer
            content_box = slide.shapes.add_textbox(Inches(0.5), Inches(1.7), Inches(12.333), Inches(5.0))
            content_frame = content_box.text_frame
            content_frame.word_wrap = True
            content_para = content_frame.paragraphs[0]
            
            # Truncate answer to fit on slide
            answer_text = exchange.answer[:800]
            if len(exchange.answer) > 800:
                answer_text += "..."
            content_para.text = answer_text
            content_para.font.size = Pt(16)
    
    # Thank you / end slide
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    
    end_box = slide.shapes.add_textbox(Inches(0.5), Inches(3), Inches(12.333), Inches(1.5))
    end_frame = end_box.text_frame
    end_para = end_frame.paragraphs[0]
    end_para.text = "Thank You"
    end_para.font.size = Pt(44)
    end_para.font.bold = True
    end_para.alignment = PP_ALIGN.CENTER
    
    subtitle_box = slide.shapes.add_textbox(Inches(0.5), Inches(4.5), Inches(12.333), Inches(0.6))
    subtitle_frame = subtitle_box.text_frame
    subtitle_para = subtitle_frame.paragraphs[0]
    subtitle_para.text = f"Generated from {notebook['title']} by LocalBook"
    subtitle_para.font.size = Pt(18)
    subtitle_para.font.color.rgb = RgbColor(100, 100, 100)
    subtitle_para.alignment = PP_ALIGN.CENTER
    
    # Save to bytes
    output = io.BytesIO()
    prs.save(output)
    output.seek(0)
    return output.getvalue()
