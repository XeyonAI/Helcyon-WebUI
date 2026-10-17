"""Observational diagnostics for slow llama-server prompt evaluation.

Built for the 2026-09-24 incidents (GPT-4o req#13 98 s, Mythic req#14 71 s to
first token) where llama-server's own log proved prompt evaluation itself ran
25-70x slower than normal, but nothing on record could say WHY: VRAM spill into
Windows shared memory, another process on the GPU, clock throttling, or host
RAM paging. This module records the machine/GPU state at the moments that
answer that, and nothing else.

Design rules (do not relax without re-measuring generation timing):

* Observational only. Never changes a request, a slot, or llama-server.
* Nothing here runs on the streaming thread except in-memory bookkeeping
  (timestamps, counters). Every probe runs in a background thread, serialised
  by one lock so probes never overlap, and child processes run at
  BELOW_NORMAL priority with no window.
* No continuous polling. Probes run only at: llama-server launch (a ~70 ms
  nvidia-smi read before spawn), model ready, a stalled request (6 s after the
  POST with no visible content, then every 20 s, at most 4 times), Stop, and
  first content after a stall.
* Per-process GPU memory comes from Windows' own GPU performance counters
  (typeperf). nvidia-smi reports per-process memory as [N/A] under WDDM, which
  is why it is used here only for the adapter-level view, clocks and
  throttle reasons. Counter names are the English ones; on a localised Windows
  the counter probe records an error rather than guessing.

Output: logs/gpu_stall_trace.jsonl (one JSON object per line) plus a one-line
human summary per snapshot on stdout (which app.py tees into hwui_full.log).
"""

import csv
import json
import os
import re
import subprocess
import threading
import time
import weakref

try:  # psutil is an app.py dependency; keep the module importable without it.
    import psutil
except Exception:  # pragma: no cover - exercised only on broken installs
    psutil = None

try:
    import requests
except Exception:  # pragma: no cover
    requests = None


STALL_FIRST_AFTER_SECONDS = 6.0
STALL_REPEAT_SECONDS = 20.0
STALL_MAX_SNAPSHOTS = 4
LAUNCH_LOG_KEEP = 10
TRACE_MAX_BYTES = 20 * 1024 * 1024
NON_JSON_SAMPLE_LIMIT = 3

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_BELOW_NORMAL = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
_CHILD_FLAGS = (_NO_WINDOW | _BELOW_NORMAL) if os.name == "nt" else 0

_NVIDIA_FIELDS = (
    "index", "uuid", "name", "driver_version", "driver_model.current",
    "memory.total", "memory.reserved", "memory.used", "memory.free",
    "utilization.gpu", "utilization.memory", "temperature.gpu",
    "clocks.sm", "clocks.max.sm", "clocks.mem", "clocks.max.mem",
    "power.draw", "power.limit", "pstate",
    "pcie.link.gen.current", "pcie.link.width.current",
    "clocks_event_reasons.active",
    "clocks_event_reasons.sw_power_cap",
    "clocks_event_reasons.hw_slowdown",
    "clocks_event_reasons.hw_thermal_slowdown",
    "clocks_event_reasons.hw_power_brake_slowdown",
    "clocks_event_reasons.sw_thermal_slowdown",
)

# Memory counters are instantaneous, so one sample is exact. Rate counters
# (engine utilisation, page reads, CPU) need two samples one second apart and
# are therefore only collected for stall snapshots.
_COUNTERS_MEMORY = (
    r"\GPU Process Memory(*)\Dedicated Usage",
    r"\GPU Process Memory(*)\Shared Usage",
    r"\GPU Adapter Memory(*)\Dedicated Usage",
    r"\GPU Adapter Memory(*)\Shared Usage",
    r"\Memory\Committed Bytes",
    r"\Memory\Commit Limit",
    r"\Memory\Available MBytes",
)
_COUNTERS_RATES = (
    r"\GPU Engine(*)\Utilization Percentage",
    r"\Memory\Page Reads/sec",
    r"\Memory\Pages/sec",
    r"\Processor(_Total)\% Processor Time",
)

