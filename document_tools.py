"""Sandboxed document read/write/edit layer for HWUI.

⚠️ SECURITY BOUNDARY. Everything the model can do to a file goes through
`resolve_document_path()`. There is deliberately no code path here that accepts
a caller-supplied absolute path, and no operation that takes a path without
naming one of the three permitted roots. Widening `DOCUMENT_ROOTS` is the only
way to grant access to a new location, and it should not be done casually.

Permitted roots (all resolved relative to this file, never from user input):

    global_docs  -> global_documents/    existing global reference documents
    memories     -> memories/            existing per-character/global memory
    editing      -> document editing/    scratch space for model-authored files

The roots are the ones the application already uses; nothing is relocated.

Design notes
------------
* The local llama-server backend has no reliable native tool-calling (see
  `_classify_chat_search_intent` in app.py), so HWUI's established pattern for a
  model capability is: cheap isolated classifier pre-pass -> server executes ->
  result injected as passive context. This module is the "server executes" half
  and knows nothing about prompts, streaming or models. That keeps it directly
  unit-testable and keeps the security boundary out of the prompt layer.
* Every operation returns a plain dict with `ok` plus a short `summary` written
  for the model to read. Failures return `ok: False` with a reason rather than
  raising, so a tool failure can be narrated instead of crashing a chat turn.
* `delete` is the one operation that refuses to act unless the caller passes
  `confirmed_by_user=True`. Nothing in the automatic path sets that.
"""

import json
import os
import re
import shutil
import unicodedata
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ⚠️ The complete list of locations the model may touch. Keys are the only
# root identifiers accepted by resolve_document_path().
DOCUMENT_ROOTS = {
    "global_docs": os.path.join(BASE_DIR, "global_documents"),
    "memories": os.path.join(BASE_DIR, "memories"),
    "editing": os.path.join(BASE_DIR, "document editing"),
}

# Friendly aliases the classifier/model may produce for a root.
ROOT_ALIASES = {
    "global": "global_docs",
    "global_documents": "global_docs",
    "global documents": "global_docs",
    "documents": "global_docs",
    "memory": "memories",
    "memories": "memories",
    "document editing": "editing",
    "document_editing": "editing",
    "editing": "editing",
    "workspace": "editing",
}

# Text-shaped formats are read and written as UTF-8. .docx round-trips through
# python-docx, which the app already depends on for reading uploads.
TEXT_EXTENSIONS = frozenset({".txt", ".md", ".json"})
BINARY_EXTENSIONS = frozenset({".docx"})
ALLOWED_EXTENSIONS = TEXT_EXTENSIONS | BINARY_EXTENSIONS

MAX_READ_BYTES = 2 * 1024 * 1024        # refuse to inline anything larger
MAX_WRITE_CHARS = 400_000               # generous, but not unbounded

# Exact prior copies saved before any destructive write to an existing file,
# inside the same root. Never listed and never addressable as a document.
VERSIONS_DIRNAME = ".versions"


class DocumentAccessError(Exception):
    """Raised when a path fails validation. Never contains the resolved path."""


# ----------------------------------------------------------------------------
# Path validation — the security boundary
# ----------------------------------------------------------------------------

def normalise_root(root):
    """Map a caller-supplied root name onto a DOCUMENT_ROOTS key."""
    key = str(root or "").strip().lower()
    if key in DOCUMENT_ROOTS:
        return key
    if key in ROOT_ALIASES:
        return ROOT_ALIASES[key]
    raise DocumentAccessError(
        "Unknown document location %r. Permitted locations: %s."
        % (root, ", ".join(sorted(DOCUMENT_ROOTS)))
    )


