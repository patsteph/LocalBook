# Flashcards Enhancement Summary

## Overview
Major improvements to the flashcards system focusing on card count accuracy, learning science best practices, and visual flashcard support.

## Changes Made

### 1. Card Count Accuracy (Critical Fix)
**Files:** `backend/services/structured_llm.py`

- **Problem:** User requested 10 cards, received 4
- **Solution:** 
  - Request 1.5x target count from LLM (e.g., 17 for 10 requested) to account for sanitization dropout
  - Added logic to trim to exact requested count after sanitization
  - Round-robin selection from different question types to maximize variety
  - Accept generation if we get at least 70% of target (previously would fail silently)
  - Increased token budget to support larger generation targets

### 2. Source Expansion for Adjacent Content
**Files:** `backend/services/context_builder.py`

- **Problem:** Limited source material leading to fewer cards
- **Solution:**
  - Added `expand_sources_for_flashcards()` method
  - Uses semantic similarity to find conceptually adjacent sources
  - Expands topic query to broader concepts ("concepts principles applications examples")
  - Embeddings-based scoring to find related but initially unranked sources

### 3. Learning Science Best Practices
**Files:** `backend/services/structured_llm.py`

Added to LLM prompt:
1. **Variety Principle:** Same concept in different formats (MC → fill_blank later)
2. **Spaced Repetition Pattern:** Interleave topics instead of clustering
3. **Dual Coding:** Visual + verbal processing for stronger memory
4. **Elaborative Interrogation:** Frame as "why/how" not just "what"
5. **Concrete Examples:** Specific instances over abstract definitions
6. **Test-Enhanced Learning:** Active recall, not passive recognition

### 4. Visual Flashcard Support (SVG Diagrams)
**Files:**
- `backend/services/structured_llm.py` - Added `visual_diagram` type
- `src/services/quiz.ts` - Added `visual_svg` and `visual_labels` fields
- `src/services/flashcards.ts` - Added to question type mix
- `src/components/chat/FlashcardsCanvasTile.tsx` - SVG rendering

- **New Question Type:** `visual_diagram`
- **Features:**
  - SVG diagrams with one label replaced by "???"
  - Labels shown/hidden tracking
  - Dual-coding for architecture/process flows
  - Supports RAG pipelines, system diagrams, workflows

### 5. Question Type Mix
**Files:** `src/services/flashcards.ts`

Previous: `['short_answer', 'multiple_choice', 'true_false', 'fill_in_the_blank']`
Current: Added `'visual_diagram'` to the mix

### 6. True/False Question Support
**Files:** `src/components/chat/FlashcardsCanvasTile.tsx`

- Already present: Renders as clickable True/False buttons
- Backend prompt updated to include in generation mix

## Technical Implementation

### Generation Flow
1. Request 1.5x target count from LLM
2. Apply deterministic sanitization
3. Round-robin select from question types to maximize variety
4. Trim to exact requested count
5. If <70% of target, retry with expanded sources

### Variety Algorithm
```python
# Group by type, then round-robin select
by_type = {mc: [...], tf: [...], fb: [...], vd: [...]}
selected = []
while len(selected) < num_questions:
    for type in question_types:
        if by_type[type]: selected.append(by_type[type].pop(0))
```

## Learning Science Research Applied

### Spaced Repetition
- Interleaving topics creates "desirable difficulty"
- Prevents massed practice (cramming) effect
- Strengthens memory consolidation

### Dual Coding Theory (Paivio, 1986)
- Visual + verbal = stronger traces than either alone
- Diagrams engage visual processing pathways
- Applied via `visual_diagram` question type

### Retrieval Practice
- Active recall > passive review
- Testing effect enhances retention
- Questions require specific answers, not recognition

### Elaborative Interrogation
- "Why" questions create deeper processing
- Causal reasoning strengthens connections
- Applied in prompt engineering

## Testing

```bash
# TypeScript
npx tsc --noEmit  # ✅ No errors

# Python
python3 -m py_compile backend/services/structured_llm.py  # ✅ OK
python3 -m py_compile backend/services/context_builder.py  # ✅ OK
```

## Future Enhancements (Planned)

1. **SVG Generation:** Auto-generate diagrams for architecture topics
2. **FSRS Integration:** Spaced repetition scheduling
3. **Progress Analytics:** Track learning curves per concept
4. **Adaptive Difficulty:** Adjust based on performance

## API Changes

### QuizQuestion (Frontend)
```typescript
interface QuizQuestion {
  // ... existing fields
  question_type: 'multiple_choice' | 'true_false' | 'short_answer' | 'fill_in_the_blank' | 'visual_diagram';
  visual_svg?: string;
  visual_labels?: {
    shown: string[];
    hidden: string[];
  };
}
```

### QuizQuestion (Backend)
```python
class QuizQuestion(BaseModel):
    # ... existing fields
    visual_svg: Optional[str] = None
    visual_labels: Optional[VisualLabels] = None
```

## Files Modified

1. `backend/services/structured_llm.py` - Core generation logic
2. `backend/services/context_builder.py` - Source expansion
3. `src/services/quiz.ts` - Type definitions
4. `src/services/flashcards.ts` - Type mix
5. `src/components/chat/FlashcardsCanvasTile.tsx` - UI rendering
