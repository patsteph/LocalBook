"""Structured LLM Service using Pydantic AI

Provides type-safe, validated LLM outputs for features like:
- Quiz generation
- Visual summaries (Mermaid diagrams)
- Timeline extraction
- Document comparison
- Writing assistance

Uses Ollama as the backend with automatic retry on validation failures.
Uses professional-grade templates from output_templates.py for quality.
"""
import asyncio
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
import httpx

from config import settings
from services.output_templates import VISUAL_TEMPLATES, build_visual_prompt


# =============================================================================
# Output Models for Structured Generation
# =============================================================================

class QuizQuestion(BaseModel):
    """A single quiz question with answer and explanation."""
    question: str = Field(description="The question text")
    answer: str = Field(description="The correct answer")
    explanation: str = Field(description="Why this answer is correct")
    difficulty: str = Field(default="medium", description="easy, medium, or hard")
    question_type: str = Field(default="multiple_choice", description="multiple_choice or true_false")
    options: Optional[List[str]] = Field(default=None, description="Answer options - required for multiple_choice, ['True', 'False'] for true_false")
    source_reference: Optional[str] = Field(default=None, description="Name of the source document this question is from")


class QuizOutput(BaseModel):
    """Complete quiz output with multiple questions."""
    questions: List[QuizQuestion] = Field(description="List of quiz questions")
    topic: str = Field(description="The main topic of the quiz")
    source_summary: str = Field(description="Brief summary of source material used")


class MermaidDiagram(BaseModel):
    """Mermaid diagram output."""
    diagram_type: str = Field(description="flowchart, mindmap, timeline, sequenceDiagram, etc.")
    code: str = Field(description="Valid Mermaid diagram code")
    title: str = Field(description="Title for the diagram")
    description: str = Field(description="Brief description of what the diagram shows")


class VisualSummary(BaseModel):
    """Visual summary with one or more diagrams."""
    diagrams: List[MermaidDiagram] = Field(description="List of Mermaid diagrams")
    key_points: List[str] = Field(description="Key points summarized from the content")


class TimelineEvent(BaseModel):
    """A single event for timeline visualization."""
    date: str = Field(description="Date or time period (e.g., '2024-01-15', 'Q1 2024', 'Early 1900s')")
    title: str = Field(description="Short title for the event")
    description: str = Field(description="Description of what happened")
    importance: str = Field(default="medium", description="low, medium, or high")
    source_reference: Optional[str] = Field(default=None, description="Reference to source document")


class TimelineOutput(BaseModel):
    """Timeline extraction output."""
    events: List[TimelineEvent] = Field(description="List of events in chronological order")
    time_span: str = Field(description="Overall time span covered (e.g., '2020-2024')")
    context: str = Field(description="Brief context about the timeline")


class DocumentComparison(BaseModel):
    """Comparison between two documents."""
    similarities: List[str] = Field(description="Key similarities between documents")
    differences: List[str] = Field(description="Key differences between documents")
    unique_to_first: List[str] = Field(description="Points unique to first document")
    unique_to_second: List[str] = Field(description="Points unique to second document")
    synthesis: str = Field(description="Synthesized understanding combining both documents")


class WritingAssistance(BaseModel):
    """Writing assistance output."""
    content: str = Field(description="The generated or improved content")
    format_used: str = Field(description="The format applied (e.g., 'academic', 'blog', 'email')")
    suggestions: List[str] = Field(default_factory=list, description="Additional suggestions for improvement")
    word_count: int = Field(description="Approximate word count of the content")


# =============================================================================
# Structured LLM Service
# =============================================================================

