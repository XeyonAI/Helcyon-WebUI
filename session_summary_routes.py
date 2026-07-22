import os, json, re
import requests
from flask import Blueprint, request, jsonify
from truncation import rough_token_count

session_summary_bp = Blueprint('session_summary', __name__)

# --------------------------------------------------
# Session Summary — load/save helpers
# --------------------------------------------------

SESSION_SUMMARY_DIR = os.path.join(os.path.dirname(__file__), "session_summaries")
MAX_SUMMARIES = 3          # keep last 3 sessions per character
MAX_SUMMARY_CHARS = 3600   # ~900 tokens injected cap (3 x 600 token summaries)
SESSION_DIVIDER = "\n\n---SESSION---\n\n"


def _resolve_session_summary_path(character_name):
    """Return the resolved on-disk path of a character's session-summary file,
    trying the same dot/space filename variants as load_session_summary().
    Returns None if no file exists. Used to read the file mtime for the
    'most recent session' relative-time marker — does not change storage."""
    base = character_name.lower()
    for candidate in [base, base.replace(" ", "."), base.replace(".", " ")]:
        p = os.path.join(SESSION_SUMMARY_DIR, f"{candidate}_summary.txt")
        if os.path.exists(p):
            return p
    return None


# ── Session memory selection ────────────────────────────────────────────────
# Each stored summary may carry an inline ISO-8601 timestamp on its
# ---SESSION--- delimiter line (written by save_session_summary on new
# appends). Legacy entries with no inline timestamp fall back to the file's
# mtime so legacy summaries can still be sorted.
#
# The newest saved summary is always returned for the tail-injection slot,
# no matter how old it is. Older stored summaries are returned for the
# secondary "recent sessions" block. The "End Session" save path still keeps
# only MAX_SUMMARIES entries, so replacement happens when a new summary is
# created, not because a clock window expired. (changes.md.)
# One capture group = the optional trailing ISO timestamp on a delimiter line.
_SESSION_TS_RE = re.compile(r'(?m)^[ \t]*---SESSION---[ \t]*([^\n]*)$')


