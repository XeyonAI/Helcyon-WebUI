import re

TOKEN_BUDGET = 8000  # ✅ Cut in half - keeps last ~10-15 message pairs
# This leaves ~10,384 tokens for generation (16,384 ctx - 6,000 budget)

def rough_token_count(text: str) -> int:
    return len(re.findall(r'\w+|[^\s\w]', text))

def trim_chat_history(messages, token_budget: int = TOKEN_BUDGET):
    if not messages:
        return []
    
    # Separate system message (always keep it)
    system_msg = messages[0] if messages[0].get("role") == "system" else None
    body = messages[1:] if system_msg else messages
    
    # Calculate system message tokens (for logging only)
    system_tokens = rough_token_count(system_msg.get("content", "")) if system_msg else 0
    print(f"📊 System message uses ~{system_tokens} tokens")
    
    # Trim conversation history to fit budget
    total = 0
    trimmed = []
    
    for msg in reversed(body):
        n = rough_token_count(msg.get("content", "")) + 20
        if total + n > token_budget:
            break
        trimmed.insert(0, msg)
        total += n
    
    print(f"📊 Kept {len(trimmed)} conversation messages (~{total} tokens)")
    print(f"📊 Total context: ~{system_tokens + total} tokens")
    
    # Always include system message at the start
    if system_msg:
        trimmed.insert(0, system_msg)
    
    return trimmed