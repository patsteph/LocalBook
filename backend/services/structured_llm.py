"""Structured LLM Service using Pydantic AI

Provides type-safe, validated LLM outputs for features like:
- Quiz generation
- Visual summaries (Mermaid diagrams)
- Timeline extraction
- Document comparison
- Writing assistance

Uses Ollama as the backend with automatic retry on validation failures.
"""
import asyncio
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
import httpx

from config import settings


# =============================================================================
# Output Models for Structured Generation
# =============================================================================

class QuizQuestion(BaseModel):
    """A single quiz question with answer and explanation."""
    question: str = Field(description="The question text")
    answer: str = Field(description="The correct answer")
    explanation: str = Field(description="Why this answer is correct, with source reference")
    difficulty: str = Field(default="medium", description="easy, medium, or hard")
    question_type: str = Field(default="short_answer", description="short_answer, multiple_choice, true_false")
    options: Optional[List[str]] = Field(default=None, description="Options for multiple choice questions")


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
        """Generate a quiz from content with structured output."""
        
        question_types = question_types or ["short_answer", "multiple_choice", "true_false"]
        
        system_prompt = f"""You are a quiz generator. Create exactly {num_questions} questions based on the provided content.
        
Output a valid JSON object with this structure:
{{
    "questions": [
        {{
            "question": "string",
            "answer": "string",
            "explanation": "string referencing the source",
            "difficulty": "{difficulty}",
            "question_type": "one of {question_types}",
            "options": ["array of options for multiple_choice, null otherwise"]
        }}
    ],
    "topic": "main topic string",
    "source_summary": "brief summary of source material"
}}

Rules:
- Questions must be directly answerable from the content
- Include source references in explanations
- Vary question types as requested
- Ensure answers are factually correct based on content"""

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
        """Generate visual summary with Mermaid diagrams."""
        
        diagram_types = diagram_types or ["mindmap", "flowchart"]
        
        system_prompt = f"""You are a visual summary generator. Create Mermaid diagrams to visualize the content.

Output a valid JSON object with this structure:
{{
    "diagrams": [
        {{
            "diagram_type": "one of {diagram_types}",
            "code": "valid mermaid code",
            "title": "diagram title",
            "description": "what it shows"
        }}
    ],
    "key_points": ["list", "of", "key", "points"]
}}

Mermaid syntax examples:
- Mindmap: mindmap\\n  root((Topic))\\n    Branch1\\n      Leaf1\\n    Branch2
- Flowchart: flowchart TD\\n    A[Start] --> B{{Decision}}\\n    B -->|Yes| C[Action]
- Timeline: timeline\\n    title Timeline\\n    2020 : Event 1\\n    2021 : Event 2

Rules:
- Use valid Mermaid syntax
- Keep diagrams readable (not too complex)
- Extract key relationships and hierarchies from content"""

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
