from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import date
from typing import Any

import httpx

from skoleklar import fact as fact_model
from skoleklar.triage.prompt_manifest import DEFAULT_SLOTS, PromptManifest, load_prompt_manifest
from skolekit.records import canonical_hash
from skolekit.items import read_lesson
from skolekit.wording import DANISH
from skoleklar.settings import DEFAULT_MODEL


PROVIDER = "openrouter"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
# What doesn't depend on the household's names is read once; the filled manifest
# (its hash and its few-shots) is read when a stage runs (prompt_manifest.py).
_PACK = load_prompt_manifest("derive-v11", slots=DEFAULT_SLOTS)
PROMPT_VERSION = _PACK.manifest_id
TAXONOMY_VERSION = _PACK.taxonomy_version
DERIVATION_SCHEMA = _PACK.output_schema
# The deterministic lesson-change reader; its version moves with the fact contract.
ROUTINE_CALENDAR_FILTER_VERSION = "routine-calendar-v4"
# A lesson change's identity, which must not move with the reader's version: a new
# key would bring back every substitute and cancelled lesson the family ticked off.
LESSON_KEY_VERSION = "routine-calendar-v3"


def derivation_manifest() -> PromptManifest:
    """The derivation's manifest with the household's names in its slots."""
    return load_prompt_manifest("derive-v11")


DROP_KEYS = {
    "body_html",
    "content_html",
    "raw_payload",
    "raw_hash",
    "content_hash",
    "state_hash",
    "source_hash",
    "snapshots",
    "previous_messages_html",
    "previous_messages_text",
    "recipients",
    "profile",
    "photo",
}


class OpenRouterHTTPError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(
            f"OpenRouter Chat Completions API returned {status_code}: {detail}"
        )


class OpenRouterGenerationError(RuntimeError):
    """A content-free failure after OpenRouter accepted the request."""

    def __init__(
        self,
        *,
        stage: str,
        error_code: str,
        response_id: str = "",
        usage: dict[str, Any] | None = None,
        finish_reason: str | None = None,
    ) -> None:
        self.stage = stage
        self.error_code = error_code
        self.response_id = response_id
        self.usage = usage or {}
        self.finish_reason = finish_reason
        super().__init__(error_code)


def _compact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _compact(child)
            for key, child in value.items()
            if key not in DROP_KEYS
        }
    if isinstance(value, list):
        return [_compact(child) for child in value]
    return value


# A weekly plan's block, as the model reads it: no link, id or code's heading guesses.
_PLAN_BLOCK_DROP = {"segments", "link", "source_plan_id"}


def _contained(part: Any, whole: Any) -> bool:
    """Whether every key and value of `part` is also in `whole`, recursively."""
    if isinstance(part, dict):
        return isinstance(whole, dict) and all(
            key in whole and _contained(value, whole[key]) for key, value in part.items())
    if isinstance(part, list):
        return isinstance(whole, list) and len(part) == len(whole) and all(
            _contained(a, b) for a, b in zip(part, whole))
    return part == whole


def _one_copy(compact: dict[str, Any]) -> dict[str, Any]:
    """The source once: `content_data` repeats `source_data`, and a weekly plan's
    `body_text` is its blocks flattened, so neither goes to the model twice."""
    if "content_data" in compact and _contained(compact["content_data"], compact.get("source_data")):
        compact.pop("content_data")
    if compact.get("source_type") == "weekly_plan" and isinstance(compact.get("source_data"), dict):
        compact.pop("body_text", None)
        data = compact["source_data"]
        blocks = [*data.get("general_plans", []), *(block for day in data.get("days", [])
                                                    for block in day.get("plans", []))]
        for block in blocks:
            for key in _PLAN_BLOCK_DROP:
                block.pop(key, None)
    return compact


def candidate_document(
    candidate: dict[str, Any], *, class_labels: Mapping[str, str]
) -> dict[str, Any]:
    """The model's input for one candidate; `class_labels` come from the household's profile."""
    sources = candidate["sources"]
    primary = json.loads(sources[0]["payload_json"])
    compact = _one_copy(_compact(primary))
    children = sorted({source["child"] for source in sources})
    compact["children"] = children
    compact["child"] = children[0] if len(children) == 1 else "household"
    child_classes = class_labels
    compact["child_context"] = [
        {"child": child, "class": child_classes.get(child)} for child in children
    ]
    compact["source_version_ids"] = [source["version_id"] for source in sources]
    return compact


def derivation_cache_key(
    candidate: dict[str, Any], *, model: str, document: dict[str, Any]
) -> str:
    return canonical_hash(
        {
            "provider": PROVIDER,
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "taxonomy_version": TAXONOMY_VERSION,
            "manifest_hash": derivation_manifest().content_hash,
            "record_versions": sorted(
                source["version_id"] for source in candidate["sources"]
            ),
            "document_hash": canonical_hash(document),
        }
    )


