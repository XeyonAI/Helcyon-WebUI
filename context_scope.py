"""Character-context isolation: who and what a /chat request is allowed to draw on.

One model-independent boundary for every inference path (2026-10-24 isolation
audit). Nothing here formats prompts or knows any model family; chat() asks it
three questions before assembling context, and reports what it assembled:

* is the character a real, safe card key, and does the chat belong to it?
* which shared (global) resources has this character opted into?
* how far may chat-history search reach for this character?

The project a request belongs to is bound per request in project_routes
(bind_request_project); everything here reads it through get_chats_dir() /
get_active_project(), so it follows the request, not the shared state file.
"""
import os
import re
import threading
import time
from collections import deque

# ── Names ──────────────────────────────────────────────────────────────────────

def chat_filename_is_plain(name):
    """True for a bare chat file name ("Sol - Hi - Oct 24.txt"): never a path."""
    if not isinstance(name, str) or not name or name != name.strip() or len(name) > 255:
        return False
    # No separators means no traversal; ".." inside a title ("Hey babe.. ok") is fine.
    if name in (".", "..") or any(c in name for c in '/\\:\0'):
        return False
    return name.lower().endswith(".txt") and os.path.basename(name) == name


def resolve_request_character(name, characters_dir):
    """(key, error) for a requested character, checked BEFORE any filesystem use.

    The key must be a valid card key (character_routes.valid_character_key: no
    separators, reserved names or traversal) and an existing card; the stored
    spelling is returned so later lookups use one key.
    """
    from character_routes import valid_character_key, find_character_key
    name = name.strip() if isinstance(name, str) else ""
    if not name:
        return None, "No character specified"
    if not valid_character_key(name):
        return None, "Invalid character name"
    key = find_character_key(name, characters_dir)
    if not key or not os.path.isfile(os.path.join(characters_dir, f"{key}.json")):
        return None, f"Character not found: {name}"
    return key, None


def chat_ownership_problem(chat_filename, character_key, characters_dir):
    """Why `chat_filename` cannot be used for `character_key`, or None.

    The chat filename prefix is the authoritative owner (chat_routes and the
    save path already rely on it; the longest matching card key wins). A chat
    that belongs to another card is refused, so a send made while the page is
    still switching characters cannot carry the previous character's history,
    Author's Note or reactions into this character's request. A filename whose
    owner cannot be determined (legacy names) is allowed, as before.
    """
    if not chat_filename:
        return None
    if not chat_filename_is_plain(chat_filename):
        return "Invalid chat file name"
    from character_routes import known_character_keys, chat_owner_key
    owner = chat_owner_key(chat_filename, known_character_keys(characters_dir))
    if owner and owner.casefold() != str(character_key or "").casefold():
        return f"Chat '{chat_filename}' belongs to {owner}, not {character_key}"
    return None


# ── Per-character use of shared resources ──────────────────────────────────────
# Absent fields keep today's behaviour (shared resources on), so existing cards
# are unchanged until the user turns a resource off for a character.
CARD_USE_GLOBAL_DOCUMENTS = "use_global_documents"
CARD_USE_GLOBAL_EXAMPLES = "use_global_example_dialogue"
CARD_CHAT_SEARCH_SCOPE = "chat_search_scope"
CHAT_SEARCH_SCOPES = ("character", "project", "all")
DEFAULT_CHAT_SEARCH_SCOPE = "character"
# Card fields the character editor writes; the save route keeps them when an
# older editor omits them.
CARD_SCOPE_FIELDS = (CARD_USE_GLOBAL_DOCUMENTS, CARD_USE_GLOBAL_EXAMPLES, CARD_CHAT_SEARCH_SCOPE)


def card_uses_global_documents(card):
    return (card or {}).get(CARD_USE_GLOBAL_DOCUMENTS) is not False


def card_uses_global_example_dialogue(card):
    return (card or {}).get(CARD_USE_GLOBAL_EXAMPLES) is not False


