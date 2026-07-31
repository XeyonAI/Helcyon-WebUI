from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests


class ApiError(Exception):
    """Raised when the judge endpoint cannot return a usable response."""

    def __init__(
        self,
        message: str,
        raw_response: str | None = None,
        status_code: int | None = None,
    ):
        super().__init__(message)
        self.raw_response = raw_response
        self.status_code = status_code


@dataclass
class ChatCompletionResult:
    text: str
    response_format_mode: str
    api_json_extraction_failed: bool = False
    api_json_parse_error: str = ""


JUDGE_LOG_PATH = Path(__file__).resolve().parent.parent / "judge_logs" / "judge_io.jsonl"
JUDGE_PIPELINE_DEBUG_PATH = Path(__file__).resolve().parent.parent / "judge_logs" / "judge_pipeline_debug.jsonl"

_debug_logging_enabled = True

FIXED_TEMPERATURE_MODEL_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def model_supports_custom_temperature(model: str) -> bool:
    """Return whether a model family accepts non-default temperature values."""
    normalized = str(model or "").strip().lower().rsplit("/", 1)[-1]
    return not any(
        normalized == prefix
        or normalized.startswith(f"{prefix}-")
        or normalized.startswith(f"{prefix}.")
        for prefix in FIXED_TEMPERATURE_MODEL_PREFIXES
    )


def set_debug_logging(enabled: bool) -> None:
    global _debug_logging_enabled
    _debug_logging_enabled = bool(enabled)


def debug_logging_enabled() -> bool:
    return _debug_logging_enabled


SMOKE_TEST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ok"],
    "properties": {"ok": {"type": "boolean"}},
}
SMOKE_TEST_GRAMMAR = r'''
root ::= "{" ws "\"ok\"" ws ":" ws ("true" | "false") ws "}" ws
ws ::= [ \t\n\r]*
'''.strip()


def message_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
        if parts:
            return "\n".join(parts)
    if content is None:
        return ""
    return str(content)


def json_dump(value: Any) -> str:
    try:
        return json.dumps(value, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def log_judge_api_event(event: str, payload: dict[str, Any]) -> None:
    if not _debug_logging_enabled:
        return
    try:
        JUDGE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "event": event,
            **payload,
        }
        with JUDGE_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def log_judge_pipeline_debug(stage: str, payload: dict[str, Any]) -> None:
    if not _debug_logging_enabled:
        return
    try:
        JUDGE_PIPELINE_DEBUG_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "temporary_debug": True,
            "stage": stage,
            **payload,
        }
        with JUDGE_PIPELINE_DEBUG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def prompt_character_length(messages: list[dict[str, str]]) -> int:
    total = 0
    for message in messages:
        total += len(str(message.get("content") or ""))
    return total


def response_preview(text: str, limit: int = 500) -> str:
    return " ".join(str(text or "").split())[:limit]


def error_detail_from_text(text: str) -> str:
    """Best-effort server error detail from an already-captured response body.

    Prefers the parsed+pretty-printed JSON body (most judge-compatible APIs
    return a structured `{"error": {"message": ...}}` shape there); falls
    back to the raw text when the body isn't valid JSON; falls back to an
    explicit placeholder when the body is empty. A blank body must never
    silently render as "no detail was captured" — that's what produced bare
    "Judge endpoint returned 400" messages with the status but nothing after it.
    """
    stripped = str(text or "").strip()
    if not stripped:
        return "(empty response body)"
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped
    return json_dump(parsed)


def response_error_detail(response: requests.Response) -> str:
    """Same as error_detail_from_text, reading directly off a live Response.

    llama.cpp closes the connection with a bare status and zero-length body
    (no JSON error object) when a request overruns the context window or a
    json_schema/grammar constraint fails to compile — there is no further
    detail to parse in that case, so callers get a targeted explanation
    instead of a placeholder that reads like diagnostics never ran.
    """
    detail = error_detail_from_text(response.text)
    if detail == "(empty response body)" and _is_llama_cpp_server(response):
        detail += (
            " — llama.cpp closed the connection without writing an error body. "
            "This almost always means the request overran the judge model's "
            "context window (n_ctx) or a json_schema/grammar constraint it "
            "could not compile, not a bug in the judge client. Try a shorter "
            "prompt/lower max_tokens, or raise --ctx-size on the judge server."
        )
    return detail