def deterministic_calendar_items(
    candidate: dict[str, Any],
) -> list[dict[str, Any]] | None:
    """Handle structured lesson slots locally; return None for richer sources."""
    if candidate.get("source_type") != "calendar":
        return None
    items: list[dict[str, Any]] = []
    for source in candidate["sources"]:
        payload = json.loads(source["payload_json"])
        source_data = payload.get("source_data") or {}
        if source_data.get("calendar_kind") != "LessonsEvents":
            return None
        event = source_data.get("event") or {}
        if not isinstance(event, dict) or event.get("description"):
            return None
        item = _deterministic_lesson_change(
            event,
            child=str(source["child"]),
            source_key=str(candidate["source_key"]),
        )
        if item is not None:
            items.append(item)
    return items


def deterministic_derivation_cache_key(candidate: dict[str, Any]) -> str:
    return canonical_hash(
        {
            "provider": "local",
            "filter_version": ROUTINE_CALENDAR_FILTER_VERSION,
            "record_versions": sorted(
                source["version_id"] for source in candidate["sources"]
            ),
        }
    )


def _deterministic_lesson_change(
    event: dict[str, Any], *, child: str, source_key: str
) -> dict[str, Any] | None:
    # What the slot holds is skolekit's per-item reading, as in the live view.
    lesson = read_lesson(event, wording=DANISH)
    if not lesson["changed"]:
        return None
    cancelled = lesson["cancelled"]
    substitute = lesson["substitute"] or ""
    start = str(event.get("start") or "")
    end = str(event.get("end") or "")
    if len(start) < 10 or len(end) < 10:
        return None
    day = start[:10]
    start_time = start[11:16] if len(start) >= 16 else ""
    end_time = end[11:16] if len(end) >= 16 else ""
    lesson = str(event.get("lessonName") or event.get("title") or "lektionen").strip()
    class_name = str(event.get("lessonClassName") or event.get("className") or "").strip()
    if class_name.casefold() in {"absent", "present", "none", "null"}:
        class_name = ""
    lesson_scope = f"{lesson} · {class_name}" if class_name else lesson
    time_text = (
        f" kl. {start_time}-{end_time}" if start_time and end_time else ""
    )
    if cancelled:
        item_type = "schedule_change"
        title = f"{lesson} er aflyst"
        summary = f"{child} har aflyst {lesson} den {day}{time_text}."
    elif substitute:
        item_type = "substitute"
        title = f"{substitute} er vikar i {lesson_scope}"
        summary = (
            f"{child} har {substitute} som vikar i {lesson_scope} den {day}{time_text}."
        )
    else:
        item_type = "schedule_change"
        title = f"Skemaændring i {lesson}"
        summary = (
            f"{child} har en skemaændring i {lesson} den {day}{time_text}; "
            "vikaren er ikke navngivet."
        )
    stable_key = canonical_hash(
        {
            "filter_version": LESSON_KEY_VERSION,
            "source_key": source_key,
            "child": child,
            "type": item_type,
        }
    )
    return fact_model.normalize({
        "dedupe_hint": f"{source_key}:{item_type}",
        "stream": "schedule",
        "type": item_type,
        # A lesson slot never moves the day's times on its own: never yellow.
        "moves_time": False,
        "school_subject": lesson,
        "teacher": substitute or None,
        "audience": "child",
        "child": child,
        "title": title,
        "summary": summary,
        "effective_date": day,
        "end_date": day,
        "all_day": True,
        "timing_rule": "explicit",
        "actionability": "informational",
        "urgency": "attention",
        "urgency_reason": "Skoleskemaet markerer en ændring i lektionen.",
        "confidence": 0.99,
        "needs_review": False,
        "review_reason": "",
        "evidence": [
            {
                "quote": str(event.get("formattedEventDuration") or title),
                "source_day": day,
                "subject": lesson,
            }
        ],
        "stable_key": stable_key,
    })


def _response_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        raise RuntimeError("OpenRouter response contained no choices")
    message = choices[0].get("message") or {}
    refusal = message.get("refusal")
    if refusal:
        raise RuntimeError(f"Model refused structured output: {refusal}")
    content = message.get("content")
    if not isinstance(content, str) or not content:
        raise RuntimeError("OpenRouter response contained no output text")
    return content


class OpenRouterClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 120.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.client = client

    def derive(self, document: dict[str, Any]) -> dict[str, Any]:
        response = self.generate(document, manifest=derivation_manifest())
        return {
            "response_id": response["response_id"],
            "usage": response["usage"],
            "items": response["output"]["items"],
        }

    def generate(
        self,
        document: dict[str, Any],
        *,
        manifest: PromptManifest,
        max_output_tokens: int = 8000,
    ) -> dict[str, Any]:
        def request(client: httpx.Client) -> httpx.Response:
            return client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {
                            "role": "system",
                            "content": manifest.render_instructions(),
                        },
                        {
                            "role": "user",
                            "content": json.dumps(
                                document,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        },
                    ],
                    "max_completion_tokens": max_output_tokens,
                    "provider": {
                        "require_parameters": True,
                        "zdr": True,
                    },
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": manifest.response_schema_name,
                            "strict": True,
                            "schema": manifest.output_schema,
                        },
                    },
                },
            )

        if self.client is None:
            with httpx.Client(timeout=self.timeout) as client:
                response = request(client)
        else:
            response = request(self.client)
        if not response.is_success:
            try:
                error = response.json().get("error", {}).get("message")
            except (ValueError, AttributeError):
                error = None
            detail = str(error or response.reason_phrase)[:1000]
            raise OpenRouterHTTPError(response.status_code, detail)
        try:
            payload = response.json()
        except ValueError as error:
            raise OpenRouterGenerationError(
                stage="provider_decode",
                error_code="response_not_json",
            ) from error
        if not isinstance(payload, dict):
            raise OpenRouterGenerationError(
                stage="provider_decode",
                error_code="response_not_object",
            )
        response_id = str(payload.get("id") or "")
        usage = payload.get("usage") or {}
        choices = payload.get("choices") or []
        finish_reason = (
            str(choices[0].get("finish_reason") or "")
            if choices and isinstance(choices[0], dict)
            else None
        )
        try:
            content = _response_text(payload)
        except RuntimeError as error:
            message = str(error)
            if "no choices" in message:
                error_code = "no_choices"
            elif "refused" in message:
                error_code = "model_refusal"
            else:
                error_code = "missing_output_text"
            raise OpenRouterGenerationError(
                stage="provider_output",
                error_code=error_code,
                response_id=response_id,
                usage=usage,
                finish_reason=finish_reason,
            ) from error
        try:
            result = json.loads(content)
        except json.JSONDecodeError as error:
            error_code = (
                "max_output_tokens"
                if finish_reason in {"length", "max_tokens", "max_output_tokens"}
                else "model_output_not_json"
            )
            raise OpenRouterGenerationError(
                stage="model_output_decode",
                error_code=error_code,
                response_id=response_id,
                usage=usage,
                finish_reason=finish_reason,
            ) from error
        return {
            "response_id": response_id,
            "usage": usage,
            "finish_reason": finish_reason,
            "output": result,
        }


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _string_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [text for child in value.values() for text in _string_values(child)]
    if isinstance(value, list):
        return [text for child in value for text in _string_values(child)]
    return [value] if isinstance(value, str) else []


def _valid_date(value: str | None) -> bool:
    if value is None:
        return True
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def validate_items(
    items: list[dict[str, Any]],
    *,
    candidate: dict[str, Any],
    document: dict[str, Any],
) -> list[dict[str, Any]]:
    allowed_children = {source["child"] for source in candidate["sources"]}
    evidence_haystack = _normalized_text("\n".join(_string_values(document)))
    result: list[dict[str, Any]] = []
    for item in items:
        for field in ("title", "summary"):
            if re.search(r"[\u3400-\u9fff]", item[field]):
                item[field] = re.sub(r"[\u3400-\u9fff]", "", item[field]).strip()
                item["needs_review"] = True
                item["review_reason"] = _append_reason(
                    item["review_reason"],
                    "Unexpected non-Danish characters were removed.",
                )
        if item["child"] not in allowed_children | {"household"}:
            item["needs_review"] = True
            item["review_reason"] = _append_reason(
                item["review_reason"], "Child scope does not match the source."
            )
        if not _valid_date(item["effective_date"]) or not _valid_date(item["end_date"]):
            item["needs_review"] = True
            item["review_reason"] = _append_reason(
                item["review_reason"], "Invalid ISO date returned by model."
            )
            item["effective_date"] = None
            item["end_date"] = None
        if item["timing_rule"] == "no_derived_date":
            item["effective_date"] = None
            item["end_date"] = None
        fact_model.normalize(item)
        evidence_valid = bool(item["evidence"])
        for evidence in item["evidence"]:
            if not _evidence_present(evidence["quote"], evidence_haystack):
                evidence_valid = False
        if not evidence_valid:
            item["needs_review"] = True
            item["review_reason"] = _append_reason(
                item["review_reason"], "Evidence could not be verified in the source."
            )
        stable_material = {
            "source_type": candidate["source_type"],
            "source_key": candidate["source_key"],
            "children": sorted(allowed_children),
            "item_type": item["type"],
            "effective_date": item["effective_date"],
            "dedupe_hint": item["dedupe_hint"].strip().casefold(),
        }
        item["stable_key"] = hashlib.sha256(
            json.dumps(stable_material, sort_keys=True).encode("utf-8")
        ).hexdigest()
        result.append(item)
    return result


def _evidence_present(quote: str, haystack: str) -> bool:
    normalized = _normalized_text(quote)
    if not normalized:
        return False
    fragments = [
        _normalized_text(fragment)
        for fragment in re.split(r"(?:\.{3}|…)", quote)
        if _normalized_text(fragment)
    ]
    if len(fragments) <= 1:
        return normalized in haystack
    position = 0
    for fragment in fragments:
        found = haystack.find(fragment, position)
        if found < 0:
            return False
        position = found + len(fragment)
    return True


def _append_reason(existing: str, addition: str) -> str:
    existing = existing.strip()
    return f"{existing} {addition}".strip()
