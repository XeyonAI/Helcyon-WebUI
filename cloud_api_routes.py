import os, json, requests
from flask import Blueprint, request, jsonify

cloud_api_bp = Blueprint('cloud_api', __name__)

# settings.json sits next to this module in the app root. Defined locally so
# this blueprint has no import dependency back on app.py — mirrors the pattern
# in theme_routes.py / situation_routes.py.
SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "settings.json")


# --------------------------------------------------
# Base-URL helpers (also imported back by app.py — chat()/continue call these)
# --------------------------------------------------
def get_openai_base_url():
    """Read the OpenAI-compatible base URL from settings.json.

    The OpenAI path is now a generic OpenAI-compatible client — it can hit any
    provider that speaks /v1/chat/completions (Anthropic, xAI/Grok, OpenRouter,
    Together, Groq, Mistral, Fireworks, LM Studio, vLLM, …) by setting
    openai_base_url to e.g. https://api.anthropic.com/v1 or
    https://openrouter.ai/api/v1.

    Returns the URL up to but NOT including /chat/completions. Falls back to
    https://api.openai.com/v1 on any of:
      - field missing from settings.json (older configs)
      - field present but empty / whitespace
      - settings.json unreadable / not valid JSON

    Trailing slash is stripped so callers can always append /chat/completions
    or /models without worrying about doubled slashes.

    ⚠️ Any code that hits an OpenAI-style API MUST go through this helper.
    Do not reintroduce hardcoded references to api.openai.com — that would
    silently break every non-OpenAI provider.
    """
    _default = "https://api.openai.com/v1"
    try:
        with open("settings.json", "r", encoding="utf-8") as f:
            s = json.load(f)
        url = (s.get("openai_base_url", "") or "").strip().rstrip("/")
        return url if url else _default
    except Exception:
        return _default


def get_anthropic_base_url():
    """Read the Anthropic base URL from settings.json.

    Unlike get_openai_base_url(), this path speaks Anthropic's NATIVE Messages
    wire format (x-api-key header, /messages endpoint, top-level `system`, SSE
    `content_block_delta` events) — it is NOT OpenAI-compatible. Point this at a
    real Anthropic-format endpoint only (api.anthropic.com or a self-hosted
    Anthropic-compatible proxy), never an OpenAI-style gateway.

    Returns the URL up to but NOT including /messages (callers append /messages
    or /models). Trailing slash stripped. Falls back to https://api.anthropic.com/v1
    when the field is missing/empty or settings.json is unreadable.
    """
    _default = "https://api.anthropic.com/v1"
    try:
        with open("settings.json", "r", encoding="utf-8") as f:
            s = json.load(f)
        url = (s.get("anthropic_base_url", "") or "").strip().rstrip("/")
        return url if url else _default
    except Exception:
        return _default


# --------------------------------------------------
# Cloud-API master switch
# --------------------------------------------------
@cloud_api_bp.route("/cloud_api_enabled", methods=["GET"])
def get_cloud_api_enabled():
    """Return the cloud-API master switch state (default false) and the current
    backend_mode, so the Connect button can decide whether a cloud provider is
    selected (and which one) without a second request."""
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
        return jsonify({
            "cloud_api_enabled": bool(s.get("cloud_api_enabled", False)),
            "backend_mode": s.get("backend_mode", "local"),
        })
    except Exception as e:
        return jsonify({"cloud_api_enabled": False, "backend_mode": "local", "error": str(e)})


