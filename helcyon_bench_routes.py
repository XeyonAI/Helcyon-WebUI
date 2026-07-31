"""HWUI-owned HTTP adapter for the bundled Helcyon-Bench data."""

from flask import Blueprint, jsonify, request

from helcyon_bench_adapter import (
    PromptPackError,
    PromptPackNotFoundError,
    build_leaderboard,
    consolidate_model_aliases,
    create_prompt_pack,
    delete_model_benchmark_runs,
    delete_prompt_pack,
    load_benchmark_result_detail,
    load_benchmark_results,
    load_prompt_packs,
    load_strength_map,
    rename_model_alias,
    update_prompt_pack,
)
from chat_routes import _parse_chat_file
from helcyon_bench_capture import (
    CaptureError,
    list_benchmark_sessions,
    load_integrated_session,
    register_association,
    resolve_association,
    save_integrated_session,
    update_association_status,
)
from helcyon_bench_judge import (
    ApiError,
    ConfigError,
    IntegratedJudgeError,
    judge_run_manager,
    load_judge_settings,
    refresh_judge_models,
    save_judge_api_key,
    test_judge_connection,
)


helcyon_bench_bp = Blueprint("helcyon_bench", __name__)


# ── Free-build feature gate ─────────────────────────────────────────────
# This is the free (GitHub) build of HWUI. Prompt pack/rubric editing and
# read-only browsing (results, leaderboard, saved-session listing) stay
# fully functional. Sending a benchmark prompt into chat, capturing the
# exact reply, running the automated Judge, and saved-session automation
# (create/load/delete) are Pro-only; those routes return this instead.
def _pro_only():
    return jsonify({
        "error": "This feature is available in HWUI Pro.",
        "pro_required": True,
    }), 403


@helcyon_bench_bp.route("/api/helcyon-bench/prompt-packs", methods=["GET"])
def prompt_packs():
    return jsonify({"prompt_packs": load_prompt_packs()})


@helcyon_bench_bp.route("/api/helcyon-bench/prompt-packs", methods=["POST"])
def create_benchmark_prompt_pack():
    data = request.get_json(silent=True) or {}
    try:
        pack = create_prompt_pack(data)
        return jsonify({"pack": pack, "prompt_packs": load_prompt_packs()}), 201
    except PromptPackError as error:
        return jsonify({"error": str(error)}), 422
    except OSError as error:
        return jsonify({"error": f"Could not save prompt pack: {error}"}), 500


@helcyon_bench_bp.route("/api/helcyon-bench/prompt-packs/<pack_id>", methods=["PUT"])
def update_benchmark_prompt_pack(pack_id: str):
    data = request.get_json(silent=True) or {}
    try:
        pack = update_prompt_pack(pack_id, data, data.get("bound_name"))
        return jsonify({"pack": pack, "prompt_packs": load_prompt_packs()})
    except PromptPackNotFoundError as error:
        return jsonify({"error": str(error)}), 404
    except PromptPackError as error:
        return jsonify({"error": str(error)}), 422
    except OSError as error:
        return jsonify({"error": f"Could not save prompt pack: {error}"}), 500


@helcyon_bench_bp.route("/api/helcyon-bench/prompt-packs/<pack_id>", methods=["DELETE"])
def delete_benchmark_prompt_pack(pack_id: str):
    try:
        deleted_name = delete_prompt_pack(pack_id)
        return jsonify({"deleted": deleted_name, "prompt_packs": load_prompt_packs()})
    except PromptPackNotFoundError as error:
        return jsonify({"error": str(error)}), 404
    except PromptPackError as error:
        return jsonify({"error": str(error)}), 422
    except OSError as error:
        return jsonify({"error": f"Could not delete prompt pack: {error}"}), 500


@helcyon_bench_bp.route("/api/helcyon-bench/results", methods=["GET"])
def benchmark_results():
    results = load_benchmark_results()
    return jsonify({"results": results})


