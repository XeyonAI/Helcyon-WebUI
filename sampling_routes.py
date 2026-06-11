from flask import Blueprint, request, jsonify
import os, json

sampling_bp = Blueprint('sampling', __name__)

# --------------------------------------------------
# Sampling presets — disk-backed (mirrors theme presets).
# Previously these lived only in browser localStorage, so edits never persisted
# to disk and were lost on cache-clear / different browser / Electron storage
# resets. Stored as a name → {temperature, max_tokens, …} map.
# --------------------------------------------------
SAMPLING_PRESETS_FILE = "sampling_presets.json"

def load_sampling_presets():
    if os.path.exists(SAMPLING_PRESETS_FILE):
        try:
            with open(SAMPLING_PRESETS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"❌ load_sampling_presets failed: {e}")
    return {}

def save_sampling_presets(presets):
    # Atomic write so a crash mid-save can't truncate the file.
    import tempfile, shutil
    tmp = SAMPLING_PRESETS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(presets, f, indent=2)
    shutil.move(tmp, SAMPLING_PRESETS_FILE)

@sampling_bp.route("/sampling_presets", methods=["GET"])
def get_sampling_presets():
    presets = load_sampling_presets()
    print(f"[SP] GET /sampling_presets → {list(presets.keys())} "
          f"(top_p per preset: { {k: v.get('top_p') for k, v in presets.items()} })", flush=True)
    return jsonify(presets)

@sampling_bp.route("/sampling_presets/save", methods=["POST"])
def save_sampling_preset_route():
    try:
        data = request.get_json()
        print(f"[SP] POST /sampling_presets/save raw body: {data}", flush=True)
        name = (data.get("name", "") or "").strip()
        preset = data.get("preset", {})
        print(f"[SP]   name={name!r}  preset={preset}  top_p={preset.get('top_p') if isinstance(preset, dict) else 'N/A'}", flush=True)
        if not name:
            return jsonify({"error": "No name provided"}), 400
        if not isinstance(preset, dict):
            return jsonify({"error": "Preset must be an object"}), 400
        presets = load_sampling_presets()
        presets[name] = preset
        save_sampling_presets(presets)
        # Read back from disk to PROVE the write landed (and what top_p actually is).
        verify = load_sampling_presets()
        print(f"[SP]   wrote {os.path.abspath(SAMPLING_PRESETS_FILE)} — "
              f"read-back '{name}'.top_p = {verify.get(name, {}).get('top_p')}", flush=True)
        return jsonify({"status": "ok"})
    except Exception as e:
        print(f"❌ save_sampling_preset failed: {e}", flush=True)
        return jsonify({"error": str(e)}), 500

@sampling_bp.route("/sampling_presets/delete", methods=["POST"])
def delete_sampling_preset_route():
    try:
        data = request.get_json()
        name = (data.get("name", "") or "").strip()
        presets = load_sampling_presets()
        if name in presets:
            del presets[name]
            save_sampling_presets(presets)
            print(f"🗑️ Sampling preset deleted: {name}")
        return jsonify({"status": "ok"})
    except Exception as e:
        print(f"❌ delete_sampling_preset failed: {e}")
        return jsonify({"error": str(e)}), 500
