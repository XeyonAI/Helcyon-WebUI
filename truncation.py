import re
import json
import os

# Read ctx_size from settings.json so CONTEXT_WINDOW always matches the running server.
# Hardcoding this separately from settings.json is the root cause of KV exhaustion:
# the trim allows prompts sized for 16384 tokens when the server might be at 12288.
def _read_ctx_size():
    try:
        _path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
        with open(_path, "r", encoding="utf-8") as _f:
            return int(json.load(_f).get("llama_args", {}).get("ctx_size", 16384))
    except Exception:
        return 16384

CONTEXT_WINDOW     = _read_ctx_size()
GENERATION_RESERVE = 4096   # tokens reserved for model response — must match max_tokens in settings.json
SYSTEM_BUFFER      = 200    # small safety margin for ChatML overhead tokens

# BPE correction factor: rough_token_count undercounts real Llama/Mistral BPE tokens
# by ~20-25% (each English word = 1 rough token but averages ~1.25 BPE tokens).
# Dividing the rough budget by this factor ensures real token usage stays within ctx_size.
TOKEN_FUDGE = 1.25

# Effective rough-token budget for the full prompt (system + conversation):
#   (CONTEXT_WINDOW - GENERATION_RESERVE - SYSTEM_BUFFER) / TOKEN_FUDGE
# This leaves real-token headroom equal to GENERATION_RESERVE for model output.


def rough_token_count(text) -> int:
    # Handle multimodal content (list of parts)
    if isinstance(text, list):
        combined = " ".join(
            part.get("text", "") for part in text if part.get("type") == "text"
        )
        return len(re.findall(r'\w+|[^\s\w]', combined))
    if not isinstance(text, str):
        return 0
    return len(re.findall(r'\w+|[^\s\w]', text))


def trim_chat_history(messages, token_budget: int = None, extra_system_overhead: int = 0):
    if not messages:
        return []

    # Separate system message (always kept in full)
    system_msg = messages[0] if messages[0].get("role") == "system" else None
    body = messages[1:] if system_msg else messages

    # Measure actual system message size
    system_tokens = rough_token_count(system_msg.get("content", "")) if system_msg else 0
    # Account for content added to system message AFTER trim (e.g. example dialogue)
    system_tokens += extra_system_overhead
    print(f"📊 System message: ~{system_tokens} tokens (includes {extra_system_overhead} overhead)")

    # Dynamically calculate how much room is left for conversation history.
    # Divide by TOKEN_FUDGE to compensate for BPE undercount — without this,
    # rough budgets translate to real token counts that overflow ctx_size during generation.
    raw_prompt_budget = CONTEXT_WINDOW - GENERATION_RESERVE - SYSTEM_BUFFER
    prompt_budget = int(raw_prompt_budget / TOKEN_FUDGE)
    conversation_budget = max(prompt_budget - system_tokens, 1024)  # never go below 1024

    # Allow caller to override if needed
    if token_budget is not None:
        conversation_budget = token_budget

    print(f"📊 Conversation budget: ~{conversation_budget} tokens "
          f"(ctx {CONTEXT_WINDOW} - gen {GENERATION_RESERVE} - buf {SYSTEM_BUFFER} "
          f"÷ fudge {TOKEN_FUDGE} - system {system_tokens})")

    # Trim conversation history to fit budget (keep most recent messages)
    total = 0
    trimmed = []

    for msg in reversed(body):
        n = rough_token_count(msg.get("content", "")) + 20  # +20 for ChatML tags
        if total + n > conversation_budget:
            break
        trimmed.insert(0, msg)
        total += n

    print(f"📊 Kept {len(trimmed)} conversation messages (~{total} tokens)")
    print(f"📊 Estimated total context: ~{system_tokens + total} / {CONTEXT_WINDOW} tokens")

    if system_msg:
        trimmed.insert(0, system_msg)

    return trimmed
