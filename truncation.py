import re, json, os

def _read_ctx_size() -> int:
    """Read ctx_size live from settings.json — never stale even if changed without restart."""
    try:
        _sf = os.path.join(os.path.dirname(__file__), "settings.json")
        with open(_sf, "r", encoding="utf-8") as f:
            return int(json.load(f).get("llama_args", {}).get("ctx_size", 16384))
    except Exception:
        return 16384


def _read_max_prompt_tokens() -> int:
    """Read the prompt-size cap live from settings.json for the active backend_mode.

    The cap is stored under max_prompt_tokens.{mode} so each backend has its own
    configurable ceiling.  Falls back to 8500 (the safe local ceiling) if the key
    is absent — local Mistral Nemo has an EOS cliff at ~10,000-10,500 tokens
    evaluated and 8500 is the documented safe ceiling for that backend.
    """
    try:
        _sf = os.path.join(os.path.dirname(__file__), "settings.json")
        with open(_sf, "r", encoding="utf-8") as f:
            s = json.load(f)
        mode = s.get("backend_mode", "local")
        caps = s.get("max_prompt_tokens", {})
        return int(caps.get(mode, caps.get("local", 8500)))
    except Exception:
        return 8500


def _read_backend_mode() -> str:
    """Read the active backend mode from settings.json."""
    try:
        _sf = os.path.join(os.path.dirname(__file__), "settings.json")
        with open(_sf, "r", encoding="utf-8") as f:
            return str(json.load(f).get("backend_mode", "local") or "local").lower()
    except Exception:
        return "local"


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

    # Dynamically calculate how much room is left for conversation history.
    # Local inference is constrained by llama.cpp ctx_size; cloud backends are
    # constrained by their configured prompt cap instead, so Claude/OpenAI do
    # not inherit the local 16k window.
    backend_mode = _read_backend_mode()
    max_prompt_tokens = _read_max_prompt_tokens()  # live read — varies by backend_mode
    context_window = _read_ctx_size()
    if backend_mode in ("openai", "anthropic"):
        prompt_budget = int(max_prompt_tokens / TOKEN_FUDGE)
    else:
        prompt_budget = int((context_window - GENERATION_RESERVE - SYSTEM_BUFFER) / TOKEN_FUDGE)
    conversation_budget = max(prompt_budget - system_tokens - extra_system_overhead, 1024)  # never go below 1024

    # Hard cap: clamp total prompt to max_prompt_tokens to stay under backend limits.
    # For local Mistral Nemo this guards the EOS cliff at ~10,000-10,500 tokens;
    # for cloud backends the cap is much higher (see max_prompt_tokens in settings.json).
    # The cap is in REAL tokens but conversation_budget is in rough tokens, so we
    # convert the cap to rough-token space before comparing (divide by TOKEN_FUDGE).
    max_prompt_rough = int(max_prompt_tokens / TOKEN_FUDGE)
    max_convo_from_cap = max(max_prompt_rough - system_tokens - extra_system_overhead, 1024)
    if conversation_budget > max_convo_from_cap:
        print(f"📊 Conversation budget clamped by max_prompt_tokens: {conversation_budget} → {max_convo_from_cap} (rough tokens, cap={max_prompt_tokens} real)")
        conversation_budget = max_convo_from_cap

    # Allow caller to override if needed
    if token_budget is not None:
        conversation_budget = token_budget

    print(f"📊 Conversation budget: ~{conversation_budget} tokens "
          f"(backend {backend_mode}, context {context_window}, cap {max_prompt_tokens}, "
          f"gen {GENERATION_RESERVE}, buffer {SYSTEM_BUFFER}, system {system_tokens})")

    # Trim conversation history to fit budget (keep most recent messages).
    # ⚠️ The latest turn (body[-1] — normally the user's current message) is
    # ALWAYS kept, even if it alone exceeds the budget. Dropping it leaves the
    # model with no user input at all, so it emits EOS or hallucinates a reply
    # with no grounding. This is critical for large attached documents: the
    # document rides inside that turn and can single-handedly exceed the
    # conversation budget — without the `and trimmed` guard the whole turn
    # (document + question) is silently dropped. DO NOT remove the guard.
    total = 0
    trimmed = []

    for msg in reversed(body):
        n = rough_token_count(msg.get("content", "")) + 20  # +20 for ChatML tags
        if total + n > conversation_budget and trimmed:
            break
        trimmed.insert(0, msg)
        total += n

    if trimmed and total > conversation_budget:
        print(f"⚠️ Latest turn alone is ~{total} rough tokens — over the "
              f"~{conversation_budget}-token conversation budget. Kept it whole "
              f"anyway (likely a large attached document); older turns dropped.")

    print(f"📊 Kept {len(trimmed)} conversation messages (~{total} tokens)")
    _limit_label = max_prompt_tokens if backend_mode in ("openai", "anthropic") else context_window
    print(f"📊 Estimated total prompt: ~{system_tokens + total} / {_limit_label} tokens")

    # Alternation guard: if the trim stopped mid-pair (first body message is
    # an assistant turn), drop it so the conversation starts with a user
    # turn. Helcyon (Mistral Nemo ChatML) requires strict `S U A U A … U`
    # alternation — a leading assistant after `S` tells the model the user
    # side has already been answered, and it emits EOS as the first token.
    # ⚠️ DO NOT remove — this is the post-trim equivalent of the
    # leading-assistant strip in app.py's chat() route.
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