def chat_search_scope(card):
    """How far chat-history search reaches for this character.

    character (default): this character's chats in this request's project.
    project: every character's chats in this request's project.
    all: every chat in every project (the old behaviour) — explicit opt-in only.
    """
    value = str((card or {}).get(CARD_CHAT_SEARCH_SCOPE) or "").strip().lower()
    return value if value in CHAT_SEARCH_SCOPES else DEFAULT_CHAT_SEARCH_SCOPE


def chat_search_targets(scope, character_key, request_chats_dir, base_dir, characters_dir):
    """(folders to scan, owner filter) for a chat search at `scope`.

    The owner filter is the character key for "character" scope and None
    otherwise. Folders are the request's own chats folder, except for "all".
    """
    if scope == "all":
        folders = []
        legacy = os.path.join(base_dir, "chats")
        if os.path.isdir(legacy):
            folders.append(legacy)
        projects = os.path.join(base_dir, "projects")
        if os.path.isdir(projects):
            for entry in sorted(os.listdir(projects)):
                chats = os.path.join(projects, entry, "chats")
                if not entry.startswith("_") and os.path.isdir(chats):
                    folders.append(chats)
        return folders, None
    folders = [request_chats_dir] if request_chats_dir and os.path.isdir(request_chats_dir) else []
    return folders, (character_key if scope == "character" else None)


def chat_file_owned_by(filename, character_key, characters_dir, keys=None):
    from character_routes import known_character_keys, chat_owner_key
    owner = chat_owner_key(filename, keys if keys is not None else known_character_keys(characters_dir))
    return bool(owner) and owner.casefold() == str(character_key or "").casefold()


# ── Per-request context manifest (diagnostics; never content) ─────────────────
# Which sources a request drew on, their scope, and their size — so a
# contamination question ("did Claire's chats reach Sol?") is answered from the
# log or GET /context_manifest without reading any private text.
SCOPE_CHARACTER = "character"
SCOPE_CHAT = "chat"
SCOPE_PROJECT = "project"
SCOPE_GLOBAL = "global"
SCOPE_USER = "user"
SCOPE_TURN = "turn"
_MANIFEST_HISTORY = deque(maxlen=20)
_MANIFEST_LOCK = threading.Lock()


class ContextManifest:
    """Source records for one request: name, scope, owner, included, size."""

    def __init__(self, request_id, character, chat, project, model_family=""):
        self.data = {"request_id": request_id, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                     "character": character, "chat": chat or None, "project": project or None,
                     "model_family": model_family or "", "sources": []}

    def add(self, source, scope, included, size=0, owner=None, detail=None):
        entry = {"source": source, "scope": scope, "included": bool(included),
                 "chars": int(size or 0)}
        if owner:
            entry["owner"] = str(owner)
        if detail:
            entry["detail"] = str(detail)[:120]
        self.data["sources"].append(entry)
        return self

    def add_text(self, source, scope, text, owner=None, detail=None):
        text = text if isinstance(text, str) else ""
        return self.add(source, scope, bool(text.strip()), len(text), owner, detail)

    def summary(self):
        parts = [f"{s['source']}={s['scope']}" + (f":{s['owner']}" if s.get("owner") else "")
                 + ("" if s["included"] else "(off)") + (f"[{s['chars']}c]" if s["chars"] else "")
                 for s in self.data["sources"]]
        return (f"🧭 CONTEXT MANIFEST req#{self.data['request_id']} character={self.data['character']!r} "
                f"project={self.data['project']!r} chat={self.data['chat']!r} "
                f"model={self.data['model_family'] or '?'} | " + " ".join(parts))

    def publish(self):
        with _MANIFEST_LOCK:
            _MANIFEST_HISTORY.append(self.data)
        print(self.summary(), flush=True)
        return self.data


def recent_manifests(limit=5):
    with _MANIFEST_LOCK:
        items = list(_MANIFEST_HISTORY)
    return items[-max(1, min(int(limit or 5), len(items) or 1)):] if items else []


_SAFE_DETAIL_RE = re.compile(r"[^\w .,()\-+'#/]")


def safe_detail(text):
    """A short label (file name, setting) with nothing that could be content."""
    return _SAFE_DETAIL_RE.sub("", str(text or ""))[:80]