def _reject_suspicious(relative_path):
    """Structural rejections applied before the filesystem is consulted."""
    value = str(relative_path or "")
    if not value.strip():
        raise DocumentAccessError("No filename was given.")
    if "\x00" in value:
        raise DocumentAccessError("Filename contains a null byte.")
    # Normalise first: a decomposed or full-width sequence must not be able to
    # smuggle a separator past the checks below.
    value = unicodedata.normalize("NFKC", value)
    if len(value) > 400:
        raise DocumentAccessError("Filename is too long.")
    # Absolute paths, drive letters and UNC shares are never accepted; the root
    # is chosen by key, never by the caller spelling out a location.
    if value.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", value):
        raise DocumentAccessError("Absolute paths are not permitted.")
    parts = re.split(r"[\\/]+", value)
    for part in parts:
        if part in ("..", "."):
            raise DocumentAccessError("Path traversal is not permitted.")
        if part.strip().lower() == VERSIONS_DIRNAME:
            raise DocumentAccessError("Saved document versions are not accessible as documents.")
    return value, parts


def resolve_document_path(root, relative_path, must_exist=False):
    """Resolve `relative_path` inside `root` or raise DocumentAccessError.

    ⚠️ This is the only sanctioned way to turn model-supplied text into a
    filesystem path. It rejects, in order: unknown roots, empty/oversized
    names, null bytes, absolute paths and drive letters, `..` segments,
    disallowed extensions, and — after resolution — anything whose real path
    escapes the real root, which is what catches a symlink or junction
    pointing outside the sandbox.
    """
    root_key = normalise_root(root)
    root_dir = DOCUMENT_ROOTS[root_key]
    value, parts = _reject_suspicious(relative_path)

    extension = os.path.splitext(parts[-1])[1].lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise DocumentAccessError(
            "Unsupported file type %r. Supported: %s."
            % (extension or "(none)", ", ".join(sorted(ALLOWED_EXTENSIONS)))
        )

    candidate = os.path.join(root_dir, *parts)
    real_root = os.path.realpath(root_dir)
    # realpath resolves symlinks/junctions on both sides, so a link inside the
    # root that points outside it fails the containment test below.
    real_candidate = os.path.realpath(candidate)

    if real_candidate != real_root and not real_candidate.startswith(real_root + os.sep):
        raise DocumentAccessError("Resolved path escapes the permitted folder.")
    if os.path.isdir(real_candidate):
        raise DocumentAccessError("That path is a folder, not a document.")
    if must_exist and not os.path.isfile(real_candidate):
        raise DocumentAccessError("No such document: %s" % parts[-1])

    return root_key, real_candidate, "/".join(parts)


def _ensure_root(root_key):
    os.makedirs(DOCUMENT_ROOTS[root_key], exist_ok=True)


# Operations that act on a file that must already exist. Only these may have
# their root corrected below; `create` must never be redirected, or a new file
# would silently land somewhere the caller did not ask for.
_EXISTING_FILE_ACTIONS = frozenset({"read", "update", "edit", "append", "save_as", "rename",
                                    "delete"})


def normalise_allowed_roots(allowed_roots):
    """Map a caller-supplied allow-list onto root keys, or None for "all".

    `None` means the caller is not constraining anything — the historical
    behaviour, and what every non-chat caller wants. A sequence narrows the
    roots this call may resolve or correct into; see `run_document_action`.
    """
    if allowed_roots is None:
        return None
    keys = set()
    for entry in allowed_roots:
        try:
            keys.add(normalise_root(entry))
        except DocumentAccessError:
            continue
    return keys


