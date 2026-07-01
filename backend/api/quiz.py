"""Quiz API endpoints for spaced repetition learning

Generates quizzes from notebook content using structured LLM outputs.
Tracks user performance with FSRS (Free Spaced Repetition Scheduler).
"""
import logging
import traceback
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
import json
from pathlib import Path

from services.event_logger import log_quiz_completed

logger = logging.getLogger(__name__)

from config import settings
from services.structured_llm import structured_llm
from services.svg_sanitizer import sanitize_svg
from storage.source_store import source_store


router = APIRouter(prefix="/quiz", tags=["quiz"])


# =============================================================================
# Request/Response Models
# =============================================================================

OPEN_ENDED_TYPES = {"short_answer", "spot_the_error"}

ALL_QUESTION_TYPES = [
    "multiple_choice",
    "true_false",
    "fill_in_the_blank",
    "short_answer",
    "spot_the_error",
]


class GenerateQuizRequest(BaseModel):
    notebook_id: str
    num_questions: int = Field(default=5, ge=1, le=50)
    difficulty: str = Field(default="medium", pattern="^(easy|medium|hard)$")
    topic: Optional[str] = None  # Focus quiz on a specific topic
    question_types: Optional[List[str]] = None
    source_ids: Optional[List[str]] = None  # Specific sources to quiz on
    from_highlights: bool = False  # Generate from user highlights only
    chat_context: Optional[str] = None  # Recent chat conversation for "From Chat" mode


class QuizQuestionResponse(BaseModel):
    id: str
    question: str
    answer: str
    explanation: str
    difficulty: str
    question_type: str
    options: Optional[List[str]] = None
    source_reference: Optional[str] = None
    visual_svg: Optional[str] = None  # sanitized inline SVG for visual_diagram questions
    visual_labels: Optional[Dict[str, Any]] = None


class QuizResponse(BaseModel):
    quiz_id: str
    notebook_id: str
    topic: str
    questions: List[QuizQuestionResponse]
    generated_at: str
    source_summary: str


class AnswerSubmission(BaseModel):
    quiz_id: str
    question_id: str
    user_answer: str
    time_taken_seconds: float = 0


class AnswerResult(BaseModel):
    correct: bool
    correct_answer: str
    explanation: str
    next_review: Optional[str] = None  # ISO datetime for next review (FSRS)


class ReviewCard(BaseModel):
    """A card due for review based on FSRS scheduling."""
    card_id: str
    question: str
    answer: str
    due_date: str
    difficulty: float  # FSRS difficulty rating
    stability: float  # FSRS stability
    reps: int  # Number of times reviewed


class FSRSRating(BaseModel):
    """User's rating after reviewing a card."""
    card_id: str
    rating: int = Field(ge=1, le=4, description="1=Again, 2=Hard, 3=Good, 4=Easy")


class MissedQuestion(BaseModel):
    question: str
    correct_answer: str
    user_answer: str
    explanation: str = ""


class GapAnalysisRequest(BaseModel):
    notebook_id: str
    missed_questions: List[MissedQuestion]
    quiz_topic: Optional[str] = None


class KnowledgeGap(BaseModel):
    gap_title: str = Field(description="Short title for the knowledge gap")
    description: str = Field(description="What the user doesn't understand yet")
    study_suggestion: str = Field(description="Specific suggestion for what to study")
    suggested_topic: str = Field(description="Topic string to pre-fill in Studio for targeted content generation")


class GapAnalysisResponse(BaseModel):
    gaps: List[KnowledgeGap]
    summary: str = Field(description="Brief overall assessment")
    score_percent: int


# =============================================================================
# Quiz Storage (JSON-based for simplicity)
# =============================================================================

def _get_quiz_dir() -> Path:
    quiz_dir = Path(settings.data_dir) / "quizzes"
    quiz_dir.mkdir(parents=True, exist_ok=True)
    return quiz_dir


