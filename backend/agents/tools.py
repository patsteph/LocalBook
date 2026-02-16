"""LocalBook Agent Tools

LangGraph tools that wrap existing LocalBook functionality.
These tools are used by agents to perform specific tasks.

STATUS: PARTIAL — These tools are only invoked via the experimental
LangGraph supervisor (agents/supervisor.py → api/agent.py). The primary
app flow calls the underlying services directly (rag_engine, structured_llm, etc.).
"""

from typing import Optional, List
from langchain_core.tools import tool


@tool
async def rag_search_tool(
    query: str,
    notebook_id: str,
    top_k: int = 5
) -> dict:
    """Search notebook sources using adaptive RAG.
    
    Args:
        query: The search query
        notebook_id: ID of the notebook to search
        top_k: Number of results to return
        
    Returns:
        Dictionary with results and sources
    """
    from services.rag_engine import rag_engine
    
    results = await rag_engine.search(
        notebook_id=notebook_id,
        query=query,
        top_k=top_k
    )
    
    return {
        "query": query,
        "results": results,
        "num_results": len(results),
        "notebook_id": notebook_id
    }


@tool
async def web_search_tool(
    query: str,
    max_results: int = 5
) -> dict:
    """Search the web for information.
    
    Args:
        query: Search query
        max_results: Maximum number of results
        
    Returns:
        Dictionary with search results
    """
    from api.web import search_web_internal
    
    results = await search_web_internal(query, max_results)
    
    return {
        "query": query,
        "results": results,
        "num_results": len(results)
    }


@tool
async def generate_document_tool(
    notebook_id: str,
    document_type: str,
    topic: Optional[str] = None,
    style: str = "professional"
) -> dict:
    """Generate a document from notebook sources.
    
    Args:
        notebook_id: ID of the notebook
        document_type: Type of document (briefing, study_guide, faq, summary, etc.)
        topic: Optional topic focus
        style: Writing style (professional, casual, academic)
        
    Returns:
        Dictionary with generated content
    """
    from storage.skills_store import skills_store
    from storage.source_store import source_store
    from services.rag_engine import rag_engine
    from services.output_templates import build_document_prompt, DOCUMENT_TEMPLATES
    
    # Get skill
    skill = await skills_store.get(document_type)
    if not skill:
        return {"error": f"Unknown document type: {document_type}"}
    
    # Get sources
    sources = await source_store.list(notebook_id)
    if not sources:
        return {"error": "No sources in notebook"}
    
    content_parts = []
    for source in sources[:10]:
        source_content = await source_store.get_content(notebook_id, source["id"])
        if source_content and source_content.get("content"):
            content_parts.append(
                f"## Source: {source.get('filename', 'Unknown')}\n{source_content['content'][:4000]}"
            )
    
    context = "\n\n---\n\n".join(content_parts)
    skill_name = skill.get("name", "Content")
    topic_focus = topic or "the main topics and insights"
    
    # Build prompt
    if document_type in DOCUMENT_TEMPLATES:
        template_system, template_format = build_document_prompt(
            document_type, topic_focus, style, len(content_parts)
        )
        system_prompt = f"{template_system}\n\n{template_format}\n\nFOCUS: {topic_focus}"
    else:
        system_prompt = skill.get("system_prompt", "")
    
    user_prompt = f"Based on these {len(content_parts)} sources, create a {skill_name}:\n\n{context[:12000]}"
    
    content = await rag_engine._call_ollama(system_prompt, user_prompt)
    
    return {
        "document_type": document_type,
        "content": content,
        "sources_used": len(content_parts),
        "topic": topic_focus
    }


@tool
async def generate_quiz_tool(
    notebook_id: str,
    num_questions: int = 5,
    difficulty: str = "medium"
) -> dict:
    """Generate a quiz from notebook sources.
    
    Args:
        notebook_id: ID of the notebook
        num_questions: Number of questions to generate
        difficulty: Difficulty level (easy, medium, hard)
        
    Returns:
        Dictionary with quiz questions
    """
    from services.structured_llm import structured_llm
    from storage.source_store import source_store
    
    sources = await source_store.list(notebook_id)
    if not sources:
        return {"error": "No sources in notebook"}
    
    source_names = [s.get("filename", s.get("title", "Unknown")) for s in sources[:5]]
    content = "\n\n".join([
        f"[Source: {source_names[i]}]\n{s.get('content', '')[:2000]}" 
        for i, s in enumerate(sources[:5])
    ])
    
    quiz_output = await structured_llm.generate_quiz(
        content=content,
        num_questions=num_questions,
        difficulty=difficulty
    )
    
    return {
        "questions": [q.model_dump() for q in quiz_output.questions],
        "topic": quiz_output.topic,
        "num_questions": len(quiz_output.questions)
    }


