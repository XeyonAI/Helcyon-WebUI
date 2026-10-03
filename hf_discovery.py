"""Hugging Face model discovery and GGUF downloads.

Deliberately independent of the inference-provider layer: this module only finds
models and puts GGUF files in the configured models directory. The existing
/list_models route reads that directory from disk, so a finished download is
available to HWUI immediately.
"""
import hashlib
import os
import re
import shutil
import threading
import time
import uuid
from urllib.parse import urlparse

import requests

HF_BASE = "https://huggingface.co"
HF_HOST = "huggingface.co"
REPO_RE = re.compile(r"^[A-Za-z0-9][\w.\-]*/[A-Za-z0-9][\w.\-]*$")
SORTS = {"downloads": "downloads", "likes": "likes", "updated": "lastModified", "trending": "trendingScore"}
DISK_MARGIN = 256 * 1024 * 1024
CHUNK = 1024 * 1024
_EXPAND = ["downloads", "likes", "lastModified", "tags", "pipeline_tag", "gated", "gguf", "author"]

_http = requests  # test seam


class HFError(RuntimeError):
    pass


def _headers(token):
    headers = {"User-Agent": "HWUI-model-discovery"}
    if token:
        headers["Authorization"] = "Bearer " + token
    return headers


def _get(url, token="", params=None, stream=False, extra_headers=None, timeout=(10, 30)):
    headers = _headers(token)
    headers.update(extra_headers or {})
    try:
        return _http.get(url, headers=headers, params=params, stream=stream, timeout=timeout)
    except requests.RequestException as exc:
        raise HFError(f"Cannot reach Hugging Face: {exc}") from exc


def _explain(response):
    if response.status_code in (401, 403):
        return "Access denied (gated or private repo). Accept the licence on huggingface.co and set an access token."
    if response.status_code == 404:
        return "Not found on Hugging Face."
    if response.status_code == 429:
        return "Hugging Face rate limit hit; try again shortly."
    return f"Hugging Face returned HTTP {response.status_code}."


# -- parsing helpers ----------------------------------------------------------
_QUANT_RE = re.compile(
    r"(?<![A-Za-z0-9])((?:UD-)?(?:IQ\d|Q\d|TQ\d)(?:_[A-Z0-9]+)*|BF16|F16|F32|MXFP4)(?![A-Za-z0-9])", re.I)
_SHARD_RE = re.compile(r"-(\d{5})-of-(\d{5})(?=\.gguf$)", re.I)


def parse_quant(filename):
    match = _QUANT_RE.search(os.path.basename(filename))
    return match.group(1).upper() if match else ""


def format_params(total):
    if not isinstance(total, (int, float)) or total <= 0:
        return ""
    for unit, size in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if total >= size:
            value = total / size
            return f"{value:.0f}{unit}" if value >= 10 else f"{value:.1f}{unit}"
    return str(int(total))


def normalise_model(item):
    tags = item.get("tags") or []
    gguf = item.get("gguf") or {}
    license_tag = next((t[8:] for t in tags if t.startswith("license:")), "")
    base = [t[11:] for t in tags if t.startswith("base_model:") and ":" not in t[11:]]
    repo_id = item.get("id") or item.get("modelId") or ""
    return {
        "id": repo_id,
        "author": item.get("author") or repo_id.split("/")[0],
        "downloads": item.get("downloads") or 0,
        "likes": item.get("likes") or 0,
        "updated": item.get("lastModified") or item.get("createdAt") or "",
        "license": license_tag,
        "base_model": base[0] if base else "",
        "pipeline": item.get("pipeline_tag") or "",
        "gated": bool(item.get("gated")),
        "is_gguf": "gguf" in tags or bool(gguf),
        "params": format_params(gguf.get("total")),
        "architecture": gguf.get("architecture") or "",
        "context_length": gguf.get("context_length") or 0,
        "vision": "image-text-to-text" in tags or item.get("pipeline_tag") == "image-text-to-text",
    }


def _next_cursor_url(response):
    link = response.headers.get("Link") or response.headers.get("link") or ""
    match = re.search(r'<([^>]+)>\s*;\s*rel="next"', link)
    if match and urlparse(match.group(1)).hostname == HF_HOST:
        return match.group(1)
    return ""


