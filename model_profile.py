"""Shadow-mode model capability profile (migration Stage 1).

Builds one description of the loaded model from what llama.cpp itself reports
(``GET /props``: chat template, template capabilities, modalities, media marker)
plus GGUF metadata (architecture, training context, embedded template), and
compares it with the routing HWUI uses today.

SHADOW ONLY. Nothing here is consulted by request building, routing or
sampling. The result is logged and served at ``/model_profile`` so mismatches
can be reviewed before any routing is moved onto it (Stage 5).

Standalone on purpose: no imports from ``app``; the caller supplies the legacy
routing description and an HTTP getter, which keeps this module unit-testable
without Flask or a running server.
"""
import hashlib
import os
import struct
from dataclasses import asdict, dataclass, field

# GGUF value types (spec): scalar sizes, string = 8, array = 9.
_SCALAR_SIZES = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
_STRING, _ARRAY = 8, 9
_MAX_METADATA_ENTRIES = 1_000_000

TEMPLATE_CAP_KEYS = (
    "supports_system_role", "supports_typed_content", "supports_string_content",
    "supports_tools", "supports_preserve_reasoning", "supports_reasoning_effort",
)

_GGUF_CACHE = {}


def _exact(handle, size):
    data = handle.read(size)
    if len(data) != size:
        raise ValueError("Truncated GGUF metadata")
    return data


def _read_string(handle):
    length = struct.unpack("<Q", _exact(handle, 8))[0]
    if length > 64 * 1024 * 1024:
        raise ValueError("Unreasonable GGUF string length")
    return _exact(handle, length).decode("utf-8", errors="replace")


def _skip_value(handle, value_type):
    if value_type in _SCALAR_SIZES:
        handle.seek(_SCALAR_SIZES[value_type], 1)
    elif value_type == _STRING:
        handle.seek(struct.unpack("<Q", _exact(handle, 8))[0], 1)
    elif value_type == _ARRAY:
        element = struct.unpack("<I", _exact(handle, 4))[0]
        count = struct.unpack("<Q", _exact(handle, 8))[0]
        if element in _SCALAR_SIZES:
            handle.seek(_SCALAR_SIZES[element] * count, 1)
        else:
            for _ in range(count):
                _skip_value(handle, element)
    else:
        raise ValueError(f"Unsupported GGUF value type: {value_type}")


def _read_scalar(handle, value_type):
    fmt = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?",
           10: "<Q", 11: "<q", 12: "<d"}[value_type]
    return struct.unpack(fmt, _exact(handle, _SCALAR_SIZES[value_type]))[0]


def read_gguf_summary(model_path):
    """Return the few GGUF fields the profile needs; ``{}`` when unreadable.

    Reads the metadata header only (tensors are never touched). Arrays such as
    the tokenizer vocabulary are skipped, not loaded. Cached per file stat.
    """
    try:
        path = os.path.abspath(model_path)
        stat = os.stat(path)
        key = (path, stat.st_size, stat.st_mtime_ns)
        if key in _GGUF_CACHE:
            return dict(_GGUF_CACHE[key])
        summary = {}
        with open(path, "rb") as handle:
            if _exact(handle, 4) != b"GGUF":
                raise ValueError("Not a GGUF file")
            version = struct.unpack("<I", _exact(handle, 4))[0]
            if version not in (2, 3):
                raise ValueError(f"Unsupported GGUF version: {version}")
            _exact(handle, 8)   # tensor count
            count = struct.unpack("<Q", _exact(handle, 8))[0]
            if count > _MAX_METADATA_ENTRIES:
                raise ValueError("Unreasonable GGUF metadata count")
            for _ in range(count):
                name = _read_string(handle)
                value_type = struct.unpack("<I", _exact(handle, 4))[0]
                wanted = (name in ("general.architecture", "general.name", "tokenizer.chat_template",
                                   "tokenizer.ggml.model")
                          or name.endswith(".context_length"))
                if not wanted:
                    _skip_value(handle, value_type)
                elif value_type == _STRING:
                    summary[name] = _read_string(handle)
                elif value_type in _SCALAR_SIZES:
                    summary[name] = _read_scalar(handle, value_type)
                else:
                    _skip_value(handle, value_type)
        _GGUF_CACHE[key] = dict(summary)
        return summary
    except (OSError, ValueError, struct.error):
        return {}


