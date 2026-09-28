"""Portable user-preference storage with legacy-file compatibility."""

import json
import os
import random
import tempfile
import unicodedata
from copy import deepcopy

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
    "random_checkins": {
        "enabled": False,
        "trigger_mode": "random",
        "min_inactivity_minutes": 30,
        "max_inactivity_minutes": 90,
        "scheduled_times": [],
        "schedule_mode": "specific",
        "schedule_interval_minutes": 60,
        "message_mode": "random",
        "guidance": "",
        "custom_messages": [],
        "quiet_hours_enabled": False,
        "quiet_start": "22:00",
        "quiet_end": "08:00",
        "max_per_day": 3,
        "desktop_notifications": True,
        "mobile_notifications": False,
        "runtime": {
            "chats": {},
            "seen_fire_ids": [],
        },
    },
}

RANDOM_CHECKINS_DEFAULTS = deepcopy(_DEFAULTS["random_checkins"])
RANDOM_CHECKINS_SCHEDULE_GRACE_MINUTES = 15
# Every user-editable Random Check-ins setting (everything except runtime state).
RANDOM_CHECKINS_SETTING_KEYS = (
    "enabled", "trigger_mode", "min_inactivity_minutes",
    "max_inactivity_minutes", "scheduled_times", "schedule_mode",
    "schedule_interval_minutes", "message_mode", "guidance",
    "custom_messages", "quiet_hours_enabled", "quiet_start",
    "quiet_end", "max_per_day", "desktop_notifications", "mobile_notifications",
)
RANDOM_CHECKIN_PROFILES_SECTION = "random_checkin_profiles"
RANDOM_CHECKIN_DEFAULT_PROFILE = "Default"
RANDOM_CHECKIN_PROFILE_NAME_MAX = 48
RANDOM_CHECKIN_PROFILE_LIMIT = 50


def _merge_random_checkins(value):
    """Return a bounded, backwards-compatible random-checkins section."""
    source = value if isinstance(value, dict) else {}
    result = deepcopy(RANDOM_CHECKINS_DEFAULTS)
    for key in RANDOM_CHECKINS_SETTING_KEYS:
        if key in source:
            result[key] = source[key]
    runtime = source.get("runtime")
    if isinstance(runtime, dict):
        result["runtime"].update(runtime)
    if not isinstance(result["runtime"].get("chats"), dict):
        result["runtime"]["chats"] = {}
    if not isinstance(result["runtime"].get("seen_fire_ids"), list):
        result["runtime"]["seen_fire_ids"] = []
    return result


def _checkin_int(value, fallback, minimum, maximum):
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return fallback


def _checkin_time(value, fallback=None):
    """Return a canonical HH:MM value, or fallback for invalid input."""
    try:
        text = str(value).strip()
        if len(text) != 5 or text[2] != ":":
            return fallback
        hours, minutes = text.split(":", 1)
        if not hours.isdigit() or not minutes.isdigit():
            return fallback
        hours, minutes = int(hours), int(minutes)
        if not (0 <= hours < 24 and 0 <= minutes < 60):
            return fallback
        return f"{hours:02d}:{minutes:02d}"
    except (TypeError, ValueError):
        return fallback


def normalise_random_checkins_config(value, preserve_runtime=None):
    """Validate user-editable settings while retaining backend runtime state."""
    result = _merge_random_checkins(value)
    result["enabled"] = bool(result["enabled"])
    result["trigger_mode"] = (
        result["trigger_mode"] if result["trigger_mode"] in ("random", "scheduled")
        else "random"
    )
    if result["message_mode"] == "generated":
        result["message_mode"] = "random"
    result["message_mode"] = (
        result["message_mode"]
        if result["message_mode"] in ("random", "guided", "custom")
        else "random"
    )
    result["min_inactivity_minutes"] = _checkin_int(
        result["min_inactivity_minutes"], 30, 1, 7 * 24 * 60
    )
    result["max_inactivity_minutes"] = _checkin_int(
        result["max_inactivity_minutes"], 90, result["min_inactivity_minutes"], 7 * 24 * 60
    )
    result["scheduled_times"] = [
        parsed for item in (result["scheduled_times"] or [])
        if (parsed := _checkin_time(item)) is not None
    ][:20]
    result["schedule_mode"] = (
        result["schedule_mode"]
        if result["schedule_mode"] in ("specific", "interval")
        else "specific"
    )
    interval = _checkin_int(result["schedule_interval_minutes"], 60, 1, 60)
    result["schedule_interval_minutes"] = interval if interval in (1, 5, 15, 30, 60) else 60
    result["guidance"] = str(result["guidance"] or "").strip()[:8000]
    result["custom_messages"] = [
        str(item).strip() for item in (result["custom_messages"] or [])
        if str(item).strip()
    ][:64]
    result["custom_messages"] = [item[:4000] for item in result["custom_messages"]]
    result["quiet_hours_enabled"] = bool(result["quiet_hours_enabled"])
    result["quiet_start"] = _checkin_time(result["quiet_start"], "22:00")
    result["quiet_end"] = _checkin_time(result["quiet_end"], "08:00")
    result["max_per_day"] = _checkin_int(result["max_per_day"], 3, 1, 100)
    result["desktop_notifications"] = result["desktop_notifications"] is not False
    # Phone push needs a registered device, so it is opt-in: only an explicit true enables it.
    result["mobile_notifications"] = result["mobile_notifications"] is True
    if preserve_runtime is not None:
        result["runtime"] = deepcopy(preserve_runtime)
    return result