def find_existing_root(name, preferred_root=None, allowed_roots=None):
    """Return the permitted root that actually holds `name`.

    ⚠️ Correction, not expansion: every candidate is one of the three permitted
    roots, and each is still validated by resolve_document_path afterwards. This
    exists because the classifier picks a root from natural language and gets it
    wrong in predictable ways — "global memory" reads as `global_docs` even
    though global_memory.txt lives in `memories`. Rather than teach the model a
    list of which file lives where, the server checks.

    `allowed_roots` narrows the candidates first. Correcting a root is a
    convenience, and it must not be able to move a write INTO a root the caller
    was not authorised to touch: that is how an ordinary document request could
    reach the memory store by naming a file that happened to be sitting in it.

    Preference order: the caller's root if it really holds the file, otherwise
    the single root that does. Returns None when no root has it, or when more
    than one does and the caller expressed no usable preference (ambiguous).
    """
    permitted = normalise_allowed_roots(allowed_roots)
    holders = []
    for key in sorted(DOCUMENT_ROOTS):
        if permitted is not None and key not in permitted:
            continue
        try:
            _key, path, _rel = resolve_document_path(key, name)
        except DocumentAccessError:
            continue
        if os.path.isfile(path):
            holders.append(key)
    if not holders:
        return None
    if preferred_root:
        try:
            preferred = normalise_root(preferred_root)
        except DocumentAccessError:
            preferred = None
        if preferred in holders:
            return preferred
    return holders[0] if len(holders) == 1 else None


def _ok(summary, **extra):
    payload = {"ok": True, "summary": summary}
    payload.update(extra)
    return payload


def _fail(reason, **extra):
    payload = {"ok": False, "summary": reason, "error": reason}
    payload.update(extra)
    return payload


def _write_failure(reason, root_key, rel, backup):
    """A failure AFTER the write began: the file may have changed.

    Callers must not report "nothing was changed" for these; `backup` (when
    there was a prior file) holds its exact previous bytes.
    """
    return _fail(reason, root=root_key, name=rel, backup=backup, write_attempted=True)


# ----------------------------------------------------------------------------
# Format handlers
# ----------------------------------------------------------------------------

def _read_file(path):
    extension = os.path.splitext(path)[1].lower()
    if extension == ".docx":
        import docx as _docx
        return "\n".join(p.text for p in _docx.Document(path).paragraphs)
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            return handle.read()
    except UnicodeDecodeError:
        with open(path, "r", encoding="latin-1") as handle:
            return handle.read()


def _write_file(path, content):
    extension = os.path.splitext(path)[1].lower()
    if extension == ".docx":
        import docx as _docx
        document = _docx.Document()
        for line in str(content).split("\n"):
            document.add_paragraph(line)
        document.save(path)
        return
    if extension == ".json":
        # Validate before writing so a malformed edit cannot corrupt a JSON file
        # that other parts of the app read (index.json, project colours, ...).
        try:
            json.loads(content)
        except Exception as exc:
            raise DocumentAccessError("Refusing to write invalid JSON: %s" % exc)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def _verify_file_content(path, expected):
    """Read a completed write back from disk before reporting success."""
    if not os.path.isfile(path):
        raise OSError("the file is missing after the write")
    if _read_file(path) != str(expected):
        raise OSError("the saved content does not match the requested content")


def _backup_version(root_key, path, rel):
    """Save the exact current bytes of an existing document before it is changed.

    The copy goes to <root>/.versions/<same subpath>/<stem>.<timestamp><ext>,
    is created exclusively (an existing backup is never overwritten), and is
    read back byte-for-byte. Returns the backup's root-relative path. Raises on
    any failure, so the caller refuses the write rather than proceeding
    without a backup.
    """
    root_dir = DOCUMENT_ROOTS[root_key]
    rel_dir, base = os.path.split(rel.replace("/", os.sep))
    stem, extension = os.path.splitext(base)
    target_dir = os.path.join(root_dir, VERSIONS_DIRNAME, rel_dir)
    real_root = os.path.realpath(root_dir)
    if not os.path.realpath(target_dir).startswith(real_root + os.sep):
        raise OSError("the version folder escapes the permitted folder")
    os.makedirs(target_dir, exist_ok=True)
    with open(path, "rb") as handle:
        original = handle.read()
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    for attempt in range(1000):
        suffix = "-%d" % attempt if attempt else ""
        target = os.path.join(target_dir, "%s.%s%s%s" % (stem, stamp, suffix, extension))
        try:
            with open(target, "xb") as handle:           # never overwrite a backup
                handle.write(original)
            break
        except FileExistsError:
            continue
    else:                                                # pragma: no cover
        raise OSError("could not choose a unique version name")
    with open(target, "rb") as handle:
        if handle.read() != original:
            raise OSError("the version backup does not match the original bytes")
    return os.path.relpath(target, root_dir).replace(os.sep, "/")


