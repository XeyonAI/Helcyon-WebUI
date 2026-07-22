import os, json, re
import requests
from flask import Blueprint, request, jsonify
from truncation import rough_token_count

shard_gen_bp = Blueprint('shard_gen', __name__)

# --------------------------------------------------
# Shard Generator — model-direct synthetic shard batches
# --------------------------------------------------
# Talks to the model directly and gets back a raw JSON array instead of
# parsing code fences out of a chat message. JSON has no fence-collision
# problem: code inside a JSON string value is just an escaped string, so
# shards whose content itself contains code (nested ChatML samples, Python
# snippets) survive intact. Coexists with the per-message "export code
# blocks as shard files" button — this is the fence-free path.

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SHARDS_FOLDER = "shards"

MAX_SHARD_COUNT = 25

# Per-format schema notes injected into the system prompt, the required JSON
# fields, and the .txt reassembly template.
SHARD_FORMATS = {
    "cx": {
        "label": "CX",
        "fields": ["instruction", "prompt", "response"],
        "schema_note": (
            'Each shard object must have exactly these string fields: '
            '"instruction", "prompt", "response".'
        ),
        "template": "Instruction: {instruction}\n\nPrompt: {prompt}\n\nResponse: {response}",
    },
    "dpo": {
        "label": "DPO",
        "fields": ["instruction", "prompt", "chosen", "rejected"],
        "schema_note": (
            'Each shard object must have exactly these string fields: '
            '"instruction", "prompt", "chosen", "rejected".'
        ),
        "template": (
            "Instruction: {instruction}\n\nPrompt: {prompt}\n\n"
            "Chosen: {chosen}\n\nRejected: {rejected}"
        ),
    },
    "chatml": {
        "label": "ChatML teaching",
        "fields": ["instruction", "prompt", "response"],
        "schema_note": (
            'Each shard object must have exactly these string fields: '
            '"instruction", "prompt", "response". The response field must contain '
            'a ChatML-style sample using the literal placeholder tags '
            '⟦im_start⟧ and ⟦im_end⟧ (never the real special '
            'tokens <|im_start|> / <|im_end|>).'
        ),
        "template": "Instruction: {instruction}\n\nPrompt: {prompt}\n\nResponse: {response}",
    },
}


def build_shard_system_prompt(format_key, count, brief):
    fmt = SHARD_FORMATS[format_key]
    return (
        "You generate synthetic training data (shards) for finetuning a language model called Helcyon.\n\n"
        f"Brief for this batch: {brief}\n\n"
        "Format rules:\n"
        f"- {fmt['schema_note']}\n"
        '- The instruction field must always be phrased in second person, telling Helcyon what it is '
        'capable of, skilled at, or enjoys doing. NEVER use "You are a helpful assistant" or similarly '
        'bland/corporate phrasing. Vary sentence structure completely across every shard, never '
        'repeating a template.\n'
        "- Content must be specific and realistic, never generic filler. Real details, real steps, "
        "real settings, real reasoning.\n"
        "- Never use markdown code fences (triple backticks) anywhere inside any field, even for code "
        "examples. Write code plainly with line breaks and indentation only.\n"
        "- Keep each shard under roughly 1024 tokens total.\n\n"
        "Output format:\n"
        f"Return ONLY a raw JSON array of exactly {count} shard objects, nothing else. No markdown "
        "fences, no preamble, no explanation, no trailing text. The response must start with [ and "
        "end with ] and be valid JSON."
    )


def parse_shard_json(raw_text):
    """Defensively parse the model's reply into a list of shard dicts.
    Strips accidental markdown fence wrappers and stray preamble/postamble
    text before handing to the JSON parser. Raises ValueError with a
    readable message on failure — caller surfaces the raw text."""
    text = (raw_text or "").strip()
    if not text:
        raise ValueError("Model returned an empty response")

    # Strip an accidental ```json ... ``` wrapper even though the prompt
    # forbids it — models occasionally wrap anyway, sometimes with preamble
    # before the fence ("Here is the batch: ```json ...").
    if not text.startswith("["):
        fence = re.search(r"```[a-zA-Z]*\s*\n(.*?)(?:\n```|\Z)", text, re.DOTALL)
        if fence:
            text = fence.group(1).strip()

    # Slice between the first [ and last ] in case of stray surrounding text.
    start = text.find("[")
    if start == -1:
        raise ValueError("No JSON array found in model response")
    end = text.rfind("]")
    if end > start:
        candidate = text[start:end + 1]
    else:
        candidate = text[start:]  # no closing ] — likely truncated generation

    # strict=False: allow literal newlines/tabs inside string values — local
    # models often emit multi-line response fields without \n escaping.
    try:
        parsed = json.loads(candidate, strict=False)
    except json.JSONDecodeError:
        # Generation may have been cut mid-shard by the token cap. Salvage the
        # complete objects: trim to the last complete '}' and close the array.
        last_obj = candidate.rfind("}")
        if last_obj == -1:
            raise
        parsed = json.loads(candidate[:last_obj + 1].rstrip().rstrip(",") + "]",
                            strict=False)
        print("⚠️ shard gen: model output looked truncated — salvaged the "
              "complete shards from a partial array", flush=True)
    if not isinstance(parsed, list):
        raise ValueError("Model returned valid JSON but not an array")
    return parsed