def load_random_checkins_config():
    return normalise_random_checkins_config(load_user_config_section("random_checkins"))


def save_random_checkins_config(value):
    config = normalise_random_checkins_config(value)
    save_user_config_section("random_checkins", config)
    return config


# --------------------------------------------------------------------------
# Random Check-in profiles — saved configurations only. They live in their own
# section so scheduler/claim runtime writes to "random_checkins" never touch
# them. The selected profile is the configuration in effect: selecting or
# saving one applies its settings to "random_checkins" (runtime preserved).
# --------------------------------------------------------------------------
def random_checkin_profile_settings(value):
    """Validated user-editable settings only — profiles never carry runtime."""
    normalised = normalise_random_checkins_config(value)
    return {key: deepcopy(normalised[key]) for key in RANDOM_CHECKINS_SETTING_KEYS}


def random_checkin_profile_name(value):
    """Return a clean profile name, or None when it is unusable."""
    if not isinstance(value, str):
        return None
    name = " ".join(value.split())
    if not name or len(name) > RANDOM_CHECKIN_PROFILE_NAME_MAX:
        return None
    if any(unicodedata.category(char).startswith("C") for char in name):
        return None
    return name


def _find_random_checkin_profile(profiles, name):
    """Case-insensitive lookup; returns the stored name or None."""
    if not isinstance(name, str):
        return None
    wanted = " ".join(name.split()).casefold()
    return next((key for key in profiles if key.casefold() == wanted), None)


def load_random_checkin_profiles(live_config=None):
    """Return {"active": name, "profiles": {name: settings}}.

    The first load seeds the protected Default profile from the live settings,
    so introducing profiles never changes what is currently running.
    """
    raw = load_user_config_section(RANDOM_CHECKIN_PROFILES_SECTION)
    raw = raw if isinstance(raw, dict) else {}
    stored = raw.get("profiles") if isinstance(raw.get("profiles"), dict) else {}
    profiles = {}
    for key, settings in stored.items():
        name = random_checkin_profile_name(key)
        if not name or not isinstance(settings, dict) or _find_random_checkin_profile(profiles, name):
            continue
        if name.casefold() == RANDOM_CHECKIN_DEFAULT_PROFILE.casefold():
            name = RANDOM_CHECKIN_DEFAULT_PROFILE
        profiles[name] = random_checkin_profile_settings(settings)
    if RANDOM_CHECKIN_DEFAULT_PROFILE not in profiles:
        live = live_config if live_config is not None else load_random_checkins_config()
        profiles = {RANDOM_CHECKIN_DEFAULT_PROFILE: random_checkin_profile_settings(live), **profiles}
    active = _find_random_checkin_profile(profiles, raw.get("active"))
    return {"active": active or RANDOM_CHECKIN_DEFAULT_PROFILE, "profiles": profiles}


def save_random_checkin_profiles(state):
    save_user_config_section(RANDOM_CHECKIN_PROFILES_SECTION, {
        "active": state["active"],
        "profiles": state["profiles"],
    })
    return state


def create_random_checkin_profile(state, name, settings):
    """Add a profile and select it. Returns (state, error_code_or_None)."""
    clean = random_checkin_profile_name(name)
    if clean is None:
        return state, "invalid_name"
    if _find_random_checkin_profile(state["profiles"], clean):
        return state, "exists"
    if len(state["profiles"]) >= RANDOM_CHECKIN_PROFILE_LIMIT:
        return state, "limit"
    state["profiles"][clean] = random_checkin_profile_settings(settings)
    state["active"] = clean
    return state, None


