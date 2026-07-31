from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from yaml.nodes import MappingNode, SequenceNode


class ConfigError(Exception):
    """Raised when config.yaml is missing or invalid."""


# --- Result classification thresholds --------------------------------------
#
# These two thresholds turn a raw overall-score gap (0-10 scale, weighted mean
# of the applicable categories) into one of three headline classifications:
# Clear Winner, Narrow Preference, or Effective Tie. See classify_score_gap()
# in llmbench.judge for the comparison logic itself; this module only owns the
# default values so they can be overridden from config.yaml without every
# caller needing to know where the numbers came from.
#
# Defaults are chosen from the rubric's own scoring scale (llmbench.judge's
# SYSTEM_PROMPT documents roughly 1.0-point bands between adjacent quality
# descriptions, e.g. "9.0 Excellent" vs "8.0 Very good"):
#   - 0.25 (a quarter of the smallest documented rubric band) is small enough
#     that two responses landing within it are indistinguishable to a human
#     reading the rubric's own scale, so it is treated as an effective tie.
#   - 1.0 (a full rubric band) is chosen as the point where the gap matches or
#     exceeds a full documented quality tier, which is a safe bar for calling
#     a result a clear win.
# These are deliberately simple, tunable constants rather than a statistically
# calibrated noise model - see protocol.py's module docstring for the fuller
# calibration approach this is intended to be upgradeable into later.
DEFAULT_EFFECTIVE_TIE_THRESHOLD = 0.25
DEFAULT_CLEAR_WINNER_THRESHOLD = 1.0


@dataclass
class ScoringConfig:
    """Configurable thresholds for the result classification system.

    effective_tie_threshold: absolute overall-score gaps at or below this are
        reported as an Effective Tie rather than a win for either side.
    clear_winner_threshold: absolute overall-score gaps at or above this are
        reported as a Clear Winner. Gaps strictly between the two thresholds
        are reported as a Narrow Preference.
    """

    effective_tie_threshold: float = DEFAULT_EFFECTIVE_TIE_THRESHOLD
    clear_winner_threshold: float = DEFAULT_CLEAR_WINNER_THRESHOLD


@dataclass
class JudgeEndpointConfig:
    name: str
    base_url: str
    api_key: str
    model: str
    local_endpoint_mode: str = "external"
    bench_lock_url: str = ""
    bench_unlock_url: str = ""
    temperature: float = 0.0
    max_tokens: int = 1200
    max_completion_tokens: int | None = None
    timeout: int = 120
    models: list[str] | None = None


@dataclass
class JudgeConfig(JudgeEndpointConfig):
    endpoints: list[JudgeEndpointConfig] | None = None


@dataclass
class AppConfig:
    judge: JudgeConfig
    debug_logging: bool = True
    scoring: ScoringConfig = field(default_factory=ScoringConfig)


def load_scoring_config(raw: dict[str, Any]) -> ScoringConfig:
    """Parse the optional top-level `scoring:` section.

    Absent entirely, or missing individual keys, in any existing config.yaml -
    older configuration files simply get the documented defaults rather than
    failing to load.
    """
    section = raw.get("scoring")
    if section is None:
        return ScoringConfig()
    if not isinstance(section, dict):
        raise ConfigError("config.yaml's scoring section must be a mapping.")

    def positive_float(key: str, default: float) -> float:
        value = section.get(key, default)
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ConfigError(f"config.yaml scoring.{key} must be a number.") from None
        if value < 0:
            raise ConfigError(f"config.yaml scoring.{key} must not be negative.")
        return value

    effective_tie_threshold = positive_float("effective_tie_threshold", DEFAULT_EFFECTIVE_TIE_THRESHOLD)
    clear_winner_threshold = positive_float("clear_winner_threshold", DEFAULT_CLEAR_WINNER_THRESHOLD)
    if clear_winner_threshold <= effective_tie_threshold:
        raise ConfigError(
            "config.yaml scoring.clear_winner_threshold must be greater than "
            "scoring.effective_tie_threshold."
        )
    return ScoringConfig(
        effective_tie_threshold=effective_tie_threshold,
        clear_winner_threshold=clear_winner_threshold,
    )