def fetch_server_props(api_url, getter, timeout=3.0):
    """``GET /props`` from llama-server; ``None`` when unavailable or not a dict."""
    try:
        response = getter(f"{str(api_url).rstrip('/')}/props", timeout=timeout)
        if getattr(response, "status_code", 200) != 200:
            return None
        data = response.json()
        return data if isinstance(data, dict) else None
    except Exception:
        return None


@dataclass(frozen=True)
class ModelProfile:
    model_ref: str = ""
    model_path: str = ""
    architecture: str = ""            # GGUF general.architecture
    gguf_name: str = ""
    train_context: int = 0            # <arch>.context_length
    gguf_template: bool = False       # GGUF embeds tokenizer.chat_template
    server_probed: bool = False       # /props answered
    template_active: bool = False     # the server holds a chat template
    template_overridden: bool = False  # active template differs from the GGUF's own
    template_sha: str = ""
    template_caps: dict = field(default_factory=dict)
    thinking_toggle: bool = False     # active template implements enable_thinking
    chatml_like: bool = False         # active template uses <|im_start|> turns
    vision: object = None             # None = unknown (server not probed)
    audio: object = None
    video: object = None
    media_marker: str = ""
    build_info: str = ""
    n_ctx: int = 0
    proposed_transport: str = "messages"
    proposed_reason: str = ""
    forced_chatml: bool = False       # per-model "Force ChatML" override is active

    def to_dict(self):
        return asdict(self)


def usable_chat_template(template):
    """True when an embedded chat template can actually render a conversation.

    It must be non-empty, mention ``messages`` and parse as Jinja. A truncated,
    placeholder or unparsable template is not usable, and the caller then keeps
    the ChatML fallback instead of sending the model through it.
    """
    text = str(template or "")
    if not text.strip() or "messages" not in text or not ("{%" in text or "{{" in text):
        return False
    try:
        from jinja2.sandbox import ImmutableSandboxedEnvironment
        ImmutableSandboxedEnvironment().parse(text)
    except Exception:
        return False
    return True


def build_profile(model_ref="", model_path="", props=None, gguf=None, forced_chatml=False):
    """Combine /props and GGUF facts. Either source may be missing."""
    gguf = gguf or {}
    props = props if isinstance(props, dict) else None
    architecture = str(gguf.get("general.architecture", "") or "").strip().lower()
    gguf_template = str(gguf.get("tokenizer.chat_template", "") or "")
    train_context = 0
    for key, value in gguf.items():
        if key.endswith(".context_length") and isinstance(value, int):
            train_context = value
            break
    active = ""
    caps = {}
    modalities = {}
    media_marker = build_info = ""
    n_ctx = 0
    if props is not None:
        active = str(props.get("chat_template", "") or "")
        raw_caps = props.get("chat_template_caps")
        if isinstance(raw_caps, dict):
            caps = {k: bool(raw_caps[k]) for k in TEMPLATE_CAP_KEYS if k in raw_caps}
        if isinstance(props.get("modalities"), dict):
            modalities = props["modalities"]
        media_marker = str(props.get("media_marker", "") or "")
        build_info = str(props.get("build_info", "") or "")
        settings = props.get("default_generation_settings")
        if isinstance(settings, dict) and isinstance(settings.get("n_ctx"), int):
            n_ctx = settings["n_ctx"]
    template = active or gguf_template
    template_active = bool(template.strip())
    overridden = bool(props is not None and gguf_template.strip() and active.strip()
                      and active.strip() != gguf_template.strip())
    if forced_chatml:
        transport, reason = "raw_chatml", "per-model override: Force ChatML"
    elif template_active:
        transport, reason = "messages", "model has a chat template; llama.cpp renders it"
    else:
        transport, reason = "raw_chatml", "no chat template available; ChatML fallback"
    return ModelProfile(
        model_ref=str(model_ref or ""), model_path=str(model_path or ""),
        architecture=architecture, gguf_name=str(gguf.get("general.name", "") or ""),
        train_context=train_context, gguf_template=bool(gguf_template.strip()),
        server_probed=props is not None, template_active=template_active,
        template_overridden=overridden,
        template_sha=hashlib.sha1(template.encode("utf-8")).hexdigest()[:12] if template_active else "",
        template_caps=caps, thinking_toggle="enable_thinking" in template,
        chatml_like="<|im_start|>" in template,
        vision=modalities.get("vision") if props is not None else None,
        audio=modalities.get("audio") if props is not None else None,
        video=modalities.get("video") if props is not None else None,
        media_marker=media_marker, build_info=build_info, n_ctx=n_ctx,
        proposed_transport=transport, proposed_reason=reason, forced_chatml=bool(forced_chatml),
    )