def select_random_checkin_profile(state, name):
    found = _find_random_checkin_profile(state["profiles"], name)
    if found is None:
        return state, "not_found"
    state["active"] = found
    return state, None


def update_active_random_checkin_profile(state, settings):
    state["profiles"][state["active"]] = random_checkin_profile_settings(settings)
    return state


def delete_random_checkin_profile(state, name):
    """Remove a profile; Default is protected. Deleting the selected profile
    falls back to Default."""
    found = _find_random_checkin_profile(state["profiles"], name)
    if found is None:
        return state, "not_found"
    if found == RANDOM_CHECKIN_DEFAULT_PROFILE:
        return state, "protected"
    del state["profiles"][found]
    if state["active"] == found:
        state["active"] = RANDOM_CHECKIN_DEFAULT_PROFILE
    return state, None


def apply_random_checkin_profile(live_config, state):
    """Live config with the selected profile's settings, runtime preserved."""
    return normalise_random_checkins_config(
        state["profiles"][state["active"]],
        preserve_runtime=live_config.get("runtime", {}),
    )


def random_checkins_record_activity(config, chat_key, is_message=False, now=None, received_at=None):
    """Update one chat's activity and schedule state; mutate and return config.

    ``now`` is the activity baseline. The page backfills it from a restored
    chat's last user turn, which can be hours old. ``received_at`` is when the
    request arrived. That request comes from a user interaction, so in random
    mode the next deadline is never earlier than the minimum inactivity after
    it: an old baseline must not make a check-in due the moment a chat is
    selected.
    """
    from datetime import datetime, timezone

    config = normalise_random_checkins_config(config)
    runtime = config["runtime"]
    chats = runtime["chats"]
    state = chats.setdefault(str(chat_key), {})
    current = now or datetime.now(timezone.utc)
    state["last_activity_at"] = current.isoformat()
    if config["trigger_mode"] == "random":
        lower = config["min_inactivity_minutes"] * 60
        upper = config["max_inactivity_minutes"] * 60
        next_due = current.timestamp() + random.uniform(lower, upper)
        if received_at is not None:
            next_due = max(next_due, received_at.timestamp() + lower)
        state["next_due_at"] = next_due
    if is_message:
        state.pop("pending_fire_id", None)
        state.pop("pending_scheduled_occurrence", None)
        state.pop("pending_completed", None)
    return config


def random_checkins_is_quiet(config, now=None):
    from datetime import datetime

    if not config.get("quiet_hours_enabled"):
        return False
    def parse(value):
        try:
            hours, minutes = str(value).split(":", 1)
            hours, minutes = int(hours), int(minutes)
            if 0 <= hours < 24 and 0 <= minutes < 60:
                return hours * 60 + minutes
        except (TypeError, ValueError):
            pass
        return None
    start, end = parse(config.get("quiet_start")), parse(config.get("quiet_end"))
    if start is None or end is None or start == end:
        return False
    current = now or datetime.now()
    minute = current.hour * 60 + current.minute
    return start <= minute < end if start < end else minute >= start or minute < end