class StructuredLLMService:
    """Service for generating structured outputs from LLM using Pydantic models."""
    
    def __init__(self):
        self.base_url = settings.ollama_base_url
        self.model = settings.ollama_model
        self.max_retries = 3
    
    async def _call_ollama_json(self, system_prompt: str, user_prompt: str, temperature: float = 0.7) -> Dict[str, Any]:
        """Call Ollama with JSON mode enabled."""
        timeout = httpx.Timeout(120.0)
        
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": f"{system_prompt}\n\nUser request:\n{user_prompt}",
                    "stream": False,
                    "format": "json",
                    "options": {
                        "temperature": temperature,
                        "num_predict": 2000,
                    }
                }
            )
            result = response.json()
            
            import json
            try:
                return json.loads(result.get("response", "{}"))
            except json.JSONDecodeError:
                return {}
    
    async def generate_quiz(
        self, 
        content: str, 
        num_questions: int = 5,
        difficulty: str = "medium",
        question_types: Optional[List[str]] = None
    ) -> QuizOutput:
        """Generate a professional-quality quiz from content with structured output."""
        
        question_types = question_types or ["multiple_choice", "true_false"]
        
        system_prompt = f"""You are an expert instructional designer creating assessment questions for mastery learning.

Create exactly {num_questions} high-quality questions based on the provided content.

Output a valid JSON object with this structure:
{{
    "questions": [
        {{
            "question": "clear, unambiguous question text",
            "answer": "the correct answer (must match one of the options exactly)",
            "explanation": "why this is correct, referencing the source material",
            "difficulty": "{difficulty}",
            "question_type": "multiple_choice or true_false",
            "options": ["array of 4 options for multiple_choice, or ['True', 'False'] for true_false"],
            "source_reference": "name of source document this question comes from"
        }}
    ],
    "topic": "main topic being tested",
    "source_summary": "brief summary of source material used"
}}

QUESTION QUALITY REQUIREMENTS:
1. Test UNDERSTANDING, not just recall of trivial facts
2. Each question should assess a meaningful concept
3. For multiple_choice:
   - All 4 options must be plausible (no obviously wrong answers)
   - Distractors should represent common misconceptions
   - Options should be similar in length and structure
   - Avoid "all of the above" or "none of the above"
4. For true_false:
   - Statement must be unambiguously true or false
   - False statements should be plausibly incorrect
5. Questions should span different topics from the source material
6. Explanations should teach, not just state the answer

DIFFICULTY GUIDELINES:
- easy: Basic recall and comprehension
- medium: Application and analysis
- hard: Synthesis and evaluation across concepts"""

        for attempt in range(self.max_retries):
            try:
                result = await self._call_ollama_json(system_prompt, f"Content:\n{content[:8000]}")
                return QuizOutput(**result)
            except Exception as e:
                if attempt == self.max_retries - 1:
                    # Return minimal valid output on final failure
                    return QuizOutput(
                        questions=[],
                        topic="Quiz generation failed",
                        source_summary=str(e)
                    )
                await asyncio.sleep(1)
        
        return QuizOutput(questions=[], topic="Failed", source_summary="Max retries exceeded")
    
    async def generate_visual_summary(
        self, 
        content: str,
        diagram_types: Optional[List[str]] = None
    ) -> VisualSummary:
        """Generate visual summary with Mermaid diagrams using professional templates."""
        
        diagram_types = diagram_types or ["mindmap", "flowchart"]
        
        # Build enhanced prompts from templates
        diagram_guidelines = []
        for dtype in diagram_types:
            if dtype in VISUAL_TEMPLATES:
                template = VISUAL_TEMPLATES[dtype]
                diagram_guidelines.append(f"""
### {template['name']} ({dtype})
{template['system_prompt']}

Example syntax:
```mermaid
{template['example']}
```
""")
        
        system_prompt = f"""You are a professional visualization expert creating publication-quality diagrams.

Output a valid JSON object with this structure:
{{
    "diagrams": [
        {{
            "diagram_type": "one of {diagram_types}",
            "code": "valid mermaid code - MUST be syntactically correct",
            "title": "descriptive diagram title",
            "description": "what this diagram reveals about the content"
        }}
    ],
    "key_points": ["list", "of", "key", "insights", "from the content"]
}}

DIAGRAM GUIDELINES:
{chr(10).join(diagram_guidelines) if diagram_guidelines else "- Mindmap, Flowchart, Timeline diagram types supported"}

QUALITY REQUIREMENTS:
- Diagrams must be readable at a glance (not overcrowded)
- Use clear, concise labels (2-5 words per node)
- Capture the essential structure, not every detail
- Ensure Mermaid syntax is valid and will render correctly
- Key points should be insights, not just facts"""

        for attempt in range(self.max_retries):
            try:
                result = await self._call_ollama_json(system_prompt, f"Content:\n{content[:8000]}")
                return VisualSummary(**result)
            except Exception as e:
                if attempt == self.max_retries - 1:
                    return VisualSummary(
                        diagrams=[],
                        key_points=[f"Visual summary generation failed: {str(e)}"]
                    )
                await asyncio.sleep(1)
        
        return VisualSummary(diagrams=[], key_points=["Failed to generate"])
    
    async def extract_timeline(self, content: str) -> TimelineOutput:
        """Extract timeline events from content."""
        
        system_prompt = """You are a timeline extractor. Identify dates and events from the content.

Output a valid JSON object with this structure:
{
    "events": [
        {
            "date": "date or time period",
            "title": "short title",
            "description": "what happened",
            "importance": "low/medium/high",
            "source_reference": "optional source reference"
        }
    ],
    "time_span": "overall time period",
    "context": "brief context"
}

Rules:
- Extract all dates, years, and time periods mentioned
- Order events chronologically
- Include approximate dates if exact dates unknown
- Rate importance based on significance in the content"""

        for attempt in range(self.max_retries):
            try:
                result = await self._call_ollama_json(system_prompt, f"Content:\n{content[:8000]}")
                return TimelineOutput(**result)
            except Exception as e:
                if attempt == self.max_retries - 1:
                    return TimelineOutput(
                        events=[],
                        time_span="Unknown",
                        context=f"Timeline extraction failed: {str(e)}"
                    )
                await asyncio.sleep(1)
        
        return TimelineOutput(events=[], time_span="Unknown", context="Failed")
    
    async def compare_documents(self, doc1_content: str, doc2_content: str) -> DocumentComparison:
        """Compare two documents and identify similarities/differences."""
        
        system_prompt = """You are a document comparison expert. Analyze two documents and compare them.

Output a valid JSON object with this structure:
{
    "similarities": ["list of similarities"],
    "differences": ["list of differences"],
    "unique_to_first": ["points only in first doc"],
    "unique_to_second": ["points only in second doc"],
    "synthesis": "synthesized understanding combining both"
}

Rules:
- Be specific about what's similar and different
- Note contradictions if any
- Provide a useful synthesis that combines insights from both"""

        combined_prompt = f"DOCUMENT 1:\n{doc1_content[:4000]}\n\nDOCUMENT 2:\n{doc2_content[:4000]}"
        
        for attempt in range(self.max_retries):
            try:
                result = await self._call_ollama_json(system_prompt, combined_prompt)
                return DocumentComparison(**result)
            except Exception as e:
                if attempt == self.max_retries - 1:
                    return DocumentComparison(
                        similarities=[],
                        differences=[],
                        unique_to_first=[],
                        unique_to_second=[],
                        synthesis=f"Comparison failed: {str(e)}"
                    )
                await asyncio.sleep(1)
        
        return DocumentComparison(
            similarities=[], differences=[], unique_to_first=[],
            unique_to_second=[], synthesis="Failed"
        )
    
    async def assist_writing(
        self, 
        content: str, 
        task: str = "improve",
        format_style: str = "professional"
    ) -> WritingAssistance:
        """Assist with writing tasks."""
        
        system_prompt = f"""You are a writing assistant. Your task is to {task} the provided content.
        
Use this format style: {format_style}

Output a valid JSON object with this structure:
{{
    "content": "the improved/generated content",
    "format_used": "{format_style}",
    "suggestions": ["additional improvement suggestions"],
    "word_count": number
}}

Available format styles:
- professional: Clear, formal, suitable for business
- academic: Scholarly, with proper structure and citations style
- casual: Friendly, conversational
- technical: Precise, detailed, for technical audiences
- blog: Engaging, readable, with good flow
- email: Concise, clear, action-oriented"""

        for attempt in range(self.max_retries):
            try:
                result = await self._call_ollama_json(system_prompt, f"Content to work with:\n{content[:6000]}")
                return WritingAssistance(**result)
            except Exception as e:
                if attempt == self.max_retries - 1:
                    return WritingAssistance(
                        content=content,
                        format_used="none",
                        suggestions=[f"Writing assistance failed: {str(e)}"],
                        word_count=len(content.split())
                    )
                await asyncio.sleep(1)
        
        return WritingAssistance(
            content=content, format_used="none", suggestions=[], word_count=0
        )


# Singleton instance
structured_llm = StructuredLLMService()
