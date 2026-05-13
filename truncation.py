import re, json, os

def _read_ctx_size() -> int:
    """Read ctx_size live from settings.json — never stale even if changed without restart."""
    try:
        _sf = os.path.join(os.path.dirname(__file__), "settings.json")
        with open(_sf, "r", encoding="utf-8") as f:
            return int(json.load(f).get("llama_args", {}).get("ctx_size", 16384))
    except Exception:
        return 16384

CONTEXT_WINDOW     = _read_ctx_size()  # read live from settings.json at import time
GENERATION_RESERVE = 2048   # tokens reserved for the model response
SYSTEM_BUFFER      = 200    # small safety margin for ChatML overhead tokens
TOKEN_FUDGE        = 1.4    # rough_token_count undercounts BPE by ~35-40% on
                            # emoji/separator/ChatML-heavy prompts (measured:
                            # 35516-char prompt → rough=7245, real=~10000 →
                            # ratio 1.38). The final-prompt path in app.py
                            # uses /tokenize for an exact count; this fudge
                            # only governs the trim-budget pre-estimate.

# Max tokens available for prompt = (CONTEXT_WINDOW - GENERATION_RESERVE) / TOKEN_FUDGE
# Of that, the system message takes what it takes — the rest goes to conversation history.

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
    print(f"📊 System message: ~{system_tokens} tokens")

    # Dynamically calculate how much room is left for conversation history
    prompt_budget = int((CONTEXT_WINDOW - GENERATION_RESERVE - SYSTEM_BUFFER) / TOKEN_FUDGE)
    conversation_budget = max(prompt_budget - system_tokens - extra_system_overhead, 1024)  # never go below 1024

    # Allow caller to override if needed
    if token_budget is not None:
        conversation_budget = token_budget

    print(f"📊 Conversation budget: ~{conversation_budget} tokens "
          f"(context {CONTEXT_WINDOW} - gen {GENERATION_RESERVE} - buffer {SYSTEM_BUFFER} - system {system_tokens})")

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

    # Alternation guard: if the trim stopped mid-pair (first body message is
    # an assistant turn), drop it so the conversation starts with a user
    # turn. Helcyon (Mistral Nemo ChatML) requires strict `S U A U A … U`
    # alternation — a leading assistant after `S` tells the model the user
    # side has already been answered, and it emits EOS as the first token
    # (tokens_predicted 1-25, stop reason "stopped_word: <|im_start|>").
    # ⚠️ DO NOT remove — this is the post-trim equivalent of the
    # leading-assistant strip in app.py's chat() route, which only runs
    # pre-trim on active_chat and cannot catch budget-driven malformation.
    print(f"🔍 GUARD CHECK: trimmed has {len(trimmed)} msgs, first role = {repr(trimmed[0].get('role')) if trimmed else 'EMPTY'}", flush=True)
    _dropped_for_alternation = 0
    while trimmed and trimmed[0].get("role") == "assistant":
        _dropped = trimmed.pop(0)
        _dropped_for_alternation += 1
        _clen = len(_dropped.get("content", "")) if isinstance(_dropped.get("content", ""), str) else 0
        print(f"🗑️ Trim alternation guard: dropped leading assistant message ({_clen} chars) "
              f"to preserve S U A U A … U sequence")
    if _dropped_for_alternation:
        print(f"🗑️ Trim alternation guard: stripped {_dropped_for_alternation} leading assistant message(s)")

    if system_msg:
        trimmed.insert(0, system_msg)

    return trimmed
