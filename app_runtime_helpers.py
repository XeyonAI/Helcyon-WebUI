import contextlib
import json
import os
import re
import socket
import tempfile
import time

import requests


_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_SETTINGS_FILE = os.path.join(_BASE_DIR, "settings.json")


def _read_settings():
    try:
        with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def get_api_url():
    settings = _read_settings()
    port = settings.get("llama_args", {}).get("port", 8080)
    return f"http://127.0.0.1:{port}"


WEB_PORT_SCAN_START = 8082
WEB_PORT_SCAN_END = 8179


def _web_port_is_free(port):
    """True when nothing is already bound on 0.0.0.0:port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        try:
            probe.bind(("0.0.0.0", int(port)))
            return True
        except OSError:
            return False


def _sibling_claimed_ports(base_dir=None):
    """Web ports persisted by other HWUI installs next to this one.

    _web_port_is_free() only sees ports bound right now, so two installs that
    are both stopped could otherwise be handed the same port at first run.
    """
    base_dir = base_dir or _BASE_DIR
    claimed = set()
    parent = os.path.dirname(base_dir)
    try:
        names = os.listdir(parent)
    except OSError:
        return claimed
    for name in names:
        other = os.path.join(parent, name)
        if os.path.normcase(other) == os.path.normcase(base_dir):
            continue
        if not os.path.isfile(os.path.join(other, "app.py")):
            continue
        try:
            with open(os.path.join(other, "settings.json"), "r", encoding="utf-8") as f:
                port = int((json.load(f) or {}).get("port"))
            claimed.add(port)
        except Exception:
            continue
    return claimed


@contextlib.contextmanager
def _port_assignment_lock(timeout=10.0):
    """Serialise port assignment/repair across installs launched together.

    Best-effort: if the lock cannot be taken in time we proceed unlocked rather
    than block startup.
    """
    handle = None
    locked = False
    try:
        handle = open(os.path.join(tempfile.gettempdir(), "hwui_web_port.lock"), "a+b")
        deadline = time.time() + timeout
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError:
                if time.time() >= deadline:
                    break
                time.sleep(0.1)
    except Exception:
        pass
    try:
        yield
    finally:
        if handle is not None:
            try:
                if locked:
                    if os.name == "nt":
                        import msvcrt
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
            except Exception:
                pass


def _persisted_port(settings):
    try:
        value = settings.get("port")
        if value is not None and int(value) > 0:
            return int(value)
    except (TypeError, ValueError):
        pass
    return None


def resolve_web_port(persist=True):
    """Return this installation's own HWUI web port, assigning/repairing as needed.

    Ownership lives in settings.json, which is gitignored and seeded from
    settings.default.json, so it is installation-local and untouched by code
    updates. The old seed hardcoded 8081 and the old resolver only avoided ports
    bound at that moment, so sibling installs could end up persisting the same
    port.

    Resolution order:
      1. A persisted numeric port that no sibling install also persists is
         returned untouched, always.
      2. A persisted port that a sibling install also persists (a duplicate
         reservation from the old resolver) is repaired: this installation, and
         only this one, moves to the first free port not reserved by any
         sibling. The sibling's settings are never written. Assignment is done
         under a cross-install lock and re-checked inside it, so when two
         conflicting builds launch together exactly one moves.
      3. A missing/null/non-numeric port (fresh install) gets the same scan.

    The scan starts above 8081 so a fresh install never claims the legacy
    default out from under an existing one that is merely stopped.

    `persist=False` resolves without writing, for callers that only display or
    probe the port.
    """
    existing = _persisted_port(_read_settings())
    if existing is not None and existing not in _sibling_claimed_ports():
        return existing

    with _port_assignment_lock():
        # Re-read inside the lock: a sibling may have just repaired/assigned.
        settings = _read_settings()
        existing = _persisted_port(settings)
        claimed = _sibling_claimed_ports()
        if existing is not None and existing not in claimed:
            return existing

        chosen = next(
            (p for p in range(WEB_PORT_SCAN_START, WEB_PORT_SCAN_END + 1)
             if p not in claimed and _web_port_is_free(p)),
            WEB_PORT_SCAN_START,
        )
        if not persist:
            return chosen

        settings["port"] = chosen
        try:
            tmp = _SETTINGS_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent=2)
            os.replace(tmp, _SETTINGS_FILE)
            if existing is None:
                print(
                    "First run: assigned this installation its own HWUI web port "
                    "{} (persisted to settings.json).".format(chosen),
                    flush=True,
                )
            else:
                print(
                    "Web port {} was also reserved by a sibling HWUI install; "
                    "moved this installation to {} (persisted to settings.json).".format(existing, chosen),
                    flush=True,
                )
        except Exception as exc:
            print(
                "WARNING: could not persist assigned web port {}: {!r}".format(chosen, exc),
                flush=True,
            )
        return chosen

    settings["port"] = chosen
    try:
        tmp = _SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
        os.replace(tmp, _SETTINGS_FILE)
        print(
            "First run: assigned this installation its own HWUI web port "
            "{} (persisted to settings.json).".format(chosen),
            flush=True,
        )
    except Exception as exc:
        print(
            "WARNING: could not persist assigned web port {}: {!r}".format(chosen, exc),
            flush=True,
        )
    return chosen


def substitute_placeholders(text, char_label, user_label):
    """Swap {{char}}/{{user}} (whitespace- and case-tolerant) for live names."""
    if not isinstance(text, str) or not text:
        return text
    if char_label:
        text = re.sub(r"\{\{\s*char\s*\}\}", char_label, text, flags=re.IGNORECASE)
    if user_label:
        text = re.sub(r"\{\{\s*user\s*\}\}", user_label, text, flags=re.IGNORECASE)
    return text


def get_stop_tokens(current_model=None):
    """Return stop tokens for the active local model/template."""
    settings = _read_settings()
    chat_template = (
        settings.get("llama_args", {})
        .get("chat_template", "chatml")
    )
    chat_template = str(chat_template or "chatml").strip().lower()
    model_name = (
        current_model
        or _detect_current_model()
        or settings.get("llama_last_model", "")
        or ""
    ).lower()

    is_gemma = "gemma" in model_name or chat_template == "jinja"
    is_qwen = "qwen" in model_name or chat_template == "qwen"

    if is_gemma:
        print("Using Gemma stop tokens (<end_of_turn>)", flush=True)
        return ["<end_of_turn>", "<start_of_turn>"]
    if is_qwen:
        print("Using Qwen stop tokens (<|im_end|>)", flush=True)
        return ["<|im_end|>", "<|im_start|>"]
    return ["<|im_end|>", "<|im_start|>"]


def _detect_current_model():
    try:
        response = requests.get(f"{get_api_url()}/v1/models", timeout=2)
        response.raise_for_status()
        data = response.json()
        models = data.get("data") or []
        if models:
            return models[0].get("id") or ""
    except Exception:
        pass
    return ""


ANTHROPIC_MODEL_SAMPLING_RULES = {
    "fable": {"allow": ["max_tokens", "stop_sequences"], "match": "contains"},
    "claude-opus-4-8": {"allow": ["max_tokens", "stop_sequences"]},
    "claude-opus-4-7": {"allow": ["max_tokens", "stop_sequences"]},
    "claude-sonnet-4-6": {"allow": ["temperature", "max_tokens", "stop_sequences"]},
    "claude-opus-4-6": {"allow": ["temperature", "max_tokens", "stop_sequences"]},
    "claude-haiku-4-5": {"allow": ["temperature", "max_tokens", "stop_sequences"]},
}
DEFAULT_ANTHROPIC_ALLOW = ["temperature", "max_tokens", "stop_sequences"]


def _anthropic_allow_for(model_id):
    model_id = (model_id or "").strip().lower()
    if model_id in ANTHROPIC_MODEL_SAMPLING_RULES:
        return ANTHROPIC_MODEL_SAMPLING_RULES[model_id]["allow"]
    best = None
    for key, rule in ANTHROPIC_MODEL_SAMPLING_RULES.items():
        if rule.get("match") == "contains":
            continue
        if model_id.startswith(key) and (best is None or len(key) > len(best)):
            best = key
    if best:
        return ANTHROPIC_MODEL_SAMPLING_RULES[best]["allow"]
    for key, rule in ANTHROPIC_MODEL_SAMPLING_RULES.items():
        if rule.get("match") == "contains" and key in model_id:
            return rule["allow"]
    return DEFAULT_ANTHROPIC_ALLOW


def supports_temperature(model_id):
    return "temperature" in _anthropic_allow_for(model_id)


OPENAI_MODEL_RULES = {
    "gpt-6": {"token_param": "max_completion_tokens", "sampling": False},
    "gpt-5": {"token_param": "max_completion_tokens", "sampling": False},
    "o1": {"token_param": "max_completion_tokens", "sampling": False},
    "o3": {"token_param": "max_completion_tokens", "sampling": False},
    "o4": {"token_param": "max_completion_tokens", "sampling": False},
}
_OPENAI_DEFAULT_RULE = {"token_param": "max_tokens", "sampling": True}


def openai_caps_for(model_id):
    """Resolve OpenAI param rules: exact match, longest prefix, then default."""
    if not model_id:
        return _OPENAI_DEFAULT_RULE
    model = model_id.strip().lower()
    if "search-preview" in model:
        return {"token_param": "max_tokens", "sampling": False}
    if model in OPENAI_MODEL_RULES:
        return OPENAI_MODEL_RULES[model]
    best = None
    for prefix, rule in OPENAI_MODEL_RULES.items():
        if model.startswith(prefix) and (best is None or len(prefix) > len(best)):
            best = prefix
    return OPENAI_MODEL_RULES[best] if best else _OPENAI_DEFAULT_RULE