def _is_llama_cpp_server(response: requests.Response) -> bool:
    return "llama.cpp" in str(response.headers.get("Server") or "").lower()


def format_endpoint_error(label: str, response: requests.Response) -> str:
    """e.g. label='Judge endpoint', 'Judge model-list request', 'Bench lifecycle hook'."""
    return f"{label} returned HTTP {response.status_code}: {response_error_detail(response)}"


def response_headers_for_log(response: requests.Response) -> dict[str, str]:
    return {str(key): str(value) for key, value in response.headers.items()}


def strip_markdown_fences(text: str) -> str:
    cleaned = str(text or "").strip()
    cleaned = cleaned.removeprefix("```json").removeprefix("```JSON").removeprefix("```")
    cleaned = cleaned.removesuffix("```")
    return cleaned.strip()


def extract_json_object_text(text: str) -> str:
    cleaned = strip_markdown_fences(text)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        first = cleaned.find("{")
        last = cleaned.rfind("}")
        if first == -1 or last == -1 or last < first:
            raise
        cleaned = cleaned[first : last + 1]
        parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("Extracted JSON must be an object.")
    return cleaned


def json_parse_failure_details(text: str) -> tuple[str | None, str | None]:
    try:
        return extract_json_object_text(text), None
    except (json.JSONDecodeError, ValueError) as exc:
        return None, f"{exc.__class__.__name__}: {exc}"


