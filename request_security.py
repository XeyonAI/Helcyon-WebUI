"""HWUI's request security boundary: who may call the API, from where.

HWUI is a local web app whose API can read and write chats, memories, settings
and files, and start local programs. Until 24 Oct 2026 it answered any client
on any interface and sent ``Access-Control-Allow-Origin`` for every origin, so
any web page the user visited, or anyone on the same network, could read API
keys and write files. This module is the single gate every request passes
before a route runs (``init_app`` installs it first in the hook chain).

Who is asking (``classify``):

* ``local`` -- the browser or launcher on this machine: a loopback socket, no
  proxy headers, and a loopback ``Host`` (the Host check is what stops DNS
  rebinding: a hostile page rebound to 127.0.0.1 still sends its own Host).
* ``tailnet`` -- a request relayed by the local Tailscale Serve proxy (loopback
  socket) carrying a Tailscale identity (``Tailscale-User-Login``). Tailscale
  authenticated that device; Funnel (public) requests are never trusted this
  way. Can be turned off with ``remote_access.trust_tailscale_identity: false``.
* ``device`` -- a paired phone or browser presenting its device token (an
  HttpOnly cookie set by /pair, or ``Authorization: Bearer`` for scripts).
* ``anonymous`` -- anything else. Only the pairing page and the PWA manifest
  and icons are served; pages redirect to /pair and API calls get 401.

What every request must satisfy, whoever it is from:

1. Route parameters and file-name fields are single plain names
   (``path_safety``): no separators, drive letters, ``..`` or NUL.
2. No cross-site browser request: ``Origin`` must be this server's own, and
   ``Sec-Fetch-Site`` must not be cross-site/same-site, except a top-level GET
   navigation (following a link to HWUI). Other localhost ports are other
   origins, so another local web page cannot drive HWUI either.
3. A small set of machine-control routes (native file dialogs, executable and
   model paths, folder exports, credential storage, pairing) stay ``local``
   only, even for paired devices.

Pairing (desktop Config -> Remote Devices): the local user creates a one-time
8-character code (10 minutes, limited attempts); entering it on the phone's
/pair page stores a random device token (only its SHA-256 is kept, in
secrets.local.json) and sets the cookie. Devices can be revoked there too.

Configuration (settings.json, optional; absent = these defaults):

    "remote_access": {
        "trust_tailscale_identity": true,
        "tailscale_allowed_logins": [],      # empty = any tailnet identity
        "allowed_origins": [],               # extra browser origins, e.g. a reverse proxy
        "allowed_hosts": []                  # extra Host names treated as this machine
    },
    "server_bind_host": "127.0.0.1"          # app.py; default 0.0.0.0 only with TLS certs
"""
import hashlib
import hmac
import html
import ipaddress
import json
import os
import secrets
import threading
import time
from urllib.parse import quote, urlsplit

from flask import Blueprint, g, jsonify, redirect, request

import path_safety
import secrets_store

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

LOCAL = "local"
TAILNET = "tailnet"
DEVICE = "device"
ANONYMOUS = "anonymous"

DEVICE_COOKIE = "hwui_device"
DEVICE_SECRET_NAME = "remote_access.devices"
DEVICE_TOKEN_MAX_AGE = 400 * 24 * 3600   # browsers cap cookie lifetimes near 400 days

LOCAL_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})
# Present on anything relayed by a proxy; a direct local browser never sends them.
FORWARDING_HEADERS = (
    "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto", "Forwarded",
    "X-Real-IP", "Via", "Tailscale-User-Login", "Tailscale-User-Name",
    "Tailscale-Funnel-Request",
)

# Request fields that name a file, folder, card, project or voice. Each must be
# a single plain name; free text (messages, titles, display names) is not here.
PATH_COMPONENT_FIELDS = frozenset({
    "filename", "old_filename", "source_filename", "original_filename",
    "current_chat_filename", "chat_filename", "image_name", "chat_id",
    "character", "character_name", "target_project", "project", "project_name",
    "active", "voice", "voice_name", "result_id", "old_name",
})