# ----------------------------------------------------------------------------
# Operations
# ----------------------------------------------------------------------------

def list_documents(root=None):
    """List the documents the model is allowed to see, per root."""
    roots = [normalise_root(root)] if root else sorted(DOCUMENT_ROOTS)
    listing = {}
    for key in roots:
        directory = DOCUMENT_ROOTS[key]
        entries = []
        if os.path.isdir(directory):
            for name in sorted(os.listdir(directory)):
                full = os.path.join(directory, name)
                if not os.path.isfile(full):
                    continue
                if os.path.splitext(name)[1].lower() not in ALLOWED_EXTENSIONS:
                    continue
                entries.append({"name": name, "bytes": os.path.getsize(full)})
        listing[key] = entries
    total = sum(len(v) for v in listing.values())
    described = "; ".join(
        "%s: %s" % (key, ", ".join(e["name"] for e in items) or "(empty)")
        for key, items in listing.items()
    )
    return _ok("%d document(s) available. %s" % (total, described), listing=listing)


def read_document(root, name):
    try:
        root_key, path, rel = resolve_document_path(root, name, must_exist=True)
    except DocumentAccessError as exc:
        return _fail(str(exc))
    if os.path.getsize(path) > MAX_READ_BYTES:
        return _fail("Document %s is too large to read in one piece." % rel)
    try:
        content = _read_file(path)
    except Exception as exc:
        return _fail("Could not read %s: %s" % (rel, exc))
    return _ok(
        "Read %s from %s (%d characters)." % (rel, root_key, len(content)),
        root=root_key, name=rel, content=content, characters=len(content),
    )


def create_document(root, name, content="", overwrite=False):
    try:
        root_key, path, rel = resolve_document_path(root, name)
    except DocumentAccessError as exc:
        return _fail(str(exc))
    content = str(content)
    if len(content) > MAX_WRITE_CHARS:
        return _fail("Content is too large to write.")
    if os.path.exists(path) and not overwrite:
        return _fail(
            "%s already exists. Use update to overwrite it, or choose another name." % rel
        )
    _ensure_root(root_key)
    backup = None
    if os.path.isfile(path):
        # Destructive write to an existing file: keep the exact prior bytes first.
        try:
            backup = _backup_version(root_key, path, rel)
        except Exception as exc:
            return _fail("Could not back up %s before overwriting it, so nothing was changed: %s"
                         % (rel, exc))
    try:
        _write_file(path, content)
        _verify_file_content(path, content)
    except DocumentAccessError as exc:
        return _write_failure(str(exc), root_key, rel, backup)
    except Exception as exc:
        return _write_failure("Could not write and verify %s: %s" % (rel, exc), root_key, rel, backup)
    verb = "Overwrote" if overwrite else "Created"
    extra = {"backup": backup} if backup else {}
    return _ok(
        "%s %s in %s (%d characters)." % (verb, rel, root_key, len(content)),
        root=root_key, name=rel, characters=len(content), **extra,
    )


def update_document(root, name, content):
    """Full-content replacement of an existing document."""
    try:
        _root_key, path, rel = resolve_document_path(root, name, must_exist=True)
    except DocumentAccessError as exc:
        return _fail(str(exc))
    if not os.path.isfile(path):
        return _fail("No such document: %s" % rel)
    return create_document(root, name, content, overwrite=True)