@cloud_api_bp.route("/cloud_api_enabled", methods=["POST"])
def set_cloud_api_enabled():
    """Persist the cloud-API master switch to settings.json.
    Body: {"enabled": bool, "backend_mode"?: "openai"|"anthropic"|"local"}.

    When connecting, the client also sends the selected backend_mode so we
    persist it ATOMICALLY in the same write. This is essential: the UI's
    setBackendMode() is display-only (it does NOT write to disk), so a user who
    picks OpenAI/Anthropic via the mode button and clicks Connect — without
    pressing that provider's Save button — would otherwise flip
    cloud_api_enabled=true while backend_mode stayed stale on disk (e.g. 'local').
    The pill (checkOpenAIIndicator) and the /chat cloud gate both read disk
    backend_mode, so the connection would never register. Persisting both here
    keeps cloud_api_enabled and backend_mode consistent. When false, the /chat
    cloud gate refuses OpenAI/Anthropic backends. (changes.md.)"""
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled", False))
    # Optional backend_mode — only applied when present (disconnect omits it, so
    # the selected provider is preserved across a disconnect).
    _mode = data.get("backend_mode")
    if _mode is not None:
        _mode = str(_mode).strip().lower()
        if _mode not in ("local", "openai", "anthropic"):
            return jsonify({"status": "error", "error": f"invalid backend_mode {_mode!r}"}), 400
    # Read existing settings first. If we CAN'T, do not write — writing a
    # stripped dict would wipe backend_mode/keys/etc. Surface the error so the
    # client doesn't flip state on a failed persist. (changes.md.)
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
    except Exception as e:
        print(f"❌ set_cloud_api_enabled: cannot read settings.json — refusing to write: {e}", flush=True)
        return jsonify({"status": "error", "error": f"read failed: {e}"}), 500
    s["cloud_api_enabled"] = enabled
    if _mode is not None:
        s["backend_mode"] = _mode
    try:
        import tempfile, shutil
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
        shutil.move(tmp, SETTINGS_FILE)
        # Read-back verification — confirm the value is actually on disk before
        # returning 200, so the client never flips its button/pill on an
        # unpersisted write. (changes.md.)
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            _verify = json.load(f)
        if bool(_verify.get("cloud_api_enabled", None)) != enabled:
            print("❌ set_cloud_api_enabled: read-back mismatch — value not persisted", flush=True)
            return jsonify({"status": "error", "error": "verification failed — value not persisted"}), 500
        if _mode is not None and _verify.get("backend_mode") != _mode:
            print("❌ set_cloud_api_enabled: backend_mode read-back mismatch — not persisted", flush=True)
            return jsonify({"status": "error", "error": "verification failed — backend_mode not persisted"}), 500
        print(f"{'🌐 Cloud API ENABLED' if enabled else '🔒 Cloud API DISABLED'}"
              f"{f' (backend_mode={_mode})' if _mode is not None else ''} (persisted + verified on disk)", flush=True)
        return jsonify({"status": "ok", "cloud_api_enabled": enabled,
                        "backend_mode": s.get("backend_mode", "local")})
    except Exception as e:
        print(f"❌ set_cloud_api_enabled failed: {e}", flush=True)
        return jsonify({"status": "error", "error": str(e)}), 500


# --------------------------------------------------
# Brave Search API key
# --------------------------------------------------
@cloud_api_bp.route("/get_brave_api_key", methods=["GET"])
def get_brave_api_key_route():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
        return jsonify({"brave_api_key": s.get("brave_api_key", "")})
    except Exception as e:
        return jsonify({"brave_api_key": "", "error": str(e)})

