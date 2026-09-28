"""Web Push endpoints for phone notifications (Random Check-ins) + the /mobile PWA shell.

Logic lives in checkin_notifications.py; these routes only validate and adapt.

Every other HWUI endpoint relies on network reachability alone, and CORS is
open. Registration is stricter because a registered subscription receives
check-in text from then on: a web page in another origin could otherwise
enrol its own browser as a "phone". Mutating push routes therefore refuse
browser requests that are not same-origin (non-browser clients, which send
neither Origin nor fetch metadata, keep the usual trust).
"""

import os
from urllib.parse import urlsplit

from flask import Blueprint, jsonify, request, send_from_directory

import checkin_notifications as notifications
import user_config

push_bp = Blueprint("push", __name__)

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
MOBILE_SCOPE = "/mobile"


def _is_same_origin_request():
    origin = request.headers.get("Origin")
    if origin:
        # Host:port only, so an HTTPS-terminating proxy in front of HWUI still matches.
        return bool(urlsplit(origin).netloc) and urlsplit(origin).netloc.lower() == request.host.lower()
    fetch_site = request.headers.get("Sec-Fetch-Site")
    if fetch_site:
        return fetch_site in ("same-origin", "none")
    return True


def _error(status, code, message):
    return jsonify({"error": message, "code": code}), status


def _refuse_cross_origin():
    if not _is_same_origin_request():
        return _error(403, "cross_origin", "Phone notification changes must come from HWUI itself.")
    return None


def _refuse_unavailable():
    if not notifications.push_library_available():
        return _error(503, "pywebpush_missing", "The server is missing pywebpush. Re-run setup to install it.")
    return None


_SUBSCRIPTION_ERRORS = {
    "invalid_subscription": "Subscription is missing its endpoint or keys.",
    "invalid_endpoint": "Subscription endpoint must be an https URL.",
    "unsupported_push_service": "This browser's push service is not supported.",
    "invalid_keys": "Subscription keys are malformed.",
}


@push_bp.route("/api/push/vapid-public-key", methods=["GET"])
def push_status():
    """Public key plus what the phone UI needs to explain its state."""
    settings = user_config.load_random_checkins_config()
    payload = {
        "available": False,
        "reason": None,
        "public_key": None,
        "secure": bool(request.is_secure),
        "devices": notifications.device_count(),
        "checkins_enabled": bool(settings.get("enabled")),
        "mobile_notifications": bool(settings.get("mobile_notifications")),
    }
    if not notifications.push_library_available():
        payload["reason"] = "pywebpush_missing"
        return jsonify(payload)
    try:
        payload["public_key"] = notifications.get_vapid_keys()["public_key"]
        payload["available"] = True
    except Exception as error:
        notifications._log(f"VAPID keys unavailable: {error}")
        payload["reason"] = "vapid_unavailable"
    return jsonify(payload)


@push_bp.route("/api/push/subscriptions", methods=["GET"])
def push_list_devices():
    return jsonify({"devices": notifications.list_devices()})


@push_bp.route("/api/push/subscriptions", methods=["POST"])
def push_register():
    refused = _refuse_cross_origin() or _refuse_unavailable()
    if refused:
        return refused
    data = request.get_json(silent=True) or {}
    device, error = notifications.register_subscription(
        data.get("subscription"),
        label=str(data.get("label") or ""),
        replaces=str(data.get("replaces") or ""),
    )
    if error:
        return _error(400, error, _SUBSCRIPTION_ERRORS[error])
    return jsonify({"device": device, "devices": notifications.device_count()}), 201


@push_bp.route("/api/push/subscriptions", methods=["DELETE"])
def push_unregister():
    refused = _refuse_cross_origin()
    if refused:
        return refused
    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint:
        return _error(400, "invalid_subscription", "endpoint is required.")
    removed = notifications.unregister_subscription(endpoint)
    return jsonify({"removed": removed, "devices": notifications.device_count()})


@push_bp.route("/api/push/test", methods=["POST"])
def push_test():
    """Send a test notification to one phone (``endpoint``) or all registered phones."""
    refused = _refuse_cross_origin() or _refuse_unavailable()
    if refused:
        return refused
    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint")
    endpoints = None
    if endpoint:
        if not isinstance(endpoint, str) or not notifications.is_registered(endpoint):
            return _error(404, "not_registered", "This phone is not registered.")
        endpoints = [endpoint]
    elif not notifications.device_count():
        return _error(409, "no_devices", "No phones are registered.")
    try:
        result = notifications.send_test_notification(endpoints=endpoints)
    except Exception as error:
        notifications._log(f"test notification failed: {error}")
        return _error(502, "send_failed", "The test notification could not be sent.")
    return jsonify({**result, "devices": notifications.device_count()})


# ── /mobile as an installable web app ────────────────────────────────────────
@push_bp.route("/manifest.webmanifest")
def mobile_manifest():
    response = jsonify({
        "name": "HWUI",
        "short_name": "HWUI",
        "id": MOBILE_SCOPE,
        "start_url": MOBILE_SCOPE,
        "scope": MOBILE_SCOPE,
        "display": "standalone",
        "background_color": "#000000",
        "theme_color": "#000000",
        "icons": [
            {"src": "/static/icons/hwui-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/static/icons/hwui-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/static/icons/hwui-192.png", "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
            {"src": "/static/icons/hwui-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    })
    response.mimetype = "application/manifest+json"
    return response


@push_bp.route("/mobile-sw.js")
def mobile_service_worker():
    # Served from the root (not /static/) so it may control /mobile; no-cache so
    # an updated worker is picked up on the next visit.
    response = send_from_directory(_STATIC_DIR, "mobile-sw.js", mimetype="text/javascript", max_age=0)
    response.headers["Cache-Control"] = "no-cache"
    return response