def search_models(query="", gguf_only=True, sort="downloads", limit=24, cursor="", token=""):
    limit = max(1, min(int(limit or 24), 50))
    if cursor:
        if urlparse(cursor).hostname != HF_HOST:
            raise HFError("Invalid page cursor")
        response = _get(cursor, token)
    else:
        params = [("limit", limit), ("sort", SORTS.get(sort, "downloads")), ("direction", -1)]
        if query.strip():
            params.append(("search", query.strip()))
        if gguf_only:
            params.append(("filter", "gguf"))
        params += [("expand[]", f) for f in _EXPAND]
        response = _get(HF_BASE + "/api/models", token, params=params)
        if response.status_code == 400:  # expand not accepted: fall back to default fields
            response = _get(HF_BASE + "/api/models", token, params=[p for p in params if p[0] != "expand[]"])
    if response.status_code != 200:
        raise HFError(_explain(response))
    return {"models": [normalise_model(m) for m in response.json()], "next": _next_cursor_url(response)}


def validate_repo(repo_id):
    if not REPO_RE.match(repo_id or "") or ".." in repo_id:
        raise HFError("Invalid repository id")
    return repo_id


def repo_detail(repo_id, token=""):
    validate_repo(repo_id)
    response = _get(f"{HF_BASE}/api/models/{repo_id}", token)
    if response.status_code != 200:
        raise HFError(_explain(response))
    data = response.json()
    info = normalise_model(data)
    card = data.get("cardData") or {}
    info["languages"] = card.get("language") if isinstance(card.get("language"), list) else (
        [card["language"]] if card.get("language") else [])
    info["url"] = f"{HF_BASE}/{repo_id}"
    return info


def list_gguf_files(repo_id, token=""):
    """GGUF files in the repo, grouped so split shards appear as one downloadable entry."""
    validate_repo(repo_id)
    response = _get(f"{HF_BASE}/api/models/{repo_id}/tree/main", token, params={"recursive": "true"})
    if response.status_code != 200:
        raise HFError(_explain(response))
    entries = {}
    for item in response.json():
        path = item.get("path") or ""
        if item.get("type") != "file" or not path.lower().endswith(".gguf"):
            continue
        lfs = item.get("lfs") or {}
        file = {"path": path, "size": lfs.get("size") or item.get("size") or 0, "sha256": lfs.get("oid") or ""}
        name = os.path.basename(path)
        key = (os.path.dirname(path), _SHARD_RE.sub("", name))
        group = entries.setdefault(key, {"name": key[1], "folder": key[0], "quant": parse_quant(name),
                                         "is_mmproj": "mmproj" in name.lower(), "files": []})
        group["files"].append(file)
    groups = []
    for group in entries.values():
        group["files"].sort(key=lambda f: f["path"])
        group["size"] = sum(f["size"] for f in group["files"])
        group["shards"] = len(group["files"])
        groups.append(group)
    groups.sort(key=lambda g: (g["is_mmproj"], g["size"]))
    return groups


def resolve_selection(repo_id, paths, token=""):
    """Map client-supplied paths onto the repo's real listing; reject anything else."""
    wanted = set(paths or [])
    if not wanted:
        raise HFError("No files selected")
    known = {f["path"]: f for g in list_gguf_files(repo_id, token) for f in g["files"]}
    missing = wanted - set(known)
    if missing:
        raise HFError("File not found in repository: " + sorted(missing)[0])
    return [known[p] for p in sorted(wanted)]


def repo_folder_name(repo_id):
    return re.sub(r"[^\w.\-]+", "_", repo_id.replace("/", "__"))


# -- downloads ------------------------------------------------------------------
class Job:
    def __init__(self, repo_id, files, dest_dir, token, verify):
        self.id = uuid.uuid4().hex[:12]
        self.repo_id = repo_id
        self.files = files
        self.dest_dir = dest_dir
        self.token = token
        self.verify = verify
        self.total = sum(f["size"] for f in files)
        self.done = 0
        self.status = "queued"  # queued|downloading|verifying|done|error|cancelled
        self.error = ""
        self.current = ""
        self.cancel_event = threading.Event()
        self.started = time.time()
        self.finished = None
        self.saved = []

    def snapshot(self):
        elapsed = max((self.finished or time.time()) - self.started, 0.001)
        return {"id": self.id, "repo_id": self.repo_id, "status": self.status, "error": self.error,
                "current": self.current, "done": self.done, "total": self.total,
                "percent": round(100 * self.done / self.total, 1) if self.total else 0,
                "speed": int(self.done / elapsed) if self.status == "downloading" else 0,
                "folder": os.path.basename(self.dest_dir), "files": [os.path.basename(f["path"]) for f in self.files],
                "saved": list(self.saved), "paths": [f["path"] for f in self.files]}


class DownloadCancelled(Exception):
    pass


def _sha256(path, job):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(CHUNK * 8)
            if not block:
                break
            if job.cancel_event.is_set():
                raise DownloadCancelled()
            digest.update(block)
    return digest.hexdigest()


