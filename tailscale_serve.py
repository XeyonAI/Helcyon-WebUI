"""Point the stable Tailscale Serve hostname at THIS installation's web port.

Run once by whatever explicitly launches a build (START_UI.bat,
START_HWUI-Dev.bat, HWUI-Launcher). It is deliberately not called from app.py
or any background thread, so builds running side by side never fight over the
mapping: the last build the user launches owns the mobile URL, deterministically.

The port comes from resolve_web_port(), the same source of truth app.py uses.
Everything here is best-effort: any failure is logged and the process exits 0
so HWUI startup is never blocked by Tailscale.
"""
import json
import os
import shutil
import subprocess
import sys

from app_runtime_helpers import resolve_web_port

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_TAILSCALE_FALLBACKS = (
    r"C:\Program Files\Tailscale\tailscale.exe",
    r"C:\Program Files (x86)\Tailscale\tailscale.exe",
)
_TIMEOUT = 15


def _log(msg):
    print("[tailscale-serve] {}".format(msg), flush=True)


def find_tailscale():
    found = shutil.which("tailscale")
    if found:
        return found
    for candidate in _TAILSCALE_FALLBACKS:
        if os.path.isfile(candidate):
            return candidate
    return None


def has_tls_certs(base_dir=_BASE_DIR):
    """Mirror app.py's cert check: HTTPS only when both cert files exist."""
    return any(
        os.path.isfile(os.path.join(base_dir, n + ".crt"))
        and os.path.isfile(os.path.join(base_dir, n + ".key"))
        for n in _cert_names(base_dir)
    )


def _cert_names(base_dir):
    return sorted({f[:-4] for f in os.listdir(base_dir) if f.endswith(".ts.net.crt")})


def desired_target(port, https):
    scheme = "https+insecure" if https else "http"
    return "{}://127.0.0.1:{}".format(scheme, int(port))


def current_root_target(status_json):
    """Return the '/' proxy target of the first web entry, or None."""
    try:
        web = (status_json or {}).get("Web") or {}
        for entry in web.values():
            proxy = ((entry.get("Handlers") or {}).get("/") or {}).get("Proxy")
            if proxy:
                return proxy.rstrip("/")
    except Exception:
        pass
    return None


def _run(cmd):
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=_TIMEOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def sync(port=None, https=None, tailscale=None):
    """Ensure Tailscale Serve proxies to `port`. Returns a short status string."""
    try:
        ts = tailscale or find_tailscale()
        if not ts:
            _log("tailscale executable not found; mobile routing left unchanged.")
            return "skipped-no-tailscale"
        port = int(port if port is not None else resolve_web_port())
        https = has_tls_certs() if https is None else https
        target = desired_target(port, https)

        current = None
        try:
            res = _run([ts, "serve", "status", "--json"])
            if res.returncode == 0 and res.stdout.strip():
                current = current_root_target(json.loads(res.stdout))
        except Exception as exc:
            _log("could not read current serve status ({!r}); will set it.".format(exc))

        if current == target:
            _log("already routing to {}; no change.".format(target))
            return "unchanged"

        res = _run([ts, "serve", "--bg", target])
        if res.returncode != 0:
            _log("`tailscale serve` failed (exit {}): {}".format(
                res.returncode, (res.stderr or res.stdout or "").strip()))
            return "failed"
        _log("routing updated: {} -> {}".format(current or "(none)", target))
        return "updated"
    except Exception as exc:
        _log("skipped due to error: {!r}".format(exc))
        return "error"


if __name__ == "__main__":
    try:
        sync()
    except BaseException as exc:  # never block the launcher
        _log("unexpected failure: {!r}".format(exc))
    sys.exit(0)