def chat_completion(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int | None = None,
    max_completion_tokens: int | None = None,
    json_schema: dict[str, Any] | None = None,
    grammar: str | None = None,
    extra_headers: dict[str, str] | None = None,
    endpoint_name: str = "",
    prompt_id: str = "",
    timeout: int = 120,
    prefer_json_object: bool = False,
    allow_raw_on_json_parse_failure: bool = False,
    return_metadata: bool = False,
    extra_log_fields: dict[str, Any] | None = None,
) -> str | ChatCompletionResult:
    # extra_log_fields is a pass-through extension point for the concise
    # per-request diagnostic log (see post_with_logged_attempt) — e.g. the
    # judge comparison caller adds candidate response lengths here. It is
    # never read for request logic, only merged into what gets logged —
    # callers must not put full prompt/response text in it.
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    headers.update(extra_headers or {})
    base_payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
    }
    if model_supports_custom_temperature(model):
        base_payload["temperature"] = temperature
    preferred_param = "max_completion_tokens" if max_completion_tokens is not None else "max_tokens"
    fallback_param = "max_tokens" if preferred_param == "max_completion_tokens" else "max_completion_tokens"
    token_values = {
        "max_tokens": max_tokens if max_tokens is not None else max_completion_tokens,
        "max_completion_tokens": max_completion_tokens if max_completion_tokens is not None else max_tokens,
    }
    token_attempts = []
    structured_attempts = []
    response = None
    non_json_attempts = []
    prompt_length = prompt_character_length(messages)

    try:
        for output_name, output_payload in structured_output_payloads(json_schema, grammar, prefer_json_object=prefer_json_object):
            attempt_payload = base_payload | output_payload
            try:
                response = post_with_logged_attempt(
                    url=url,
                    headers=headers,
                    base_payload=attempt_payload,
                    token_param=preferred_param,
                    token_value=token_values[preferred_param],
                    timeout=timeout,
                    endpoint=endpoint_name or base_url,
                    model=model,
                    prompt_id=prompt_id,
                    response_format_mode=output_name,
                    prompt_character_count=prompt_length,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    max_completion_tokens=max_completion_tokens,
                    extra_log_fields=extra_log_fields,
                )
            except requests.Timeout:
                raise
            except requests.RequestException as exc:
                if should_try_next_response_format_error(exc, output_name):
                    log_response_format_fallback(
                        prompt_id=prompt_id,
                        model=model,
                        endpoint=endpoint_name or base_url,
                        response_format_mode=output_name,
                        next_response_format_mode=next_response_format_mode(output_name, json_schema, grammar, prefer_json_object=prefer_json_object),
                        status_code=None,
                        error_text=str(exc),
                        fallback_attempted=True,
                        reason=exc.__class__.__name__,
                    )
                    continue
                raise
            structured_attempts.append((output_name, response.status_code, response.text))
            token_attempts.append((preferred_param, response.status_code, response.text))
            if is_unsupported_parameter(response, preferred_param):
                response = post_with_logged_attempt(
                    url=url,
                    headers=headers,
                    base_payload=attempt_payload,
                    token_param=fallback_param,
                    token_value=token_values[fallback_param],
                    timeout=timeout,
                    endpoint=endpoint_name or base_url,
                    model=model,
                    prompt_id=prompt_id,
                    response_format_mode=output_name,
                    prompt_character_count=prompt_length,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    max_completion_tokens=max_completion_tokens,
                    extra_log_fields=extra_log_fields,
                )
                structured_attempts.append((output_name, response.status_code, response.text))
                token_attempts.append((fallback_param, response.status_code, response.text))
            if response.status_code < 400:
                content = response_content_or_retry(
                    response=response,
                    url=url,
                    headers=headers,
                    base_payload=attempt_payload,
                    token_param=preferred_param,
                    token_value=token_values[preferred_param],
                    timeout=timeout,
                    endpoint=endpoint_name or base_url,
                    model=model,
                    prompt_id=prompt_id,
                    response_format_mode=output_name,
                    prompt_character_count=prompt_length,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    max_completion_tokens=max_completion_tokens,
                    extra_log_fields=extra_log_fields,
                )
                log_judge_pipeline_debug(
                    "RAW MODEL OUTPUT",
                    {
                        "prompt_id": prompt_id,
                        "judge_model": model,
                        "endpoint": endpoint_name or base_url,
                        "response_format_mode": output_name,
                        "text": content,
                    },
                )
                extracted_content, parse_error = json_parse_failure_details(content)
                if extracted_content is not None:
                    log_judge_pipeline_debug(
                        "AFTER API JSON EXTRACTION",
                        {
                            "prompt_id": prompt_id,
                            "judge_model": model,
                            "endpoint": endpoint_name or base_url,
                            "response_format_mode": output_name,
                            "text": extracted_content,
                            "changed_from_raw": extracted_content != content,
                        },
                    )
                    if return_metadata:
                        return ChatCompletionResult(
                            text=extracted_content,
                            response_format_mode=output_name,
                        )
                    return extracted_content
                log_judge_api_event(
                    "raw_assistant_content_parse_failed",
                    {
                        "prompt_id": prompt_id,
                        "judge_model": model,
                        "endpoint": endpoint_name or base_url,
                        "response_format_mode": output_name,
                        "prompt_character_length": prompt_length,
                        "raw_assistant_content": content,
                        "raw_content_received": bool(content),
                        "json_parse_exception": parse_error,
                        "raw_content_preview": str(content)[:500],
                    },
                )
                non_json_attempts.append(
                    {
                        "response_format_mode": output_name,
                        "content": content,
                        "parse_error": parse_error or "",
                    }
                )
                if allow_raw_on_json_parse_failure:
                    if return_metadata:
                        return ChatCompletionResult(
                            text=content,
                            response_format_mode=output_name,
                            api_json_extraction_failed=True,
                            api_json_parse_error=parse_error or "",
                        )
                    return content
                continue
            fallback_attempted = should_try_next_response_format(response, output_name)
            log_response_format_fallback(
                prompt_id=prompt_id,
                model=model,
                endpoint=endpoint_name or base_url,
                response_format_mode=output_name,
                next_response_format_mode=next_response_format_mode(output_name, json_schema, grammar, prefer_json_object=prefer_json_object),
                status_code=response.status_code,
                error_text=response.text,
                fallback_attempted=fallback_attempted,
                reason="recoverable_response_format_failure" if fallback_attempted else "final_http_failure",
            )
            if not fallback_attempted:
                break
    except requests.Timeout as exc:
        raise ApiError(f"Judge endpoint timed out after {timeout}s. The local judge generation exceeded the timeout.") from exc
    except requests.ConnectionError as exc:
        raise ApiError(f"Judge endpoint is not running or refused the connection at {base_url}: {exc}") from exc
    except requests.RequestException as exc:
        raise ApiError(f"Could not reach judge endpoint at {base_url}: {exc}") from exc

    if response is None:
        raise ApiError("No structured output strategy was available for the judge endpoint.")

    if response.status_code >= 400:
        if any(is_unsupported_parameter_text(text, param) for param, _, text in token_attempts):
            raise ApiError(
                format_token_parameter_error(token_attempts),
                raw_response=response_error_detail(response),
                status_code=response.status_code,
            )
        if any(is_unsupported_structured_output_text(text, output_name) for output_name, _, text in structured_attempts):
            raise ApiError(
                format_structured_output_error(structured_attempts),
                raw_response=response_error_detail(response),
                status_code=response.status_code,
            )
        raise ApiError(
            format_endpoint_error("Judge endpoint", response),
            raw_response=response_error_detail(response),
            status_code=response.status_code,
        )

    raise ApiError(
        format_non_json_structured_output_error(non_json_attempts),
        raw_response=raw_content_from_non_json_attempts(non_json_attempts),
    )