def edit_document(root, name, find, replace, count=0):
    """Targeted edit: replace `find` with `replace`, leaving the rest intact.

    Returns a failure rather than writing when `find` is absent, so a model
    guessing at wording cannot silently no-op and report success.
    """
    try:
        root_key, path, rel = resolve_document_path(root, name, must_exist=True)
    except DocumentAccessError as exc:
        return _fail(str(exc))
    if not str(find or ""):
        return _fail("No search text was given for the edit.")
    try:
        original = _read_file(path)
    except Exception as exc:
        return _fail("Could not read %s: %s" % (rel, exc))

    occurrences = original.count(find)
    if occurrences == 0:
        return _fail(
            "Could not find that text in %s, so nothing was changed." % rel
        )
    updated = original.replace(find, replace, count) if count else original.replace(find, replace)
    if len(updated) > MAX_WRITE_CHARS:
        return _fail("Edit would make the document too large.")
    try:
        backup = _backup_version(root_key, path, rel)
    except Exception as exc:
        return _fail("Could not back up %s before editing it, so nothing was changed: %s"
                     % (rel, exc))
    try:
        _write_file(path, updated)
        _verify_file_content(path, updated)
    except DocumentAccessError as exc:
        return _write_failure(str(exc), root_key, rel, backup)
    except Exception as exc:
        return _write_failure("Could not write and verify %s: %s" % (rel, exc), root_key, rel, backup)
    replaced = occurrences if not count else min(count, occurrences)
    return _ok(
        "Edited %s in %s — replaced %d occurrence(s); the rest of the document is unchanged."
        % (rel, root_key, replaced),
        root=root_key, name=rel, replacements=replaced,
        characters=len(updated), previous_characters=len(original), backup=backup,
    )


# ----------------------------------------------------------------------------
# Targeted addition — the existing document is never rewritten
# ----------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+\S")


def _heading_text(line):
    return re.sub(r"^#{1,6}\s+", "", line.strip()).strip().casefold()


def _append_insertion_index(lines, after):
    """Index of the line after which new text goes, or raise DocumentAccessError.

    `after` must identify exactly one existing line (exact text, or a Markdown
    heading's text). For a heading the insertion point is the end of that
    heading's section — its last non-blank line before the next heading of the
    same or higher level — so "add a section after Early years" lands after
    the Early years section rather than inside it.
    """
    wanted = str(after or "").strip()
    texts = [line.decode("utf-8").rstrip("\r\n") for line in lines]
    matches = [i for i, text in enumerate(texts) if text.strip() == wanted]
    if not matches:
        key = _heading_text(wanted)
        matches = [i for i, text in enumerate(texts) if key and _heading_text(text) == key]
    if not matches:
        raise DocumentAccessError("Could not find %r in the document, so nothing was changed." % wanted)
    if len(matches) > 1:
        raise DocumentAccessError("%r appears more than once in the document, so the place to add "
                                  "the text is ambiguous and nothing was changed." % wanted)
    index = matches[0]
    heading = _HEADING_RE.match(texts[index].strip())
    if not heading:
        return index
    level = len(heading.group(1))
    end = len(texts)
    for j in range(index + 1, len(texts)):
        other = _HEADING_RE.match(texts[j].strip())
        if other and len(other.group(1)) <= level:
            end = j
            break
    last = index
    for j in range(index + 1, end):
        if texts[j].strip():
            last = j
    return last