# Routes that act on this machine itself rather than on HWUI content: native
# file dialogs, executable/model/folder paths, exports to arbitrary folders,
# server logs, credentials, and device pairing. Never needed by /mobile.
LOCAL_ONLY_RULES = frozenset({
    "/browse_file", "/browse_folder",
    "/save_llama_config", "/save_lora_path", "/save_mmproj_path",
    "/save_automatic_vision_config", "/auto_detect_mmproj",
    "/shards/export",
    "/chats/open_folder",
    "/projects/<project_name>/documents/open_folder",
    "/projects/<project_name>/documents/upload_from_dialog",
    "/api/tts/voice-forge/export",
    "/api/llama_server/logs",
    "/reset_settings_to_default",
    "/save_brave_api_key", "/hf/token", "/provider/save",
    "/api/helcyon-bench/judge/api-key",
    "/security/status", "/security/pairing-code", "/security/devices",
    "/security/devices/revoke",
})

# What an unpaired remote browser may fetch: the pairing page, and the PWA
# manifest and icons (browsers fetch manifests without cookies).
ANONYMOUS_RULES = frozenset({"/pair", "/manifest.webmanifest"})
ANONYMOUS_PATH_PREFIXES = ("/static/icons/",)

PAIRING_CODE_TTL = 600
PAIRING_MAX_FAILURES = 10
_PAIRING_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # no 0/O, 1/I/L

security_bp = Blueprint("request_security", __name__)
_lock = threading.Lock()
_pairing = {"code": None, "expires": 0.0, "failures": 0}
_settings_cache = {"key": None, "value": {}}
_devices_cache = {"key": None, "value": []}
_SETTINGS_PATH = os.path.join(_BASE_DIR, "settings.json")


# ── Configuration ─────────────────────────────────────────────────────────────

def _settings():
    """settings.json's remote_access block, re-read only when the file changes."""
    try:
        stat = os.stat(_SETTINGS_PATH)
        key = (stat.st_size, stat.st_mtime_ns)
    except OSError:
        return {}
    if _settings_cache["key"] != key:
        value = {}
        try:
            with open(_SETTINGS_PATH, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict) and isinstance(data.get("remote_access"), dict):
                value = data["remote_access"]
        except (OSError, ValueError):
            value = {}
        _settings_cache.update(key=key, value=value)
    return _settings_cache["value"]


def _setting_list(name):
    value = _settings().get(name)
    return [str(v).strip() for v in value if str(v).strip()] if isinstance(value, list) else []


def _trust_tailscale_identity():
    return _settings().get("trust_tailscale_identity", True) is not False


# ── Who is asking ─────────────────────────────────────────────────────────────

