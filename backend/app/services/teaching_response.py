"""One authored lesson, shared by chat and notes; no extra model call or stock question."""
import logging
import re

from app.services.generation_protocol import GenerationError

INSTRUCTIONS = """
For this teaching response use the lesson contract instead of model_knowledge or learning.next_step.
Write the PRIMARY response ONCE as lesson: {mode, explanation, next_action}.
explanation is your learner-facing explanation, feedback or worked example (Markdown/code
allowed). next_action is your naturally phrased invitation, operation or prediction, not an
outline for the application to expand. Do not also put this invitation in explanation.
Normally mode continue: answer first, then write ONE nonempty next_action that builds on the
learner's attempt and this example. It may be a concrete operation, observation or prediction,
not necessarily a question. Explicit requests for an example/practice must actually include it.
Do not quiz them on the exact result already stated in your worked example; vary one boundary
or invite a useful observation. For a requested operation, explanation briefly explains why;
put its SQL/procedure and observation in next_action, not execution instructions in both fields.
Use answer_only when asked only to explain or not to be quizzed; if they explicitly request a
hands-on next step while forbidding questions, use continue with that requested operation.
Use pause when ending/stopping;
simplify when the learner needs a slower worked example; none for greetings/administration.
These modes have next_action null. Do not add opt-out acknowledgements or internal diagnostics.
Never classify an ordinary request to be guided as answer_only or none to avoid guidance.
For general: answer is empty, citations [], claims []; lesson is the entire response.
For hybrid: answer contains ONLY document-supported observations with citations and claims;
lesson contains the separate teaching response. Do not return model_knowledge. The application
will audit the assembled lesson as model_knowledge, including its next_action, before display.
Never say "the materials/manual say" in lesson, even with basis general. Statements about what
the documents prescribe belong ONLY in answer with their actual citations and literal quotes.
General explanations and recommended procedures may teach the concept without asserting what
the user's document says. A general basis does not exempt lesson from the safety audit.
For grounded/insufficient: lesson is null; do not bypass document evidence rules.
learning contains focus, evidence and initial_goal if requested, but NOT next_step: notes are
derived directly from the visible next_action, or cleared when no action is displayed.
evidence is null unless THIS user message contains an actual attempt, uncertainty, correction
or reflection. Otherwise use {kind:attempt|question|correction|reflection, text:a tentative
factual observation, quote:EXACT contiguous quote from THIS user message}. Requests alone are
not evidence of understanding. Never infer mastery or modify goals/personal notes.
Never treat a previous AI instruction as proof that the learner executed it. Acknowledge only
what THIS user actually reported; phrase other execution states as conditions, not confirmed
private facts. Do not add an isolation level/client setting to the user's reported observation.
"""


def compose_lesson(parsed):
    """Validate the primary response, then derive the note from its one authored action."""
    lesson = parsed.get("lesson")
    if parsed.get("basis") not in {"general", "hybrid"}:
        if lesson is not None:
            raise GenerationError("schema_validation")
        return "", "", "none"
    if not isinstance(lesson, dict) or set(lesson) != {"mode", "explanation", "next_action"}:
        raise GenerationError("schema_validation")
    mode, explanation, action = lesson["mode"], lesson["explanation"], lesson["next_action"]
    if not isinstance(mode, str) or mode not in {"continue", "answer_only", "pause", "simplify", "none"}:
        raise GenerationError("schema_validation")
    if not isinstance(explanation, str) or not explanation.strip():
        raise GenerationError("schema_validation")
    if mode == "continue":
        if not isinstance(action, str) or not action.strip():
            raise GenerationError("schema_validation")
        action = action.strip()
    elif action is not None:
        raise GenerationError("schema_validation")
    else:
        action = ""
    explanation = explanation.strip()
    primary = explanation + ("\n\n" + action if action else "")
    if len(primary) > 6000 or len(action) > 1000 or (action and action in explanation):
        raise GenerationError("schema_validation")
    if re.search(r"\[\d+\]", primary):
        raise GenerationError("citation_validation")
    return primary, action, mode


def visible_observation(parsed, answer, update):
    """Never persist an invitation withheld by citation safety or absent from the answer."""
    if update is None:
        return
    record = parsed.get("learning")
    if not isinstance(record, dict):
        update["value"] = None
        return
    record = dict(record)
    action = record.get("next_step", "")
    if not isinstance(action, str) or len(action) > 1000 or action.strip() not in answer:
        record["next_step"] = ""
    else:
        record["next_step"] = action.strip()
    update["value"] = record
    logging.getLogger(__name__).info("Teaching response: action=%s mode=%s",
        "visible" if record["next_step"] else "none", (parsed.get("lesson") or {}).get("mode", "legacy"))
