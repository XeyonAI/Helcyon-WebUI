import re

CONTEXT_WINDOW = 16384   # llama.cpp context size
GENERATION_RESERVE = 4096  # tokens reserved for the model's response
# Max tokens available for prompt = CONTEXT_WINDOW - GENERATION_RESERVE = 12288

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

def trim_chat_history(messages, token_budget: int = None):
    if not messages:
        return []
    
    # Separate system message (always keep it)
    system_msg = messages[0] if messages[0].get("role") == "system" else None
    body = messages[1:] if system_msg else messages
    
    # Calculate system message tokens
    system_tokens = rough_token_count(system_msg.get("content", "")) if system_msg else 0
    print(f"📊 System message uses ~{system_tokens} tokens")
    
    # Dynamically calculate how many tokens are left for conversation history
    max_prompt_tokens = CONTEXT_WINDOW - GENERATION_RESERVE  # 12288
    history_budget = max_prompt_tokens - system_tokens - 200  # 200 token safety buffer
    
    if history_budget < 500:
        print(f"🔴 WARNING: System message is too large ({system_tokens} tokens) — almost no room for chat history!")
        history_budget = 500  # keep at least a few messages
    
    print(f"📊 History budget: ~{history_budget} tokens ({max_prompt_tokens} max prompt - {system_tokens} system - 200 buffer)")
    
    # Trim conversation history to fit budget
    total = 0
    trimmed = []
    
    for msg in reversed(body):
        n = rough_token_count(msg.get("content", "")) + 20
        if total + n > history_budget:
            break
        trimmed.insert(0, msg)
        total += n
    
    print(f"📊 Kept {len(trimmed)} conversation messages (~{total} tokens)")
    print(f"📊 Total context: ~{system_tokens + total} tokens (leaves ~{CONTEXT_WINDOW - system_tokens - total} for generation)")
    
    # Always include system message at the start
    if system_msg:
        trimmed.insert(0, system_msg)
    
    return trimmed