def _is_loopback(address):
    try:
        return ipaddress.ip_address(str(address or "").split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _hostname(netloc):
    """'Example.com:8081' -> 'example.com'; '[::1]:80' -> '::1'."""
    try:
        return (urlsplit("//" + str(netloc or "")).hostname or "").lower()
    except ValueError:
        return ""


def _normalise_netloc(netloc, scheme=None):
    """Lower-cased host[:port] with the scheme's default port dropped."""
    try:
        parts = urlsplit("//" + str(netloc or "").strip())
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return ""
    if not host:
        return ""
    if ":" in host:
        host = f"[{host}]"
    defaults = {"http": (80,), "https": (443,)}.get(scheme, (80, 443))
    return host if port is None or port in defaults else f"{host}:{port}"


def _has_forwarding_headers():
    return any(request.headers.get(name) for name in FORWARDING_HEADERS)


def _host_is_local():
    host = _hostname(request.host)
    return host in LOCAL_HOSTNAMES or host in {h.lower() for h in _setting_list("allowed_hosts")}


def _device_records():
    try:
        path = secrets_store.secrets_path()
        stat = os.stat(path)
        key = (path, stat.st_size, stat.st_mtime_ns)
    except OSError:
        return []
    if _devices_cache["key"] != key:
        try:
            records = json.loads(secrets_store.get_secret(DEVICE_SECRET_NAME) or "[]")
        except ValueError:
            records = []
        _devices_cache.update(key=key, value=[r for r in records if isinstance(r, dict)])
    return _devices_cache["value"]


def _save_device_records(records):
    secrets_store.set_secret(DEVICE_SECRET_NAME, json.dumps(records) if records else "")
    _devices_cache["key"] = None


def _token_digest(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _presented_token():
    auth = request.headers.get("Authorization", "")
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    return (request.cookies.get(DEVICE_COOKIE) or "").strip()


def _matching_device(token):
    if not token or len(token) > 200:
        return None
    digest = _token_digest(token)
    for record in _device_records():
        if hmac.compare_digest(str(record.get("hash", "")), digest):
            return record
    return None


def classify():
    """(class, detail) for the current request; see the module docstring."""
    loopback = _is_loopback(request.remote_addr)
    if loopback and not _has_forwarding_headers() and _host_is_local():
        return LOCAL, "loopback"
    if loopback and _trust_tailscale_identity() and not request.headers.get("Tailscale-Funnel-Request"):
        login = (request.headers.get("Tailscale-User-Login") or "").strip()
        allowed = {v.lower() for v in _setting_list("tailscale_allowed_logins")}
        if login and (not allowed or login.lower() in allowed):
            return TAILNET, login
    device = _matching_device(_presented_token())
    if device:
        return DEVICE, str(device.get("id", ""))
    return ANONYMOUS, ""


def is_local_request():
    return getattr(g, "hwui_client_class", None) == LOCAL or classify()[0] == LOCAL


# ── Checks ────────────────────────────────────────────────────────────────────

def _path_problem():
    """Why a route parameter or file-name field is not a plain name, or None."""
    rule = request.url_rule
    converters = getattr(rule, "_converters", {}) if rule is not None else {}
    for name, value in (request.view_args or {}).items():
        if not isinstance(value, str):
            continue
        is_path = type(converters.get(name)).__name__ == "PathConverter"
        ok = path_safety.is_relative_subpath(value) if is_path else path_safety.is_plain_component(value)
        if not ok:
            return f"route parameter {name!r}"
    sources = [request.args]
    # JSON bodies, including sendBeacon's text/plain ones (/chats/save parses
    # those with force=True). Never force-parse multipart: reading the raw body
    # here would leave the route with no uploaded files.
    if request.is_json or request.mimetype == "text/plain":
        body = request.get_json(silent=True, force=True)
        if isinstance(body, dict):
            sources.append(body)
    elif request.mimetype in ("application/x-www-form-urlencoded", "multipart/form-data"):
        sources.append(request.form)
    for source in sources:
        for field in PATH_COMPONENT_FIELDS:
            value = source.get(field) if hasattr(source, "get") else None
            if isinstance(value, str) and value.strip() and not path_safety.is_plain_component(value):
                return f"field {field!r}"
    return None


def _own_netlocs():
    netlocs = {_normalise_netloc(request.host)}
    if _is_loopback(request.remote_addr):
        # Only a proxy on this machine (Tailscale Serve) can set these.
        for value in (request.headers.get("X-Forwarded-Host") or "").split(","):
            if value.strip():
                netlocs.add(_normalise_netloc(value.strip()))
    netlocs.discard("")
    return netlocs


def _origin_is_own(origin):
    origin = (origin or "").strip()
    if not origin or origin == "null":
        return False
    if origin.rstrip("/") in {o.rstrip("/") for o in _setting_list("allowed_origins")}:
        return True
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    return _normalise_netloc(parts.netloc, parts.scheme) in _own_netlocs()


def _cross_site_problem():
    """Why this looks like a request another site's page made, or None."""
    origin = request.headers.get("Origin")
    if origin is not None:
        return None if _origin_is_own(origin) else f"origin {origin[:80]!r}"
    site = (request.headers.get("Sec-Fetch-Site") or "").lower()
    if site in ("cross-site", "same-site"):
        navigation = request.method in ("GET", "HEAD") and \
            (request.headers.get("Sec-Fetch-Mode") or "").lower() == "navigate"
        return None if navigation else f"sec-fetch-site {site}"
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        referer = request.headers.get("Referer")
        if referer and not _origin_is_own(f"{urlsplit(referer).scheme}://{urlsplit(referer).netloc}"):
            return "referer from another site"
    return None


def _anonymous_allowed():
    rule = request.url_rule.rule if request.url_rule is not None else None
    return rule in ANONYMOUS_RULES or request.path.startswith(ANONYMOUS_PATH_PREFIXES)


def _wants_page():
    """A top-level page load (redirect to /pair), not an API call (401).

    fetch() and curl send "Accept: */*", which also matches text/html, so a
    wildcard is not enough: the browser must say it is loading a document,
    or list text/html explicitly.
    """
    if request.method != "GET" or request.path.startswith("/api/"):
        return False
    dest = (request.headers.get("Sec-Fetch-Dest") or "").lower()
    if dest:
        return dest == "document"
    return any(value == "text/html" for value, _quality in request.accept_mimetypes)


def _deny(status, code, message):
    response = jsonify({"error": message, "code": code})
    response.status_code = status
    return response


def guard():
    """before_request: run every check, or return the refusal."""
    problem = _path_problem()
    if problem:
        print(f"🛡️ Refused {request.method} {request.path}: unsafe {problem}", flush=True)
        return _deny(400, "unsafe_path", "That name is not allowed (it must be a single file or folder name).")
    problem = _cross_site_problem()
    if problem:
        print(f"🛡️ Refused cross-site {request.method} {request.path} ({problem})", flush=True)
        return _deny(403, "cross_site_request", "Requests from other websites are not allowed.")
    client, detail = classify()
    g.hwui_client_class = client
    rule = request.url_rule.rule if request.url_rule is not None else None
    if client == LOCAL:
        return None
    if rule in LOCAL_ONLY_RULES:
        print(f"🛡️ Refused {request.path} from {client} client: local-only", flush=True)
        return _deny(403, "local_only", "This action is only available on the computer running HWUI.")
    if client in (TAILNET, DEVICE) or _anonymous_allowed():
        return None
    if _wants_page():
        target = request.full_path.rstrip("?") if request.query_string else request.path
        return redirect("/pair?next=" + quote(target, safe="/"))
    response = _deny(401, "device_not_paired",
                     "This device is not paired with HWUI. Open HWUI on the computer, go to "
                     "Config → Remote Devices, create a pairing code and enter it on this device.")
    response.headers["X-HWUI-Auth"] = "pair-required"   # hwui_chat_core.js redirects to /pair
    return response


def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


def init_app(app):
    """Install the guard first (before every other request hook) and the routes."""
    app.before_request_funcs.setdefault(None, []).insert(0, guard)
    app.after_request(add_security_headers)
    app.register_blueprint(security_bp)


def bind_host(settings, tls_enabled):
    """The interface app.run() listens on.

    127.0.0.1 unless TLS certificates are installed (direct HTTPS over Tailscale
    needs the tailnet interface); Tailscale Serve relays to 127.0.0.1, so it
    works either way. ``server_bind_host`` in settings.json overrides; every
    non-local client still has to be paired.
    """
    configured = str((settings or {}).get("server_bind_host") or "").strip()
    if configured:
        try:
            ipaddress.ip_address(configured)
            return configured
        except ValueError:
            print(f"⚠️ server_bind_host {configured!r} is not an IP address; using the default.", flush=True)
    return "0.0.0.0" if tls_enabled else "127.0.0.1"


# ── Pairing ───────────────────────────────────────────────────────────────────

def _new_pairing_code():
    raw = "".join(secrets.choice(_PAIRING_ALPHABET) for _ in range(8))
    return raw[:4] + "-" + raw[4:]


def _normalise_code(value):
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def _safe_next(value):
    value = str(value or "")
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/mobile"
    return value


def _request_is_https():
    return request.is_secure or (request.headers.get("X-Forwarded-Proto") or "").lower() == "https"


_PAIR_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pair this device</title>
<style>
 :root {{ color-scheme: light dark; }}
 body {{ font-family: system-ui, sans-serif; margin: 0; padding: 24px 16px; background: #111; color: #eee; }}
 main {{ max-width: 420px; margin: 0 auto; }}
 h1 {{ font-size: 1.4rem; }}
 p {{ line-height: 1.5; color: #bbb; }}
 input, button {{ font-size: 1.2rem; padding: 12px; width: 100%; box-sizing: border-box; border-radius: 8px; }}
 input {{ letter-spacing: 0.15em; text-transform: uppercase; border: 1px solid #444; background: #1c1c1c; color: #eee; }}
 button {{ margin-top: 12px; border: 0; background: #3b82f6; color: #fff; }}
 .msg {{ color: #f87171; }}
</style></head>
<body><main>
<h1>Pair this device with HWUI</h1>
<p>On the computer running HWUI, open <b>Config → Remote Devices</b> and select
<b>Create pairing code</b>. Enter the code here. You only need to do this once per device.</p>
{message}
<form method="post" action="/pair">
 <input name="code" autocomplete="one-time-code" placeholder="XXXX-XXXX" required maxlength="12" autofocus>
 <input type="hidden" name="next" value="{next}">
 <input type="hidden" name="device_name" id="device_name" value="">
 <button type="submit">Pair device</button>
</form>
</main>
<script>document.getElementById('device_name').value = (navigator.userAgent || '').slice(0, 120);</script>
</body></html>"""


def _pair_page(message="", status=200, next_path="/mobile"):
    body = _PAIR_PAGE.format(
        message=f'<p class="msg">{html.escape(message)}</p>' if message else "",
        next=html.escape(_safe_next(next_path), quote=True))
    return body, status, {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store"}


@security_bp.route("/pair", methods=["GET", "POST"])
def pair():
    if request.method == "GET":
        return _pair_page(next_path=request.args.get("next"))
    data = request.get_json(silent=True) if request.is_json else request.form
    data = data or {}
    next_path = _safe_next(data.get("next"))
    code = _normalise_code(data.get("code"))
    with _lock:
        active = _pairing["code"]
        if not active or time.time() > _pairing["expires"]:
            _pairing.update(code=None, failures=0)
            ok, message = False, "No pairing code is active. Create a new one on the computer."
        elif hmac.compare_digest(code, _normalise_code(active)):
            _pairing.update(code=None, failures=0)
            ok, message = True, ""
        else:
            _pairing["failures"] += 1
            if _pairing["failures"] >= PAIRING_MAX_FAILURES:
                _pairing.update(code=None, failures=0)
                message = "Too many wrong codes. Create a new pairing code on the computer."
            else:
                message = "That code is not right. Check it and try again."
            ok = False
    if not ok:
        print("🛡️ Pairing attempt refused", flush=True)
        if request.is_json:
            return _deny(403, "pairing_failed", message)
        return _pair_page(message, 403, next_path)
    token = secrets.token_urlsafe(32)
    record = {"id": secrets.token_hex(6), "hash": _token_digest(token),
              "name": str(data.get("device_name") or "Device")[:120],
              "created": time.strftime("%Y-%m-%d %H:%M:%S")}
    with _lock:
        _save_device_records(_device_records() + [record])
    print(f"🔐 Paired a new device ({record['id']})", flush=True)
    if request.is_json:
        response = jsonify({"status": "ok", "device_id": record["id"], "token": token})
    else:
        response = redirect(next_path)
    response.set_cookie(DEVICE_COOKIE, token, max_age=DEVICE_TOKEN_MAX_AGE, httponly=True,
                        secure=_request_is_https(), samesite="Lax", path="/")
    return response


@security_bp.route("/security/pairing-code", methods=["POST"])
def create_pairing_code():
    code = _new_pairing_code()
    with _lock:
        _pairing.update(code=code, expires=time.time() + PAIRING_CODE_TTL, failures=0)
    return jsonify({"status": "ok", "code": code, "expires_in": PAIRING_CODE_TTL})


@security_bp.route("/security/devices", methods=["GET"])
def list_devices():
    return jsonify({"devices": [{k: r.get(k) for k in ("id", "name", "created")}
                                for r in _device_records()]})


@security_bp.route("/security/devices/revoke", methods=["POST"])
def revoke_devices():
    data = request.get_json(silent=True) or {}
    with _lock:
        records = _device_records()
        if data.get("all") is True:
            kept = []
        else:
            kept = [r for r in records if r.get("id") != data.get("id")]
        _save_device_records(kept)
    return jsonify({"status": "ok", "removed": len(records) - len(kept), "remaining": len(kept)})


@security_bp.route("/security/status", methods=["GET"])
def security_status():
    with _lock:
        active = bool(_pairing["code"]) and time.time() <= _pairing["expires"]
    return jsonify({
        "devices": len(_device_records()),
        "pairing_code_active": active,
        "trust_tailscale_identity": _trust_tailscale_identity(),
    })