def _get_cards_path(notebook_id: str) -> Path:
    return _get_quiz_dir() / f"{notebook_id}_cards.json"


def _load_cards(notebook_id: str) -> Dict[str, Any]:
    path = _get_cards_path(notebook_id)
    if path.exists():
        return json.loads(path.read_text())
    return {"cards": {}, "reviews": []}


def _save_cards(notebook_id: str, data: Dict[str, Any]):
    path = _get_cards_path(notebook_id)
    path.write_text(json.dumps(data, indent=2, default=str))


# =============================================================================
# FSRS Algorithm Implementation (Simplified)
# =============================================================================

# FSRS Parameters (default values from the algorithm)
FSRS_W = [0.4, 0.6, 2.4, 5.8, 4.93, 0.94, 0.86, 0.01, 1.49, 0.14, 0.94, 2.18, 0.05, 0.34, 1.26, 0.29, 2.61]


def fsrs_initial_difficulty(rating: int) -> float:
    """Calculate initial difficulty based on first rating."""
    return max(1, min(10, FSRS_W[4] - (rating - 3) * FSRS_W[5]))


def fsrs_initial_stability(rating: int) -> float:
    """Calculate initial stability based on first rating."""
    return max(0.1, FSRS_W[rating - 1])


def fsrs_next_difficulty(d: float, rating: int) -> float:
    """Update difficulty after a review."""
    delta = (rating - 3) * FSRS_W[6]
    return max(1, min(10, d - delta))


def fsrs_next_stability(s: float, d: float, rating: int, reps: int) -> float:
    """Update stability after a review."""
    if rating == 1:  # Again - reset stability
        return max(0.1, s * FSRS_W[11])
    
    # Calculate new stability based on current stability, difficulty, and rating
    hard_penalty = FSRS_W[15] if rating == 2 else 1
    easy_bonus = FSRS_W[16] if rating == 4 else 1
    
    new_s = s * (1 + FSRS_W[8] * (11 - d) * (s ** -FSRS_W[9]) * (hard_penalty * easy_bonus - 1))
    return max(0.1, new_s)


def fsrs_next_interval(stability: float) -> int:
    """Calculate days until next review based on stability."""
    # Target retrievability of 90%
    return max(1, round(stability * 0.9))


# =============================================================================
# API Endpoints
# =============================================================================