def _download_file(job, file):
    name = os.path.basename(file["path"])
    final = os.path.join(job.dest_dir, name)
    part = final + ".part"
    job.current = name
    if os.path.exists(final) and file["size"] and os.path.getsize(final) == file["size"]:
        job.done += file["size"]
        job.saved.append(name)
        return
    resumed = os.path.getsize(part) if os.path.exists(part) else 0
    if file["size"] and resumed > file["size"]:
        os.remove(part)
        resumed = 0
    url = f"{HF_BASE}/{job.repo_id}/resolve/main/{file['path']}"
    headers = {"Range": f"bytes={resumed}-"} if resumed else None
    response = _get(url, job.token, stream=True, extra_headers=headers, timeout=(15, 60))
    try:
        if response.status_code == 416 and resumed and resumed == file["size"]:
            resumed, response_ok = file["size"], False
        elif response.status_code not in (200, 206):
            raise HFError(_explain(response))
        else:
            response_ok = True
            if response.status_code == 200:
                resumed = 0  # server ignored Range: start over
        job.done += resumed
        if response_ok:
            with open(part, "ab" if resumed else "wb") as handle:
                for chunk in response.iter_content(CHUNK):
                    if job.cancel_event.is_set():
                        raise DownloadCancelled()
                    if chunk:
                        handle.write(chunk)
                        job.done += len(chunk)
    finally:
        response.close()
    if file["size"] and os.path.getsize(part) != file["size"]:
        raise HFError(f"{name}: download incomplete ({os.path.getsize(part)} of {file['size']} bytes); retry to resume")
    if job.verify and file.get("sha256"):
        job.status = "verifying"
        if _sha256(part, job) != file["sha256"]:
            os.remove(part)
            raise HFError(f"{name}: checksum mismatch; the partial file was removed")
        job.status = "downloading"
    os.replace(part, final)
    job.saved.append(name)


class DownloadManager:
    def __init__(self):
        self.jobs = {}
        self.lock = threading.Lock()

    def start(self, repo_id, files, models_dir, token="", verify=True):
        if not models_dir or not os.path.isdir(models_dir):
            raise HFError("Models folder is not configured or does not exist")
        validate_repo(repo_id)
        for file in files:
            name = os.path.basename(file["path"])
            if not name.lower().endswith(".gguf") or name in (".", ".."):
                raise HFError("Only .gguf files can be downloaded")
        dest = os.path.join(models_dir, repo_folder_name(repo_id))
        needed = sum(f["size"] for f in files)
        for f in files:  # a resumable .part counts towards what is already on disk
            part = os.path.join(dest, os.path.basename(f["path"])) + ".part"
            if os.path.exists(part):
                needed -= min(os.path.getsize(part), f["size"])
        free = shutil.disk_usage(models_dir).free
        if needed + DISK_MARGIN > free:
            raise HFError(f"Not enough disk space: need {needed / 1e9:.1f} GB, {free / 1e9:.1f} GB free")
        with self.lock:
            for job in self.jobs.values():
                if job.status in ("queued", "downloading", "verifying") and job.dest_dir == dest and \
                        {os.path.basename(f['path']) for f in job.files} & {os.path.basename(f['path']) for f in files}:
                    raise HFError("That file is already downloading")
            os.makedirs(dest, exist_ok=True)
            job = Job(repo_id, files, dest, token, verify)
            self.jobs[job.id] = job
        threading.Thread(target=self._run, args=(job,), daemon=True, name=f"hf-dl-{job.id}").start()
        return job

    def _run(self, job):
        job.status = "downloading"
        try:
            for file in job.files:
                _download_file(job, file)
            job.status = "done"
        except DownloadCancelled:
            job.status = "cancelled"
            for file in job.files:  # cancel is an explicit discard; failures keep the .part for resume
                part = os.path.join(job.dest_dir, os.path.basename(file["path"])) + ".part"
                if os.path.exists(part):
                    try:
                        os.remove(part)
                    except OSError:
                        pass
        except (HFError, OSError) as exc:
            job.status, job.error = "error", str(exc)
        finally:
            job.finished = time.time()

    def cancel(self, job_id):
        job = self.jobs.get(job_id)
        if not job:
            return False
        job.cancel_event.set()
        return True

    def get(self, job_id):
        job = self.jobs.get(job_id)
        return job.snapshot() if job else None

    def list(self):
        return [j.snapshot() for j in sorted(self.jobs.values(), key=lambda j: -j.started)]


MANAGER = DownloadManager()