def list_models(
    *,
    base_url: str,
    api_key: str,
    extra_headers: dict[str, str] | None = None,
    timeout: int = 10,
) -> list[str]:
    url = f"{base_url.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    headers.update(extra_headers or {})
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
    except requests.Timeout as exc:
        raise ApiError(f"Judge model-list request timed out after {timeout}s at {url}.") from exc
    except requests.ConnectionError as exc:
        raise ApiError(f"Judge endpoint is not running or refused the connection at {url}: {exc}") from exc
    except requests.RequestException as exc:
        raise ApiError(f"Could not reach judge endpoint at {url}: {exc}") from exc
    if response.status_code >= 400:
        raise ApiError(
            format_endpoint_error("Judge endpoint", response),
            raw_response=response_error_detail(response),
            status_code=response.status_code,
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise ApiError("Judge endpoint returned malformed model-list JSON.", raw_response=response.text) from exc

    raw_models = data.get("data") if isinstance(data, dict) else data
    if not isinstance(raw_models, list):
        raise ApiError("Judge endpoint returned an unexpected model-list shape.", raw_response=response.text)

    models = []
    for item in raw_models:
        if isinstance(item, str):
            models.append(item)
        elif isinstance(item, dict) and item.get("id"):
            models.append(str(item["id"]))
    return sorted(dict.fromkeys(models), key=str.lower)


def preflight_chat_completion(
    *,
    base_url: str,
    api_key: str,
    model: str,
    temperature: float = 0.0,
    max_tokens: int | None = 64,
    max_completion_tokens: int | None = None,
    extra_headers: dict[str, str] | None = None,
    endpoint_name: str = "",
    prompt_id: str = "preflight",
    timeout: int = 30,
) -> str:
    return chat_completion(
        base_url=base_url,
        api_key=api_key,
        model=model,
        messages=[
            {"role": "system", "content": "Return JSON only."},
            {"role": "user", "content": 'Return exactly {"ok": true}.'},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
        max_completion_tokens=max_completion_tokens,
        json_schema=SMOKE_TEST_SCHEMA,
        grammar=SMOKE_TEST_GRAMMAR,
        extra_headers=extra_headers,
        endpoint_name=endpoint_name,
        prompt_id=prompt_id,
        timeout=timeout,
    )


def post_lifecycle_hook(
    *,
    url: str,
    extra_headers: dict[str, str] | None = None,
    timeout: int = 10,
) -> None:
    try:
        response = requests.post(url, headers=extra_headers or {}, timeout=timeout)
    except requests.Timeout as exc:
        raise ApiError(f"Bench lifecycle hook timed out after {timeout}s at {url}.") from exc
    except requests.ConnectionError as exc:
        raise ApiError(f"Bench lifecycle hook is not reachable at {url}: {exc}") from exc
    except requests.RequestException as exc:
        raise ApiError(f"Bench lifecycle hook failed at {url}: {exc}") from exc
    if response.status_code >= 400:
        raise ApiError(
            format_endpoint_error("Bench lifecycle hook", response),
            raw_response=response_error_detail(response),
            status_code=response.status_code,
        )


def endpoint_origin(base_url: str) -> str:
    parsed = urlparse(base_url)
    if not parsed.scheme or not parsed.netloc:
        return base_url.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}"


def structured_output_payloads(
    json_schema: dict[str, Any] | None,
    grammar: str | None,
    *,
    prefer_json_object: bool = False,
) -> list[tuple[str, dict[str, Any]]]:
    payloads = []
    json_schema_payload = None
    if json_schema:
        json_schema_payload = (
            "json_schema",
            {
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "helcyon_bench_judgement",
                        "strict": True,
                        "schema": json_schema,
                    },
                }
            },
        )
    json_object_payload = ("json_object", {"response_format": {"type": "json_object"}})
    grammar_payload = ("grammar", {"grammar": grammar}) if grammar else None

    if prefer_json_object:
        # Local llama.cpp path. json_object only guarantees *valid* JSON, not the
        # right shape, so a fine-tuned judge can drift into its own schema and
        # lose the run. Both json_schema and the GBNF grammar constrain the shape
        # itself, so the shape-constraining modes come first.
        #
        # json_schema leads because a server that does not support it answers
        # 400 and we fall through. A server that does not support the llama.cpp
        # grammar field answers 200 and silently ignores it, which looks like a
        # constrained request while generating freely - so grammar is second,
        # where an unsupported json_schema has already failed loudly.
        if json_schema_payload:
            payloads.append(json_schema_payload)
        if grammar_payload:
            payloads.append(grammar_payload)
        payloads.append(json_object_payload)
        payloads.append(("none", {}))
    else:
        if json_schema_payload:
            payloads.append(json_schema_payload)
        payloads.append(json_object_payload)
        payloads.append(("none", {}))
        if grammar_payload:
            payloads.append(grammar_payload)
    return payloads


