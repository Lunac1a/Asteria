"""Bounded generation contracts. Never log prompt or response content here."""
import json
import logging
import re
from fastapi import HTTPException


class GenerationError(RuntimeError):
    def __init__(self, category, message="Model generation failed", metadata=None):
        super().__init__(message)
        self.category = category
        self.metadata = metadata or {}


class GenerationText(str):
    def __new__(cls, text, metadata=None):
        value = super().__new__(cls, text)
        value.metadata = metadata or {}
        return value


def failure(error, operation="chat"):
    category = error.category
    logging.getLogger(__name__).warning("Generation rejected: operation=%s category=%s", operation, category)
    detail = ("Couldn't create your recap. Your conversation and learning notes are safe. Try again or keep learning."
              if operation == "recap" else
              "The model returned an invalid answer format or citation. Retry; this turn was not saved.")
    if category == "provider_timeout":
        detail = "The model timed out. Your saved records are safe. Please retry."
    elif category.startswith("provider_"):
        detail = "The model provider is unavailable. Your saved records are safe. Check Settings and retry."
    exc = HTTPException(504 if category == "provider_timeout" else 502, detail)
    exc.generation_category = category
    return exc


def parse_object(raw):
    """Accept a whole JSON object, optionally one complete Markdown fence, only."""
    if not isinstance(raw, str) or not raw.strip():
        raise GenerationError("empty_response")
    text = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\n?(.*?)\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    def constant(_):
        raise ValueError("non-JSON numeric constant")
    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, TypeError, RecursionError):
        raise GenerationError("json_parse") from None
    if not isinstance(value, dict):
        raise GenerationError("schema_validation")
    return value


def object_schema(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def chat_schema(learning=False, initialize_goal=False):
    fields = {
        "answer": {"type": "string"},
        "citations": {"type": "array", "items": {"type": "integer"}},
        "basis": {"type": "string", "enum": ["grounded", "hybrid", "general", "insufficient"]},
        "model_knowledge": {"type": ["string", "null"]},
    }
    if learning:
        evidence = object_schema({"kind": {"type": "string", "enum": ["attempt", "question", "correction", "reflection"]},
                                  "text": {"type": "string"}, "quote": {"type": "string"}})
        fields["learning"] = {"anyOf": [{"type": "null"}, object_schema({
            "focus": {"type": "string"}, "next_step": {"type": "string"},
            "evidence": {"anyOf": [{"type": "null"}, evidence]}})]}
        if initialize_goal:
            record = fields["learning"]["anyOf"][1]
            record["properties"]["initial_goal"] = {"type": "string"}
            record["required"].append("initial_goal")
            fields["learning"] = record
    return object_schema(fields)


RECAP_SCHEMA = object_schema({key: {"type": "string"} for key in ("explored", "tried", "unclear", "next")})


def response_format(base_url, model, schema, profiles):
    # Exact operator-verified endpoint/model pair; never infer capability from a brand name.
    if schema and any(p.get("base_url", "").rstrip("/") == base_url.rstrip("/") and p.get("model") == model for p in profiles):
        return {"type": "json_schema", "json_schema": {"name": "asteria_generation", "strict": True, "schema": schema}}
    return None
