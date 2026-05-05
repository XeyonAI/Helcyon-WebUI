import re

CONTEXT_WINDOW     = 16384  # llama.cpp context size — updated to match --ctx-size
GENERATION_RESERVE = 4096   # tokens reserved for model response — must match max_tokens in settings.json
SYSTEM_BUFFER      = 200    # small safety margin for ChatML overhead tokens

# Max tokens available for prompt = CONTEXT_WINDOW - GENERATION_RESERVE = 12288
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
    # Account for content added to system message AFTER trim (e.g. example dialogue)
    system_tokens += extra_system_overhead
    print(f"📊 System message: ~{system_tokens} tokens (includes {extra_system_overhead} overhead)")

    # Dynamically calculate how much room is left for conversation history
    prompt_budget = CONTEXT_WINDOW - GENERATION_RESERVE - SYSTEM_BUFFER
    conversation_budget = max(prompt_budget - system_tokens, 1024)  # never go below 1024

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

    if system_msg:
        trimmed.insert(0, system_msg)

    return trimmed
