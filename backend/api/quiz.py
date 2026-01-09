"""Quiz API endpoints for spaced repetition learning

Generates quizzes from notebook content using structured LLM outputs.
Tracks user performance with FSRS (Free Spaced Repetition Scheduler).
"""
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
import json
from pathlib import Path

from config import settings
from services.structured_llm import structured_llm, QuizQuestion
from storage.source_store import source_store


router = APIRouter(prefix="/quiz", tags=["quiz"])


# =============================================================================
# Request/Response Models
# =============================================================================

class GenerateQuizRequest(BaseModel):
    notebook_id: str
    num_questions: int = Field(default=5, ge=1, le=20)
    difficulty: str = Field(default="medium", pattern="^(easy|medium|hard)$")
    topic: Optional[str] = None  # Focus quiz on a specific topic
    question_types: Optional[List[str]] = None
    source_ids: Optional[List[str]] = None  # Specific sources to quiz on
    from_highlights: bool = False  # Generate from user highlights only


class QuizQuestionResponse(BaseModel):
    id: str
    question: str
    answer: str
    explanation: str
    difficulty: str
    question_type: str
    options: Optional[List[str]] = None
    source_reference: Optional[str] = None


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
    
    # Get sources for the notebook
    sources = await source_store.list(request.notebook_id)
    if not sources:
        raise HTTPException(status_code=404, detail="No sources found in notebook")
    
    # Filter by specific source IDs if provided
    if request.source_ids:
        sources = [s for s in sources if s.get("id") in request.source_ids]
    
    # Collect content with source names for reference
    source_names = [s.get("filename", s.get("title", "Unknown")) for s in sources[:5]]
    if request.from_highlights:
        # TODO: Get highlighted content from highlights API
        content = "\n\n".join([f"[Source: {source_names[i]}]\n{s.get('content', '')[:2000]}" for i, s in enumerate(sources[:5])])
    else:
        content = "\n\n".join([f"[Source: {source_names[i]}]\n{s.get('content', '')[:2000]}" for i, s in enumerate(sources[:5])])
    
    if not content.strip():
        raise HTTPException(status_code=400, detail="No content available to generate quiz")
    
    # Add topic focus if provided
    if request.topic:
        content = f"FOCUS TOPIC: {request.topic}\nGenerate questions specifically about this topic.\n\n{content}"
    
    # Generate quiz using structured LLM
    quiz_output = await structured_llm.generate_quiz(
        content=content,
        num_questions=request.num_questions,
        difficulty=request.difficulty,
        question_types=request.question_types
    )
    
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
            source_reference=q.source_reference
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
    
    return QuizResponse(
        quiz_id=quiz_id,
        notebook_id=request.notebook_id,
        topic=quiz_output.topic,
        questions=questions,
        generated_at=datetime.utcnow().isoformat(),
        source_summary=quiz_output.source_summary
    )


@router.post("/answer", response_model=AnswerResult)
async def submit_answer(submission: AnswerSubmission):
    """Submit an answer and get feedback."""
    
    # Find the card
    notebook_id = submission.quiz_id.split("_")[0] if "_" in submission.question_id else None
    
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
