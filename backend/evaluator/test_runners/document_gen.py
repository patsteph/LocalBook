"""Document generation test runner — tests content generation via real API."""

import time
from datetime import datetime
from evaluator.models import EvalResult
from evaluator import scoring


async def run(notebook_id: str, config: dict, combo_name: str, hw_fingerprint: str) -> list[EvalResult]:
    """Generate an executive brief and evaluate quality."""
    from services.context_builder import context_builder
    from services.output_templates import build_document_prompt, DOCUMENT_TEMPLATES
    from services.rag_engine import rag_engine
    from config import settings

    gen_config = config["content_generation"]
    result = EvalResult(
        test_id="document_gen_brief",
        category="document_gen",
        test_name="Executive Brief Generation",
        model_combo=combo_name,
        model_used=settings.ollama_model,
        hardware_fingerprint=hw_fingerprint,
        timestamp=datetime.utcnow().isoformat(),
    )

    try:
        start = time.time()

        # Build context from test notebook
        built = await context_builder.build_context(
            notebook_id=notebook_id,
            skill_id=gen_config["skill_id"],
            topic=gen_config["topic"],
        )

        if built.sources_used == 0:
            raise ValueError("No sources found in test notebook for context building")

        # Build prompt
        skill_id = gen_config["skill_id"]
        topic = gen_config["topic"]

        if skill_id in DOCUMENT_TEMPLATES:
            system_prompt, format_instructions = build_document_prompt(
                skill_id, topic, "professional", built.sources_used
            )
        else:
            system_prompt = "You are a professional document writer."
            format_instructions = f"Write an executive brief about: {topic}"

        user_prompt = f"""{format_instructions}

Source material:
{built.context[:6000]}"""

        # Generate content
        content = await rag_engine._call_ollama(
            system_prompt,
            user_prompt,
            model=settings.ollama_model,
            num_predict=2000,
            temperature=0.6,
        )

        elapsed = (time.time() - start) * 1000
        result.total_time_ms = elapsed
        result.output_chars = len(content)
        result.input_chars = len(user_prompt)
        result.actual_output_preview = content[:500]

        # Score
        word_count = len(content.split())
        heading_score = scoring.score_has_headings(content, min_headings=1)
        length_score = scoring.score_output_length(
            content,
            min_words=gen_config.get("expected_min_words", 200),
            max_words=gen_config.get("expected_max_words", 2000),
        )
        # Check that content references source material
        source_ref_score = scoring.score_must_contain(
            content, ["rag", "model", "embed"], case_insensitive=True
        )
        speed_score = 100 if elapsed < 60000 else max(0, int(100 - (elapsed - 60000) / 1000))

        result.format_score = heading_score
        result.accuracy_score = source_ref_score
        result.completeness_score = length_score
        result.overall_score = int(
            heading_score * 0.25 + length_score * 0.20 + source_ref_score * 0.30 + speed_score * 0.25
        )
        result.passed = result.overall_score >= 40

        print(f"[EVAL-DOCGEN] Score={result.overall_score}, {word_count} words, {elapsed:.0f}ms")

    except Exception as e:
        result.passed = False
        result.failure_reason = str(e)[:200]
        result.overall_score = 0
        print(f"[EVAL-DOCGEN] FAILED: {e}")

    return [result]