@helcyon_bench_bp.route("/api/helcyon-bench/results/<source>", methods=["GET"])
def benchmark_result_detail(source: str):
    try:
        return jsonify({"result": load_benchmark_result_detail(source)})
    except ValueError as error:
        return jsonify({"error": str(error)}), 422
    except FileNotFoundError:
        return jsonify({"error": "Saved benchmark result was not found."}), 404


@helcyon_bench_bp.route("/api/helcyon-bench/leaderboard", methods=["GET"])
def benchmark_leaderboard():
    results = load_benchmark_results()
    return jsonify({"leaderboard": build_leaderboard(results)})


@helcyon_bench_bp.route("/api/helcyon-bench/strength-map", methods=["GET"])
def benchmark_strength_map():
    return jsonify(load_strength_map())


@helcyon_bench_bp.route("/api/helcyon-bench/model-aliases", methods=["POST"])
def consolidate_benchmark_model_aliases():
    data = request.get_json(silent=True) or {}
    try:
        aliases = consolidate_model_aliases(
            data.get("canonical_model"),
            data.get("models"),
        )
        return jsonify({"aliases": aliases})
    except ValueError as error:
        return jsonify({"error": str(error)}), 422
    except OSError as error:
        return jsonify({"error": f"Could not save consolidation: {error}"}), 500


@helcyon_bench_bp.route("/api/helcyon-bench/model-aliases", methods=["PATCH"])
def rename_benchmark_model_alias():
    data = request.get_json(silent=True) or {}
    try:
        aliases = rename_model_alias(
            data.get("model"),
            data.get("new_name"),
        )
        return jsonify({"aliases": aliases})
    except ValueError as error:
        return jsonify({"error": str(error)}), 422
    except OSError as error:
        return jsonify({"error": f"Could not rename model: {error}"}), 500


@helcyon_bench_bp.route("/api/helcyon-bench/model-runs", methods=["DELETE"])
def delete_benchmark_model_runs():
    data = request.get_json(silent=True) or {}
    try:
        deleted = delete_model_benchmark_runs(data.get("model"))
        return jsonify({"deleted": deleted})
    except ValueError as error:
        return jsonify({"error": str(error)}), 422
    except OSError as error:
        return jsonify({"error": f"Could not delete model runs: {error}"}), 500


@helcyon_bench_bp.route("/api/helcyon-bench/session", methods=["GET"])
def get_integrated_benchmark_session():
    try:
        return jsonify({"session": load_integrated_session()})
    except CaptureError as error:
        return jsonify({"error": str(error), "code": error.code}), 500


@helcyon_bench_bp.route("/api/helcyon-bench/session", methods=["PUT"])
def save_integrated_benchmark_session():
    try:
        session = save_integrated_session(request.get_json(silent=True) or {})
        return jsonify({"session": session})
    except CaptureError as error:
        return jsonify({"error": str(error), "code": error.code}), 422


@helcyon_bench_bp.route("/api/helcyon-bench/saved-sessions", methods=["GET"])
def get_saved_benchmark_sessions():
    return jsonify({"sessions": list_benchmark_sessions()})


@helcyon_bench_bp.route("/api/helcyon-bench/saved-sessions", methods=["POST"])
def create_saved_benchmark_session():
    """Saved-session automation is Pro-only in the free build."""
    return _pro_only()


@helcyon_bench_bp.route("/api/helcyon-bench/saved-sessions/<filename>", methods=["GET"])
def get_saved_benchmark_session(filename: str):
    """Saved-session automation is Pro-only in the free build."""
    return _pro_only()


@helcyon_bench_bp.route("/api/helcyon-bench/saved-sessions/<filename>", methods=["DELETE"])
def delete_saved_benchmark_session(filename: str):
    """Saved-session automation is Pro-only in the free build."""
    return _pro_only()


@helcyon_bench_bp.route("/api/helcyon-bench/capture-associations", methods=["POST"])
def create_capture_association():
    try:
        association = register_association(request.get_json(silent=True) or {}, _parse_chat_file)
        return jsonify({"association": association}), 201
    except CaptureError as error:
        return jsonify({"error": str(error), "code": error.code, "status": error.status}), 422