def response_content_or_retry(
    *,
    response: requests.Response,
    url: str,
    headers: dict[str, str],
    base_payload: dict[str, Any],
    token_param: str,
    token_value: int | None,
    timeout: int,
    endpoint: str,
    model: str,
    prompt_id: str,
    response_format_mode: str,
    prompt_character_count: int,
    temperature: float,
    max_tokens: int | None,
    max_completion_tokens: int | None,
    extra_log_fields: dict[str, Any] | None = None,
) -> str:
    def retry_with_larger_token_budget(mode_suffix: str) -> tuple[dict[str, Any], str, str] | None:
        """Re-issues the request once with a much larger token budget.

        Returns (retry_data, retry_content, retry_finish_reason), or None if the
        retry itself couldn't be attempted (no usable token_value) or came back
        as an HTTP error. Callers decide what "usable" means for retry_content -
        this only performs the request and extraction, it never retries again.
        """
        if not token_value:
            return None
        retry_value = max(token_value * 4, token_value + 2400)
        retry_response = post_with_logged_attempt(
            url=url,
            headers=headers,
            base_payload=base_payload,
            token_param=token_param,
            token_value=retry_value,
            timeout=timeout,
            endpoint=endpoint,
            model=model,
            prompt_id=prompt_id,
            response_format_mode=f"{response_format_mode}_{mode_suffix}",
            prompt_character_count=prompt_character_count,
            temperature=temperature,
            max_tokens=max_tokens,
            max_completion_tokens=max_completion_tokens,
            extra_log_fields=extra_log_fields,
        )
        if retry_response.status_code >= 400:
            return None
        retry_data = retry_response.json()
        retry_choice = retry_data["choices"][0]
        retry_content = message_content_text(retry_choice["message"].get("content"))
        retry_finish_reason = retry_choice.get("finish_reason") or "unknown"
        return retry_data, retry_content, retry_finish_reason

    try:
        data = response.json()
        choice = data["choices"][0]
        content = message_content_text(choice["message"].get("content"))
        finish_reason = choice.get("finish_reason") or "unknown"

        if content.strip():
            # A response truncated mid-JSON by the token limit is not the same
            # problem as a model writing malformed JSON: json_repair usually
            # can't reconstruct an unterminated string with no signal of what
            # should close it. Retry once with a larger budget instead of
            # handing the doomed truncated text to the repair pipeline.
            if finish_reason == "length":
                _, parse_error = json_parse_failure_details(content)
                if parse_error is not None:
                    retried = retry_with_larger_token_budget("truncated_length_retry")
                    if retried is not None:
                        _, retry_content, _ = retried
                        if retry_content.strip():
                            return retry_content
            return content

        if finish_reason == "length":
            retried = retry_with_larger_token_budget("empty_length_retry")
            if retried is not None:
                retry_data, retry_content, retry_finish_reason = retried
                if retry_content.strip():
                    return retry_content
                data = retry_data
                finish_reason = retry_finish_reason
        raw_response = json_dump(data)
        raise ApiError(
            f"Judge endpoint returned an empty message (finish_reason: {finish_reason}).",
            raw_response=raw_response,
        )
    except ApiError:
        raise
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ApiError("Judge endpoint returned an unexpected response shape.", raw_response=response.text) from exc