@cloud_api_bp.route("/save_brave_api_key", methods=["POST"])
def save_brave_api_key_route():
    data = request.get_json()
    key = data.get("brave_api_key", "").strip()
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
        s["brave_api_key"] = key
        import tempfile, shutil
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
        shutil.move(tmp, SETTINGS_FILE)
        print(f"✅ Brave API key saved ({len(key)} chars)")
        return jsonify({"status": "ok"})
    except Exception as e:
        print(f"❌ save_brave_api_key failed: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500


# --------------------------------------------------
# OpenAI Backend Settings Routes
# --------------------------------------------------
@cloud_api_bp.route("/get_openai_settings", methods=["GET"])
def get_openai_settings_route():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
        # openai_base_url: surface the resolved value through the helper so the
        # UI displays the actual default (https://api.openai.com/v1) on first
        # load even when the field is missing from older settings.json files.
        return jsonify({
            "backend_mode":    s.get("backend_mode", "local"),
            "openai_api_key":  s.get("openai_api_key", ""),
            "openai_model":    s.get("openai_model", "gpt-4o"),
            "openai_base_url": (s.get("openai_base_url", "") or "").strip() or get_openai_base_url(),
            "cloud_api_enabled": bool(s.get("cloud_api_enabled", False)),
        })
    except Exception as e:
        return jsonify({
            "backend_mode": "local",
            "openai_api_key": "",
            "openai_model": "gpt-4o",
            "openai_base_url": "https://api.openai.com/v1",
            "error": str(e),
        })

@cloud_api_bp.route("/save_openai_settings", methods=["POST"])
def save_openai_settings_route():
    data = request.get_json()
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
        _old_mode = s.get("backend_mode", "local")
        s["backend_mode"]   = data.get("backend_mode", "local")
        # Changing the backend mode force-disconnects cloud: selecting a mode must
        # never silently keep a live cloud session. Reconnect explicitly via the
        # chat page's Connect button. (changes.md.)
        if s["backend_mode"] != _old_mode:
            s["cloud_api_enabled"] = False
        # ⚠️ Preserve-on-empty — an empty incoming key NEVER overwrites a stored
        # one. This is the guard against the #1 cause of "my API key vanished":
        # a Save fired while the field was blank (e.g. an intermittent
        # get_openai_settings read failure repopulated the field with ""), which
        # would otherwise persist "" and wipe the key. To intentionally clear a
        # key, delete the field from settings.json directly. (changes.md.)
        _incoming_key = (data.get("openai_api_key", "") or "").strip()
        if _incoming_key:
            s["openai_api_key"] = _incoming_key
        elif "openai_api_key" not in s:
            s["openai_api_key"] = ""   # establish the field on first save; never wipe an existing key
        s["openai_model"]   = data.get("openai_model", "gpt-4o").strip()
        # openai_base_url: strip trailing slash; empty → write OpenAI default
        # back to disk so the round-trip from a fresh settings.json populates
        # the field explicitly on the second load.
        _incoming_base = (data.get("openai_base_url", "") or "").strip().rstrip("/")
        s["openai_base_url"] = _incoming_base or "https://api.openai.com/v1"
        import tempfile, shutil
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
        shutil.move(tmp, SETTINGS_FILE)
        mode = s["backend_mode"]
        print(f"✅ OpenAI settings saved — backend_mode={mode}, model={s['openai_model']}, base_url={s['openai_base_url']}")
        return jsonify({"status": "ok"})
    except Exception as e:
        print(f"❌ save_openai_settings failed: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500


@cloud_api_bp.route("/get_openai_models", methods=["GET"])
def get_openai_models_route():
    """Fetch available chat models from the configured OpenAI-compatible endpoint.

    Hits {openai_base_url}/models. Most providers (OpenAI, OpenRouter, Together,
    Groq, Mistral, Fireworks, Anthropic) support this. Providers that don't
    will return a non-200 here, which is surfaced as an error — users on those
    providers should type the model name into the dropdown directly (it
    persists via _setOpenAIModelSelect).
    """
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
        api_key = s.get("openai_api_key", "").strip()
        if not api_key:
            return jsonify({"status": "error", "error": "No API key set"}), 400

        _base_url = get_openai_base_url()
        r = requests.get(
            f"{_base_url}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        if r.status_code != 200:
            return jsonify({"status": "error", "error": f"Provider returned {r.status_code}: {r.text[:200]}"}), 502

        all_models = r.json().get("data", [])

        # Filter to chat-capable models only — exclude embeddings, tts, whisper, dall-e, etc.
        CHAT_PREFIXES = ("gpt-", "o1", "o3", "o4", "chatgpt")
        EXCLUDE_SUFFIXES = ("-instruct", "-search", "-realtime", "-audio")
        EXCLUDE_CONTAINS = ("embedding", "tts", "whisper", "dall-e", "moderation", "babbage", "davinci", "ada", "curie")

        chat_ids = []
        for m in all_models:
            mid = m.get("id", "")
            ml = mid.lower()
            if not any(ml.startswith(p) for p in CHAT_PREFIXES):
                continue
            if any(ml.endswith(s) for s in EXCLUDE_SUFFIXES):
                continue
            if any(x in ml for x in EXCLUDE_CONTAINS):
                continue
            chat_ids.append(mid)

        # Sort: put flagship models first, then by name
        def _sort_key(mid):
            ml = mid.lower()
            if "gpt-4o" in ml and "mini" not in ml:
                return (0, mid)
            if "gpt-4o-mini" in ml:
                return (1, mid)
            if ml.startswith("o"):
                return (2, mid)
            return (3, mid)

        chat_ids.sort(key=_sort_key)
        print(f"✅ OpenAI models fetched: {len(chat_ids)} chat models")
        return jsonify({"status": "ok", "models": chat_ids})

    except Exception as e:
        print(f"❌ get_openai_models failed: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500


# --------------------------------------------------
# Anthropic Backend Settings Routes
# --------------------------------------------------
# Anthropic creds are stored ALONGSIDE the OpenAI ones (separate keys in
# settings.json), so both providers stay saved and switching is just a
# backend_mode flip — no re-pasting. backend_mode is shared and written by both
# this route and /save_openai_settings (whichever Save button you press persists
# the toggle's current value); the per-provider key/model/base_url fields are
# only ever touched by their own route, so saving one never clobbers the other.
@cloud_api_bp.route("/get_anthropic_settings", methods=["GET"])
def get_anthropic_settings_route():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
        return jsonify({
            "backend_mode":       s.get("backend_mode", "local"),
            "anthropic_api_key":  s.get("anthropic_api_key", ""),
            "anthropic_model":    s.get("anthropic_model", ""),
            "anthropic_base_url": (s.get("anthropic_base_url", "") or "").strip() or get_anthropic_base_url(),
            "cloud_api_enabled":  bool(s.get("cloud_api_enabled", False)),
            # Extended-thinking toggle + token budget (display of Claude's reasoning).
            "anthropic_thinking":        bool(s.get("anthropic_thinking", False)),
            "anthropic_thinking_budget": int(s.get("anthropic_thinking_budget", 2048) or 2048),
        })
    except Exception as e:
        return jsonify({
            "backend_mode": "local",
            "anthropic_api_key": "",
            "anthropic_model": "",
            "anthropic_base_url": "https://api.anthropic.com/v1",
            "anthropic_thinking": False,
            "anthropic_thinking_budget": 2048,
            "error": str(e),
        })

@cloud_api_bp.route("/save_anthropic_settings", methods=["POST"])
def save_anthropic_settings_route():
    data = request.get_json()
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
        _old_mode = s.get("backend_mode", "local")
        s["backend_mode"]       = data.get("backend_mode", "local")
        # Changing the backend mode force-disconnects cloud (see save_openai). (changes.md.)
        if s["backend_mode"] != _old_mode:
            s["cloud_api_enabled"] = False
        # ⚠️ Preserve-on-empty — see save_openai_settings_route. An empty incoming
        # key never overwrites a stored one (guards against blank-field saves /
        # intermittent read failures wiping the key). (changes.md.)
        _incoming_key = (data.get("anthropic_api_key", "") or "").strip()
        if _incoming_key:
            s["anthropic_api_key"] = _incoming_key
        elif "anthropic_api_key" not in s:
            s["anthropic_api_key"] = ""   # establish the field on first save; never wipe an existing key
        s["anthropic_model"]    = data.get("anthropic_model", "").strip()
        _incoming_base = (data.get("anthropic_base_url", "") or "").strip().rstrip("/")
        s["anthropic_base_url"] = _incoming_base or "https://api.anthropic.com/v1"
        # Extended thinking — only written when the field is present in the POST,
        # so a save from any other code path can't silently flip it.
        if "anthropic_thinking" in data:
            s["anthropic_thinking"] = bool(data.get("anthropic_thinking"))
        if "anthropic_thinking_budget" in data:
            try:
                _b = int(data.get("anthropic_thinking_budget") or 2048)
            except (TypeError, ValueError):
                _b = 2048
            s["anthropic_thinking_budget"] = max(1024, _b)   # API minimum is 1024
        import tempfile, shutil
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
        shutil.move(tmp, SETTINGS_FILE)
        print(f"✅ Anthropic settings saved — backend_mode={s['backend_mode']}, model={s['anthropic_model']}, base_url={s['anthropic_base_url']}")
        return jsonify({"status": "ok"})
    except Exception as e:
        print(f"❌ save_anthropic_settings failed: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500


@cloud_api_bp.route("/save_backend_mode", methods=["POST"])
def save_backend_mode_route():
    """Persist ONLY backend_mode — no provider keys touched.

    The OpenAI/Anthropic Save buttons persist backend_mode as a side-effect of
    saving their key/model fields, but Local has no Save button. Without this
    route, clicking Local only changed the UI (setBackendMode) and never wrote
    to disk, so on restart settings.json still held the last-saved cloud mode and
    the cloud master gate blocked all chat. This lets the Local button persist
    its choice immediately on click. (changes.md.)

    Hardened like /cloud_api_enabled: refuses to write if it can't read existing
    settings (a stripped {} would wipe keys/backend_mode), and reads settings
    back to confirm the value landed before returning 200.
    """
    data = request.get_json(silent=True) or {}
    mode = (data.get("backend_mode") or "local").strip().lower()
    if mode not in ("local", "openai", "anthropic"):
        return jsonify({"status": "error", "error": f"invalid backend_mode {mode!r}"}), 400
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
    except Exception as e:
        # Do NOT write a stripped dict — that would wipe keys/backend_mode.
        print(f"❌ save_backend_mode: cannot read settings.json, refusing to write: {e}")
        return jsonify({"status": "error", "error": f"cannot read settings: {e}"}), 500
    try:
        _old_mode = s.get("backend_mode", "local")
        s["backend_mode"] = mode
        # Changing the backend mode force-disconnects cloud (see save_openai). For
        # local this also guarantees the cloud master switch is off. (changes.md.)
        if mode != _old_mode:
            s["cloud_api_enabled"] = False
        import shutil
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
        shutil.move(tmp, SETTINGS_FILE)
        # Read back and confirm the write landed before claiming success.
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            _verify = json.load(f)
        if _verify.get("backend_mode") != mode:
            print(f"❌ save_backend_mode: read-back mismatch (wanted {mode}, "
                  f"got {_verify.get('backend_mode')})")
            return jsonify({"status": "error", "error": "write not confirmed"}), 500
        print(f"✅ backend_mode saved — {_old_mode} → {mode}", flush=True)
        return jsonify({"status": "ok", "backend_mode": mode})
    except Exception as e:
        print(f"❌ save_backend_mode failed: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500


@cloud_api_bp.route("/get_anthropic_models", methods=["GET"])
def get_anthropic_models_route():
    """Fetch available models from the Anthropic /v1/models endpoint.

    Native Anthropic format: GET {base}/models with x-api-key + anthropic-version
    headers, returns {"data": [{"id": "claude-..."}]}. Newest first (the API
    already returns descending by creation), filtered to claude-* chat models.
    """
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            s = json.load(f)
        api_key = s.get("anthropic_api_key", "").strip()
        if not api_key:
            return jsonify({"status": "error", "error": "No API key set"}), 400

        _base_url = get_anthropic_base_url()
        r = requests.get(
            f"{_base_url}/models",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            params={"limit": 100},
            timeout=10,
        )
        if r.status_code != 200:
            return jsonify({"status": "error", "error": f"Provider returned {r.status_code}: {r.text[:200]}"}), 502

        all_models = r.json().get("data", [])
        chat_ids = [m.get("id", "") for m in all_models if m.get("id", "").lower().startswith("claude")]
        print(f"✅ Anthropic models fetched: {len(chat_ids)} claude models")
        return jsonify({"status": "ok", "models": chat_ids})

    except Exception as e:
        print(f"❌ get_anthropic_models failed: {e}")
        return jsonify({"status": "error", "error": str(e)}), 500