def random_checkins_scheduled_occurrence(config, now=None):
    """Return the latest eligible scheduled occurrence within the grace window."""
    from datetime import datetime, timedelta

    current = now or datetime.now()
    grace_seconds = RANDOM_CHECKINS_SCHEDULE_GRACE_MINUTES * 60
    candidates = []
    if config.get("schedule_mode") == "interval":
        interval = config.get("schedule_interval_minutes", 60)
        if interval not in (1, 5, 15, 30, 60):
            interval = 60
        occurrence = current.replace(
            minute=(current.minute // interval) * interval,
            second=0,
            microsecond=0,
        )
        age = (current - occurrence).total_seconds()
        if 0 <= age <= grace_seconds and not random_checkins_is_quiet(config, occurrence):
            candidates.append(occurrence)
    else:
        for value in config.get("scheduled_times", []):
            parsed = _checkin_time(value)
            if parsed is None:
                continue
            hours, minutes = (int(part) for part in parsed.split(":", 1))
            for day_offset in (0, -1):
                occurrence = current.replace(
                    hour=hours, minute=minutes, second=0, microsecond=0
                ) + timedelta(days=day_offset)
                age = (current - occurrence).total_seconds()
                if 0 <= age <= grace_seconds and not random_checkins_is_quiet(config, occurrence):
                    candidates.append(occurrence)
    if not candidates:
        return None
    return max(candidates).isoformat(timespec="minutes")


def random_checkins_is_due(config, chat_key, now=None):
    from datetime import datetime

    current = now or datetime.now()
    if random_checkins_is_quiet(config, current):
        return False, "quiet_hours"
    state = config.get("runtime", {}).get("chats", {}).get(str(chat_key), {})
    if config.get("trigger_mode") == "random":
        try:
            if current.timestamp() < float(state.get("next_due_at")):
                return False, "not_due"
        except (TypeError, ValueError):
            return False, "not_due"
        return True, None
    return (
        (True, None)
        if random_checkins_scheduled_occurrence(config, current)
        else (False, "not_due")
    )


def random_checkins_claim(config, chat_key, fire_id, now=None):
    """Atomically evaluate a fire claim against pending/daily/idempotency state."""
    from datetime import datetime

    fire_id = str(fire_id or "").strip()
    chat_key = str(chat_key or "").strip()
    if not fire_id or not chat_key:
        return normalise_random_checkins_config(config), {"accepted": False, "reason": "invalid_claim"}
    config = normalise_random_checkins_config(config)
    runtime = config["runtime"]
    seen = runtime["seen_fire_ids"]
    # Accepted claim IDs stay idempotent even after activity reschedules the
    # chat, quiet hours begin, or settings are disabled.
    if fire_id in seen:
        return config, {
            "accepted": True,
            "already_claimed": True,
            "fire_id": fire_id,
            "message_mode": config["message_mode"],
        }
    if not config["enabled"]:
        return config, {"accepted": False, "reason": "disabled"}
    if config["message_mode"] == "custom" and not config["custom_messages"]:
        return config, {"accepted": False, "reason": "custom_pool_empty"}
    if (
        config["trigger_mode"] == "scheduled"
        and config["schedule_mode"] == "specific"
        and not config["scheduled_times"]
    ):
        return config, {"accepted": False, "reason": "schedule_empty"}
    due, due_reason = random_checkins_is_due(config, chat_key, now)
    if not due:
        return config, {"accepted": False, "reason": due_reason}
    state = runtime["chats"].setdefault(chat_key, {})
    pending = state.get("pending_fire_id")
    if pending and pending != fire_id:
        return config, {"accepted": False, "reason": "pending_reply"}
    current = now or datetime.now()
    today = current.date().isoformat()
    if state.get("daily_date") != today:
        state["daily_date"] = today
        state["daily_count"] = 0
    if int(state.get("daily_count", 0) or 0) >= config["max_per_day"]:
        return config, {"accepted": False, "reason": "daily_max"}
    scheduled_occurrence = None
    if config["trigger_mode"] == "scheduled":
        scheduled_occurrence = random_checkins_scheduled_occurrence(config, current)
        if state.get("last_scheduled_occurrence") == scheduled_occurrence:
            return config, {"accepted": False, "reason": "occurrence_claimed"}
    state["pending_fire_id"] = fire_id
    state["pending_completed"] = False
    state["daily_count"] = int(state.get("daily_count", 0) or 0) + 1
    state["last_fire_at"] = current.isoformat()
    if scheduled_occurrence:
        state["pending_scheduled_occurrence"] = scheduled_occurrence
        state["last_scheduled_occurrence"] = scheduled_occurrence
    seen.append(fire_id)
    runtime["seen_fire_ids"] = seen[-1024:]
    return config, {
        "accepted": True,
        "already_claimed": False,
        "fire_id": fire_id,
        "message_mode": config["message_mode"],
    }


def random_checkins_complete(config, chat_key, fire_id, success):
    config = normalise_random_checkins_config(config)
    state = config["runtime"]["chats"].get(str(chat_key), {})
    if state.get("pending_fire_id") == str(fire_id):
        if success:
            state["pending_completed"] = True
        else:
            pending_occurrence = state.pop("pending_scheduled_occurrence", None)
            if pending_occurrence and state.get("last_scheduled_occurrence") == pending_occurrence:
                state.pop("last_scheduled_occurrence", None)
            state.pop("pending_fire_id", None)
            state["pending_completed"] = False
            state["daily_count"] = max(0, int(state.get("daily_count", 0) or 0) - 1)
    return config


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
