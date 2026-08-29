"""Portable user-preference storage with legacy-file compatibility."""

import json
import os
import tempfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USERS_DIR = os.path.join(BASE_DIR, "users")
USER_CONFIG_FILE = os.path.join(USERS_DIR, "user_config.json")

_LEGACY_FILES = {
    "character_groups": os.path.join(BASE_DIR, "characters", "_character_groups.json"),
    "voice_groups": os.path.join(BASE_DIR, "voice_groups.json"),
    "active_character": os.path.join(BASE_DIR, "characters", "_active_character.json"),
    "sampling_presets": os.path.join(BASE_DIR, "sampling_presets.json"),
    "theme_presets": os.path.join(BASE_DIR, "theme_presets.json"),
    "project_colours": os.path.join(BASE_DIR, "project_colours.json"),
    "tts_paths": os.path.join(BASE_DIR, "tts_paths.local.json"),
}

_DEFAULTS = {
    "character_groups": {"groups": [], "assignments": {}, "collapsed": {}},
    "voice_groups": {"groups": [], "assignments": {}, "collapsed": {}},
    "active_character": {"active_character": None},
    "sampling_presets": {},
    "theme_presets": {},
    "project_colours": {},
    "tts_paths": {},
}


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _write_config(config):
    os.makedirs(USERS_DIR, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix="user_config_", suffix=".tmp", dir=USERS_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temp_path, USER_CONFIG_FILE)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def load_user_config():
    config = _read_json(USER_CONFIG_FILE) or {}
    changed = False
    for section, legacy_path in _LEGACY_FILES.items():
        if section not in config:
            legacy_value = _read_json(legacy_path)
            config[section] = legacy_value if legacy_value is not None else dict(_DEFAULTS[section])
            changed = True
    if config.get("version") != 1:
        config["version"] = 1
        changed = True
    if changed:
        _write_config(config)
    return config


def load_user_config_section(section):
    return load_user_config().get(section, dict(_DEFAULTS.get(section, {})))


def save_user_config_section(section, value):
    config = load_user_config()
    config[section] = value
    config["version"] = 1
    _write_config(config)