def post_with_token_param(
    *,
    url: str,
    headers: dict[str, str],
    base_payload: dict[str, Any],
    token_param: str,
    token_value: int | None,
    timeout: int,
) -> requests.Response:
    payload = dict(base_payload)
    if token_value is not None:
        payload[token_param] = token_value

    response = requests.post(url, headers=headers, json=payload, timeout=timeout)
    return response


def post_with_logged_attempt(
    *,
    url: str,
    headers: dict[str, str],
    base_payload: dict[str, Any],
    token_param: str,
    token_value: int | None,
    timeout: int,
    endpoint: str,
    model: str,
    prompt_id: str,
    response_format_mode: str,
    prompt_character_count: int,
    temperature: float,
    max_tokens: int | None,
    max_completion_tokens: int | None,
    extra_log_fields: dict[str, Any] | None = None,
) -> requests.Response:
    payload = dict(base_payload)
    if token_value is not None:
        payload[token_param] = token_value
    attempt_max_tokens = token_value if token_param == "max_tokens" else max_tokens
    attempt_max_completion_tokens = token_value if token_param == "max_completion_tokens" else max_completion_tokens
    log_payload = {
        "prompt_id": prompt_id,
        "judge_model": model,
        "endpoint": endpoint,
        "url": url,
        "timeout_seconds": timeout,
        "max_tokens": attempt_max_tokens,
        "max_completion_tokens": attempt_max_completion_tokens,
        "temperature": temperature,
        "response_format_mode": response_format_mode,
        "prompt_character_length": prompt_character_count,
        "token_param": token_param,
        "token_value": token_value,
    } | (extra_log_fields or {})
    temperature_retry_available = "temperature" in payload
    while True:
        attempt_log_payload = log_payload | {
            "temperature_sent": payload.get("temperature"),
            "temperature_omitted": "temperature" not in payload,
        }
        log_judge_api_event("judge_request_start", attempt_log_payload)
        log_judge_pipeline_debug(
            "REQUEST SENT TO LLAMA.CPP",
            {
                "prompt_id": prompt_id,
                "judge_model": model,
                "endpoint": endpoint,
                "url": url,
                "response_format_mode": response_format_mode,
                "payload": payload,
                "payload_json": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            },
        )
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except requests.Timeout as exc:
            log_judge_api_event("judge_request_timed_out", attempt_log_payload | {"error": str(exc)})
            raise
        except requests.RequestException as exc:
            log_judge_api_event(
                "judge_request_errored",
                attempt_log_payload | {"error": str(exc), "error_type": exc.__class__.__name__},
            )
            raise
        completed_payload = attempt_log_payload | {
            "status_code": response.status_code,
            "response_preview": response_preview(response.text),
        }
        if response.status_code != 200:
            completed_payload.update(
                {
                    "response_text": response_error_detail(response),
                    "response_headers": response_headers_for_log(response),
                }
            )
        log_judge_api_event("judge_request_completed", completed_payload)
        if temperature_retry_available and is_unsupported_temperature_value(response):
            log_judge_api_event(
                "judge_temperature_fallback",
                completed_payload | {"fallback_attempted": True},
            )
            payload.pop("temperature", None)
            temperature_retry_available = False
            continue
        return response


def is_unsupported_temperature_value(response: requests.Response) -> bool:
    if response.status_code not in {400, 422}:
        return False
    try:
        payload = response.json()
    except ValueError:
        payload = None
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        code = str(error.get("code") or error.get("type") or "").strip().lower()
        parameter = str(error.get("param") or "").strip().lower()
        message = str(error.get("message") or "").lower()
        return code == "unsupported_value" and (
            parameter == "temperature" or "temperature" in message
        )
    lowered = response.text.lower()
    return "unsupported_value" in lowered and "temperature" in lowered


def is_unsupported_parameter(response: requests.Response, parameter: str) -> bool:
    return response.status_code in {400, 422} and is_unsupported_parameter_text(response.text, parameter)


def is_unsupported_parameter_text(text: str, parameter: str) -> bool:
    lowered = text.lower()
    return "unsupported parameter" in lowered and parameter.lower() in lowered


def is_unsupported_structured_output(response: requests.Response, output_name: str) -> bool:
    return response.status_code in {400, 422} and is_unsupported_structured_output_text(response.text, output_name)