def slugify_shard_name(text, max_len=40):
    """Lowercase, collapse non-alphanumeric runs to underscores, trim, cap."""
    slug = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("_")
    return slug or "shard"


def resolve_output_folder(raw_folder):
    """Default 'shards' next to app.py; relative overrides join to the app
    dir, absolute overrides are used as-is (local single-user app)."""
    folder = (raw_folder or "").strip() or DEFAULT_SHARDS_FOLDER
    if not os.path.isabs(folder):
        folder = os.path.join(BASE_DIR, folder)
    return os.path.normpath(folder)


def _load_settings():
    try:
        with open(os.path.join(BASE_DIR, "settings.json"), "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _generate_local(system_prompt, max_new_tokens):
    """llama.cpp /completion path — same wiring as generate_session_summary."""
    from app_runtime_helpers import get_api_url, get_stop_tokens

    prompt = (
        f"<|im_start|>system\n{system_prompt}<|im_end|>\n"
        f"<|im_start|>user\n"
        f"Generate the batch now. Output only the raw JSON array.<|im_end|>\n"
        f"<|im_start|>assistant\n"
    )

    # Cap n_predict to actual KV space left after the prompt (rough_token_count
    # undercounts BPE by ~25% — same fudge as truncation.py / summary route).
    try:
        ctx_size = int(_load_settings().get("llama_args", {}).get("ctx_size", 12288))
    except Exception:
        ctx_size = 12288
    est_prompt = int(rough_token_count(prompt) * 1.25)
    n_predict = max(256, min(max_new_tokens, ctx_size - est_prompt - 64))
    print(f"🧩 shard gen: local path, prompt ~{est_prompt} real / {ctx_size} ctx, "
          f"n_predict={n_predict}", flush=True)

    resp = requests.post(
        f"{get_api_url()}/completion",
        json={
            "prompt": prompt,
            "temperature": 0.8,
            "top_p": 0.9,
            "n_predict": n_predict,
            "repeat_penalty": 1.05,
            "stream": False,
            "stop": get_stop_tokens(),
        },
        timeout=600,
    )
    if resp.status_code >= 400:
        body = ""
        try:
            body = resp.text[:500]
        except Exception:
            pass
        raise RuntimeError(f"llama.cpp returned {resp.status_code}: {body or 'no body'}")
    return resp.json().get("content", "").strip()


def _generate_openai(system_prompt, max_new_tokens):
    from app_runtime_helpers import openai_caps_for
    from cloud_api_routes import get_openai_base_url

    settings = _load_settings()
    api_key = (settings.get("openai_api_key", "") or "").strip()
    model = (settings.get("openai_model", "") or "").strip() or "gpt-4o"
    if not api_key:
        raise RuntimeError("OpenAI backend selected but no API key is configured")

    caps = openai_caps_for(model)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "Generate the batch now. Output only the raw JSON array."},
        ],
        "stream": False,
    }
    payload[caps["token_param"]] = max_new_tokens
    if caps["sampling"]:
        payload["temperature"] = 0.8
        payload["top_p"] = 0.9

    print(f"🧩 shard gen: OpenAI path model={model}, max_tokens={max_new_tokens}", flush=True)
    resp = requests.post(
        f"{get_openai_base_url()}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=600,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"OpenAI returned {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    return data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()


def _generate_anthropic(system_prompt, max_new_tokens):
    from app_runtime_helpers import supports_temperature
    from cloud_api_routes import get_anthropic_base_url

    settings = _load_settings()
    api_key = (settings.get("anthropic_api_key", "") or "").strip()
    model = (settings.get("anthropic_model", "") or "").strip() or "claude-sonnet-4-5"
    if not api_key:
        raise RuntimeError("Anthropic backend selected but no API key is configured")

    payload = {
        "model": model,
        "max_tokens": max_new_tokens,
        "stream": False,
        "system": system_prompt,
        "messages": [
            {"role": "user", "content": "Generate the batch now. Output only the raw JSON array."},
        ],
    }
    if supports_temperature(model):
        payload["temperature"] = 0.8

    print(f"🧩 shard gen: Anthropic path model={model}, max_tokens={max_new_tokens}", flush=True)
    resp = requests.post(
        f"{get_anthropic_base_url()}/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json=payload,
        timeout=600,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Anthropic returned {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    return "".join(
        part.get("text", "")
        for part in data.get("content", [])
        if isinstance(part, dict) and part.get("type") == "text"
    ).strip()


@shard_gen_bp.route("/shards/generate", methods=["POST"])
def generate_shards():
    """Generate a batch of shards via the active backend and write one .txt
    per shard. On parse/validation failure the raw model output is returned
    so the user can see what went wrong instead of a silent/generic error."""
    raw_output = ""
    try:
        data = request.get_json(force=True, silent=True) or {}
        format_key = (data.get("format") or "").strip().lower()
        brief = (data.get("brief") or "").strip()
        folder_override = data.get("folder") or ""
        try:
            count = int(data.get("count") or 0)
        except (TypeError, ValueError):
            count = 0

        if format_key not in SHARD_FORMATS:
            return jsonify({"status": "error",
                            "error": f"Unknown format '{format_key}'. "
                                     f"Expected one of: {', '.join(SHARD_FORMATS)}."}), 400
        if not brief:
            return jsonify({"status": "error", "error": "Brief is required."}), 400
        if count < 1 or count > MAX_SHARD_COUNT:
            return jsonify({"status": "error",
                            "error": f"Count must be between 1 and {MAX_SHARD_COUNT}."}), 400

        fmt = SHARD_FORMATS[format_key]
        system_prompt = build_shard_system_prompt(format_key, count, brief)

        # Shards are capped at ~1024 tokens each by the prompt, but models
        # routinely overshoot — budget double per shard so the closing ] lands
        # before the token cap. Cloud caps at 16384 to stay inside gpt-4o /
        # claude output limits, local additionally caps by KV space.
        max_new_tokens = min(count * 2048 + 1024, 16384)

        settings = _load_settings()
        backend_mode = (settings.get("backend_mode", "local") or "local").lower()
        cloud_enabled = bool(settings.get("cloud_api_enabled", False))

        if backend_mode == "openai" and cloud_enabled:
            raw_output = _generate_openai(system_prompt, max_new_tokens)
        elif backend_mode == "anthropic" and cloud_enabled:
            raw_output = _generate_anthropic(system_prompt, max_new_tokens)
        else:
            raw_output = _generate_local(system_prompt, max_new_tokens)

        shards = parse_shard_json(raw_output)

        # Validate shapes before writing anything — one malformed shard fails
        # the batch with the raw output shown, so nothing half-written lands.
        for i, shard in enumerate(shards, 1):
            if not isinstance(shard, dict):
                raise ValueError(f"Shard {i} is not a JSON object")
            for field in fmt["fields"]:
                if not isinstance(shard.get(field), str):
                    raise ValueError(f"Shard {i} is missing string field '{field}'")

        if len(shards) != count:
            print(f"⚠️ shard gen: asked for {count}, model returned {len(shards)} — "
                  f"writing what came back", flush=True)

        shards_dir = resolve_output_folder(folder_override)
        os.makedirs(shards_dir, exist_ok=True)

        written = []
        width = max(2, len(str(len(shards))))
        for idx, shard in enumerate(shards, 1):
            if format_key == "chatml":
                # Defensive: swap any real ChatML special tokens the model
                # leaked for the placeholder tags so nested samples can never
                # be parsed as real turn boundaries during training.
                shard = dict(shard)
                shard["response"] = (
                    shard["response"]
                    .replace("<|im_start|>", "⟦im_start⟧")
                    .replace("<|im_end|>", "⟦im_end⟧")
                )

            body = fmt["template"].format(**{f: shard[f] for f in fmt["fields"]})
            body = body.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"

            slug = slugify_shard_name(shard["instruction"].strip() or shard["prompt"])
            name = f"shard_{idx:0{width}d}_{slug}.txt"
            path = os.path.join(shards_dir, name)
            suffix = 2
            while os.path.exists(path):
                name = f"shard_{idx:0{width}d}_{slug}_{suffix}.txt"
                path = os.path.join(shards_dir, name)
                suffix += 1

            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(body)
            written.append(name)

        print(f"🧩 Generated {len(written)} {fmt['label']} shard(s) → {shards_dir}", flush=True)
        return jsonify({
            "status": "ok",
            "count": len(written),
            "folder": shards_dir,
            "files": written,
        })

    except (ValueError, json.JSONDecodeError) as e:
        # Parse/validation failure — surface the raw model output.
        print(f"❌ shard gen parse failed: {e}", flush=True)
        return jsonify({
            "status": "error",
            "error": f"Could not parse model output: {e}",
            "raw": raw_output,
        }), 502
    except Exception as e:
        import traceback
        print(f"❌ generate_shards error: {e}")
        traceback.print_exc()
        return jsonify({"status": "error", "error": str(e), "raw": raw_output}), 500