def compare_to_legacy(profile, legacy):
    """Compare a profile with today's routing; return ``[(severity, message), ...]``.

    ``legacy`` keys: family, transport ('messages'|'completion'), system_role
    (bool, a real system turn is sent), reasoning_toggle (bool, HWUI treats the
    template as owning enable_thinking), mmproj_configured (bool).
    Severities: ``gap`` = a known limitation of today's routing the migration is
    meant to remove; ``review`` = unexpected disagreement worth a look;
    ``info`` = context only.
    """
    out = []
    family = legacy.get("family", "other")
    legacy_messages = legacy.get("transport") == "messages"
    if profile.forced_chatml:
        out.append(("info", "chat template forced to ChatML for this model (per-model override); the model's "
                            "own template is ignored"))
    elif not legacy_messages and profile.template_active:
        if family == "other":
            out.append(("gap", "unlisted architecture is sent as raw ChatML although the model has its own "
                               "chat template (proposed: messages)"))
        else:
            out.append(("info", f"{family} keeps its raw ChatML path by design (the model's own template is "
                                f"{'ChatML-like' if profile.chatml_like else 'not ChatML'}); not a defect"))
    if legacy_messages and not profile.template_active and profile.server_probed:
        out.append(("review", "legacy sends chat-completions messages but the server reports no chat template"))
    if profile.template_overridden:
        out.append(("info", "server template differs from the GGUF's own (launched with an override such as "
                            "--chat-template chatml)"))
    caps = profile.template_caps
    if legacy.get("system_role") and caps and caps.get("supports_system_role") is False:
        out.append(("review", "legacy sends a real system turn but the active template reports "
                              "supports_system_role=false"))
    if (not legacy.get("system_role") and family == "other" and legacy_messages
            and caps.get("supports_system_role") is True):
        out.append(("gap", "legacy folds the system prompt into the first user turn although the template "
                           "supports a system role"))
    if bool(legacy.get("reasoning_toggle")) != bool(profile.thinking_toggle) and profile.template_active:
        out.append(("review", f"thinking switch: legacy={bool(legacy.get('reasoning_toggle'))} "
                              f"template={bool(profile.thinking_toggle)}"))
    if profile.server_probed and profile.vision is False and legacy.get("mmproj_configured"):
        out.append(("info", "an mmproj is configured in settings but the loaded server reports no vision "
                            "(expected while HWUI uses its temporary vision launch)"))
    if profile.server_probed and profile.vision and not legacy.get("mmproj_configured"):
        out.append(("info", "server reports vision support with no mmproj configured in settings"))
    return out


def format_log_line(profile, mismatches):
    caps = profile.template_caps
    bits = [
        f"arch={profile.architecture or '?'}",
        f"probed={'yes' if profile.server_probed else 'no'}",
        f"template={'override' if profile.template_overridden else ('yes' if profile.template_active else 'none')}",
        f"sys_role={caps.get('supports_system_role', '?')}",
        f"thinking_toggle={profile.thinking_toggle}",
        f"vision={profile.vision}",
        f"proposed={profile.proposed_transport}",
    ]
    text = "🧭 ModelProfile (shadow, routing unchanged): " + " ".join(bits)
    notable = [f"{sev}: {msg}" for sev, msg in mismatches if sev in ("review", "gap")]
    if notable:
        text += " | " + " | ".join(notable)
    return text
