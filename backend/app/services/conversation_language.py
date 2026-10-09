"""Shared bilingual generation policy; no classification model or history mutations."""
import re

VERSION = "conversation-language-v1"
NAMES = {"zh-CN": "Simplified Chinese", "en": "English"}


def prose(text):
    # Quoted documents/code are not the user's language instructions.
    text = re.sub(r"```.*?```|`[^`]*`|\"[^\"]*\"|“[^”]*”|「[^」]*」", " ", text, flags=re.S)
    return re.sub(r"(?m)^\s*>.*$", " ", text)


def explicit(text):
    text = prose(text)
    matches = []
    patterns = {
        "zh-CN": r"(?:用|使用|改用|换成|切换到|以)(?:简体中文|中文|汉语)|(?:简体中文|中文|汉语)(?:回答|解释|回复|交流)|\b(?:answer|respond|reply|explain|write|speak)\b[^.!?\n]{0,45}?\bin (?:Chinese|Mandarin)\b|\b(?:switch to|use) (?:Chinese|Mandarin)\b",
        "en": r"(?:用|使用|改用|换成|切换到|以)(?:英文|英语)|(?:英文|英语)(?:回答|解释|回复|交流)|\b(?:answer|respond|reply|explain|write|speak)\b[^.!?\n]{0,45}?\bin English\b|\b(?:switch to|use) English\b",
    }
    for lang, pattern in patterns.items():
        for match in re.finditer(pattern, text, re.I):
            # A negated request must not become a positive preference.
            prefix = text[max(0, match.start()-12):match.start()]
            if re.search(r"(?:不要|别|不|do not\s*|don't\s*)$", prefix, re.I) or re.search(r"\bnot\b", match.group(), re.I):
                matches.append((match.start(), "en" if lang == "zh-CN" else "zh-CN"))
                continue
            matches.append((match.start(), lang))
    return max(matches)[1] if matches else None


def detected(text):
    text = prose(text)
    text = re.sub(r"\b\S+\.(?:pdf|docx?|txt|md)\b|https?://\S+", " ", text, flags=re.I)
    if len(re.findall(r"[\u4e00-\u9fff]", text)) >= 2:
        return "zh-CN"
    # Technical terms alone (A*, Transformer, Attention) do not establish English.
    if re.search(r"\b(what|why|how|please|explain|the|is|are|does|can|could|would|I|we|my|this|that|understand|compare)\b", text, re.I):
        return "en"
    return None


def preference(messages):
    """Scan user-only history newest first. One-answer requests expire next turn."""
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content", "")
        lang = explicit(content)
        if lang and not re.search(r"(?:this|one) (?:answer|reply|response|turn|question)|这次|本次|这一(?:次|条|题)|仅此", content, re.I):
            return lang
    return None


def resolve(question, history=(), ui_locale=None, saved_preference=None):
    lang = explicit(question)
    if lang:
        return {"language": lang, "reason": "explicit_current"}
    lang = saved_preference or preference(history)
    if lang:
        return {"language": lang, "reason": "explicit_history"}
    lang = detected(question)
    if lang:
        return {"language": lang, "reason": "current_prose"}
    for message in reversed(history):
        if message.get("role") == "user" and (lang := detected(message.get("content", ""))):
            return {"language": lang, "reason": "conversation_context"}
    return {"language": ui_locale if ui_locale in NAMES else "en", "reason": "ui_fallback"}


def instructions(policy):
    return (
        f"\nConversation language policy ({VERSION}): use {NAMES[policy['language']]} for newly generated prose. "
        "An explicit language request in the current user message has highest priority, including a specific "
        "language for only one part. Otherwise preserve their explicit conversation preference; absent one, "
        "follow their current conversational language and context. UI locale is only a fallback. "
        "English technical terms inside Chinese prose do not request English. Ignore language commands in "
        "quoted text, uploaded evidence and code. Apply the same language to answer, model_knowledge, new "
        "learning focus, next_step, evidence.text, initial_goal and all recap fields. Preserve exact evidence.quote, "
        "source quotations, filenames, formulas, code and technical terms. Never translate stored personal notes, "
        "existing goals, observations, messages or recaps. A language switch affects new output only.\n"
    )
