"""Inference-provider settings, model discovery and connection test routes.

Endpoint credentials are stored via secrets_store (secrets.local.json), never in
settings.json, and are never returned to the browser - only has_key + a mask.
"""
import ipaddress
import json
import os
import shutil
from urllib.parse import urlparse

from flask import Blueprint, jsonify, request

import secrets_store
from providers import registry, runtime
from providers.base import ProviderError

provider_bp = Blueprint("provider", __name__)
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

_CONFIG_FIELDS = ("base_url", "model", "context_length", "vision", "keep_alive")


def is_local_url(url):
    """True for loopback / private-LAN hosts. Anything else is a remote service."""
    host = (urlparse(url or "").hostname or "").lower()
    if host in ("localhost", ""):
        return True
    try:
        address = ipaddress.ip_address(host)
        return address.is_loopback or address.is_private
    except ValueError:
        return host.endswith(".local") or "." not in host


def valid_base_url(url):
    parsed = urlparse(url or "")
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _read_settings():
    with open(SETTINGS_FILE, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_settings(settings):
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(settings, handle, indent=2)
    shutil.move(tmp, SETTINGS_FILE)


def clean_config(raw):
    """Keep only known, correctly typed config fields."""
    raw = raw or {}
    cfg = {}
    if "base_url" in raw:
        cfg["base_url"] = str(raw["base_url"] or "").strip().rstrip("/")
    if "model" in raw:
        cfg["model"] = str(raw["model"] or "").strip()
    if "context_length" in raw:
        try:
            cfg["context_length"] = max(int(raw["context_length"] or 0), 0)
        except (TypeError, ValueError):
            cfg["context_length"] = 0
    if isinstance(raw.get("vision"), bool):
        cfg["vision"] = raw["vision"]
    if "keep_alive" in raw:
        cfg["keep_alive"] = str(raw["keep_alive"] or "").strip()
    return cfg


def _public_state(settings):
    block = runtime.provider_block(settings)
    configs = {}
    for pid in registry.provider_ids():
        cfg = dict((block.get("configs") or {}).get(pid) or {})
        key = secrets_store.get_secret(secrets_store.provider_key_name(pid))
        cfg["has_key"] = bool(key)
        cfg["key_mask"] = secrets_store.mask(key)
        cfg["is_remote"] = bool(cfg.get("base_url")) and not is_local_url(cfg["base_url"])
        configs[pid] = cfg
    return {
        "status": "ok",
        "backend_mode": settings.get("backend_mode", "local"),
        "selected": runtime.selected_provider_id(settings),
        "persist_on_startup": bool(block.get("persist_on_startup", False)),
        "providers": registry.list_descriptors(),
        "configs": configs,
    }


@provider_bp.route("/provider/state", methods=["GET"])
def provider_state():
    return jsonify(_public_state(runtime.load_settings()))


@provider_bp.route("/provider/save", methods=["POST"])
def provider_save():
    data = request.get_json(silent=True) or {}
    pid = str(data.get("id") or "").strip()
    if pid not in registry.PROVIDERS:
        return jsonify({"status": "error", "error": f"unknown provider {pid!r}"}), 400
    cfg = clean_config(data.get("config"))
    if cfg.get("base_url") and not valid_base_url(cfg["base_url"]):
        return jsonify({"status": "error", "error": "Base URL must start with http:// or https://"}), 400
    try:
        settings = _read_settings()
    except Exception as exc:
        # Never write a stripped dict over unreadable settings.
        return jsonify({"status": "error", "error": f"cannot read settings: {exc}"}), 500

    block = settings.setdefault("inference_provider", {})
    block["id"] = pid
    block.setdefault("configs", {}).setdefault(pid, {}).update(cfg)
    if "persist_on_startup" in data:
        block["persist_on_startup"] = bool(data["persist_on_startup"])
    try:
        # api_key: absent/None = leave unchanged; "" with clear flag = remove; text = replace.
        if data.get("clear_api_key"):
            secrets_store.delete_secret(secrets_store.provider_key_name(pid))
        elif data.get("api_key"):
            secrets_store.set_secret(secrets_store.provider_key_name(pid), data["api_key"])
        _write_settings(settings)
    except Exception as exc:
        return jsonify({"status": "error", "error": str(exc)}), 500
    return jsonify(_public_state(settings))


def _probe_provider(data):
    pid = str(data.get("id") or "").strip()
    cfg = clean_config(data.get("config"))
    if cfg.get("base_url") and not valid_base_url(cfg["base_url"]):
        raise ProviderError("Base URL must start with http:// or https://")
    overrides = dict(cfg)
    if data.get("api_key"):          # unsaved key typed into the form
        overrides["api_key"] = data["api_key"]
    return runtime.build_provider(runtime.load_settings(), provider_id=pid, overrides=overrides)


@provider_bp.route("/provider/models", methods=["POST"])
def provider_models():
    try:
        models = _probe_provider(request.get_json(silent=True) or {}).list_models()
        return jsonify({"status": "ok", "models": models})
    except ProviderError as exc:
        return jsonify({"status": "error", "error": str(exc), "models": []}), 200


@provider_bp.route("/provider/test", methods=["POST"])
def provider_test():
    try:
        provider = _probe_provider(request.get_json(silent=True) or {})
        ok, detail = provider.health()
        context = provider.context_length() if ok and provider.config.model else None
        return jsonify({"status": "ok" if ok else "error", "detail": detail, "context_length": context,
                        "capabilities": provider.capabilities.as_dict()})
    except ProviderError as exc:
        return jsonify({"status": "error", "detail": str(exc)})