@helcyon_bench_bp.route(
    "/api/helcyon-bench/capture-associations/<association_id>/status",
    methods=["PATCH"],
)
def set_capture_association_status(association_id):
    data = request.get_json(silent=True) or {}
    try:
        association = update_association_status(
            association_id,
            str(data.get("status") or ""),
            assistant_message_id=data.get("assistant_message_id"),
        )
        return jsonify({"association": association})
    except CaptureError as error:
        return jsonify({"error": str(error), "code": error.code, "status": error.status}), 422


@helcyon_bench_bp.route(
    "/api/helcyon-bench/capture-associations/<association_id>/status",
    methods=["GET"],
)
def get_capture_association_status(association_id):
    try:
        resolved = resolve_association(association_id, _parse_chat_file)
        return jsonify(resolved)
    except CaptureError as error:
        return jsonify({"error": str(error), "code": error.code, "status": error.status})


@helcyon_bench_bp.route(
    "/api/helcyon-bench/capture-associations/<association_id>/capture",
    methods=["POST"],
)
def capture_benchmark_response(association_id):
    """Capturing the exact chat reply into a candidate slot is Pro-only in the free build."""
    return _pro_only()


@helcyon_bench_bp.route("/api/helcyon-bench/judge/settings", methods=["GET"])
def get_judge_settings():
    return jsonify(
        load_judge_settings(
            request.args.get("endpoint", ""),
            request.args.get("model", ""),
        )
    )


@helcyon_bench_bp.route("/api/helcyon-bench/judge/api-key", methods=["POST"])
def set_judge_api_key():
    data = request.get_json(silent=True) or {}
    try:
        settings = save_judge_api_key(
            str(data.get("endpoint") or ""),
            str(data.get("api_key") or ""),
        )
        return jsonify({"settings": settings, "message": "Judge API key saved."})
    except (ConfigError, IntegratedJudgeError, OSError) as error:
        return jsonify({"error": str(error)}), 422


@helcyon_bench_bp.route("/api/helcyon-bench/judge/models", methods=["POST"])
def get_judge_models():
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(refresh_judge_models(str(data.get("endpoint") or "")))
    except (ApiError, ConfigError, IntegratedJudgeError, OSError) as error:
        return jsonify(
            {
                "error": str(error),
                "raw_response": getattr(error, "raw_response", None),
                "status_code": getattr(error, "status_code", None),
            }
        ), 502


@helcyon_bench_bp.route("/api/helcyon-bench/judge/test", methods=["POST"])
def test_integrated_judge_connection():
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(
            test_judge_connection(
                str(data.get("endpoint") or ""),
                str(data.get("model") or ""),
            )
        )
    except (ApiError, ConfigError, IntegratedJudgeError, OSError) as error:
        return jsonify(
            {
                "error": str(error),
                "raw_response": getattr(error, "raw_response", None),
                "status_code": getattr(error, "status_code", None),
            }
        ), 502


@helcyon_bench_bp.route("/api/helcyon-bench/judge/runs", methods=["POST"])
def start_integrated_judge_run():
    """The automated Judge run is Pro-only in the free build."""
    return _pro_only()


@helcyon_bench_bp.route(
    "/api/helcyon-bench/judge/runs/<job_id>",
    methods=["GET"],
)
def get_integrated_judge_run(job_id):
    try:
        return jsonify({"job": judge_run_manager.get(job_id)})
    except IntegratedJudgeError as error:
        return jsonify({"error": str(error)}), 404


@helcyon_bench_bp.route(
    "/api/helcyon-bench/judge/runs/<job_id>",
    methods=["DELETE"],
)
def cancel_integrated_judge_run(job_id):
    try:
        return jsonify({"job": judge_run_manager.cancel(job_id)})
    except IntegratedJudgeError as error:
        return jsonify({"error": str(error)}), 404