def load_config(app_dir: Path) -> AppConfig:
    path = app_dir / "config.yaml"
    if not path.exists():
        raise ConfigError("config.yaml was not found.")

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    judge = raw.get("judge")
    if not isinstance(judge, dict):
        raise ConfigError("config.yaml must contain a judge section.")

    endpoints = load_judge_endpoints(judge)
    default_endpoint = endpoint_default(judge, endpoints)

    return AppConfig(
        judge=JudgeConfig(
            name=default_endpoint.name,
            base_url=default_endpoint.base_url,
            api_key=default_endpoint.api_key,
            model=default_endpoint.model,
            local_endpoint_mode=default_endpoint.local_endpoint_mode,
            bench_lock_url=default_endpoint.bench_lock_url,
            bench_unlock_url=default_endpoint.bench_unlock_url,
            temperature=default_endpoint.temperature,
            max_tokens=default_endpoint.max_tokens,
            max_completion_tokens=default_endpoint.max_completion_tokens,
            timeout=default_endpoint.timeout,
            models=default_endpoint.models,
            endpoints=endpoints,
        ),
        debug_logging=bool(judge.get("debug_logging", True)),
        scoring=load_scoring_config(raw),
    )


def load_judge_endpoint(name: str, raw: dict[str, Any]) -> JudgeEndpointConfig:
    required = ["base_url", "api_key", "model"]
    missing = [key for key in required if not raw.get(key)]
    if missing:
        raise ConfigError(f"config.yaml is missing judge.{', judge.'.join(missing)}.")

    models = raw.get("models")
    if models is None:
        models = [raw["model"]]
    elif not isinstance(models, list) or not all(isinstance(item, str) for item in models):
        raise ConfigError("judge.models must be a list of model names.")

    return JudgeEndpointConfig(
        name=str(raw.get("name") or name),
        base_url=str(raw["base_url"]).rstrip("/"),
        api_key=str(raw["api_key"]),
        model=str(raw["model"]),
        local_endpoint_mode=str(raw.get("local_endpoint_mode") or "external"),
        bench_lock_url=str(raw.get("bench_lock_url") or ""),
        bench_unlock_url=str(raw.get("bench_unlock_url") or ""),
        temperature=float(raw.get("temperature", 0.0)),
        max_tokens=int(raw.get("max_tokens", 1200)),
        max_completion_tokens=(
            int(raw["max_completion_tokens"])
            if raw.get("max_completion_tokens") is not None
            else None
        ),
        timeout=int(raw.get("timeout", 120)),
        models=models,
    )


def load_judge_endpoints(judge: dict[str, Any]) -> list[JudgeEndpointConfig]:
    raw_endpoints = judge.get("endpoints")
    if raw_endpoints is None:
        return [load_judge_endpoint("Default", judge)]

    endpoints = []
    if isinstance(raw_endpoints, dict):
        endpoint_items = raw_endpoints.items()
    elif isinstance(raw_endpoints, list):
        endpoint_items = [
            (str(item.get("name") or f"Endpoint {index}"), item)
            for index, item in enumerate(raw_endpoints, start=1)
            if isinstance(item, dict)
        ]
    else:
        raise ConfigError("judge.endpoints must be a mapping or list.")

    for name, endpoint in endpoint_items:
        if not isinstance(endpoint, dict):
            raise ConfigError("Each judge endpoint must be an object.")
        merged = {
            "api_key": judge.get("api_key"),
            "temperature": judge.get("temperature", 0.0),
            "max_tokens": judge.get("max_tokens", 1200),
            "max_completion_tokens": judge.get("max_completion_tokens"),
            "timeout": judge.get("timeout", 120),
        } | endpoint
        endpoints.append(load_judge_endpoint(str(name), merged))

    if not endpoints:
        raise ConfigError("judge.endpoints must contain at least one endpoint.")
    return endpoints


def endpoint_default(judge: dict[str, Any], endpoints: list[JudgeEndpointConfig]) -> JudgeEndpointConfig:
    default_name = str(judge.get("default_endpoint") or "").strip()
    if default_name:
        for endpoint in endpoints:
            if endpoint.name == default_name:
                return endpoint
        raise ConfigError(f"judge.default_endpoint does not match a configured endpoint: {default_name}")
    return endpoints[0]


