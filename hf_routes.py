"""Hugging Face discovery routes (separate from inference-provider selection)."""
import os
import shutil

from flask import Blueprint, jsonify, request

import hf_discovery as hf
import secrets_store
from providers import runtime

hf_bp = Blueprint("hf", __name__)


def _token():
    return secrets_store.get_secret(secrets_store.HF_TOKEN_NAME)


def _fail(exc, code=200):
    return jsonify({"status": "error", "error": str(exc)}), code


@hf_bp.route("/hf/info", methods=["GET"])
def hf_info():
    """Where downloads go, and whether there is room / a token."""
    models_dir = runtime.load_settings().get("llama_models_dir", "")
    exists = bool(models_dir) and os.path.isdir(models_dir)
    free = shutil.disk_usage(models_dir).free if exists else 0
    return jsonify({"status": "ok", "models_dir": models_dir, "exists": exists, "free_bytes": free,
                    "has_token": bool(_token())})


@hf_bp.route("/hf/search", methods=["GET"])
def hf_search():
    args = request.args
    try:
        result = hf.search_models(
            query=args.get("q", ""), gguf_only=args.get("gguf", "1") != "0",
            sort=args.get("sort", "downloads"), limit=args.get("limit", 24),
            cursor=args.get("cursor", ""), token=_token())
        return jsonify({"status": "ok", **result})
    except hf.HFError as exc:
        return _fail(exc)


@hf_bp.route("/hf/repo", methods=["GET"])
def hf_repo():
    repo_id = request.args.get("id", "")
    try:
        return jsonify({"status": "ok", "model": hf.repo_detail(repo_id, _token()),
                        "files": hf.list_gguf_files(repo_id, _token())})
    except hf.HFError as exc:
        return _fail(exc)


@hf_bp.route("/hf/download", methods=["POST"])
def hf_download():
    data = request.get_json(silent=True) or {}
    repo_id = data.get("repo_id", "")
    models_dir = runtime.load_settings().get("llama_models_dir", "")
    try:
        files = hf.resolve_selection(repo_id, data.get("paths"), _token())
        job = hf.MANAGER.start(repo_id, files, models_dir, token=_token(), verify=data.get("verify", True))
        return jsonify({"status": "ok", "job": job.snapshot()})
    except hf.HFError as exc:
        return _fail(exc)


@hf_bp.route("/hf/downloads", methods=["GET"])
def hf_downloads():
    return jsonify({"status": "ok", "jobs": hf.MANAGER.list()})


@hf_bp.route("/hf/cancel", methods=["POST"])
def hf_cancel():
    job_id = (request.get_json(silent=True) or {}).get("id", "")
    return jsonify({"status": "ok" if hf.MANAGER.cancel(job_id) else "error"})


@hf_bp.route("/hf/token", methods=["GET"])
def hf_token_status():
    token = _token()
    return jsonify({"has_token": bool(token), "mask": secrets_store.mask(token)})


@hf_bp.route("/hf/token", methods=["POST"])
def hf_token_save():
    data = request.get_json(silent=True) or {}
    try:
        secrets_store.set_secret(secrets_store.HF_TOKEN_NAME, "" if data.get("clear") else data.get("token", ""))
    except Exception as exc:
        return _fail(exc, 500)
    return hf_token_status()