_COUNTER_PATH_RE = re.compile(
    r"^\\\\[^\\]+\\(?P<object>[^\\(]+)(?:\((?P<instance>.*)\))?\\(?P<counter>[^\\]+)$"
)
_PID_INSTANCE_RE = re.compile(r"^pid_(?P<pid>\d+)_(?P<luid>luid_0x[0-9A-Fa-f]+_0x[0-9A-Fa-f]+)")
_LUID_RE = re.compile(r"(luid_0x[0-9A-Fa-f]+_0x[0-9A-Fa-f]+)")
_ENGTYPE_RE = re.compile(r"engtype_(?P<engtype>.+)$")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_SERVER_UPTIME_RE = re.compile(r"^\s*(\d+)\.(\d{2})\.(\d{3})\.(\d{3})\s")
_PROMPT_PROGRESS_RE = re.compile(
    r"task\s+(?P<task>\d+)\s*\|\s*prompt processing, n_tokens\s*=\s*(?P<n_tokens>\d+),"
    r"\s*progress\s*=\s*(?P<progress>[\d.]+),\s*t\s*=\s*(?P<t>[\d.]+)\s*s\s*/\s*"
    r"(?P<tps>[\d.]+)\s*tokens per second"
)
_PROMPT_EVAL_DONE_RE = re.compile(
    r"task\s+(?P<task>\d+)\s*\|\s*prompt eval time\s*=\s*(?P<ms>[\d.]+)\s*ms\s*/\s*"
    r"(?P<tokens>\d+)\s*tokens.*?(?P<tps>[\d.]+)\s*tokens per second"
)
_LAUNCH_TASK_RE = re.compile(r"launch_slot_:.*task\s+(?P<task>\d+)\s*\|\s*processing task")


def _mib(value):
    return None if value is None else round(float(value) / (1024 * 1024), 1)


def _num(text):
    text = (text or "").strip()
    if not text or text.startswith("[") or text in ("N/A", "Not Active", "Active"):
        return text or None
    try:
        return float(text) if "." in text else int(text)
    except ValueError:
        return text


def _server_uptime_seconds(line):
    """llama.cpp log prefix m.ss.mmm.uuu = time since the server started."""
    match = _SERVER_UPTIME_RE.match(line)
    if not match:
        return None
    minutes, seconds, millis, micros = (int(part) for part in match.groups())
    return round(minutes * 60 + seconds + millis / 1000 + micros / 1_000_000, 3)


def parse_llama_log_tail(text):
    """Latest prompt-progress / prompt-eval / task-launch facts from a log tail.

    llama-server logs prompt processing per 2048-token batch, so during a slow
    prompt evaluation this is the only source that still advances: /slots is
    answered by the same main loop that is busy inside the batch.
    """
    result = {"progress": None, "prompt_eval_done": None, "last_task_launched": None, "last_line": None}
    lines = [_ANSI_RE.sub("", raw).rstrip() for raw in text.splitlines()]
    lines = [line for line in lines if line.strip()]
    if lines:
        result["last_line"] = lines[-1][-240:]
    for line in reversed(lines):
        if result["progress"] is None:
            match = _PROMPT_PROGRESS_RE.search(line)
            if match:
                result["progress"] = {
                    "task": int(match["task"]),
                    "n_tokens": int(match["n_tokens"]),
                    "progress": float(match["progress"]),
                    "t_seconds": float(match["t"]),
                    "tokens_per_second": float(match["tps"]),
                    "server_uptime_seconds": _server_uptime_seconds(line),
                }
        if result["prompt_eval_done"] is None:
            match = _PROMPT_EVAL_DONE_RE.search(line)
            if match:
                result["prompt_eval_done"] = {
                    "task": int(match["task"]),
                    "ms": float(match["ms"]),
                    "tokens": int(match["tokens"]),
                    "tokens_per_second": float(match["tps"]),
                    "server_uptime_seconds": _server_uptime_seconds(line),
                }
        if result["last_task_launched"] is None:
            match = _LAUNCH_TASK_RE.search(line)
            if match:
                result["last_task_launched"] = {
                    "task": int(match["task"]),
                    "server_uptime_seconds": _server_uptime_seconds(line),
                }
        if all(result[key] is not None for key in ("progress", "prompt_eval_done", "last_task_launched")):
            break
    return result


def parse_typeperf_csv(text):
    """Last sample of typeperf CSV output as {counter path: float}."""
    rows = list(csv.reader(line for line in text.splitlines() if line.startswith('"')))
    if len(rows) < 2:
        return {}
    header, last = rows[0], rows[-1]
    values = {}
    for index in range(1, min(len(header), len(last))):
        raw = last[index].strip()
        if not raw:
            continue
        try:
            values[header[index]] = float(raw)
        except ValueError:
            continue
    return values