def _parse_iso_utc(s):
    """Parse an ISO-8601 timestamp string to an aware UTC datetime, or None."""
    import datetime as _dt
    s = (s or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = _dt.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return dt.astimezone(_dt.timezone.utc)
    except Exception:
        return None


def parse_session_summaries(character_name):
    """Parse a character's session-summary file into [(timestamp, text), ...]
    in stored order (oldest first). Each timestamp is the inline ISO-8601 stamp
    on that entry's ---SESSION--- delimiter line; entries with no inline stamp
    (legacy) fall back to the file's mtime. Returns [] when the file is missing
    or empty. Does NOT mutate the file."""
    import datetime as _dt
    path = _resolve_session_summary_path(character_name)
    if not path:
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except Exception as e:
        print(f"⚠️ parse_session_summaries error: {e}")
        return []
    if not raw.strip():
        return []
    try:
        mtime = _dt.datetime.fromtimestamp(os.path.getmtime(path), _dt.timezone.utc)
    except Exception:
        mtime = _dt.datetime.now(_dt.timezone.utc)
    # re.split with one capture group yields:
    #   [text_before_first_delim, cap0, text_after_delim0, cap1, text1, ...]
    pieces = _SESSION_TS_RE.split(raw)
    entries = []
    # Leading text before any delimiter = a legacy first entry (no timestamp).
    lead = pieces[0].strip()
    if lead:
        entries.append((mtime, lead))
    i = 1
    while i + 1 <= len(pieces) - 1:
        cap = pieces[i]
        text = (pieces[i + 1] or "").strip()
        i += 2
        if not text:
            continue
        entries.append((_parse_iso_utc(cap) or mtime, text))
    return entries


def select_session_summaries(character_name):
    """Select a character's stored session summaries without time expiry.
    Returns (hot, cold):
      hot  : (timestamp, text) of the single most-recent summary, always.
      cold : every other stored summary, oldest first.
    Returns (None, []) when the summary file is missing or empty."""
    entries = parse_session_summaries(character_name)
    if not entries:
        return None, []
    newest_idx = max(range(len(entries)), key=lambda i: entries[i][0])
    hot = entries[newest_idx]
    cold = [
        (ts, text)
        for i, (ts, text) in enumerate(entries)
        if i != newest_idx
    ]
    cold.sort(key=lambda e: e[0])          # oldest first
    return hot, cold


def load_session_summary(character_name):
    """Return stored session summaries for a character, capped at MAX_SUMMARY_CHARS.
    Tries multiple filename variants to handle dot/space mismatches."""
    base = character_name.lower()
    candidates = [
        base,
        base.replace(" ", "."),
        base.replace(".", " "),
    ]
    print(f"🧠 load_session_summary called with: '{character_name}'")
    print(f"🧠 SESSION_SUMMARY_DIR: {SESSION_SUMMARY_DIR}")
    print(f"🧠 Trying candidates: {[c + '_summary.txt' for c in candidates]}")
    path = None
    for candidate in candidates:
        p = os.path.join(SESSION_SUMMARY_DIR, f"{candidate}_summary.txt")
        print(f"🧠 Checking: {p} — exists: {os.path.exists(p)}")
        if os.path.exists(p):
            path = p
            break
    if not path:
        print(f"🧠 No summary file found for '{character_name}' — skipping injection")
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read().strip()
        if not text:
            print(f"🧠 Summary file exists but is empty")
            return ""
        if len(text) > MAX_SUMMARY_CHARS:
            text = text[-MAX_SUMMARY_CHARS:]
            idx = text.find(SESSION_DIVIDER)
            if idx != -1:
                text = text[idx + len(SESSION_DIVIDER):]
        print(f"🧠 Summary loaded: {len(text)} chars from {path}")
        return text.strip()
    except Exception as e:
        print(f"⚠️ load_session_summary error: {e}")
        return ""


def save_session_summary(character_name, new_entry):
    """Append a new summary entry, keeping only the last MAX_SUMMARIES.

    The NEW entry is written with an inline ISO-8601 UTC timestamp on its
    '---SESSION---' delimiter line. Pre-existing legacy entries are preserved
    verbatim with NO timestamp backfilled, using file mtime only for sorting.
    The file is rewritten in the header-per-entry format (a
    '---SESSION--- <iso>' line before each entry); every entry's text content
    is preserved unchanged."""
    import datetime as _dt
    os.makedirs(SESSION_SUMMARY_DIR, exist_ok=True)
    path = os.path.join(SESSION_SUMMARY_DIR, f"{character_name.lower()}_summary.txt")
    existing = []   # list of (iso_str_or_None, text)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read()
        except Exception as e:
            print(f"⚠️ save_session_summary read error: {e}")
            raw = ""
        if raw.strip():
            pieces = _SESSION_TS_RE.split(raw)
            lead = pieces[0].strip()
            if lead:
                existing.append((None, lead))   # legacy first entry — no backfill
            i = 1
            while i + 1 <= len(pieces) - 1:
                cap = (pieces[i] or "").strip()
                text = (pieces[i + 1] or "").strip()
                i += 2
                if text:
                    existing.append((cap or None, text))
    now_iso = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    existing.append((now_iso, new_entry.strip()))
    existing = existing[-MAX_SUMMARIES:]
    blocks = []
    for iso, text in existing:
        header = f"---SESSION--- {iso}" if iso else "---SESSION---"
        blocks.append(f"{header}\n{text}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(blocks))
    print(f"✅ Session summary saved for {character_name} "
          f"({len(existing)} entries stored, newest timestamped {now_iso})")


@session_summary_bp.route("/generate_session_summary", methods=["POST"])
def generate_session_summary():
    """
    Generate a first-person session summary in the character's own voice,
    then append it to session_summaries/<character>_summary.txt.
    Called by the frontend 'End Session' button.
    """
    try:
        try:
            from app_runtime_helpers import get_api_url, get_stop_tokens
        except Exception as helper_error:
            print(f"⚠️ summary helper import failed: {helper_error}", flush=True)

            def get_api_url():
                try:
                    with open("settings.json", "r", encoding="utf-8") as _sf:
                        _settings = json.load(_sf) or {}
                    _port = _settings.get("llama_args", {}).get("port", 8080)
                except Exception:
                    _port = 8080
                return f"http://127.0.0.1:{_port}"

            def get_stop_tokens():
                try:
                    with open("settings.json", "r", encoding="utf-8") as _sf:
                        _settings = json.load(_sf) or {}
                    _template = (
                        _settings.get("llama_args", {})
                        .get("chat_template", "chatml")
                    )
                    _model = (_settings.get("llama_last_model", "") or "").lower()
                except Exception:
                    _template = "chatml"
                    _model = ""
                _template = str(_template or "chatml").strip().lower()
                if "gemma" in _model or _template == "jinja":
                    return ["<end_of_turn>", "<start_of_turn>"]
                return ["<|im_end|>", "<|im_start|>"]

        data = request.get_json()
        character_name = data.get("character", "").strip()
        messages = data.get("messages", [])
        user_name = data.get("user_name", "").strip() or "the user"

        if not character_name:
            return jsonify({"status": "error", "error": "No character specified"}), 400
        if not messages:
            return jsonify({"status": "error", "error": "No messages to summarise"}), 400

        # Load character card
        char_dir = os.path.join(os.path.dirname(__file__), "characters")
        char_path = os.path.join(char_dir, f"{character_name}.json")
        if not os.path.exists(char_path):
            return jsonify({"status": "error", "error": f"Character not found: {character_name}"}), 404
        with open(char_path, "r", encoding="utf-8") as f:
            char_data = json.load(f)

        char_display_name = char_data.get("name", character_name)

        # Build character context for summary call (description + main prompt)
        card_parts = []
        if char_data.get("description"):
            card_parts.append(f"Description: {char_data['description']}")
        if char_data.get("main_prompt"):
            card_parts.append(char_data["main_prompt"])
        char_card_text = "\n\n".join(card_parts).strip()

        # Add example dialogue as style reference only — fenced so model mirrors tone not content
        ex_style = ""
        if char_data.get("example_dialogue"):
            ex = char_data["example_dialogue"].strip()
            ex = re.sub(r'<\|im_start\|>\w*', '', ex)
            ex = re.sub(r'<\|im_end\|>', '', ex)
            ex = ex.strip()
            ex_style = (
                "\n\n───────────────────────────────────────\n"
                "STYLE REFERENCE — mirror this tone and writing style in your memory note.\n"
                "DO NOT reproduce or reference any of these topics — style only.\n"
                "───────────────────────────────────────\n"
                + ex +
                "\n───────────────────────────────────────"
            )

        summary_system = (
            f"IMPORTANT: You are {char_display_name}, not {user_name}. "
            f"{user_name} is the other person in the conversation. "
            f"Never write from {user_name}'s perspective. Never confuse yourself with {user_name}.\n\n"
            f"You are {char_display_name}.\n\n"
            f"{char_card_text}\n\n"
            f"You are writing a private memory note to yourself about the conversation that just happened. "
            f"You are {char_display_name} — write ONLY from your own perspective, about what YOU experienced, felt, and observed. "
            "NEVER write from the user's perspective or slip into their point of view.\n\n"
            "Write it entirely in your own voice, in first person, as if thinking to yourself. "
            f"Every sentence should reflect YOUR thoughts and feelings as {char_display_name}.\n\n"
            f"The person you were speaking with is called {user_name}. Always refer to them by name — never call them 'the user'.\n\n"
            "Focus on:\n"
            "- The key topics and things you discussed — be specific, name actual things that were said\n"
            "- The tone and vibe of the conversation by the end — this is the most important thing\n"
            "- How YOU felt during the conversation\n"
            "- Any unresolved threads worth picking up next time\n\n"
            "Write at least 10 full sentences. Do NOT stop after a few sentences — keep going until you "
            "have fully captured everything that mattered about this conversation. "
            "Do NOT write formally or in third person. Write naturally, like a personal diary entry. "
            "Do not wrap up or conclude early — if you have more to say, say it."
            + ex_style
        )

        # Filter to user/assistant only, cap at last 30 messages
        conv_messages = [m for m in messages if m.get("role") in ("user", "assistant")][-30:]

        import datetime as _dt
        timestamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")

        # Build plain transcript — labelled by name, no user/assistant ChatML roles
        # This prevents the model confusing its summariser role with its chatting role
        def _build_transcript(msgs):
            lines = []
            for m in msgs:
                role = m.get("role", "user")
                content = m.get("content", "")
                if isinstance(content, list):
                    content = " ".join(p.get("text", "") for p in content if p.get("type") == "text")
                content = content.strip()
                # Strip injected search blocks so stale web/chat-search content
                # doesn't get baked into the saved memory file
                if "WEB SEARCH RESULTS" in content:
                    content = re.split(r'\[WEB SEARCH RESULTS', content)[0].strip()
                if "CHAT HISTORY RESULTS" in content:
                    content = re.split(r'\[CHAT HISTORY RESULTS', content)[0].strip()
                if content:
                    label = user_name if role == "user" else char_display_name
                    lines.append(f"{label}: {content}")
            return "\n\n".join(lines)

        def _build_prompt(transcript_text):
            return (
                f"<|im_start|>system\n"
                f"{summary_system}\n\n"
                f"───────────────────────────────────────\n"
                f"CONVERSATION TRANSCRIPT:\n"
                f"───────────────────────────────────────\n"
                f"{transcript_text}\n"
                f"───────────────────────────────────────\n"
                f"<|im_end|>\n"
                f"<|im_start|>user\n"
                f"You are {char_display_name}. Write your private memory note about this conversation. "
                f"{user_name} is the person you spoke WITH — refer to them by name. "
                f"You are NOT {user_name}. Write only from {char_display_name}'s own perspective.\n"
                f"<|im_end|>\n"
                f"<|im_start|>assistant\n"
                f"[{timestamp}]\n"
                f"Last time {user_name} and I talked,"
            )

        def _build_cloud_user_prompt(transcript_text):
            return (
                "CONVERSATION TRANSCRIPT:\n"
                "----------------------------------------\n"
                f"{transcript_text}\n"
                "----------------------------------------\n\n"
                f"You are {char_display_name}. Write your private memory note about this conversation. "
                f"{user_name} is the person you spoke WITH — refer to them by name. "
                f"You are NOT {user_name}. Write only from {char_display_name}'s own perspective.\n\n"
                f"Do not include a timestamp. Do not include the words "
                f"\"Last time {user_name} and I talked\"; continue naturally after that phrase."
            )

        def _load_cloud_settings():
            try:
                with open("settings.json", "r", encoding="utf-8") as _sf:
                    return json.load(_sf)
            except Exception:
                return {}

        def _generate_openai_summary(prompt_text, max_tokens):
            from app_runtime_helpers import openai_caps_for
            from cloud_api_routes import get_openai_base_url

            settings = _load_cloud_settings()
            api_key = (settings.get("openai_api_key", "") or "").strip()
            model = (settings.get("openai_model", "") or "").strip() or "gpt-4o"
            if not api_key:
                raise RuntimeError("OpenAI backend selected but no API key is configured")

            caps = openai_caps_for(model)
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": summary_system},
                    {"role": "user", "content": prompt_text},
                ],
                "stream": False,
            }
            payload[caps["token_param"]] = max_tokens
            if caps["sampling"]:
                payload["temperature"] = 0.75
                payload["top_p"] = 0.9

            base_url = get_openai_base_url()
            print(f"🧠 summary: OpenAI path model={model}, max_tokens={max_tokens}", flush=True)
            resp = requests.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=120,
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"OpenAI returned {resp.status_code}: {resp.text[:500]}")
            data = resp.json()
            return data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()

        def _generate_anthropic_summary(prompt_text, max_tokens):
            from app_runtime_helpers import supports_temperature
            from cloud_api_routes import get_anthropic_base_url

            settings = _load_cloud_settings()
            api_key = (settings.get("anthropic_api_key", "") or "").strip()
            model = (settings.get("anthropic_model", "") or "").strip() or "claude-sonnet-4-5"
            if not api_key:
                raise RuntimeError("Anthropic backend selected but no API key is configured")

            payload = {
                "model": model,
                "max_tokens": max_tokens,
                "stream": False,
                "system": summary_system,
                "messages": [{"role": "user", "content": prompt_text}],
            }
            if supports_temperature(model):
                payload["temperature"] = 0.75

            base_url = get_anthropic_base_url()
            print(f"🧠 summary: Anthropic path model={model}, max_tokens={max_tokens}", flush=True)
            resp = requests.post(
                f"{base_url}/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
                timeout=120,
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"Anthropic returned {resp.status_code}: {resp.text[:500]}")
            data = resp.json()
            parts = data.get("content", [])
            return "".join(
                part.get("text", "")
                for part in parts
                if isinstance(part, dict) and part.get("type") == "text"
            ).strip()

        # Read live ctx_size from settings.json — same approach as the chat route's
        # dynamic-n_predict fix (May 9). Without this, we hardcoded n_predict=600
        # and trusted the prompt fit; for big character cards (Helcyon's ~2k-token
        # example dialogue + main_prompt) plus 30 messages of transcript, this
        # could exceed available KV space and llama.cpp returns 400.
        try:
            with open("settings.json", "r", encoding="utf-8") as _snf:
                _ctx_size_live = int(json.load(_snf).get("llama_args", {}).get("ctx_size", 12288))
        except Exception:
            _ctx_size_live = 12288

        # Reserve space for generation. We aim for up to 600 tokens of summary
        # but will accept as little as 256 if context is tight.
        _GEN_RESERVE_MIN = 256
        _GEN_RESERVE_TARGET = 600

        # Build prompt and trim transcript from the head if it overflows.
        # rough_token_count undercounts BPE by ~25%, so multiply by 1.25 for the
        # real-tokens estimate (same fudge as truncation.py).
        transcript = _build_transcript(conv_messages)
        summary_prompt = _build_prompt(transcript)
        _est_real = int(rough_token_count(summary_prompt) * 1.25)
        _budget = _ctx_size_live - _GEN_RESERVE_MIN

        if _est_real > _budget and conv_messages:
            # Drop oldest messages a few at a time until prompt fits or transcript empty.
            print(
                f"⚠️ summary prompt ~{_est_real} real tokens > budget {_budget} "
                f"(ctx={_ctx_size_live}, reserve={_GEN_RESERVE_MIN}) — "
                f"trimming transcript from {len(conv_messages)} msgs",
                flush=True,
            )
            while _est_real > _budget and len(conv_messages) > 4:
                conv_messages = conv_messages[2:]  # drop two oldest at a time
                transcript = _build_transcript(conv_messages)
                summary_prompt = _build_prompt(transcript)
                _est_real = int(rough_token_count(summary_prompt) * 1.25)
            print(f"   → trimmed to {len(conv_messages)} msgs, ~{_est_real} real tokens", flush=True)

        # Cap n_predict to actual KV space remaining after the (possibly-trimmed)
        # prompt. Floor at GEN_RESERVE_MIN so we always have at least some headroom.
        _available_for_gen = max(_GEN_RESERVE_MIN, _ctx_size_live - _est_real)
        _n_predict = min(_GEN_RESERVE_TARGET, _available_for_gen)
        print(
            f"🧠 summary: prompt ~{_est_real} real / {_ctx_size_live} ctx, "
            f"n_predict={_n_predict}",
            flush=True,
        )

        payload = {
            "prompt": summary_prompt,
            "temperature": 0.75,
            "n_predict": _n_predict,
            "top_p": 0.9,
            "repeat_penalty": 1.05,
            "stream": False,
            "stop": get_stop_tokens(),
        }

        cloud_settings = _load_cloud_settings()
        backend_mode = (cloud_settings.get("backend_mode", "local") or "local").lower()
        cloud_enabled = bool(cloud_settings.get("cloud_api_enabled", False))
        if backend_mode == "openai" and cloud_enabled:
            summary_text = _generate_openai_summary(_build_cloud_user_prompt(transcript), _n_predict)
        elif backend_mode == "anthropic" and cloud_enabled:
            summary_text = _generate_anthropic_summary(_build_cloud_user_prompt(transcript), _n_predict)
        else:
            resp = requests.post(f"{get_api_url()}/completion", json=payload, timeout=60)
            if resp.status_code >= 400:
                # Surface llama.cpp's actual error message — the bare HTTPError string
                # (e.g. "400 Client Error: Bad Request for url: …") tells you nothing
                # about WHAT the server rejected. Read the body and include it.
                body = ""
                try:
                    body = resp.text[:500]
                except Exception:
                    pass
                print(f"❌ llama.cpp /completion returned {resp.status_code}: {body}", flush=True)
                print(f"   prompt length: {len(summary_prompt)} chars, ~{_est_real} real tokens", flush=True)
                print(f"   ctx_size={_ctx_size_live}, n_predict={_n_predict}, msgs={len(conv_messages)}", flush=True)
                return jsonify({
                    "status": "error",
                    "error": f"llama.cpp returned {resp.status_code}: {body or 'no body'}",
                }), 500
            result = resp.json()
            summary_text = result.get("content", "").strip()

        # Strip any leaked ChatML tokens
        summary_text = re.sub(r'<\|im_start\|>\w*', '', summary_text)
        summary_text = re.sub(r'<\|im_end\|>', '', summary_text)
        summary_text = re.sub(r'\[im_end\]', '', summary_text)  # malformed variant
        summary_text = summary_text.strip()

        if not summary_text:
            return jsonify({"status": "error", "error": "Model returned empty summary"}), 500

        # Prepend the prefill we forced ("Last time") so the saved entry is complete
        entry = f"[{timestamp}]\nLast time {user_name} and I talked, {summary_text}"
        save_session_summary(character_name, entry)

        print(f"🧠 Session summary generated for {character_name}: {summary_text[:80]}...")
        return jsonify({"status": "ok", "summary": summary_text})

    except Exception as e:
        import traceback
        print(f"❌ generate_session_summary error: {e}")
        traceback.print_exc()
        return jsonify({"status": "error", "error": str(e)}), 500
