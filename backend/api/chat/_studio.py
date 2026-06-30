"""_stream_studio handler — extracted from api/chat.py (Wave 5 split)."""
from ._common import *  # noqa: F401,F403
from ._common import (
    _build_mental_model_block,
    _is_help_request,
    _stream_help,
    _dispatch_multi_intent,
    _quick_intent_for_correspondent,
    _CURATOR_HELP,
    _COLLECTOR_HELP,
    _RESEARCH_HELP,
    _STUDIO_HELP,
)

async def _stream_studio(chat_query: ChatQuery, injected_action: Optional[Dict[str, Any]] = None):
    """Stream a Studio agent response in SSE format.

    LLM-based intent router — lets the user create Studio content (audio,
    documents, quizzes, visuals, videos) directly from the chat, using the
    current conversation as context.
    """
    from services.ollama_service import ollama_service
    from services.intent_classifier import classify_intent
    from services.event_logger import log_content_generated

    q = chat_query.question
    notebook_id = chat_query.notebook_id
    chat_context = chat_query.chat_context or ""

    # ── Help shortcut ──
    if _is_help_request(q):
        for chunk in _stream_help(_STUDIO_HELP, "Studio", "studio"):
            yield chunk
        return

    yield f"data: {json.dumps({'type': 'status', 'message': 'Studio interpreting your request...', 'query_type': 'studio'})}\n\n"

    try:
        # ── Intent classification (bypassed if injected by dispatcher) ──
        if injected_action:
            classified = injected_action
        else:
            classified = await classify_intent(q, "studio")
        intent = classified["intent"]
        params = classified.get("params", {})
        topic = (params.get("topic") or "").strip() or None
        reply = ""
        follow_ups = ['Make a podcast on this', 'Create a study guide', 'Quiz me on this topic']

        # -----------------------------------------------------------------
        # GENERATE AUDIO (podcast, interview, etc.)
        # -----------------------------------------------------------------
        if intent == "generate_audio":
            yield f"data: {json.dumps({'type': 'status', 'message': 'Studio generating podcast...', 'query_type': 'studio'})}\n\n"
            try:
                from services.audio_generator import audio_service

                skill_id = (params.get("skill_id") or "podcast").strip()
                host1 = (params.get("host1_gender") or "male").strip().lower()
                host2 = (params.get("host2_gender") or "female").strip().lower()
                duration = int(params.get("duration_minutes", 10))
                if duration < 5: duration = 5
                if duration > 45: duration = 45

                result = await audio_service.generate(
                    notebook_id=notebook_id,
                    topic=topic or "the current discussion",
                    duration_minutes=duration,
                    skill_id=skill_id,
                    host1_gender=host1,
                    host2_gender=host2,
                    accent="us",
                    chat_context=chat_context,
                )
                audio_id = result.get("audio_id", "")
                status = result.get("status", "pending")
                log_content_generated(notebook_id, "audio", skill_id, topic or "chat-context")

                lines = [
                    f"**Podcast generation started!** 🎙️",
                    f"",
                    f"- **Style:** {skill_id.replace('_', ' ').title()}",
                    f"- **Duration:** ~{duration} min",
                    f"- **Hosts:** {host1.title()} & {host2.title()}",
                    f"- **Status:** {status}",
                    f"",
                    f"The podcast is being generated in the background. You'll find it in **Studio → Audio** when it's ready.",
                ]
                reply = "\n".join(lines)
                follow_ups = ['Create a study guide too', 'Make a quiz on this', 'Show me a visual']

            except Exception as ae:
                reply = f"Podcast generation failed: {ae}"

        # -----------------------------------------------------------------
        # GENERATE DOCUMENT (brief, guide, summary, etc.)
        # -----------------------------------------------------------------
        elif intent == "generate_document":
            yield f"data: {json.dumps({'type': 'status', 'message': 'Studio generating document...', 'query_type': 'studio'})}\n\n"
            try:
                from api.content import generate_content as _gen_content, ContentGenerateRequest

                skill_id = (params.get("skill_id") or "research_summary").strip()
                style = (params.get("style") or "professional").strip()

                result = await _gen_content(ContentGenerateRequest(
                    notebook_id=notebook_id,
                    skill_id=skill_id,
                    topic=topic,
                    style=style,
                    chat_context=chat_context,
                ))
                content = result.content
                skill_name = result.skill_name
                log_content_generated(notebook_id, "document", skill_id, topic or "chat-context")

                lines = [
                    f"**{skill_name} generated!** 📄",
                    f"",
                    f"---",
                    f"",
                    content[:3000] if len(content) > 3000 else content,
                ]
                if len(content) > 3000:
                    lines.append(f"\n\n*...truncated. Full document available in Studio → Documents.*")
                reply = "\n".join(lines)
                follow_ups = ['Make a podcast on this', 'Quiz me on this', 'Create a visual']

            except Exception as de:
                reply = f"Document generation failed: {de}"

        # -----------------------------------------------------------------
        # GENERATE QUIZ
        # -----------------------------------------------------------------
        elif intent == "generate_quiz":
            yield f"data: {json.dumps({'type': 'status', 'message': 'Studio generating quiz...', 'query_type': 'studio'})}\n\n"
            try:
                from api.quiz import generate_quiz as _gen_quiz, GenerateQuizRequest

                num_q = int(params.get("num_questions", 5))
                if num_q < 3: num_q = 3
                if num_q > 10: num_q = 10
                difficulty = (params.get("difficulty") or "medium").strip().lower()
                if difficulty not in ("easy", "medium", "hard"):
                    difficulty = "medium"

                result = await _gen_quiz(GenerateQuizRequest(
                    notebook_id=notebook_id,
                    num_questions=num_q,
                    difficulty=difficulty,
                    topic=topic,
                    chat_context=chat_context,
                ))
                questions = result.questions
                log_content_generated(notebook_id, "quiz", "quiz", topic or "chat-context")

                lines = [
                    f"**Quiz generated!** 🎯  ({len(questions)} questions, {difficulty})",
                    f"",
                    f"Head to **Studio → Quiz** to take it interactively, or preview below:",
                    f"",
                ]
                for i, q_item in enumerate(questions[:5]):
                    lines.append(f"**Q{i+1}.** {q_item.question}")
                    for opt in (q_item.options or []):
                        lines.append(f"  - {opt}")
                    lines.append("")
                if len(questions) > 5:
                    lines.append(f"*...plus {len(questions) - 5} more questions*")
                reply = "\n".join(lines)
                follow_ups = ['Make it harder', 'Create a study guide', 'Podcast on this topic']

            except Exception as qe:
                reply = f"Quiz generation failed: {qe}"

        # -----------------------------------------------------------------
        # GENERATE FLASH CARDS (reuses quiz generator, directed to Cards tab)
        # -----------------------------------------------------------------
        elif intent == "generate_flashcards":
            yield f"data: {json.dumps({'type': 'status', 'message': 'Studio generating flash cards...', 'query_type': 'studio'})}\n\n"
            try:
                from api.quiz import generate_quiz as _gen_quiz, GenerateQuizRequest

                # Flash Cards accept 3..50 (backend model is 1..50, we tighten here)
                num_cards = int(params.get("num_cards", params.get("num_questions", 10)))
                if num_cards < 3: num_cards = 3
                if num_cards > 50: num_cards = 50
                difficulty = (params.get("difficulty") or "medium").strip().lower()
                if difficulty not in ("easy", "medium", "hard"):
                    difficulty = "medium"

                # Flash-card-friendly mix: mostly short_answer (free recall) plus a
                # few multiple_choice for quick wins. No T/F (too easy to guess on
                # flashcards) and no spot_the_error (needs highlighted context).
                result = await _gen_quiz(GenerateQuizRequest(
                    notebook_id=notebook_id,
                    num_questions=num_cards,
                    difficulty=difficulty,
                    topic=topic,
                    chat_context=chat_context,
                    question_types=["short_answer", "multiple_choice", "fill_in_the_blank"],
                ))
                questions = result.questions
                log_content_generated(notebook_id, "flashcards", "flashcards", topic or "chat-context")

                lines = [
                    f"**Flash Cards ready!** 🧠  ({len(questions)} cards, {difficulty})",
                    f"",
                    f"An interactive deck has been dropped onto your canvas — answer by click, type, or voice. "
                    f"Your tutor will read feedback aloud when you miss one.",
                    f"",
                ]
                # Preview the first few card fronts
                for i, q_item in enumerate(questions[:3]):
                    lines.append(f"**Card {i+1}.** {q_item.question}")
                if len(questions) > 3:
                    lines.append(f"")
                    lines.append(f"*...plus {len(questions) - 3} more cards waiting.*")
                reply = "\n".join(lines)
                follow_ups = ['Make it harder', 'Give me fewer cards', 'Switch to a full quiz']

            except Exception as fe:
                reply = f"Flash card generation failed: {fe}"

        # -----------------------------------------------------------------
        # GENERATE VISUAL (diagram, chart, etc.)
        # -----------------------------------------------------------------
        elif intent == "generate_visual":
            yield f"data: {json.dumps({'type': 'status', 'message': 'Studio generating visual...', 'query_type': 'studio'})}\n\n"
            try:
                from api.visual import generate_visual_summary as _gen_visual, GenerateVisualRequest

                result = await _gen_visual(GenerateVisualRequest(
                    notebook_id=notebook_id,
                    diagram_types=["mindmap", "flowchart"],
                    focus_topic=topic or q,
                ))
                diagrams = result.diagrams
                log_content_generated(notebook_id, "visual", "visual", topic or "chat-context")

                if diagrams:
                    lines = []
                    for d in diagrams:
                        lines.append(f"**{d.title}** ({d.diagram_type}) 📊")
                        lines.append(f"")
                        lines.append(f"```mermaid")
                        lines.append(d.code)
                        lines.append(f"```")
                        lines.append(f"")
                    lines.append(f"*Open in Studio → Visual for an interactive view.*")
                    reply = "\n".join(lines)
                else:
                    reply = "Could not generate a visual from the current context. Try providing more specific content."
                follow_ups = ['Make a flowchart instead', 'Create a document', 'Make a podcast']

            except Exception as ve:
                reply = f"Visual generation failed: {ve}"

        # -----------------------------------------------------------------
        # GENERATE VIDEO
        # -----------------------------------------------------------------
        elif intent == "generate_video":
            yield f"data: {json.dumps({'type': 'status', 'message': 'Studio generating video...', 'query_type': 'studio'})}\n\n"
            try:
                from services.video_generator import video_generator

                duration = int(params.get("duration_minutes", 5))
                if duration < 1: duration = 1
                if duration > 10: duration = 10
                visual_style = (params.get("visual_style") or "classic").strip()
                narrator_gender = (params.get("narrator_gender") or "female").strip()
                accent = (params.get("accent") or "us").strip()

                result = await video_generator.generate(
                    notebook_id=notebook_id,
                    topic=topic or "the current discussion",
                    duration_minutes=duration,
                    visual_style=visual_style,
                    narrator_gender=narrator_gender,
                    accent=accent,
                    format_type="explainer",
                    chat_context=chat_context,
                )
                video_id = result.get("video_id", "")
                status = result.get("status", "pending")
                log_content_generated(notebook_id, "video", "explainer", topic or "chat-context")

                lines = [
                    f"**Video generation started!** 🎬",
                    f"",
                    f"- **Duration:** ~{duration} min",
                    f"- **Style:** {visual_style}",
                    f"- **Status:** {status}",
                    f"",
                    f"You'll find the video in **Studio → Video** when it's ready.",
                ]
                reply = "\n".join(lines)
                follow_ups = ['Create a podcast too', 'Make a study guide', 'Quiz me']

            except Exception as vie:
                reply = f"Video generation failed: {vie}"

        # -----------------------------------------------------------------
        # FALLBACK
        # -----------------------------------------------------------------
        else:
            reply = (
                "I'm not sure what type of content you'd like me to create. "
                "Try something like:\n\n"
                "- *\"Make a podcast on this topic\"*\n"
                "- *\"Create a study guide\"*\n"
                "- *\"Quiz me on what we discussed\"*\n"
                "- *\"Visualize this as a flowchart\"*\n"
                "- *\"Make a video explainer\"*\n\n"
                "Type **@studio ?** for full help."
            )

        # Stream the reply
        chunk_size = 12
        for i in range(0, len(reply), chunk_size):
            yield f"data: {json.dumps({'type': 'token', 'content': reply[i:i+chunk_size]})}\n\n"

        yield f"data: {json.dumps({'type': 'done', 'follow_up_questions': follow_ups, 'agent_name': 'Studio', 'agent_type': 'studio'})}\n\n"

        # Log interaction
        try:
            log_chat_qa(notebook_id, f"@studio {q}", reply[:500], [])
        except Exception as _e:
            logger.debug(f"[chat] log_chat_qa failed (non-fatal): {_e}")

    except Exception as e:
        import traceback
        traceback.print_exc()
        yield f"data: {json.dumps({'error': f'Studio error: {e}'})}\n\n"