@router.post("/generate", response_model=QuizResponse)
async def generate_quiz(request: GenerateQuizRequest):
    """Generate a quiz from notebook content."""
    try:
        logger.info(f"[STUDIO] Quiz generation started for notebook={request.notebook_id}, questions={request.num_questions}")
        log_quiz_completed(request.notebook_id, request.topic or "", request.difficulty or "medium", total=request.num_questions)
        
        # Build context via the RAG context builder (chunk-level precision, topic-aware)
        from services.context_builder import context_builder
        built = await context_builder.build_context(
            notebook_id=request.notebook_id,
            skill_id="quiz",
            topic=request.topic,
            source_ids=request.source_ids,
        )
        content = built.context

        if not content.strip():
            raise HTTPException(status_code=400, detail="No content available to generate quiz")
        
        # Add topic focus header if provided
        if request.topic:
            content = f"FOCUS TOPIC: {request.topic}\nGenerate questions specifically about this topic.\n\n{content}"
        
        # Inject chat context if provided ("From Chat" mode)
        if request.chat_context:
            content = f"""The user has been learning about this topic in a chat conversation. Focus quiz questions on what they discussed:

--- RECENT CHAT ---
{request.chat_context[:3000]}
--- END CHAT ---

{content}"""
        
        # Explicit opt-in — the Studio "Include diagrams" toggle passes
        # visual_diagram in question_types — should COMPEL at least one diagram,
        # unlike the passive keyword auto-detect which leaves it optional.
        require_visuals = bool(request.question_types and 'visual_diagram' in request.question_types)

        # Generate quiz using structured LLM
        quiz_output = await structured_llm.generate_quiz(
            content=content,
            num_questions=request.num_questions,
            difficulty=request.difficulty,
            question_types=request.question_types,
            source_names=built.source_names,
            require_visuals=require_visuals,
        )
        _vis_n = sum(1 for q in quiz_output.questions if q.question_type == 'visual_diagram')
        logger.info(f"[Quiz] {len(quiz_output.questions)} questions returned, {_vis_n} visual_diagram (require_visuals={require_visuals})")

        if not quiz_output.questions:
            raise HTTPException(
                status_code=503,
                detail="Quiz generation produced no valid questions. The source material may be too short or off-topic. Try adding more sources or changing the topic."
            )

        # Explicit visuals opt-in: gemma can't reliably author SVG, so compose ONE
        # diagram via the proven visual pipeline (Klein / SVG templates + critic)
        # and attach it to a question that lacks one, as a supporting illustration.
        # Best-effort + bounded — a failure or a Mermaid-only result never blocks
        # the quiz. Only runs when the user opted in (adds ~1-3 min).
        if require_visuals:
            try:
                from services.visual_composer import VisualComposer
                composed = await VisualComposer().compose(
                    content=content[:4000],
                    topic=quiz_output.topic or request.topic or "",
                )
                if composed and composed.success and composed.svg_markup:
                    target = next((q for q in quiz_output.questions if not q.visual_svg), None)
                    if target is not None:
                        target.visual_svg = composed.svg_markup
                        logger.info(f"[Quiz] attached visual_composer diagram (path={getattr(composed, 'path', '?')})")
                else:
                    logger.info(
                        f"[Quiz] visual_composer produced no usable SVG "
                        f"(success={getattr(composed, 'success', None)}, "
                        f"mermaid_only={bool(getattr(composed, 'mermaid_code', None))})"
                    )
            except Exception as e:
                logger.warning(f"[Quiz] visual_composer diagram failed: {e}")

        # Create quiz response
        import uuid
        quiz_id = str(uuid.uuid4())[:8]
        
        questions = []
        for i, q in enumerate(quiz_output.questions):
            questions.append(QuizQuestionResponse(
                id=f"{quiz_id}_q{i}",
                question=q.question,
                answer=q.answer,
                explanation=q.explanation,
                difficulty=q.difficulty,
                question_type=q.question_type,
                options=q.options,
                source_reference=q.source_reference,
                # Sanitize model-authored inline SVG once, server-side, before it
                # persists + reaches any renderer (canonical XSS gate).
                visual_svg=sanitize_svg(q.visual_svg or ""),
                visual_labels=(q.visual_labels.model_dump() if q.visual_labels else None),
            ))
        
        # Save questions as cards for spaced repetition
        cards_data = _load_cards(request.notebook_id)
        for q in questions:
            cards_data["cards"][q.id] = {
                "question": q.question,
                "answer": q.answer,
                "explanation": q.explanation,
                "difficulty": 5.0,  # Initial FSRS difficulty
                "stability": 0.0,  # Will be set on first review
                "reps": 0,
                "due": datetime.utcnow().isoformat(),
                "created": datetime.utcnow().isoformat()
            }
        _save_cards(request.notebook_id, cards_data)

        # Tier 5 (2026-06-02): persist the quiz so it appears in Library.
        # The spaced-repetition card store above is separate (per-question
        # scheduling) — this row preserves the original quiz as a unit so
        # the user can revisit / download / delete the whole quiz later.
        try:
            from storage.quiz_store import quiz_store
            saved_quiz = await quiz_store.create(
                notebook_id=request.notebook_id,
                topic=quiz_output.topic or request.topic or "",
                difficulty=request.difficulty or "medium",
                num_questions=len(questions),
                questions=[q.model_dump() for q in questions],
                source_summary=quiz_output.source_summary,
                sources_used=built.sources_used,
            )
            # Use the persisted ID so the response matches what shows up in Library.
            if saved_quiz.get("quiz_id"):
                quiz_id = saved_quiz["quiz_id"]
                # Re-attach the new ID to question.id (which was a short prefix earlier).
                for i, q in enumerate(questions):
                    q.id = f"{quiz_id}_q{i}"
        except Exception as _persist_err:
            # Non-fatal — the user still gets the quiz inline, it just won't be in Library.
            logger.warning(f"[STUDIO] quiz_store.create failed (non-fatal): {_persist_err}")

        logger.info(f"[STUDIO] Quiz generated successfully: {len(questions)} questions")
        return QuizResponse(
            quiz_id=quiz_id,
            notebook_id=request.notebook_id,
            topic=quiz_output.topic,
            questions=questions,
            generated_at=datetime.utcnow().isoformat(),
            source_summary=quiz_output.source_summary
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[STUDIO] Quiz generation failed for notebook={request.notebook_id}")
        logger.error(f"[STUDIO] Error: {type(e).__name__}: {str(e)}")
        logger.error(f"[STUDIO] Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Quiz generation failed: {str(e)}")


@router.post("/answer", response_model=AnswerResult)
async def submit_answer(submission: AnswerSubmission):
    """Submit an answer and get feedback."""
    
    # Find the card
    submission.quiz_id.split("_")[0] if "_" in submission.question_id else None
    
    # For now, do simple string matching
    # In production, could use LLM to evaluate answer similarity
    
    return AnswerResult(
        correct=True,  # Placeholder - needs actual answer checking
        correct_answer="",
        explanation="Answer submitted. Use the review endpoint to see if you got it right.",
        next_review=None
    )


@router.get("/due/{notebook_id}", response_model=List[ReviewCard])
async def get_due_cards(notebook_id: str, limit: int = Query(default=20, le=100)):
    """Get cards due for review based on FSRS scheduling."""
    
    cards_data = _load_cards(notebook_id)
    now = datetime.utcnow()
    
    due_cards = []
    for card_id, card in cards_data.get("cards", {}).items():
        due_date = datetime.fromisoformat(card.get("due", now.isoformat()))
        if due_date <= now:
            due_cards.append(ReviewCard(
                card_id=card_id,
                question=card["question"],
                answer=card["answer"],
                due_date=due_date.isoformat(),
                difficulty=card.get("difficulty", 5.0),
                stability=card.get("stability", 0.0),
                reps=card.get("reps", 0)
            ))
    
    # Sort by due date (most overdue first)
    due_cards.sort(key=lambda c: c.due_date)
    
    return due_cards[:limit]


@router.post("/review")
async def review_card(rating: FSRSRating):
    """Submit a review rating for a card (FSRS algorithm)."""
    
    # Extract notebook_id from card_id (format: quizid_qN)
    # We need to search all notebooks for this card
    quiz_dir = _get_quiz_dir()
    
    for cards_file in quiz_dir.glob("*_cards.json"):
        notebook_id = cards_file.stem.replace("_cards", "")
        cards_data = _load_cards(notebook_id)
        
        if rating.card_id in cards_data.get("cards", {}):
            card = cards_data["cards"][rating.card_id]
            
            reps = card.get("reps", 0)
            d = card.get("difficulty", 5.0)
            s = card.get("stability", 0.0)
            
            if reps == 0:
                # First review
                d = fsrs_initial_difficulty(rating.rating)
                s = fsrs_initial_stability(rating.rating)
            else:
                # Subsequent reviews
                d = fsrs_next_difficulty(d, rating.rating)
                s = fsrs_next_stability(s, d, rating.rating, reps)
            
            # Calculate next review date
            interval = fsrs_next_interval(s)
            next_due = datetime.utcnow() + timedelta(days=interval)
            
            # Update card
            cards_data["cards"][rating.card_id].update({
                "difficulty": d,
                "stability": s,
                "reps": reps + 1,
                "due": next_due.isoformat(),
                "last_review": datetime.utcnow().isoformat()
            })
            
            # Record review
            cards_data["reviews"].append({
                "card_id": rating.card_id,
                "rating": rating.rating,
                "timestamp": datetime.utcnow().isoformat()
            })
            
            _save_cards(notebook_id, cards_data)
            
            return {
                "success": True,
                "next_review": next_due.isoformat(),
                "interval_days": interval,
                "new_difficulty": d,
                "new_stability": s
            }
    
    raise HTTPException(status_code=404, detail="Card not found")


@router.get("/stats/{notebook_id}")
async def get_quiz_stats(notebook_id: str):
    """Get quiz and review statistics for a notebook."""
    
    cards_data = _load_cards(notebook_id)
    now = datetime.utcnow()
    
    total_cards = len(cards_data.get("cards", {}))
    due_count = 0
    reviewed_count = 0
    
    for card in cards_data.get("cards", {}).values():
        if card.get("reps", 0) > 0:
            reviewed_count += 1
        due_date = datetime.fromisoformat(card.get("due", now.isoformat()))
        if due_date <= now:
            due_count += 1
    
    return {
        "notebook_id": notebook_id,
        "total_cards": total_cards,
        "cards_reviewed": reviewed_count,
        "cards_due": due_count,
        "total_reviews": len(cards_data.get("reviews", []))
    }


@router.post("/gap-analysis", response_model=GapAnalysisResponse)
async def analyze_knowledge_gaps(request: GapAnalysisRequest):
    """Analyze missed quiz questions to identify knowledge gaps and suggest targeted study.
    
    Takes the questions a user got wrong, uses LLM to group them into
    knowledge gap themes, and returns suggestions for Studio content generation
    to address each gap.
    """
    if not request.missed_questions:
        return GapAnalysisResponse(gaps=[], summary="Perfect score — no gaps detected!", score_percent=100)
    
    # Build prompt with missed questions
    missed_text = ""
    for i, mq in enumerate(request.missed_questions, 1):
        missed_text += f"\n{i}. Question: {mq.question}\n"
        missed_text += f"   Correct answer: {mq.correct_answer}\n"
        missed_text += f"   User answered: {mq.user_answer}\n"
        if mq.explanation:
            missed_text += f"   Why: {mq.explanation}\n"
    
    topic_context = f" on the topic of '{request.quiz_topic}'" if request.quiz_topic else ""
    
    prompt = f"""A user just took a quiz{topic_context} and got these questions wrong:
{missed_text}

Analyze the missed questions and identify the KNOWLEDGE GAPS — the underlying concepts or areas the user doesn't fully understand yet. Group related misses into gap themes (1-3 gaps max).

For each gap, provide:
1. A short title (e.g., "Supply Chain Economics" or "Neural Network Architectures")
2. A description of what the user is missing (1-2 sentences)
3. A specific study suggestion (what to read/review)
4. A suggested_topic string that could be used to generate a focused study document or podcast (keep it specific and actionable, e.g., "Supply chain cost structures and pricing models" not just "supply chains")

Also provide a brief overall summary (1 sentence) of the user's understanding level.

Respond in valid JSON with this exact structure:
{{
  "gaps": [
    {{
      "gap_title": "...",
      "description": "...",
      "study_suggestion": "...",
      "suggested_topic": "..."
    }}
  ],
  "summary": "..."
}}"""

    try:
        result = await structured_llm._call_ollama_json(
            system_prompt="You are a learning assessment expert. Identify knowledge gaps from quiz results and suggest targeted study areas. Always respond in valid JSON.",
            user_prompt=prompt,
        )
        
        if not result or "gaps" not in result:
            # Fallback: create a single gap from the quiz topic
            fallback_topic = request.quiz_topic or "the topics covered in this quiz"
            return GapAnalysisResponse(
                gaps=[KnowledgeGap(
                    gap_title=f"Review: {fallback_topic}",
                    description=f"You missed {len(request.missed_questions)} question(s). A focused review would help solidify your understanding.",
                    study_suggestion=f"Generate a study document or podcast focused on {fallback_topic}.",
                    suggested_topic=fallback_topic,
                )],
                summary=f"Review recommended on {fallback_topic}.",
                score_percent=0,
            )
        
        gaps = []
        for g in result.get("gaps", [])[:3]:
            gaps.append(KnowledgeGap(
                gap_title=g.get("gap_title", "Knowledge Gap"),
                description=g.get("description", ""),
                study_suggestion=g.get("study_suggestion", ""),
                suggested_topic=g.get("suggested_topic", request.quiz_topic or ""),
            ))
        
        return GapAnalysisResponse(
            gaps=gaps,
            summary=result.get("summary", ""),
            score_percent=0,  # Frontend will fill this from its own score calc
        )
    
    except Exception as e:
        logger.error(f"Gap analysis failed: {e}")
        logger.error(traceback.format_exc())
        fallback_topic = request.quiz_topic or "the quiz topics"
        return GapAnalysisResponse(
            gaps=[KnowledgeGap(
                gap_title=f"Review: {fallback_topic}",
                description=f"You missed {len(request.missed_questions)} question(s).",
                study_suggestion=f"Try generating a focused document on {fallback_topic}.",
                suggested_topic=fallback_topic,
            )],
            summary=f"Gap analysis unavailable — review {fallback_topic}.",
            score_percent=0,
        )


class GradeAnswerRequest(BaseModel):
    question: str
    correct_answer: str
    user_answer: str
    question_type: str = "short_answer"


class GradeAnswerResponse(BaseModel):
    correct: bool
    score: float = Field(description="0.0 to 1.0 — partial credit for short_answer")
    feedback: str


class InteractiveQuizRequest(BaseModel):
    """Phase 11 — wrap an already-generated quiz as a sandboxed interactive
    HTML page. Accepts either a quiz_id (looked up server-side) or the raw
    questions list (frontend already has the data and avoids a round-trip).
    """
    questions: Optional[List[Dict[str, Any]]] = None
    title: Optional[str] = None


@router.post("/interactive-html")
async def quiz_interactive_html(request: InteractiveQuizRequest):
    """Compose a self-contained interactive HTML page from a quiz."""
    from services.interactive_quiz_renderer import quiz_to_interactive_html
    questions = request.questions or []
    if not questions:
        raise HTTPException(status_code=400, detail="No questions supplied")
    html = quiz_to_interactive_html(questions=questions, title=request.title)
    return {"html": html}


@router.post("/grade", response_model=GradeAnswerResponse)
async def grade_open_ended_answer(request: GradeAnswerRequest):
    """LLM-grade an open-ended answer (short_answer, spot_the_error, fill_in_the_blank).
    
    Uses the fast model for low latency. Returns a correctness score 0-1 and
    brief feedback so the frontend can show partial credit.
    """
    if not request.user_answer.strip():
        return GradeAnswerResponse(correct=False, score=0.0, feedback="No answer provided.")

    prompt = f"""You are grading a quiz answer. Be fair but accurate.

Question: {request.question}
Expected answer: {request.correct_answer}
Student answer: {request.user_answer}

Grade the student answer:
- Is it essentially correct? (captures the key idea, even if worded differently)
- Give a score from 0.0 (completely wrong) to 1.0 (fully correct). Partial credit (0.3-0.7) for partially correct answers.
- Give 1 sentence of feedback.

Respond in JSON:
{{"correct": true/false, "score": 0.0-1.0, "feedback": "one sentence"}}"""

    try:
        result = await structured_llm._call_ollama_json(
            system_prompt="You are a fair quiz grader. Respond only with valid JSON.",
            user_prompt=prompt,
            temperature=0.2,
            timeout_seconds=30.0,
        )
        score = float(result.get("score", 0.0))
        return GradeAnswerResponse(
            correct=bool(result.get("correct", score >= 0.7)),
            score=min(1.0, max(0.0, score)),
            feedback=str(result.get("feedback", "")),
        )
    except Exception as e:
        logger.warning(f"[Quiz] LLM grading failed, falling back to string match: {e}")
        clean_user = request.user_answer.lower().strip()
        clean_correct = request.correct_answer.lower().strip()
        is_correct = clean_user == clean_correct or clean_correct in clean_user
        return GradeAnswerResponse(
            correct=is_correct,
            score=1.0 if is_correct else 0.0,
            feedback="Correct!" if is_correct else f"Expected: {request.correct_answer}",
        )


# ─── Library endpoints (Tier 5, 2026-06-02) ──────────────────────────────────

@router.get("/list/{notebook_id}")
async def list_quizzes(notebook_id: str):
    """List persisted quizzes for a notebook (newest first). Used by Library."""
    from storage.quiz_store import quiz_store
    return await quiz_store.list(notebook_id)


@router.get("/{quiz_id}")
async def get_quiz(quiz_id: str):
    """Fetch a single persisted quiz by ID."""
    from storage.quiz_store import quiz_store
    quiz = await quiz_store.get(quiz_id)
    if not quiz:
        raise HTTPException(status_code=404, detail="Quiz not found")
    return quiz


@router.delete("/{quiz_id}")
async def delete_quiz(quiz_id: str):
    """Delete a persisted quiz row (Library trash action)."""
    from storage.quiz_store import quiz_store
    ok = await quiz_store.delete(quiz_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Quiz not found")
    return {"deleted": True, "quiz_id": quiz_id}


@router.get("/{quiz_id}/download")
async def download_quiz(quiz_id: str):
    """Download the quiz as a markdown file — questions, options, answers,
    explanations. Format the user picked: Markdown (Tier 5, 2026-06-02)."""
    from fastapi.responses import Response
    from storage.quiz_store import quiz_store

    quiz = await quiz_store.get(quiz_id)
    if not quiz:
        raise HTTPException(status_code=404, detail="Quiz not found")

    lines = []
    title = quiz.get("topic") or "Quiz"
    lines.append(f"# {title}")
    lines.append("")
    diff = quiz.get("difficulty") or "medium"
    n = quiz.get("num_questions", 0)
    lines.append(f"*Difficulty: {diff}  ·  {n} questions  ·  generated {quiz.get('created_at', '')}*")
    lines.append("")
    if quiz.get("source_summary"):
        lines.append(f"**Source summary:** {quiz['source_summary']}")
        lines.append("")
    lines.append("---")
    lines.append("")

    for i, q in enumerate(quiz.get("questions", []), start=1):
        lines.append(f"## Question {i}")
        lines.append("")
        lines.append(q.get("question", ""))
        lines.append("")
        opts = q.get("options")
        if opts:
            for j, opt in enumerate(opts):
                marker = chr(ord("A") + j)
                lines.append(f"- **{marker}.** {opt}")
            lines.append("")
        lines.append(f"**Answer:** {q.get('answer', '')}")
        if q.get("explanation"):
            lines.append("")
            lines.append(f"**Why:** {q['explanation']}")
        if q.get("evidence_quote"):
            lines.append("")
            lines.append(f"> {q['evidence_quote']}")
        if q.get("source_reference"):
            lines.append("")
            lines.append(f"*Source: {q['source_reference']}*")
        lines.append("")
        lines.append("---")
        lines.append("")

    md = "\n".join(lines)
    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title).strip()[:60] or "quiz"
    filename = f"{safe_title}.md"
    return Response(
        content=md,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