# A server that cannot parse the GBNF, or does not accept a grammar field at
# all, must not take the whole run down: it falls through to the looser modes.
FALLBACK_RESPONSE_FORMAT_MODES = {"json_schema", "json_object", "grammar"}


def should_try_next_response_format(response: requests.Response, output_name: str) -> bool:
    return (
        output_name in FALLBACK_RESPONSE_FORMAT_MODES
        and is_unsupported_structured_output(response, output_name)
    )


def should_try_next_response_format_error(exc: requests.RequestException, output_name: str) -> bool:
    return output_name in FALLBACK_RESPONSE_FORMAT_MODES and isinstance(exc, requests.ConnectionError)


def next_response_format_mode(
    output_name: str,
    json_schema: dict[str, Any] | None,
    grammar: str | None,
    *,
    prefer_json_object: bool = False,
) -> str | None:
    names = [
        name
        for name, _ in structured_output_payloads(
            json_schema,
            grammar,
            prefer_json_object=prefer_json_object,
        )
    ]
    try:
        index = names.index(output_name)
    except ValueError:
        return None
    if index + 1 >= len(names):
        return None
    return names[index + 1]


def log_response_format_fallback(
    *,
    prompt_id: str,
    model: str,
    endpoint: str,
    response_format_mode: str,
    next_response_format_mode: str | None,
    status_code: int | None,
    error_text: str,
    fallback_attempted: bool,
    reason: str,
) -> None:
    log_judge_api_event(
        "judge_response_format_fallback",
        {
            "prompt_id": prompt_id,
            "judge_model": model,
            "endpoint": endpoint,
            "response_format_mode": response_format_mode,
            "next_response_format_mode": next_response_format_mode,
            "status_code": status_code,
            "error_text": error_text,
            "response_preview": response_preview(error_text),
            "fallback_attempted": fallback_attempted,
            "reason": reason,
        },
    )


def is_unsupported_structured_output_text(text: str, output_name: str) -> bool:
    lowered = text.lower()
    markers = {
        "json_schema": ["json_schema", "schema", "response_format"],
        "json_object": ["json_object", "response_format"],
        "grammar": ["grammar"],
    }.get(output_name, [output_name])
    failure_words = ["unsupported", "not supported", "invalid", "unknown", "unrecognized", "not allowed"]
    return any(marker in lowered for marker in markers) and any(word in lowered for word in failure_words)


def format_token_parameter_error(attempts: list[tuple[str, int, str]]) -> str:
    lines = [
        "Judge endpoint rejected the configured token limit parameter.",
        "Tried both max_tokens and max_completion_tokens where applicable.",
    ]
    for param, status, text in attempts:
        cleaned = " ".join(error_detail_from_text(text).split())
        lines.append(f"- {param}: HTTP {status}: {cleaned}")
    return "\n".join(lines)


def format_structured_output_error(attempts: list[tuple[str, int, str]]) -> str:
    lines = [
        "Judge endpoint rejected every structured output mode.",
        "Tried json_schema, json_object, no response_format, and llama.cpp grammar where configured.",
    ]
    seen = set()
    for output_name, status, text in attempts:
        key = (output_name, status, text)
        if key in seen:
            continue
        seen.add(key)
        cleaned = " ".join(error_detail_from_text(text).split())
        lines.append(f"- {output_name}: HTTP {status}: {cleaned}")
    return "\n".join(lines)


def format_non_json_structured_output_error(attempts: list[dict[str, str]]) -> str:
    lines = [
        "Judge endpoint accepted structured output settings but still returned non-JSON prose.",
        "Tried json_schema, json_object, no response_format, and llama.cpp grammar where configured.",
    ]
    for attempt in attempts:
        output_name = attempt.get("response_format_mode", "unknown")
        content = attempt.get("content", "")
        parse_error = attempt.get("parse_error", "")
        preview = str(content)[:500]
        lines.append(
            f"- {output_name}: raw_content_received={bool(content)}; "
            f"json_parse_exception={parse_error or 'unknown'}; "
            f"first_500={preview}"
        )
    return "\n".join(lines)


def raw_content_from_non_json_attempts(attempts: list[dict[str, str]]) -> str:
    return "\n\n".join(
        f"[{attempt.get('response_format_mode', 'unknown')}]\n{attempt.get('content', '')}"
        for attempt in attempts
    )