def _plan_append(path, text, after=None):
    """Compute the exact new bytes for an addition without writing anything.

    Returns (original_bytes, new_bytes, prefix_bytes, suffix_bytes, block_bytes):
    new_bytes == prefix + block + suffix, and prefix + suffix == original, so no
    existing byte is changed. Line endings follow the file's own style.
    """
    extension = os.path.splitext(path)[1].lower()
    if extension == ".json":
        raise DocumentAccessError("Adding text to a JSON file would make it invalid; "
                                  "use an exact edit instead.")
    if extension == ".docx":
        raise DocumentAccessError("Adding text to a .docx document is not supported yet; "
                                  "nothing was changed.")
    with open(path, "rb") as handle:
        original = handle.read()
    try:
        original.decode("utf-8")
    except UnicodeDecodeError:
        raise DocumentAccessError("The document is not UTF-8 text, so nothing was added.")
    newline = b"\r\n" if b"\r\n" in original else b"\n"
    addition = str(text or "").replace("\r\n", "\n").strip("\n")
    if not addition.strip():
        raise DocumentAccessError("There was no text to add.")
    addition_bytes = addition.replace("\n", newline.decode()).encode("utf-8")
    if after:
        lines = original.splitlines(keepends=True)
        index = _append_insertion_index(lines, after)
        prefix = b"".join(lines[:index + 1])
        suffix = b"".join(lines[index + 1:])
    else:
        prefix, suffix = original, b""
    if not prefix:
        separator = b""
    elif prefix.endswith(newline + newline):
        separator = b""
    elif prefix.endswith(newline):
        separator = newline
    else:
        separator = newline + newline
    block = separator + addition_bytes + newline
    if suffix.strip() and not suffix.startswith(newline):
        block += newline
    return original, prefix + block + suffix, prefix, suffix, block


def check_append_target(root, name, after=None):
    """Validate an addition (location, format, anchor) without writing."""
    try:
        root_key, path, rel = resolve_document_path(root, name, must_exist=True)
        _plan_append(path, "placeholder", after)
    except DocumentAccessError as exc:
        return _fail(str(exc))
    return _ok("Can add to %s in %s." % (rel, root_key), root=root_key, name=rel)


def append_document(root, name, text, after=None):
    """Add `text` to an existing document without changing any existing byte.

    Appends at the end by default, or inserts after the line/heading named by
    `after`. The prior file is saved to .versions/ first, the write is atomic,
    and success requires the saved bytes to equal original-prefix + new block
    + original-suffix exactly.
    """
    try:
        root_key, path, rel = resolve_document_path(root, name, must_exist=True)
        original, updated, prefix, suffix, block = _plan_append(path, text, after)
    except DocumentAccessError as exc:
        return _fail(str(exc))
    if len(updated.decode("utf-8")) > MAX_WRITE_CHARS:
        return _fail("The addition would make the document too large.")
    try:
        backup = _backup_version(root_key, path, rel)
    except Exception as exc:
        return _fail("Could not back up %s before adding to it, so nothing was changed: %s"
                     % (rel, exc))
    temp_path = path + ".hwui-append.tmp"
    try:
        with open(temp_path, "wb") as handle:
            handle.write(updated)
        os.replace(temp_path, path)
        with open(path, "rb") as handle:
            saved = handle.read()
        if saved != updated or not (saved.startswith(prefix) and saved.endswith(suffix)
                                    and saved[len(prefix):len(saved) - len(suffix)] == block):
            raise OSError("the saved file is not the original plus the addition")
    except Exception as exc:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        return _write_failure("Could not add to and verify %s: %s (the prior version is in %s)"
                              % (rel, exc, backup), root_key, rel, backup)
    added = block.decode("utf-8").replace("\r\n", "\n").strip("\n")
    where = ("after %r" % str(after).strip()) if after else "at the end"
    return _ok(
        "Added %d characters to %s in %s, %s; every existing line is unchanged."
        % (len(added), rel, root_key, where),
        root=root_key, name=rel, added_text=added, position=where, backup=backup,
        characters=len(saved.decode("utf-8")), previous_characters=len(original.decode("utf-8")),
    )