def summarise_counters(values):
    """Group raw counter values into per-PID, per-adapter and host figures."""
    per_pid = {}
    adapters = {}
    engines = {}
    host = {}
    for path, value in values.items():
        match = _COUNTER_PATH_RE.match(path)
        if not match:
            continue
        obj, instance, counter = match["object"], match["instance"] or "", match["counter"]
        if obj == "GPU Process Memory":
            pid_match = _PID_INSTANCE_RE.match(instance)
            if not pid_match:
                continue
            entry = per_pid.setdefault(int(pid_match["pid"]), {"dedicated": 0.0, "shared": 0.0, "luids": set()})
            entry["luids"].add(pid_match["luid"])
            if counter == "Dedicated Usage":
                entry["dedicated"] += value
            elif counter == "Shared Usage":
                entry["shared"] += value
        elif obj == "GPU Adapter Memory":
            luid = _LUID_RE.search(instance)
            key = luid.group(1) if luid else instance
            entry = adapters.setdefault(key, {"dedicated": 0.0, "shared": 0.0})
            if counter == "Dedicated Usage":
                entry["dedicated"] += value
            elif counter == "Shared Usage":
                entry["shared"] += value
        elif obj == "GPU Engine" and counter == "Utilization Percentage":
            pid_match = _PID_INSTANCE_RE.match(instance)
            engtype = _ENGTYPE_RE.search(instance)
            if not pid_match or value <= 0:
                continue
            key = (int(pid_match["pid"]), engtype["engtype"] if engtype else "?")
            engines[key] = engines.get(key, 0.0) + value
        elif obj == "Memory":
            host[counter] = value
        elif obj == "Processor":
            host["processor_total_percent"] = value
    return per_pid, adapters, engines, host


class StreamWatch:
    """Per-request timing marks plus the bounded stall trigger.

    The streaming thread only calls mark()/note_non_json()/finish(); those do
    in-memory work under a lock. The stall trigger is one threading.Timer that
    is cancelled by the first visible content, so a normal request costs one
    timer thread start/cancel and one JSONL line at the end.
    """

    def __init__(self, diag, request_id, endpoint, context=None):
        self.diag = diag
        self.request_id = request_id
        self.endpoint = endpoint
        self.context = dict(context or {})
        self.t0 = time.perf_counter()
        self.started_wall = time.time()
        self.launch = diag.current_launch()
        self.model_at_request = diag.current_model()
        self.marks = {}
        self.non_json_count = 0
        self.non_json_samples = []
        self.stall_snapshots = 0
        self.recovery_taken = False
        self.finished = False
        self.outcome = None
        self._timer = None
        self._lock = threading.Lock()
        self._prev_cpu = None

    # -- streaming-thread API ---------------------------------------------

    def elapsed(self):
        return round(time.perf_counter() - self.t0, 3)

    def start(self):
        self._schedule(self.diag.stall_first_after)
        return self

    def mark(self, name):
        """Record the first time `name` happens. Returns seconds since POST."""
        with self._lock:
            if name in self.marks:
                return self.marks[name]
            value = self.elapsed()
            self.marks[name] = value
            content_now = name == "first_content"
            if content_now:
                self._cancel_timer()
            stalled = content_now and self.stall_snapshots > 0 and not self.recovery_taken
            if stalled:
                self.recovery_taken = True
        if stalled:
            self.diag.snapshot_async("recovery", watch=self, heavy=False)
        return value

    def note_non_json(self, raw_line):
        with self._lock:
            self.non_json_count += 1
            if len(self.non_json_samples) < NON_JSON_SAMPLE_LIMIT:
                if isinstance(raw_line, (bytes, bytearray)):
                    shown = repr(bytes(raw_line[:120]))
                else:
                    shown = repr(str(raw_line)[:120])
                self.non_json_samples.append({"t": self.elapsed(), "line": shown})

    def finish(self, outcome, **extra):
        with self._lock:
            if self.finished:
                return
            self.finished = True
            self.outcome = outcome
            self._cancel_timer()
        self.diag.request_finished(self, outcome, extra)

    # -- internals ----------------------------------------------------------

    def _schedule(self, delay):
        timer = threading.Timer(delay, self._fire)
        timer.daemon = True
        timer.name = f"hwui-stall-watch-{self.request_id}"
        with self._lock:
            if self.finished or "first_content" in self.marks:
                return
            self._timer = timer
        timer.start()

    def _cancel_timer(self):
        timer, self._timer = self._timer, None
        if timer is not None:
            timer.cancel()

    def _fire(self):
        with self._lock:
            if self.finished or "first_content" in self.marks:
                return
            if self.stall_snapshots >= self.diag.stall_max_snapshots:
                return
            self.stall_snapshots += 1
            seq = self.stall_snapshots
            again = seq < self.diag.stall_max_snapshots
        self.diag.snapshot_async("stall", watch=self, heavy=True, seq=seq)
        if again:
            self._schedule(self.diag.stall_repeat)

    def identity(self):
        with self._lock:
            return {
                "request_id": self.request_id,
                "endpoint": self.endpoint,
                "context": dict(self.context),
                "model_at_request": self.model_at_request,
                "post_started_at": _iso(self.started_wall),
                "elapsed_since_post": self.elapsed(),
                "marks": dict(self.marks),
                "non_json_lines": self.non_json_count,
                "stall_snapshots": self.stall_snapshots,
                "launch_at_request": dict(self.launch or {}),
            }


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts)) + f".{int((ts % 1) * 1000):03d}"