def judge_endpoint_by_name(config: JudgeConfig, name: str | None) -> JudgeEndpointConfig:
    endpoints = config.endpoints or [config]
    for endpoint in endpoints:
        if endpoint.name == name:
            return endpoint
    return endpoints[0]


def _mapping_value(node: MappingNode, key: str):
    for key_node, value_node in node.value:
        if key_node.value == key:
            return value_node
    return None


def save_judge_endpoint_api_key(app_dir: Path, endpoint_name: str, api_key: str) -> None:
    api_key = api_key.strip()
    if not api_key:
        raise ConfigError("API key cannot be empty.")

    path = app_dir / "config.yaml"
    if not path.exists():
        raise ConfigError("config.yaml was not found.")

    text = path.read_text(encoding="utf-8")
    root = yaml.compose(text)
    if not isinstance(root, MappingNode):
        raise ConfigError("config.yaml must contain a judge section.")
    judge = _mapping_value(root, "judge")
    if not isinstance(judge, MappingNode):
        raise ConfigError("config.yaml must contain a judge section.")

    target = judge
    endpoints = _mapping_value(judge, "endpoints")
    if isinstance(endpoints, MappingNode):
        target = next(
            (
                value_node
                for key_node, value_node in endpoints.value
                if key_node.value == endpoint_name and isinstance(value_node, MappingNode)
            ),
            None,
        )
    elif isinstance(endpoints, SequenceNode):
        target = next(
            (
                item
                for index, item in enumerate(endpoints.value, start=1)
                if isinstance(item, MappingNode)
                and (_mapping_value(item, "name").value if _mapping_value(item, "name") else f"Endpoint {index}")
                == endpoint_name
            ),
            None,
        )

    if not isinstance(target, MappingNode):
        raise ConfigError(f"Judge endpoint was not found: {endpoint_name}")
    if target.flow_style:
        raise ConfigError("Flow-style endpoint configuration cannot be updated from the UI.")

    encoded_key = json.dumps(api_key)
    existing_value = _mapping_value(target, "api_key")
    if existing_value is not None:
        updated = text[: existing_value.start_mark.index] + encoded_key + text[existing_value.end_mark.index :]
    elif target.value:
        first_key = target.value[0][0]
        insert_at = first_key.start_mark.index - first_key.start_mark.column
        indent = " " * first_key.start_mark.column
        updated = text[:insert_at] + f"{indent}api_key: {encoded_key}\n" + text[insert_at:]
    else:
        raise ConfigError("Empty endpoint configuration cannot be updated from the UI.")

    path.write_text(updated, encoding="utf-8")


def as_dict(config: AppConfig) -> dict[str, Any]:
    return {
        "scoring": {
            "effective_tie_threshold": config.scoring.effective_tie_threshold,
            "clear_winner_threshold": config.scoring.clear_winner_threshold,
        },
        "judge": {
            "name": config.judge.name,
            "base_url": config.judge.base_url,
            "api_key": config.judge.api_key,
            "model": config.judge.model,
            "local_endpoint_mode": config.judge.local_endpoint_mode,
            "bench_lock_url": config.judge.bench_lock_url,
            "bench_unlock_url": config.judge.bench_unlock_url,
            "temperature": config.judge.temperature,
            "max_tokens": config.judge.max_tokens,
            "max_completion_tokens": config.judge.max_completion_tokens,
            "timeout": config.judge.timeout,
            "models": config.judge.models or [config.judge.model],
            "endpoints": [
                {
                    "name": endpoint.name,
                    "base_url": endpoint.base_url,
                    "api_key": endpoint.api_key,
                    "model": endpoint.model,
                    "local_endpoint_mode": endpoint.local_endpoint_mode,
                    "bench_lock_url": endpoint.bench_lock_url,
                    "bench_unlock_url": endpoint.bench_unlock_url,
                    "temperature": endpoint.temperature,
                    "max_tokens": endpoint.max_tokens,
                    "max_completion_tokens": endpoint.max_completion_tokens,
                    "timeout": endpoint.timeout,
                    "models": endpoint.models or [endpoint.model],
                }
                for endpoint in (config.judge.endpoints or [config.judge])
            ],
        }
    }