def save_as(root, name, new_name, new_root=None):
    """Save a copy of an existing document under another name."""
    try:
        _src_key, src_path, src_rel = resolve_document_path(root, name, must_exist=True)
        dest_key, dest_path, dest_rel = resolve_document_path(new_root or root, new_name)
    except DocumentAccessError as exc:
        return _fail(str(exc))
    if os.path.exists(dest_path):
        return _fail("%s already exists; choose another name." % dest_rel)
    _ensure_root(dest_key)
    try:
        expected = _read_file(src_path)
        if os.path.splitext(src_path)[1].lower() == os.path.splitext(dest_path)[1].lower():
            shutil.copy2(src_path, dest_path)
        else:
            # Cross-format save (e.g. .md -> .docx) goes through the handlers so
            # the destination is a real file of its own type, not a renamed copy.
            _write_file(dest_path, expected)
        _verify_file_content(dest_path, expected)
    except DocumentAccessError as exc:
        return _fail(str(exc))
    except Exception as exc:
        return _fail("Could not save and verify a copy: %s" % exc)
    return _ok(
        "Saved a copy of %s as %s in %s. The original is unchanged."
        % (src_rel, dest_rel, dest_key),
        root=dest_key, name=dest_rel, source=src_rel,
    )


def rename_document(root, name, new_name):
    try:
        _root_key, src_path, src_rel = resolve_document_path(root, name, must_exist=True)
        dest_key, dest_path, dest_rel = resolve_document_path(root, new_name)
    except DocumentAccessError as exc:
        return _fail(str(exc))
    if os.path.exists(dest_path):
        return _fail("%s already exists; choose another name." % dest_rel)
    try:
        expected = _read_file(src_path)
        os.replace(src_path, dest_path)
        if os.path.exists(src_path):
            raise OSError("the original file still exists after the rename")
        _verify_file_content(dest_path, expected)
    except Exception as exc:
        return _fail("Could not rename and verify %s: %s" % (src_rel, exc))
    return _ok(
        "Renamed %s to %s in %s." % (src_rel, dest_rel, dest_key),
        root=dest_key, name=dest_rel, previous_name=src_rel,
    )


def delete_document(root, name, confirmed_by_user=False):
    """Delete a document. Refuses unless the caller states the user asked.

    ⚠️ `confirmed_by_user` is never set by the automatic classifier path. It is
    set only by an explicit user-initiated request, so an inferred intent can
    never remove a file.
    """
    if not confirmed_by_user:
        return _fail(
            "Deleting needs an explicit request from the user, so nothing was deleted."
        )
    try:
        root_key, path, rel = resolve_document_path(root, name, must_exist=True)
    except DocumentAccessError as exc:
        return _fail(str(exc))
    try:
        os.remove(path)
        if os.path.exists(path):
            raise OSError("the file still exists after deletion")
    except Exception as exc:
        return _fail("Could not delete and verify %s: %s" % (rel, exc))
    return _ok("Deleted %s from %s." % (rel, root_key), root=root_key, name=rel)


# ----------------------------------------------------------------------------
# Dispatch
# ----------------------------------------------------------------------------

# The exact action vocabulary exposed to the model. Keep this list and the
# classifier's few-shot examples in step.
DOCUMENT_ACTIONS = ("list", "read", "create", "append", "update", "edit", "save_as", "rename",
                    "delete")