class StallDiagnostics:
    def __init__(
        self,
        log_dir,
        api_url_getter=lambda: None,
        model_getter=lambda: None,
        run=None,
        emit=None,
        stall_first_after=STALL_FIRST_AFTER_SECONDS,
        stall_repeat=STALL_REPEAT_SECONDS,
        stall_max_snapshots=STALL_MAX_SNAPSHOTS,
        launch_log_keep=LAUNCH_LOG_KEEP,
    ):
        self.log_dir = log_dir
        self.trace_path = os.path.join(log_dir, "gpu_stall_trace.jsonl")
        self.server_log_dir = os.path.join(log_dir, "llama_server_logs")
        self._api_url_getter = api_url_getter
        self._model_getter = model_getter
        self._run = run or self._run_subprocess
        self._emit = emit or (lambda text: print(text, flush=True))
        self.stall_first_after = stall_first_after
        self.stall_repeat = stall_repeat
        self.stall_max_snapshots = stall_max_snapshots
        self.launch_log_keep = max(1, int(launch_log_keep))
        self.hwui_pid = os.getpid()
        self._write_lock = threading.Lock()
        self._probe_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._launch_seq = 0
        self._launch = {}
        self._active_watch = None
        self._probe_threads = []

    # -- identity -----------------------------------------------------------

    def current_launch(self):
        with self._state_lock:
            return dict(self._launch)

    def current_model(self):
        try:
            return str(self._model_getter() or "") or None
        except Exception:
            return None

    # -- llama-server launch lifecycle ---------------------------------------

    def new_launch(self, cmd, lifecycle=None):
        """Allocate the launch id and its own server log path; prune old logs.

        Called by _spawn_llama_server before Popen, so a log is never shared
        between two server processes and an earlier server's log survives the
        next model swap (the #12/#13 server's log was lost to exactly that).
        """
        os.makedirs(self.server_log_dir, exist_ok=True)
        with self._state_lock:
            self._launch_seq += 1
            seq = self._launch_seq
        stamp = time.strftime("%Y%m%d-%H%M%S")
        launch_id = f"{stamp}-p{self.hwui_pid}-L{seq}"
        self._prune_server_logs(keep=self.launch_log_keep - 1)
        log_path = os.path.join(self.server_log_dir, f"llama_server_{launch_id}.log")
        model_path = None
        port = None
        try:
            parts = [str(part) for part in cmd]
            if "-m" in parts:
                model_path = parts[parts.index("-m") + 1]
            if "--port" in parts:
                port = parts[parts.index("--port") + 1]
        except Exception:
            pass
        with self._state_lock:
            self._launch = {
                "launch_id": launch_id,
                "launch_seq": seq,
                "hwui_pid": self.hwui_pid,
                "lifecycle": lifecycle,
                "model_path": model_path,
                "port": port,
                "server_log": log_path,
                "pid": None,
                "spawned_at": None,
                "ready_at": None,
            }
        return log_path

    def launch_pre_spawn(self, cmd):
        """Synchronous, deliberately cheap: one nvidia-smi read + process list.

        Taken after the previous server was killed and before the new one
        allocates anything, so it shows whether the old VRAM was released and
        whether any other llama-server (another build) is still resident.
        """
        started = time.monotonic()
        record = self._base_record("launch_pre_spawn")
        record["nvidia_smi"] = self._probe_nvidia_smi()
        record["llama_servers"] = self._probe_llama_servers()
        record["probe_seconds"] = round(time.monotonic() - started, 3)
        self._write(record)
        self._emit_summary(record)

    def launch_spawned(self, pid):
        with self._state_lock:
            self._launch["pid"] = pid
            self._launch["spawned_at"] = _iso(time.time())
        record = self._base_record("launch_spawned")
        self._write(record)

    def launch_ready(self, lifecycle=None):
        with self._state_lock:
            self._launch["ready_at"] = _iso(time.time())
            if lifecycle:
                self._launch["ready_lifecycle"] = lifecycle
        self.snapshot_async("launch_ready", heavy=False)

    # -- request lifecycle ----------------------------------------------------

    def start_watch(self, request_id, endpoint, context=None):
        watch = StreamWatch(self, request_id, endpoint, context)
        with self._state_lock:
            self._active_watch = watch
        return watch.start()

    def attach_response(self, watch, response):
        """Finish the watch when a generator is abandoned without reaching its end."""
        try:
            weakref.finalize(response, watch.finish, "stream_abandoned")
        except TypeError:
            pass

    def request_finished(self, watch, outcome, extra):
        with self._state_lock:
            if self._active_watch is watch:
                self._active_watch = None
        record = self._base_record("request_timing", watch=watch)
        record["outcome"] = outcome
        record["non_json_samples"] = list(watch.non_json_samples)
        record.update({key: value for key, value in (extra or {}).items() if value is not None})
        self._write(record)

    def on_stop(self):
        with self._state_lock:
            watch = self._active_watch
        if watch is None:
            self._write(self._base_record("stop_no_active_stream"))
            return
        watch.mark("stop_requested")
        self.snapshot_async("stop", watch=watch, heavy=False)

    # -- snapshots ------------------------------------------------------------

    def snapshot_async(self, kind, watch=None, heavy=False, seq=None):
        launch = self.current_launch()
        identity = watch.identity() if watch is not None else None
        thread = threading.Thread(
            target=self._snapshot,
            args=(kind, watch, heavy, seq, launch, identity),
            name=f"hwui-gpu-snapshot-{kind}",
            daemon=True,
        )
        self._probe_threads = [t for t in self._probe_threads if t.is_alive()] + [thread]
        thread.start()
        return thread

    def wait_for_probes(self, timeout=30):
        """Test helper: join outstanding snapshot threads."""
        deadline = time.monotonic() + timeout
        for thread in list(self._probe_threads):
            thread.join(max(0.0, deadline - time.monotonic()))

    def _snapshot(self, kind, watch, heavy, seq, launch, identity):
        try:
            with self._probe_lock:
                started = time.monotonic()
                record = self._base_record(f"{kind}_snapshot", launch=launch)
                record["kind"] = kind
                if seq is not None:
                    record["seq"] = seq
                if identity is not None:
                    record["request"] = identity
                    record["request"]["elapsed_at_probe_start"] = watch.elapsed()
                record["nvidia_smi"] = self._probe_nvidia_smi()
                record.update(self._probe_counters(heavy, launch.get("pid"), watch))
                record["llama_servers"] = self._probe_llama_servers()
                record["slots"] = self._probe_slots()
                record["llama_log"] = self._probe_llama_log(launch.get("server_log"))
                record["probe_seconds"] = round(time.monotonic() - started, 3)
                if watch is not None:
                    record["request"]["elapsed_at_probe_end"] = watch.elapsed()
                    record["request"]["marks_at_probe_end"] = dict(watch.marks)
            self._write(record)
            self._emit_summary(record)
        except Exception as exc:  # diagnostics must never take anything down
            try:
                self._write({"ts": time.time(), "event": "snapshot_error", "kind": kind, "error": repr(exc)})
            except Exception:
                pass

    def _base_record(self, event, watch=None, launch=None):
        now = time.time()
        record = {
            "ts": now,
            "time": _iso(now),
            "event": event,
            "hwui_pid": self.hwui_pid,
            "launch": launch if launch is not None else self.current_launch(),
            "current_model": self.current_model(),
        }
        if watch is not None:
            record["request"] = watch.identity()
        return record

    # -- probes -----------------------------------------------------------------

    @staticmethod
    def _run_subprocess(args, timeout):
        completed = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            creationflags=_CHILD_FLAGS,
        )
        return (completed.stdout or b"").decode("utf-8", "replace")

    def _probe_nvidia_smi(self):
        started = time.monotonic()
        try:
            out = self._run(
                ["nvidia-smi", f"--query-gpu={','.join(_NVIDIA_FIELDS)}", "--format=csv,noheader,nounits"],
                5,
            )
        except Exception as exc:
            return {"error": repr(exc)}
        gpus = []
        for row in csv.reader(line for line in out.splitlines() if line.strip()):
            if len(row) != len(_NVIDIA_FIELDS):
                return {"error": "unexpected nvidia-smi output", "raw": out[-400:]}
            gpus.append({field: _num(value) for field, value in zip(_NVIDIA_FIELDS, row)})
        return {"gpus": gpus, "probe_ms": round((time.monotonic() - started) * 1000)}

    def _probe_counters(self, heavy, llama_pid, watch):
        counters = _COUNTERS_MEMORY + (_COUNTERS_RATES if heavy else ())
        samples = 2 if heavy else 1
        args = ["typeperf", *counters, "-sc", str(samples)]
        if samples > 1:
            args += ["-si", "1"]
        started = time.monotonic()
        try:
            values = parse_typeperf_csv(self._run(args, 10 + 2 * samples))
        except Exception as exc:
            return {"counters_error": repr(exc)}
        if not values:
            return {"counters_error": "typeperf returned no values (localised counter names?)"}
        per_pid, adapters, engines, host = summarise_counters(values)

        llama_luids = set(per_pid.get(llama_pid, {}).get("luids", ())) if llama_pid else set()
        ranked = sorted(per_pid.items(), key=lambda item: item[1]["dedicated"] + item[1]["shared"], reverse=True)
        chosen = [pid for pid, _ in ranked[:10]]
        for pid in (llama_pid, self.hwui_pid):
            if pid and pid in per_pid and pid not in chosen:
                chosen.append(pid)
        gpu_processes = []
        for pid in chosen:
            entry = per_pid[pid]
            gpu_processes.append({
                "pid": pid,
                "name": self._process_name(pid),
                "dedicated_mib": _mib(entry["dedicated"]),
                "shared_mib": _mib(entry["shared"]),
                "is_llama_server": pid == llama_pid,
                "is_hwui": pid == self.hwui_pid,
            })
        adapter_list = [
            {
                "luid": luid,
                "dedicated_mib": _mib(entry["dedicated"]),
                "shared_mib": _mib(entry["shared"]),
                "used_by_llama_server": luid in llama_luids,
            }
            for luid, entry in sorted(adapters.items())
        ]
        result = {
            "gpu_processes": gpu_processes,
            "gpu_adapters": adapter_list,
            "host": self._host_memory(host),
            "llama_process": self._llama_process(llama_pid, watch),
            "counters_probe_ms": round((time.monotonic() - started) * 1000),
            "counters_samples": samples,
        }
        if heavy:
            busy = sorted(engines.items(), key=lambda item: item[1], reverse=True)[:12]
            result["gpu_engines"] = [
                {
                    "pid": pid,
                    "name": self._process_name(pid),
                    "engtype": engtype,
                    "utilization_percent": round(value, 1),
                    "is_llama_server": pid == llama_pid,
                }
                for (pid, engtype), value in busy
                if value >= 1.0
            ]
        return result

    def _host_memory(self, host_counters):
        host = {}
        committed = host_counters.get("Committed Bytes")
        limit = host_counters.get("Commit Limit")
        if committed is not None:
            host["committed_mib"] = _mib(committed)
        if limit is not None:
            host["commit_limit_mib"] = _mib(limit)
        if committed and limit:
            host["commit_percent"] = round(100.0 * committed / limit, 1)
        if "Available MBytes" in host_counters:
            host["available_mib"] = host_counters["Available MBytes"]
        for key, name in (("Page Reads/sec", "page_reads_per_sec"), ("Pages/sec", "pages_per_sec")):
            if key in host_counters:
                host[name] = round(host_counters[key], 1)
        if "processor_total_percent" in host_counters:
            host["processor_total_percent"] = round(host_counters["processor_total_percent"], 1)
        if psutil is not None:
            try:
                vm = psutil.virtual_memory()
                host.update({
                    "ram_total_mib": _mib(vm.total),
                    "ram_available_mib": _mib(vm.available),
                    "ram_used_percent": vm.percent,
                })
                swap = psutil.swap_memory()
                host.update({"pagefile_used_mib": _mib(swap.used), "pagefile_total_mib": _mib(swap.total)})
            except Exception as exc:
                host["psutil_error"] = repr(exc)
        return host

    def _llama_process(self, pid, watch):
        if not pid or psutil is None:
            return {"pid": pid, "alive": None}
        try:
            proc = psutil.Process(pid)
            mem = proc.memory_info()
            cpu = proc.cpu_times()
            info = {
                "pid": pid,
                "alive": proc.is_running(),
                "status": proc.status(),
                "num_threads": proc.num_threads(),
                "cpu_user_s": round(cpu.user, 2),
                "cpu_system_s": round(cpu.system, 2),
            }
            for field in ("rss", "vms", "private", "pagefile", "peak_wset", "num_page_faults"):
                value = getattr(mem, field, None)
                if value is not None:
                    info[field if field == "num_page_faults" else f"{field}_mib"] = (
                        value if field == "num_page_faults" else _mib(value)
                    )
            # CPU cores used since this request's previous snapshot: a prompt
            # evaluation running on the CPU instead of the GPU shows up here.
            now = time.monotonic()
            total_cpu = cpu.user + cpu.system
            if watch is not None:
                previous = watch._prev_cpu
                watch._prev_cpu = (now, total_cpu)
                if previous is not None and now > previous[0]:
                    info["cpu_cores_since_prev_snapshot"] = round((total_cpu - previous[1]) / (now - previous[0]), 2)
            return info
        except Exception as exc:
            return {"pid": pid, "alive": False, "error": repr(exc)}

    def _probe_llama_servers(self):
        """Every llama-server on the machine: another build's server holding
        VRAM, or a killed PID that has not exited yet, is visible here."""
        if psutil is None:
            return None
        servers = []
        try:
            for proc in psutil.process_iter(["pid", "name", "create_time"]):
                name = (proc.info.get("name") or "").lower()
                if not name.startswith("llama-server"):
                    continue
                entry = {
                    "pid": proc.info["pid"],
                    "started": _iso(proc.info["create_time"]) if proc.info.get("create_time") else None,
                    "is_this_launch": proc.info["pid"] == self.current_launch().get("pid"),
                }
                try:
                    parts = proc.cmdline()
                    if "-m" in parts:
                        entry["model"] = parts[parts.index("-m") + 1]
                    if "--port" in parts:
                        entry["port"] = parts[parts.index("--port") + 1]
                except Exception:
                    pass
                servers.append(entry)
        except Exception as exc:
            return {"error": repr(exc)}
        return servers

    def _probe_slots(self):
        """One /slots GET. A timeout is itself evidence: the server's main loop
        answers /slots between batches, so it times out mid-batch."""
        api_url = None
        try:
            api_url = self._api_url_getter()
        except Exception:
            pass
        if not api_url or requests is None:
            return {"skipped": True}
        started = time.monotonic()
        try:
            response = requests.get(f"{api_url}/slots", timeout=(0.25, 0.5))
            latency = round((time.monotonic() - started) * 1000)
            if response.status_code != 200:
                return {"status": response.status_code, "latency_ms": latency}
            slots = response.json()
            out = []
            for slot in slots if isinstance(slots, list) else []:
                next_token = (slot.get("next_token") or [{}])[0]
                out.append({
                    "id": slot.get("id"),
                    "id_task": slot.get("id_task"),
                    "is_processing": slot.get("is_processing"),
                    "n_prompt_tokens": slot.get("n_prompt_tokens"),
                    "n_prompt_tokens_processed": slot.get("n_prompt_tokens_processed"),
                    "n_prompt_tokens_cache": slot.get("n_prompt_tokens_cache"),
                    "n_decoded": next_token.get("n_decoded"),
                })
            return {"status": 200, "latency_ms": latency, "slots": out}
        except Exception as exc:
            return {
                "error": type(exc).__name__,
                "latency_ms": round((time.monotonic() - started) * 1000),
            }

    def _probe_llama_log(self, path):
        if not path:
            return {"skipped": True}
        try:
            with open(path, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - 65536))
                tail = handle.read().decode("utf-8", "replace")
        except OSError as exc:
            return {"path": path, "error": repr(exc)}
        parsed = parse_llama_log_tail(tail)
        parsed["path"] = path
        parsed["size"] = size
        return parsed

    @staticmethod
    def _process_name(pid):
        if psutil is None:
            return None
        try:
            return psutil.Process(pid).name()
        except Exception:
            return None

    # -- output -------------------------------------------------------------------

    def _prune_server_logs(self, keep):
        try:
            names = sorted(
                name for name in os.listdir(self.server_log_dir)
                if name.startswith("llama_server_") and name.endswith(".log")
            )
        except OSError:
            return
        # Names start with a sortable timestamp, so oldest first.
        for name in names[: max(0, len(names) - keep)]:
            try:
                os.remove(os.path.join(self.server_log_dir, name))
            except OSError:
                pass  # still open by a live server; retried on the next launch

    def _write(self, record):
        line = json.dumps(record, ensure_ascii=False, default=_json_default)
        with self._write_lock:
            try:
                if os.path.exists(self.trace_path) and os.path.getsize(self.trace_path) > TRACE_MAX_BYTES:
                    os.replace(self.trace_path, self.trace_path + ".1")
            except OSError:
                pass
            with open(self.trace_path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def _emit_summary(self, record):
        try:
            self._emit(format_summary(record))
        except Exception:
            pass


def _json_default(value):
    if isinstance(value, set):
        return sorted(value)
    return str(value)


def format_summary(record):
    """One human line per snapshot for hwui_full.log."""
    kind = record.get("kind") or record.get("event")
    launch = record.get("launch") or {}
    request = record.get("request") or {}
    parts = [f"🔬 GPU SNAPSHOT {kind}{('#' + str(record['seq'])) if record.get('seq') else ''}"]
    if request:
        parts.append(f"req#{request.get('request_id')} t={request.get('elapsed_since_post')}s")
    parts.append(
        f"launch={launch.get('launch_id')} pid={launch.get('pid')} "
        f"model={os.path.basename(str(launch.get('model_path') or '')) or '?'}"
    )
    smi = (record.get("nvidia_smi") or {}).get("gpus") or []
    if smi:
        gpu = smi[0]
        parts.append(
            f"VRAM {gpu.get('memory.used')}/{gpu.get('memory.total')} MiB free {gpu.get('memory.free')} "
            f"util {gpu.get('utilization.gpu')}% sm {gpu.get('clocks.sm')}/{gpu.get('clocks.max.sm')} MHz "
            f"{gpu.get('pstate')} pcie gen{gpu.get('pcie.link.gen.current')} "
            f"throttle={gpu.get('clocks_event_reasons.active')}"
        )
    elif record.get("nvidia_smi"):
        parts.append(f"nvidia-smi {record['nvidia_smi'].get('error')}")
    for proc in record.get("gpu_processes") or []:
        if proc.get("is_llama_server"):
            parts.append(f"llama ded {proc.get('dedicated_mib')} MiB shared {proc.get('shared_mib')} MiB")
    adapters = [a for a in record.get("gpu_adapters") or [] if a.get("used_by_llama_server")]
    for adapter in adapters:
        parts.append(f"adapter shared {adapter.get('shared_mib')} MiB")
    host = record.get("host") or {}
    if host:
        parts.append(
            f"RAM avail {host.get('ram_available_mib')} MiB commit {host.get('commit_percent')}%"
            + (f" pagereads/s {host['page_reads_per_sec']}" if "page_reads_per_sec" in host else "")
        )
    progress = ((record.get("llama_log") or {}).get("progress")) or None
    if progress:
        parts.append(
            f"prompt task {progress['task']} {progress['n_tokens']} tok "
            f"({progress['progress']:.0%}) {progress['tokens_per_second']} tok/s"
        )
    slots = record.get("slots") or {}
    if slots.get("error"):
        parts.append(f"/slots {slots['error']} after {slots.get('latency_ms')} ms")
    servers = record.get("llama_servers")
    if isinstance(servers, list):
        parts.append(f"llama-servers running={len(servers)}")
    if "probe_seconds" in record:
        parts.append(f"probe {record['probe_seconds']}s")
    return " | ".join(parts)