@tool
async def generate_visual_tool(
    notebook_id: str,
    diagram_types: Optional[List[str]] = None
) -> dict:
    """Generate visual diagrams from notebook sources.
    
    Args:
        notebook_id: ID of the notebook
        diagram_types: Types of diagrams to generate (mindmap, flowchart, timeline)
        
    Returns:
        Dictionary with diagram code
    """
    from services.structured_llm import structured_llm
    from storage.source_store import source_store
    
    diagram_types = diagram_types or ["mindmap", "flowchart"]
    
    sources = await source_store.list(notebook_id)
    if not sources:
        return {"error": "No sources in notebook"}
    
    content = "\n\n".join([s.get("content", "")[:3000] for s in sources[:5]])
    
    result = await structured_llm.generate_visual_summary(
        content=content,
        diagram_types=diagram_types
    )
    
    return {
        "diagrams": [d.model_dump() for d in result.diagrams],
        "key_points": result.key_points,
        "num_diagrams": len(result.diagrams)
    }


@tool
async def capture_page_tool(
    url: str,
    title: str,
    content: str,
    notebook_id: str,
    meta_tags: Optional[dict] = None
) -> dict:
    """Capture a web page to a notebook.
    
    Args:
        url: Page URL
        title: Page title
        content: Page content (text)
        notebook_id: Target notebook ID
        meta_tags: Optional metadata from page
        
    Returns:
        Dictionary with capture result
    """
    from services.source_ingestion import create_and_ingest_source
    
    word_count = len(content.split())
    reading_time = max(1, word_count // 200)
    
    result = await create_and_ingest_source(
        notebook_id=notebook_id,
        filename=title,
        text=content,
        source_type="web",
        url=url,
        extra_metadata={
            "word_count": word_count,
            "reading_time_minutes": reading_time,
            "meta_tags": meta_tags or {},
        },
    )
    
    return {
        "success": True,
        "source_id": result["source_id"],
        "title": title,
        "word_count": word_count,
        "reading_time_minutes": reading_time
    }


async def _summarize_page_impl(content: str, url: str) -> dict:
    """Implementation of page summarization - can be called directly.
    
    Args:
        content: Page text content
        url: Page URL for context
        
    Returns:
        Dictionary with summary, key points, and key concepts
    """
    from services.rag_engine import rag_engine
    
    # Calculate content length to scale summary depth
    word_count = len(content.split())
    
    system_prompt = """You are an expert content analyst. Your job is to create comprehensive, engaging summaries that make readers want to explore the content further.

Analyze the web page and provide a RICH summary with THREE sections:

1. **KEY POINTS** (5-8 bullet points)
   - Each bullet should be a complete, informative statement
   - Cover the most important ideas, findings, or arguments
   - Include specific details, numbers, or examples when available
   - Make each point standalone and valuable

2. **SUMMARY** (2-3 substantial paragraphs)
   - First paragraph: What is this content about? Why does it matter?
   - Second paragraph: The main arguments, findings, or narrative
   - Third paragraph (if needed): Implications, conclusions, or what to watch for
   - Write in an engaging, accessible style

3. **KEY CONCEPTS** (5-10 terms/topics)
   - Extract the main topics, technologies, people, or ideas mentioned
   - These help categorize and connect the content

Output ONLY valid JSON (no markdown, no extra text):
{
    "key_points": ["Point 1 with specific detail", "Point 2 with context", ...],
    "summary": "Paragraph 1...\\n\\nParagraph 2...\\n\\nParagraph 3...",
    "key_concepts": ["concept1", "concept2", ...]
}"""
    
    user_prompt = f"This content has approximately {word_count} words. Create a comprehensive summary:\n\n{content[:12000]}"
    
    response = await rag_engine._call_ollama(system_prompt, user_prompt)
    
    # Robust JSON extraction
    result = _extract_summary_json(response)
    return result


@tool
async def summarize_page_tool(content: str, url: str) -> dict:
    """Summarize a web page and extract key concepts.
    
    Args:
        content: Page text content
        url: Page URL for context
        
    Returns:
        Dictionary with summary, key points, and key concepts
    """
    return await _summarize_page_impl(content, url)


def _sanitize_llm_json(text: str) -> str:
    """Fix common LLM JSON issues that break json.loads."""
    import re
    # Remove trailing commas before ] or }
    text = re.sub(r',\s*([}\]])', r'\1', text)
    # Fix unescaped newlines inside JSON string values
    # Walk through and escape literal newlines that are inside quotes
    result = []
    in_string = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == '\\' and in_string and i + 1 < len(text):
            result.append(ch)
            result.append(text[i + 1])
            i += 2
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
        elif ch == '\n' and in_string:
            result.append('\\n')
        elif ch == '\r' and in_string:
            result.append('\\r')
        elif ch == '\t' and in_string:
            result.append('\\t')
        else:
            result.append(ch)
        i += 1
    return ''.join(result)


def _regex_extract_fields(text: str) -> dict:
    """Nuclear fallback: extract key_points, summary, key_concepts via regex from malformed JSON."""
    import re
    
    result = {"key_points": [], "summary": "", "key_concepts": []}
    
    # Extract summary — find "summary": "..." (handles multiline)
    summary_match = re.search(r'"(?:summary|Summary)":\s*"((?:[^"\\]|\\.|"(?=\s*[,}\]]))*)"', text, re.DOTALL)
    if summary_match:
        raw = summary_match.group(1)
        result["summary"] = raw.replace('\\n', '\n').replace('\\"', '"').strip()
    
    # Extract key_points array items
    kp_match = re.search(r'"(?:key_points|takeaways)":\s*\[(.*?)\]', text, re.DOTALL)
    if kp_match:
        items = re.findall(r'"((?:[^"\\]|\\.)*)"', kp_match.group(1))
        result["key_points"] = [s.replace('\\n', '\n').replace('\\"', '"').strip() for s in items if s.strip()]
    
    # Extract key_concepts array items
    kc_match = re.search(r'"(?:key_concepts|concepts)":\s*\[(.*?)\]', text, re.DOTALL)
    if kc_match:
        items = re.findall(r'"((?:[^"\\]|\\.)*)"', kc_match.group(1))
        result["key_concepts"] = [s.replace('\\n', '\n').replace('\\"', '"').strip() for s in items if s.strip()]
    
    return result


def _try_parse_json(text: str) -> dict | None:
    """Try json.loads with sanitization. Returns parsed dict or None."""
    import json
    # Try raw first
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    # Try after sanitizing
    try:
        return json.loads(_sanitize_llm_json(text))
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _extract_summary_json(response: str) -> dict:
    """Extract JSON from LLM response with multiple fallback strategies."""
    import json
    
    text = response.strip()
    print(f"[DEBUG] Extracting JSON from response ({len(text)} chars), starts with: {text[:120]}...")
    
    # Strategy 1: Extract content between ``` markers, then find JSON
    if "```" in text:
        parts = text.split("```")
        for i, part in enumerate(parts):
            if i % 2 == 1:
                content = part.strip()
                if content.lower().startswith("json"):
                    content = content[4:].strip()
                if content.startswith("{"):
                    balanced = _extract_balanced_json(content)
                    if balanced:
                        parsed = _try_parse_json(balanced)
                        if parsed:
                            print("[DEBUG] Strategy 1 (code block + sanitize) succeeded")
                            return _parse_and_validate(parsed)
    
    # Strategy 2: Find balanced JSON object directly in text, with sanitization
    balanced = _extract_balanced_json(text)
    if balanced:
        parsed = _try_parse_json(balanced)
        if parsed:
            print("[DEBUG] Strategy 2 (balanced + sanitize) succeeded")
            return _parse_and_validate(parsed)
    
    # Strategy 3: Try parsing the whole response with sanitization
    parsed = _try_parse_json(text)
    if parsed:
        print("[DEBUG] Strategy 3 (direct + sanitize) succeeded")
        return _parse_and_validate(parsed)
    
    # Strategy 4: Regex field extraction — works even on badly malformed JSON
    if '"summary"' in text.lower() or '"key_points"' in text.lower():
        regex_result = _regex_extract_fields(text)
        if regex_result["summary"] or regex_result["key_points"]:
            print(f"[DEBUG] Strategy 4 (regex) succeeded: summary={len(regex_result['summary'])} chars, {len(regex_result['key_points'])} points")
            return regex_result
    
    # Fallback: Return response as summary text
    print("[DEBUG] All JSON extraction strategies failed, using raw text")
    return {
        "key_points": [],
        "summary": text,
        "key_concepts": []
    }


def _extract_balanced_json(text: str) -> str:
    """Extract a balanced JSON object from text."""
    if not text.startswith("{"):
        idx = text.find("{")
        if idx == -1:
            print("[DEBUG] _extract_balanced_json: No { found in text")
            return ""
        text = text[idx:]
    
    brace_count = 0
    in_string = False
    escape_next = False
    
    for i, char in enumerate(text):
        if escape_next:
            escape_next = False
            continue
        if char == '\\':
            escape_next = True
            continue
        if char == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == '{':
            brace_count += 1
        elif char == '}':
            brace_count -= 1
            if brace_count == 0:
                result = text[:i+1]
                print(f"[DEBUG] _extract_balanced_json: Extracted {len(result)} chars")
                return result
    
    print(f"[DEBUG] _extract_balanced_json: Failed to find balanced braces, brace_count={brace_count}")
    return ""


def _parse_and_validate(data: dict) -> dict:
    """Ensure the parsed JSON has required fields and all values are clean strings."""
    if not isinstance(data, dict):
        raise ValueError("Not a dict")
    
    result = {
        "key_points": data.get("key_points", data.get("takeaways", [])),
        "summary": data.get("summary", ""),
        "key_concepts": data.get("key_concepts", data.get("concepts", []))
    }
    
    # Validate types
    if not isinstance(result["key_points"], list):
        result["key_points"] = []
    if isinstance(result["summary"], list):
        # LLM sometimes returns summary as a list of paragraphs — join them
        result["summary"] = "\n\n".join(str(s) for s in result["summary"] if s)
    elif not isinstance(result["summary"], str):
        result["summary"] = str(result["summary"]) if result["summary"] else ""
    if not isinstance(result["key_concepts"], list):
        result["key_concepts"] = []
    
    # Flatten list elements: LLMs sometimes return objects instead of strings
    # e.g. {"Indexing Phase: ..."} or {"point": "text"} instead of just "text"
    def _flatten_item(item) -> str:
        if isinstance(item, str):
            return item.strip()
        if isinstance(item, dict):
            # If single-key dict, use the key (or value if key is generic)
            if len(item) == 1:
                k, v = next(iter(item.items()))
                if isinstance(v, str) and v:
                    return f"{k}: {v}".strip() if k not in ("point", "text", "item", "concept") else v.strip()
                return str(k).strip()
            # Multi-key: try common field names
            for field in ("text", "point", "description", "content", "name", "title"):
                if field in item and isinstance(item[field], str):
                    return item[field].strip()
            return str(item)
        return str(item).strip()
    
    result["key_points"] = [s for s in (_flatten_item(x) for x in result["key_points"]) if s]
    result["key_concepts"] = [s for s in (_flatten_item(x) for x in result["key_concepts"]) if s]
    
    return result


@tool
async def extract_page_metadata_tool(
    html_content: str,
    url: str
) -> dict:
    """Extract metadata from HTML page.
    
    Args:
        html_content: Raw HTML content
        url: Page URL
        
    Returns:
        Dictionary with extracted metadata
    """
    from bs4 import BeautifulSoup
    
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # Title - try multiple sources in priority order
    title = ""
    
    # Priority 1: og:title (usually the cleanest article title)
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = og_title.get("content", "").strip()
    
    # Priority 2: twitter:title
    if not title:
        twitter_title = soup.find("meta", attrs={"name": "twitter:title"})
        if twitter_title and twitter_title.get("content"):
            title = twitter_title.get("content", "").strip()
    
    # Priority 3: <h1> tag (often the article title on Medium, Substack, etc.)
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)
    
    # Priority 4: article:title meta tag
    if not title:
        article_title = soup.find("meta", property="article:title")
        if article_title and article_title.get("content"):
            title = article_title.get("content", "").strip()
    
    # Priority 5: <title> tag (fallback, often includes site name)
    if not title and soup.title:
        title = soup.title.string or ""
        # Clean up common suffixes like " | Medium" or " - Substack"
        for suffix in [" | Medium", " - Medium", " | Substack", " - Substack", 
                       " | LinkedIn", " - LinkedIn", " | by ", " — "]:
            if suffix in title:
                title = title.split(suffix)[0].strip()
    
    # Ensure title is clean
    title = title.strip() if title else ""
    
    # Description
    description = ""
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc:
        description = meta_desc.get("content", "")
    og_desc = soup.find("meta", property="og:description")
    if og_desc:
        description = og_desc.get("content", description)
    
    # Author
    author = ""
    meta_author = soup.find("meta", attrs={"name": "author"})
    if meta_author:
        author = meta_author.get("content", "")
    
    # Publish date
    publish_date = ""
    for prop in ["article:published_time", "datePublished", "pubdate"]:
        meta = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        if meta:
            publish_date = meta.get("content", "")
            break
    
    # OG Image
    og_image = ""
    meta_img = soup.find("meta", property="og:image")
    if meta_img:
        og_image = meta_img.get("content", "")
    
    # Keywords
    keywords = []
    meta_keywords = soup.find("meta", attrs={"name": "keywords"})
    if meta_keywords:
        kw_content = meta_keywords.get("content", "")
        keywords = [k.strip() for k in kw_content.split(",") if k.strip()]
    
    # Word count and reading time from text
    text = soup.get_text(separator=" ", strip=True)
    word_count = len(text.split())
    reading_time = max(1, word_count // 200)
    
    return {
        "title": title,
        "description": description,
        "author": author,
        "publish_date": publish_date,
        "og_image": og_image,
        "keywords": keywords[:10],
        "word_count": word_count,
        "reading_time_minutes": reading_time,
        "url": url
    }