def run_document_action(action, allowed_roots=None, **kwargs):
    """Execute one document action by name, returning the tool-result dict.

    `allowed_roots` is the caller's authority, not the module's permission: the
    three roots in DOCUMENT_ROOTS remain the security boundary, and this narrows
    which of them THIS call may use. A caller that leaves it None is unchanged.
    The chat path sets it because the root there is chosen by a model from
    natural language, and one of the roots is the memory store.
    """
    verb = str(action or "").strip().lower()
    permitted = normalise_allowed_roots(allowed_roots)
    if permitted is not None and kwargs.get("root") is not None:
        try:
            requested = normalise_root(kwargs.get("root"))
        except DocumentAccessError:
            requested = None
        if requested is not None and requested not in permitted:
            return _fail(
                "%s is not an available location for this request. Permitted here: %s."
                % (requested, ", ".join(sorted(permitted)) or "none"),
                root=requested, name=kwargs.get("name"))
    # Correct an obviously-wrong root before acting, so a caller that named the
    # right file in the wrong permitted folder still succeeds. Never applied to
    # `create`; never widens the search beyond the three permitted roots, and
    # never past `allowed_roots`.
    if verb in _EXISTING_FILE_ACTIONS and kwargs.get("name"):
        try:
            _key, path, _rel = resolve_document_path(kwargs.get("root"), kwargs["name"])
            already_there = os.path.isfile(path)
        except DocumentAccessError:
            already_there = False
        if not already_there:
            corrected = find_existing_root(kwargs["name"], kwargs.get("root"),
                                           allowed_roots=allowed_roots)
            if corrected:
                print("📄 Document root corrected: %r -> %r for %s"
                      % (kwargs.get("root"), corrected, kwargs["name"]), flush=True)
                kwargs = dict(kwargs, root=corrected)
    try:
        if verb == "list":
            return list_documents(kwargs.get("root"))
        if verb == "read":
            return read_document(kwargs.get("root"), kwargs.get("name"))
        if verb == "create":
            return create_document(kwargs.get("root"), kwargs.get("name"),
                                   kwargs.get("content", ""), bool(kwargs.get("overwrite")))
        if verb == "update":
            return update_document(kwargs.get("root"), kwargs.get("name"),
                                   kwargs.get("content", ""))
        if verb == "edit":
            return edit_document(kwargs.get("root"), kwargs.get("name"),
                                 kwargs.get("find", ""), kwargs.get("replace", ""),
                                 int(kwargs.get("count") or 0))
        if verb == "append":
            return append_document(kwargs.get("root"), kwargs.get("name"),
                                   kwargs.get("text", ""), kwargs.get("after"))
        if verb == "save_as":
            return save_as(kwargs.get("root"), kwargs.get("name"),
                           kwargs.get("new_name"), kwargs.get("new_root"))
        if verb == "rename":
            return rename_document(kwargs.get("root"), kwargs.get("name"),
                                   kwargs.get("new_name"))
        if verb == "delete":
            return delete_document(kwargs.get("root"), kwargs.get("name"),
                                   bool(kwargs.get("confirmed_by_user")))
    except DocumentAccessError as exc:
        return _fail(str(exc))
    except Exception as exc:                                  # pragma: no cover
        return _fail("Document action failed: %s" % exc)
    return _fail("Unknown document action %r." % action)


def format_tool_result(action, result):
    """Render a tool result for injection as passive context.

    ⚠️ Deliberately does NOT include file contents except for `read`, and even
    then the caller decides whether to inline them. The model needs to know what
    happened, not to have every document echoed back into the chat.
    """
    status = "SUCCESS" if result.get("ok") else "FAILED"
    lines = ["[DOCUMENT TOOL RESULT]",
             "action: %s" % action,
             "status: %s" % status,
             "detail: %s" % result.get("summary", "")]
    if result.get("name"):
        lines.append("file: %s" % result["name"])
    if result.get("root"):
        lines.append("location: %s" % result["root"])
    # ⚠️ A bare "status: FAILED" is not enough. Observed live: the model was
    # handed a failed read and answered as though it had no file access at all,
    # inventing contents. A failed file operation must produce an explicit
    # instruction, the same way the zero-results web-search branch does.
    if not result.get("ok"):
        lines.append(
            "instruction: This file operation did NOT succeed. Tell the user plainly that it "
            "failed and why, using the detail above. Do NOT invent, guess, summarise or "
            "describe any file contents, and do NOT imply you cannot access files at all — "
            "the tool ran and reported this specific failure."
        )
    # A SUCCESSFUL read never reaches this function: its contents are handed to
    # the caller as an [ATTACHED DOCUMENT: …] block and framed by the existing
    # uploaded-document pipeline instead. Only list results and failures render
    # here, which is why there is no success-read instruction.
    return "\n".join(lines) + "\n[END DOCUMENT TOOL RESULT]"
