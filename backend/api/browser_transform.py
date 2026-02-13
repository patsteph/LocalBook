"""Browser Extension Transform API

Transforms collected content into different formats:
Key Takeaways, Action Items, Executive Brief, Timeline, FAQ, Quiz, 
Comparison, Study Guide, Outline
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from services.rag_engine import rag_engine

router = APIRouter(prefix="/browser", tags=["browser-transform"])


class TransformRequest(BaseModel):
    content: str
    title: Optional[str] = ""
    transform_type: str  # key_takeaways, action_items, executive_brief, timeline, faq, quiz, comparison, study_guide, outline
    notebook_id: Optional[str] = None


class TransformResponse(BaseModel):
    success: bool
    transform_type: str
    content: str
    error: Optional[str] = None


TRANSFORM_PROMPTS = {
    "key_takeaways": """Extract the key takeaways from this content. Return a concise bulleted list of the most important points, insights, and conclusions. Focus on what the reader should remember.

CONTENT:
{content}

Return only the key takeaways as a clean bulleted list. No preamble.""",

    "action_items": """Extract actionable items from this content. What should someone DO based on this information? Return a numbered list of specific, concrete actions.

CONTENT:
{content}

Return only the action items as a numbered list. Be specific and actionable. No preamble.""",

    "executive_brief": """Write a concise executive brief (3-5 paragraphs) summarizing this content for a busy decision-maker. Include: the core message, key findings, implications, and recommended next steps.

CONTENT:
{content}

Return only the executive brief. Professional tone, no preamble.""",

    "timeline": """Extract a chronological timeline of events, milestones, or developments from this content. Format each entry as a date/period followed by the event.

CONTENT:
{content}

Return the timeline in chronological order. Format: **[Date/Period]** — Event description. No preamble.""",

    "faq": """Generate a FAQ (Frequently Asked Questions) based on this content. Create 5-8 questions that a reader would likely ask, with concise answers drawn from the content.

CONTENT:
{content}

Format each as:
**Q: [Question]**
A: [Answer]

No preamble.""",

    "quiz": """Create a quiz based on this content with 5-7 multiple choice questions to test comprehension. Include the correct answer for each.

CONTENT:
{content}

Format each question as:
**Q[N]: [Question]**
a) [Option]
b) [Option]
c) [Option]
d) [Option]
✓ Correct: [letter]

No preamble.""",

    "comparison": """Analyze this content and create a structured comparison of the key concepts, positions, or options discussed. Use a clear format that highlights similarities, differences, pros, and cons.

CONTENT:
{content}

Return a structured comparison. No preamble.""",

    "study_guide": """Create a comprehensive study guide from this content. Include: key concepts with definitions, important relationships between ideas, and review questions.

CONTENT:
{content}

Format with clear sections using markdown headers. No preamble.""",

    "outline": """Create a detailed hierarchical outline of this content. Capture the structure, main topics, subtopics, and key details in outline format.

CONTENT:
{content}

Use markdown heading levels and indented bullets. No preamble.""",
}


@router.post("/transform", response_model=TransformResponse)
async def transform_content(request: TransformRequest):
    """Transform page content into a specific format."""
    if request.transform_type not in TRANSFORM_PROMPTS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown transform type: {request.transform_type}. "
                   f"Available: {', '.join(TRANSFORM_PROMPTS.keys())}"
        )

    try:
        prompt_template = TRANSFORM_PROMPTS[request.transform_type]
        # Truncate content to fit context window
        max_content = 12000
        content = request.content[:max_content]
        if request.title:
            content = f"Title: {request.title}\n\n{content}"

        user_prompt = prompt_template.format(content=content)

        system_prompt = (
            "You are a precise content transformation assistant. "
            "Follow the requested format exactly. Be thorough but concise. "
            "Only use information from the provided content — do not add external knowledge."
        )

        result = await rag_engine._call_ollama(system_prompt, user_prompt)

        return TransformResponse(
            success=True,
            transform_type=request.transform_type,
            content=result
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        return TransformResponse(
            success=False,
            transform_type=request.transform_type,
            content="",
            error=str(e)
        )


@router.post("/suggest-links")
async def suggest_links(request: dict):
    """AI-analyze outbound links and suggest which ones are worth adding as sources."""
    links = request.get("links", [])
    intent = request.get("intent", "")
    title = request.get("title", "")

    if not links:
        return {"suggestions": []}

    # Format links for the LLM
    link_text = "\n".join(
        f"- [{l.get('text', 'Link')}]({l.get('url', '')}) — Context: {l.get('context', '')[:100]}"
        for l in links[:20]
    )

    prompt = f"""The user is reading an article titled "{title}".
Their research intent: "{intent}"

Here are outbound links from the article:
{link_text}

Select the 3-5 most relevant links that would be valuable to add as research sources.
For each, explain in one sentence why it's worth reading.

Respond with JSON array only:
[{{"url": "...", "text": "...", "reason": "..."}}]

Only include links that are clearly relevant to the research intent. Respond with JSON only."""

    try:
        result = await rag_engine._call_ollama(
            "You are a research assistant that identifies valuable sources.",
            prompt
        )
        import json
        json_start = result.find("[")
        json_end = result.rfind("]") + 1
        if json_start >= 0 and json_end > json_start:
            suggestions = json.loads(result[json_start:json_end])
            return {"suggestions": suggestions[:5]}
    except Exception as e:
        print(f"[SUGGEST-LINKS] Failed: {e}")

    return {"suggestions": []}
