/*
 * HWUI Launcher — standalone Electron wrapper.
 *
 * Lives entirely under C:\HWUI-Launcher\ and has no hardcoded dependency
 * on any particular HWUI install folder. Each HWUI install is registered in
 * builds.json (path + friendly name + service list) and selected at runtime.
 *
 * Boot flow:
 *   1. If builds.json is empty/missing → first-run setup; user adds builds.
 *   2. Show the picker so the user confirms which build to launch.
 *   3. Pre-kill anything bound to Flask :8081 and F5 :8003.
 *   4. Spawn Flask via the selected build's venv python; F5 via the build's
 *      Start_F5_XTTS.bat (.bat handles its own venv activation).
 *   5. Detect HTTPS via local cert files in the build root; poll Flask.
 *   6. Once Flask responds, navigate the main window from loading.html
 *      to the live Flask URL.
 *
 * Tray:
 *   • Show HWUI            — restore the main window (disabled pre-launch)
 *   • Manage Builds…       — reopens the setup screen at any time
 *   • Quit                 — only path that taskkills subprocesses and exits
 *
 * Window close (X) → full shutdown: quits the launcher and taskkills every
 * spawned console (Flask / F5-TTS / llama-server). Tray Quit does the same.
 */

const { app, BrowserWindow, Tray, Menu, ipcMain, nativeImage, dialog, session } = require('electron');
const path  = require('path');
const fs    = require('fs');
const os    = require('os');
const http  = require('http');
const https = require('https');
const { spawn, exec, execSync } = require('child_process');

// Redirect Chromium's disk cache to a writable temp location and disable the
// GPU shader cache. Avoids "Unable to move cache / Access denied" spam when
// the launcher (or its containing drive) is somewhere Chromium's default
// %LOCALAPPDATA% cache path can't move into. MUST be set before app.whenReady().
app.commandLine.appendSwitch('disable-gpu-shader-disk-cache');
app.commandLine.appendSwitch('disk-cache-dir', path.join(os.tmpdir(), 'hwui-launcher-cache'));
// Disable Chromium's back-forward cache. bfcache restoring index/config without a
// fresh load left the renderer's input pipeline stalled (focus correct, typing
// dead until a reflow). A desktop Electron app gains nothing from bfcache (it
// speeds up website back-button nav), so kill it app-wide. Belt-and-braces with
// the Flask Cache-Control: no-store header. ⚠️ DO NOT revert.
app.commandLine.appendSwitch('disable-features', 'BackForwardCache');

const FLASK_PORT       = 8081;
const F5_PORT          = 8003;
const QWEN_FAST_PORT   = 8767;
const READY_TIMEOUT_MS = 60_000;
const POLL_INTERVAL_MS = 300;
const TRAY_ICON_PATH   = path.join(__dirname, 'assets', 'icon.png');
const LOG_PATH         = path.join(__dirname, 'electron-flask.log');
const BUILDS_CONFIG    = path.join(__dirname, 'builds.json');
const DEFAULT_SERVICES = ['f5', 'whisper'];

// Window zoom. Chromium zoom is logarithmic (level 0 = 100%, factor = 1.2^level).
// Clamp to a usable band so the UI can't be zoomed into uselessness either way:
// level -3 ≈ 58%, level +5 ≈ 249%. STEP is per keypress / wheel notch (~10%).
const ZOOM_MIN  = -3;
const ZOOM_MAX  =  5;
const ZOOM_STEP =  0.5;

let mainWindow    = null;
let pickerWindow  = null;
let setupWindow   = null;
let tray          = null;
let isQuitting    = false;
let selectedBuild = null;

let currentZoom    = 0;     // applied zoom level for the live main window
let _saveZoomTimer = null;  // debounce handle for persisting zoom to builds.json

// State read by setup.html / picker.html over IPC.
let currentSetupMode     = 'manage';   // 'first-run' | 'manage'
let setupFinishCallback  = null;       // resolves the createSetupWindow promise
let pickerSelectCallback = null;       // resolves the createPickerWindow promise

// Every spawned Python subprocess; killAllSubprocesses() reaps them on quit.
const subprocesses = []; // [{ name, child }]

// --------------------------------------------------------------------------
// Logging
// --------------------------------------------------------------------------
function logLine(msg) {
  try { fs.appendFileSync(LOG_PATH, `[${new Date().toISOString()}] ${msg}\n`); } catch (_) {}
}

// --------------------------------------------------------------------------
// Builds config — load / save / validate
// --------------------------------------------------------------------------
function loadBuilds() {
  try {
    if (!fs.existsSync(BUILDS_CONFIG)) return [];
    const raw = fs.readFileSync(BUILDS_CONFIG, 'utf8').trim();
    if (!raw) return [];
    const arr = JSON.parse(raw);
    if (!Array.isArray(arr)) throw new Error('builds.json must be a JSON array');
    return arr.filter(b => b && typeof b.name === 'string' && typeof b.path === 'string');
  } catch (e) {
    logLine(`loadBuilds error: ${e.message}`);
    dialog.showErrorBox(
      'HWUI Launcher: builds.json error',
      `Could not read ${BUILDS_CONFIG}\n\n${e.message}\n\nStarting with an empty list.`
    );
    return [];
  }
}

function saveBuilds(builds) {
  try {
    fs.writeFileSync(BUILDS_CONFIG, JSON.stringify(builds, null, 2) + '\n', 'utf8');
  } catch (e) {
    logLine(`saveBuilds error: ${e.message}`);
    dialog.showErrorBox('HWUI Launcher', `Could not save builds.json:\n\n${e.message}`);
  }
}

function validateBuildPath(p) {
  if (!p)                       return { ok: false, error: 'No folder selected.' };
  if (!fs.existsSync(p))        return { ok: false, error: 'Folder does not exist.' };
  const appPy = path.join(p, 'app.py');
  if (!fs.existsSync(appPy))    return { ok: false, error: `app.py not found in:\n${p}` };
  return { ok: true };
}

// --------------------------------------------------------------------------
// HTTPS vs HTTP — mirror the cert check in app.py
// --------------------------------------------------------------------------
function detectScheme(buildPath) {
  const cert = path.join(buildPath, 'music.tail39b776.ts.net.crt');
  const key  = path.join(buildPath, 'music.tail39b776.ts.net.key');
  return (fs.existsSync(cert) && fs.existsSync(key)) ? 'https' : 'http';
}

// --------------------------------------------------------------------------
// Pre-launch: kill anything already listening on a target port
// --------------------------------------------------------------------------
function killPort(port) {
  return new Promise((resolve) => {
    exec(`netstat -ano | findstr :${port}`, (err, stdout) => {
      if (err || !stdout) return resolve();
      const pids = new Set();
      stdout.split('\n').forEach(line => {
        const trimmed = line.trim();
        if (!trimmed.includes('LISTENING')) return;
        const parts = trimmed.split(/\s+/);
        const pid = parts[parts.length - 1];
        if (/^\d+$/.test(pid) && pid !== '0') pids.add(pid);
      });
      if (pids.size === 0) return resolve();
      logLine(`Killing stale PIDs on port ${port}: ${[...pids].join(', ')}`);
      const cmds = [...pids].map(pid => `taskkill /PID ${pid} /F /T`).join(' & ');
      exec(cmds, () => resolve());
    });
  });
}

// --------------------------------------------------------------------------
// Subprocess management — Flask / F5 / etc.
// --------------------------------------------------------------------------
function spawnService(name, scriptPath, buildPath) {
  const python = path.join(buildPath, 'venv', 'Scripts', 'python.exe');
  if (!fs.existsSync(python))     throw new Error(`Python not found at ${python}`);
  if (!fs.existsSync(scriptPath)) throw new Error(`${name} script not found at ${scriptPath}`);

  logLine(`Spawning ${name}: ${python} ${scriptPath}  (cwd=${buildPath})`);
  const child = spawn(python, [scriptPath], {
    cwd: buildPath,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
    env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
  });

  const stream = fs.createWriteStream(LOG_PATH, { flags: 'a' });
  stream.write(`\n=== ${new Date().toISOString()} ${name} spawn (build=${buildPath}) ===\n`);
  child.stdout.on('data', d => stream.write(d));
  child.stderr.on('data', d => stream.write(d));
  child.on('exit', (code, sig) => logLine(`${name} exited code=${code} sig=${sig}`));

  subprocesses.push({ name, child });
  return child;
}

// Launch an isolated Python service using the venv beside its configured
// server file. HWUI needs only the explicit server-file path.
function spawnExternalPythonService(name, scriptPath) {
  if (!fs.existsSync(scriptPath)) throw new Error(`${name} script not found at ${scriptPath}`);
  const serviceRoot = path.dirname(scriptPath);
  const directPython = path.join(serviceRoot, 'venv', 'python.exe');
  const scriptsPython = path.join(serviceRoot, 'venv', 'Scripts', 'python.exe');
  const python = fs.existsSync(directPython) ? directPython : scriptsPython;
  if (!fs.existsSync(python)) throw new Error(`Python not found at ${python}`);

  logLine(`Spawning ${name}: ${python} ${scriptPath}  (cwd=${serviceRoot})`);
  const child = spawn(python, [scriptPath], {
    cwd: serviceRoot,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
    env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
  });

  const stream = fs.createWriteStream(LOG_PATH, { flags: 'a' });
  stream.write(`\n=== ${new Date().toISOString()} ${name} spawn (service=${scriptPath}) ===\n`);
  child.stdout.on('data', d => stream.write(d));
  child.stderr.on('data', d => stream.write(d));
  child.on('exit', (code, sig) => logLine(`${name} exited code=${code} sig=${sig}`));

  subprocesses.push({ name, child });
  return child;
}

// Spawn via cmd.exe /c <bat> for services whose launch script handles its own
// venv activation. The python process started by the .bat is a direct child
// of cmd.exe, so taskkill /T /F on the cmd PID reaps it on quit.
function spawnBatService(name, batPath, buildPath) {
  if (!fs.existsSync(batPath)) throw new Error(`${name} .bat not found at ${batPath}`);

  logLine(`Spawning ${name}: cmd.exe /c "${batPath}"  (cwd=${buildPath})`);
  const child = spawn('cmd.exe', ['/c', batPath], {
    cwd: buildPath,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
    env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
  });

  const stream = fs.createWriteStream(LOG_PATH, { flags: 'a' });
  stream.write(`\n=== ${new Date().toISOString()} ${name} spawn via bat (build=${buildPath}) ===\n`);
  child.stdout.on('data', d => stream.write(d));
  child.stderr.on('data', d => stream.write(d));
  child.on('exit', (code, sig) => logLine(`${name} exited code=${code} sig=${sig}`));

  subprocesses.push({ name, child });
  return child;
}

function killAllSubprocesses() {
  // SYNCHRONOUS taskkill: the old async exec() frequently didn't finish before
  // app.quit() tore the main process down, orphaning Flask/F5 (and their child
  // trees). execSync blocks until each tree is reaped, so quit actually cleans up.
  for (const { name, child } of subprocesses) {
    if (!child || child.killed) continue;
    logLine(`Killing ${name} pid=${child.pid}`);
    try { execSync(`taskkill /pid ${child.pid} /T /F`, { windowsHide: true }); }
    catch (e) { logLine(`taskkill error (${name}): ${e.message}`); }
  }
  subprocesses.length = 0;
}

// Synchronously kill whatever is LISTENING on a port (and its child tree).
function killPortSync(port) {
  let out = '';
  try {
    out = execSync(`netstat -ano | findstr :${port}`, { encoding: 'utf8', windowsHide: true });
  } catch (e) {
    return; // findstr exits non-zero when nothing matches → port is free.
  }
  const pids = new Set();
  out.split('\n').forEach(line => {
    const trimmed = line.trim();
    if (!trimmed.includes('LISTENING')) return;
    const parts = trimmed.split(/\s+/);
    const pid = parts[parts.length - 1];
    if (/^\d+$/.test(pid) && pid !== '0') pids.add(pid);
  });
  for (const pid of pids) {
    logLine(`Quit: killing PID ${pid} on port ${port}`);
    try { execSync(`taskkill /PID ${pid} /F /T`, { windowsHide: true }); }
    catch (e) { logLine(`taskkill port ${port} pid ${pid} error: ${e.message}`); }
  }
}

// Catch-all on quit: sweep the known service ports. Flask (8081) and F5 (8003)
// are direct children, but llama-server is a GRANDCHILD (Flask spawns it) and
// often runs in its own console window, so the PID-tree kill can miss it — its
// port (build-specific, default 5000) is read from the build's settings.json
// and swept explicitly so no stray server/console is left behind.
function killKnownPortsSync() {
  const ports = [FLASK_PORT, F5_PORT];
  try {
    if (selectedBuild && selectedBuild.path) {
      const sp = path.join(selectedBuild.path, 'settings.json');
      if (fs.existsSync(sp)) {
        const s = JSON.parse(fs.readFileSync(sp, 'utf8'));
        if (s && s.tts_engine === 'qwen-fast') ports.push(QWEN_FAST_PORT);
        const lport = s && s.llama_args && s.llama_args.port;
        if (lport && !ports.includes(lport)) ports.push(lport);
      }
    }
  } catch (e) {
    logLine(`Could not read llama port from build settings: ${e.message}`);
  }
  for (const p of ports) killPortSync(p);
}

// --------------------------------------------------------------------------
// Wait for Flask to respond on the chosen scheme
// --------------------------------------------------------------------------
function waitForFlask(scheme) {
  return new Promise((resolve) => {
    const start = Date.now();
    const url   = `${scheme}://127.0.0.1:${FLASK_PORT}/`;
    const mod   = scheme === 'https' ? https : http;
    const opts  = scheme === 'https' ? { rejectUnauthorized: false } : {};

    const tryOnce = () => {
      const req = mod.get(url, opts, (res) => { res.resume(); resolve(true); });
      req.setTimeout(1500, () => req.destroy());
      req.on('error', () => {
        if (Date.now() - start >= READY_TIMEOUT_MS) return resolve(false);
        setTimeout(tryOnce, POLL_INTERVAL_MS);
      });
    };
    tryOnce();
  });
}

// --------------------------------------------------------------------------
// Cert bypass (verification layer) for the local Flask URL only.
// --------------------------------------------------------------------------
function isLocalHost(host) {
  return host === '127.0.0.1' || host === 'localhost' || host === '[::1]';
}
function installLocalCertBypass(sess) {
  sess.setCertificateVerifyProc((request, callback) => {
    if (isLocalHost(request.hostname)) callback(0);  // 0 = OK
    else                               callback(-3); // -3 = use default verification
  });
}
app.on('certificate-error', (event, _wc, url, _err, _cert, callback) => {
  try {
    if (isLocalHost(new URL(url).hostname)) {
      event.preventDefault();
      callback(true);
      return;
    }
  } catch (_) {}
  callback(false);
});

// --------------------------------------------------------------------------
// Picker window (shown on every launch when builds.json has ≥1 entry)
// --------------------------------------------------------------------------
function createPickerWindow(builds) {
  return new Promise((resolve) => {
    pickerSelectCallback = (chosen) => {
      pickerSelectCallback = null;
      resolve(chosen);
    };
    pickerWindow = new BrowserWindow({
      width: 520, height: 500,
      resizable: false, maximizable: false, minimizable: false,
      frame: false, show: false,
      icon: TRAY_ICON_PATH,
      backgroundColor: '#0e0e0e',
      title: 'HWUI — Select Build',
      webPreferences: {
        preload: path.join(__dirname, 'preload.js'),
        contextIsolation: true,
        nodeIntegration: false,
      },
    });
    pickerWindow.loadFile(path.join(__dirname, 'picker.html'));
    pickerWindow.once('ready-to-show', () => pickerWindow.show());
    pickerWindow.on('closed', () => {
      pickerWindow = null;
      if (pickerSelectCallback) {
        const cb = pickerSelectCallback;
        pickerSelectCallback = null;
        cb(null);
      }
    });
  });
}

// --------------------------------------------------------------------------
// Setup window — first-run or manage-builds
// --------------------------------------------------------------------------
function createSetupWindow(mode) {
  return new Promise((resolve) => {
    if (setupWindow && !setupWindow.isDestroyed()) {
      setupWindow.show();
      setupWindow.focus();
      resolve('already-open');
      return;
    }
    currentSetupMode = mode;
    setupFinishCallback = (result) => {
      setupFinishCallback = null;
      resolve(result);
    };
    setupWindow = new BrowserWindow({
      width: 560, height: 480,
      resizable: false, maximizable: false, minimizable: false,
      frame: false, show: false,
      icon: TRAY_ICON_PATH,
      backgroundColor: '#0e0e0e',
      title: 'HWUI Launcher',
      webPreferences: {
        preload: path.join(__dirname, 'preload.js'),
        contextIsolation: true,
        nodeIntegration: false,
      },
    });
    setupWindow.loadFile(path.join(__dirname, 'setup.html'));
    setupWindow.once('ready-to-show', () => setupWindow.show());
    setupWindow.on('closed', () => {
      setupWindow = null;
      if (setupFinishCallback) {
        const cb = setupFinishCallback;
        setupFinishCallback = null;
        cb('closed');
      }
    });
  });
}

async function browseForBuildFolder() {
  const parent = (setupWindow && !setupWindow.isDestroyed()) ? setupWindow
                : (mainWindow  && !mainWindow.isDestroyed())  ? mainWindow
                : null;
  const r = await dialog.showOpenDialog(parent, {
    title: 'Select HWUI install folder',
    properties: ['openDirectory'],
  });
  if (r.canceled || !r.filePaths || !r.filePaths[0]) return null;
  const chosen = r.filePaths[0];
  const v = validateBuildPath(chosen);
  if (!v.ok) return { error: v.error };
  return { path: chosen };
}

// --------------------------------------------------------------------------
// IPC — registered once on app ready; handlers are persistent.
// --------------------------------------------------------------------------
function registerIpc() {
  ipcMain.handle('hwui:get-builds',      () => loadBuilds());
  ipcMain.handle('hwui:get-setup-mode',  () => currentSetupMode);
  ipcMain.handle('hwui:browse-for-build', () => browseForBuildFolder());

  ipcMain.handle('hwui:add-build', (_e, payload) => {
    if (!payload || typeof payload !== 'object') return { error: 'Bad request.' };
    const name = (payload.name || '').trim();
    const p    = payload.path;
    if (!name) return { error: 'Name cannot be empty.' };
    const v = validateBuildPath(p);
    if (!v.ok) return { error: v.error };
    const builds = loadBuilds();
    if (builds.some(b => b.path.toLowerCase() === p.toLowerCase())) {
      return { error: 'This folder is already registered.' };
    }
    builds.push({ name, path: p, services: [...DEFAULT_SERVICES] });
    saveBuilds(builds);
    return { ok: true, builds };
  });

  ipcMain.handle('hwui:remove-build', (_e, i) => {
    const builds = loadBuilds();
    if (typeof i !== 'number' || i < 0 || i >= builds.length) {
      return { error: 'Invalid index.' };
    }
    builds.splice(i, 1);
    saveBuilds(builds);
    return { ok: true, builds };
  });

  ipcMain.on('hwui:select-build', (_e, i) => {
    const builds = loadBuilds();
    if (pickerSelectCallback && typeof i === 'number' && builds[i]) {
      const cb = pickerSelectCallback;
      pickerSelectCallback = null;
      // destroy() (not close()) tears the picker down SYNCHRONOUSLY — close() is
      // async, so the picker could still be alive when the main window loads and
      // grab foreground focus back from it. Destroying now prevents that.
      if (pickerWindow && !pickerWindow.isDestroyed()) pickerWindow.destroy();
      cb(builds[i]);
    }
  });

  ipcMain.on('hwui:quit', () => {
    if (pickerWindow && !pickerWindow.isDestroyed()) pickerWindow.close();
    if (setupWindow  && !setupWindow.isDestroyed())  setupWindow.close();
    quitApp();
  });

  ipcMain.on('hwui:finish-setup', () => {
    if (setupFinishCallback) {
      const cb = setupFinishCallback;
      setupFinishCallback = null;
      if (setupWindow && !setupWindow.isDestroyed()) setupWindow.close();
      cb('finished');
    }
  });

  ipcMain.on('hwui:close-setup', () => {
    if (setupFinishCallback) {
      const cb = setupFinishCallback;
      setupFinishCallback = null;
      if (setupWindow && !setupWindow.isDestroyed()) setupWindow.close();
      cb('closed');
    }
  });
}

// --------------------------------------------------------------------------
// Window zoom — Ctrl+wheel / Ctrl+= / Ctrl+- / Ctrl+0 / context-menu items.
// Persisted per build in builds.json (matched by path). Wired by hand because
// the nulled app menu (Menu.setApplicationMenu(null)) stripped Electron's default
// zoom accelerators along with the menu bar.
// --------------------------------------------------------------------------
function clampZoom(level) {
  return Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, level));
}

// Push the tracked zoom level into the renderer. No-ops if the window is gone.
function applyZoom() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.setZoomLevel(currentZoom);
  }
}

// Flash a brief, self-fading "Zoom NNN%" toast in the page (like a browser).
// Injected at runtime via executeJavaScript so the Flask templates stay untouched
// — the renderer is contextIsolated, so this is the no-template-edit way to show
// feedback. percent = round(1.2^level * 100); shown only on user-driven changes.
function showZoomOverlay() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  const percent = Math.round(Math.pow(1.2, currentZoom) * 100);
  const js = `(function(){
    if(!document.body)return;
    var id='__hwui_zoom_toast__',el=document.getElementById(id);
    if(!el){
      el=document.createElement('div');el.id=id;
      el.style.cssText='position:fixed;bottom:22px;left:50%;transform:translateX(-50%);z-index:2147483647;background:rgba(20,24,30,0.92);color:#e8e8e8;font:600 14px/1 system-ui,-apple-system,sans-serif;padding:8px 14px;border-radius:8px;border:1px solid #2d394b;pointer-events:none;transition:opacity .25s ease;box-shadow:0 2px 10px rgba(0,0,0,.45)';
      document.body.appendChild(el);
    }
    el.textContent='Zoom ${percent}%';
    el.style.opacity='1';
    clearTimeout(el.__hwuiHide);
    el.__hwuiHide=setTimeout(function(){el.style.opacity='0';},900);
  })();`;
  mainWindow.webContents.executeJavaScript(js).catch(() => {});
}

// Persist the current zoom onto the SELECTED build's builds.json entry (matched
// by path, preserving its name/services). Debounced ~400ms so a fast Ctrl+wheel
// spin doesn't thrash the file on every notch.
function saveZoomDebounced() {
  if (_saveZoomTimer) clearTimeout(_saveZoomTimer);
  _saveZoomTimer = setTimeout(() => {
    _saveZoomTimer = null;
    if (!selectedBuild || !selectedBuild.path) return;
    const builds = loadBuilds();
    const entry  = builds.find(b => b.path &&
      b.path.toLowerCase() === selectedBuild.path.toLowerCase());
    if (!entry) return;
    entry.zoom = currentZoom;
    selectedBuild.zoom = currentZoom;   // keep the in-memory copy in sync
    saveBuilds(builds);
  }, 400);
}

// Step / reset entry points — shared by the keyboard accelerators, the Ctrl+wheel
// handler, and the context-menu items.
function stepZoom(delta) {
  const next = clampZoom(currentZoom + delta);
  if (next !== currentZoom) {          // skip apply/save at a clamp edge…
    currentZoom = next;
    applyZoom();
    saveZoomDebounced();
  }
  showZoomOverlay();                   // …but always re-flash the % for feedback
}
function resetZoom() {
  currentZoom = 0;       // 100% — always applied AND persisted, not just visual
  applyZoom();
  saveZoomDebounced();
  showZoomOverlay();
}

// --------------------------------------------------------------------------
// Main window
// --------------------------------------------------------------------------
function createMainWindow() {
  // Seed the tracked zoom from the selected build's saved level (default 100%).
  currentZoom = clampZoom(Number(selectedBuild && selectedBuild.zoom) || 0);

  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 900,
    minHeight: 600,
    show: false,
    icon: TRAY_ICON_PATH,
    backgroundColor: '#0e0e0e',
    title: 'HWUI Pro',
    titleBarStyle: 'hidden',
    titleBarOverlay: {
      color: '#0e0e0e',
      symbolColor: '#e8e8e8',
      height: 32,
    },
    resizable:   true,
    maximizable: true,
    autoHideMenuBar: true,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      spellcheck: true,   // built-in spellchecker (default on; explicit for clarity)
    },
  });
  Menu.setApplicationMenu(null);

  // Pin the spellchecker to en-US so misspelled-word detection + suggestions are
  // deterministic regardless of system locale. (Adjust/extend this list to add
  // other languages.)
  try { mainWindow.webContents.session.setSpellCheckerLanguages(['en-US']); }
  catch (e) { logLine(`setSpellCheckerLanguages failed: ${e.message}`); }

  mainWindow.loadFile(path.join(__dirname, 'loading.html'));
  mainWindow.once('ready-to-show', () => mainWindow.show());

  // Right-click context menu — Cut / Copy / Paste / Select All / Inspect.
  mainWindow.webContents.on('context-menu', (_e, params) => {
    const { editFlags, isEditable, selectionText, misspelledWord,
            dictionarySuggestions, x, y } = params;
    const hasSelection = !!(selectionText && selectionText.trim());
    const template = [];

    // Spelling suggestions for a misspelled word under the cursor. Electron's
    // built-in spellchecker flags the word (params.misspelledWord) and offers
    // corrections (params.dictionarySuggestions); replaceMisspelling() swaps the
    // word in place. Shown at the TOP like a native context menu.
    if (misspelledWord) {
      if (dictionarySuggestions.length) {
        for (const suggestion of dictionarySuggestions) {
          template.push({
            label: suggestion,
            click: () => mainWindow.webContents.replaceMisspelling(suggestion),
          });
        }
      } else {
        template.push({ label: 'No spelling suggestions', enabled: false });
      }
      template.push({
        label: 'Add to dictionary',
        click: () => mainWindow.webContents.session
                       .addWordToSpellCheckerDictionary(misspelledWord),
      });
      template.push({ type: 'separator' });
    }

    template.push(
      { label: 'Cut',        role: 'cut',       enabled: isEditable && editFlags.canCut },
      { label: 'Copy',       role: 'copy',      enabled: hasSelection && editFlags.canCopy },
      { label: 'Paste',      role: 'paste',     enabled: isEditable && editFlags.canPaste },
      { type: 'separator' },
      { label: 'Select All', role: 'selectAll', enabled: editFlags.canSelectAll },
      { type: 'separator' },
      { label: 'Zoom In',    click: () => stepZoom(ZOOM_STEP) },
      { label: 'Zoom Out',   click: () => stepZoom(-ZOOM_STEP) },
      { label: 'Reset Zoom', click: () => resetZoom() },
      { type: 'separator' },
      { label: 'Inspect Element', click: () => mainWindow.webContents.inspectElement(x, y) },
    );

    Menu.buildFromTemplate(template).popup({ window: mainWindow });
  });

  // Re-register reload shortcuts. The app menu is nulled out (above), which
  // also strips Electron's default Ctrl+R / F5 / Ctrl+Shift+R accelerators, so
  // we wire them back by hand here — no visible menu bar involved. keyDown only,
  // so we don't double-fire on keyUp.
  mainWindow.webContents.on('before-input-event', (event, input) => {
    if (input.type !== 'keyDown') return;
    const key = (input.key || '').toLowerCase();
    const isReload =
      key === 'f5' ||
      (input.control && key === 'r');           // Ctrl+R (and Ctrl+Shift+R)
    if (isReload) {
      event.preventDefault();
      if (input.shift) mainWindow.webContents.reloadIgnoringCache();
      else             mainWindow.webContents.reload();
      return;
    }

    // Zoom accelerators: Ctrl+= / Ctrl++ (in), Ctrl+- (out), Ctrl+0 (reset to
    // 100%). Wired by hand because the nulled app menu stripped Electron's
    // defaults. Numpad +/- arrive as 'Add'/'Subtract'. All route through the same
    // step/reset helpers the context menu and Ctrl+wheel use.
    if (input.control) {
      if (key === '=' || key === '+' || key === 'add')      { event.preventDefault(); stepZoom(ZOOM_STEP);  return; }
      if (key === '-' || key === 'subtract')                { event.preventDefault(); stepZoom(-ZOOM_STEP); return; }
      if (key === '0')                                      { event.preventDefault(); resetZoom();          return; }
    }
  });

  // Ctrl+mouse-wheel zoom. Chromium emits 'zoom-changed' for Ctrl+wheel; we mirror
  // the direction into our tracked level, then re-apply our clamped value so the
  // step size and bounds stay consistent with the keyboard/menu paths (and never
  // exceed the clamp). Persisted via the same debounced writer.
  mainWindow.webContents.on('zoom-changed', (_e, zoomDirection) => {
    stepZoom(zoomDirection === 'in' ? ZOOM_STEP : -ZOOM_STEP);
  });

  // Close (X) → full shutdown. Closing the window now QUITS the launcher and
  // reaps every spawned console (Flask, F5-TTS, and the llama-server grandchild)
  // via quitApp()'s synchronous taskkill + port sweep. Previously this hid to the
  // tray, leaving the F5 and llama console windows running — closing the window
  // read as "shut it all down" but two consoles kept hanging.
  mainWindow.on('close', (e) => {
    if (isQuitting) return;   // already tearing down — let it close normally
    e.preventDefault();       // run cleanup in order, then app.quit() closes us
    quitApp();
  });
  mainWindow.on('closed', () => { mainWindow = null; rebuildTrayMenu(); });

  // Re-focus the renderer whenever the OS re-foregrounds the window (app-switch,
  // Alt-Tab, taskbar click). Windows restores window-level focus but Chromium
  // does NOT reliably restore renderer keyboard focus, so typing goes nowhere
  // until the user clicks inside. A bare webContents.focus() fixes that.
  // ⚠️ Intentionally NOT forceFocusMain(): that toggles alwaysOnTop, which can
  // itself emit window focus events and feed back on itself. forceFocusMain() is
  // reserved for cold-start + explicit re-show paths; this hot path only restores
  // renderer keyboard focus.
  mainWindow.on('focus', () => {
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.focus();
  });

  // Restore renderer keyboard focus on internal navigation completion. Standard
  // <a href> nav between index ⇄ config is a fresh document load, but on the first
  // return to index the renderer can come back WITHOUT keyboard focus (caret on
  // <body>, typing dead) until a click — and a bfcache restore doesn't even fire
  // DOMContentLoaded. did-navigate fires when a main-frame nav commits;
  // did-navigate-in-page covers hash/history changes. Same guard as the window
  // focus handler: bare focus, destroyed-guard, no alwaysOnTop. Complementary to
  // the renderer-side 'pageshow' handler in index.html — one fires per renderer
  // event, the other per main-process navigation, so we're covered either way.
  // Defer 300ms: calling focus() the instant a navigation commits fires BEFORE the
  // renderer has fully committed, and Chromium's own post-commit focus handling
  // then overrides ours. (Proven by repro: minimize→restore — which routes through
  // the window 'focus' handler on an already-settled renderer — fixes the dead-
  // typing state, so the bare call is correct; it's just too early on navigation.)
  // Matches the reliable did-finish-load + 300ms pattern.
  const _refocusRenderer = () => {
    setTimeout(() => {
      if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.focus();
    }, 300);
  };
  mainWindow.webContents.on('did-navigate',         _refocusRenderer);
  mainWindow.webContents.on('did-navigate-in-page', _refocusRenderer);
}

// --------------------------------------------------------------------------
// Tray
// --------------------------------------------------------------------------
function createTray() {
  let img = nativeImage.createFromPath(TRAY_ICON_PATH);
  if (img.isEmpty()) {
    logLine(`Tray icon not found / unreadable at ${TRAY_ICON_PATH}`);
    img = nativeImage.createEmpty();
  } else {
    img = img.resize({ width: 16, height: 16 });
  }
  tray = new Tray(img);
  tray.setToolTip('HWUI Launcher');
  rebuildTrayMenu();
  tray.on('click',        () => showMain());
  tray.on('double-click', () => showMain());
}

function rebuildTrayMenu() {
  if (!tray) return;
  const menu = Menu.buildFromTemplate([
    { label: 'Show HWUI',      click: () => showMain(),   enabled: !!mainWindow },
    { label: 'Reload HWUI',    click: () => reloadMain(), enabled: !!mainWindow },
    { label: 'Manage Builds…', click: () => openManageBuilds() },
    { type: 'separator' },
    { label: 'Quit',           click: () => quitApp() },
  ]);
  tray.setContextMenu(menu);
}

async function openManageBuilds() {
  if (setupWindow && !setupWindow.isDestroyed()) {
    setupWindow.show();
    setupWindow.focus();
    return;
  }
  await createSetupWindow('manage');
}

// Force the main window to the OS foreground AND push keyboard focus into the
// page. On Windows, focus() alone frequently fails to STEAL foreground
// activation — the window is visible but not the active window, so keystrokes
// go nowhere until the user clicks inside. Briefly toggling alwaysOnTop reliably
// pulls the window to the foreground; we then drop alwaysOnTop and focus the
// webContents so the keyboard lands in the page.
function forceFocusMain() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.setAlwaysOnTop(true);
  mainWindow.show();
  mainWindow.setAlwaysOnTop(false);
  mainWindow.focus();
  mainWindow.webContents.focus();
  try { app.focus({ steal: true }); } catch (_) {}
}

function showMain() {
  forceFocusMain();
}

// Reload the page currently in the main window (HWUI, or loading.html if Flask
// isn't up yet). Exposed via the tray + the in-page title-bar button so the
// user can recover from a stuck/failed load — the default Ctrl+R/F5 reload
// accelerators are gone because the app menu is nulled out.
function reloadMain() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  showMain();
  mainWindow.webContents.reload();
}

function quitApp() {
  isQuitting = true;
  killAllSubprocesses();
  killKnownPortsSync();
  if (tray) { try { tray.destroy(); } catch (_) {} tray = null; }
  app.quit();
}

// Safety net: fires on every quit path (tray Quit, OS shutdown, app.quit from
// anywhere), so subprocess + port cleanup runs even if quitApp() was bypassed.
app.on('before-quit',        () => { isQuitting = true; killAllSubprocesses(); killKnownPortsSync(); });
app.on('window-all-closed',  (e) => { if (!isQuitting) e.preventDefault(); });

// --------------------------------------------------------------------------
// Single-instance lock — only ONE launcher may run at a time. Re-running
// START_HWUI-Launcher.bat used to spawn a whole new Electron + cmd console each
// time; if one hung (e.g. the Chromium network service crashed and the window/
// tray never came up) it became an unkillable orphan, and they stacked up "two
// or three" deep. A duplicate launch now exits IMMEDIATELY.
//
// ⚠️ Uses app.exit(0), NOT app.quit(): app.exit skips the 'before-quit' handler,
// so the duplicate does NOT run killKnownPortsSync() and therefore cannot sweep
// the already-running instance's Flask/F5/llama ports out from under it.
// --------------------------------------------------------------------------
const gotSingleInstanceLock = app.requestSingleInstanceLock();
if (!gotSingleInstanceLock) {
  app.exit(0);
} else {
  // Re-running the launcher focuses the live window instead of stacking a new one.
  app.on('second-instance', () => {
    if (mainWindow && !mainWindow.isDestroyed()) showMain();
  });

// --------------------------------------------------------------------------
// Boot
// --------------------------------------------------------------------------
app.whenReady().then(async () => {
  installLocalCertBypass(session.defaultSession);
  registerIpc();
  createTray();

  let builds = loadBuilds();

  // First-run: no builds configured. Require at least one before we can launch.
  if (builds.length === 0) {
    const result = await createSetupWindow('first-run');
    if (result !== 'finished') { quitApp(); return; }
    builds = loadBuilds();
    if (builds.length === 0)   { quitApp(); return; }
  }

  // Pick a build. Picker is always shown — even with a single entry the user
  // confirms which build to launch on every run.
  selectedBuild = await createPickerWindow(builds);
  if (!selectedBuild) { quitApp(); return; }

  logLine(`Selected build: ${selectedBuild.name} → ${selectedBuild.path}`);
  if (!fs.existsSync(selectedBuild.path)) {
    dialog.showErrorBox('HWUI Launcher', `Build path does not exist:\n\n${selectedBuild.path}`);
    quitApp();
    return;
  }

  const settingsPath = path.join(selectedBuild.path, 'settings.json');
  let buildSettings = {};
  try {
    if (fs.existsSync(settingsPath)) buildSettings = JSON.parse(fs.readFileSync(settingsPath, 'utf8'));
  } catch (e) {
    dialog.showErrorBox('HWUI Launcher: settings error', `Could not read settings.json:\n\n${e.message}`);
    quitApp();
    return;
  }
  const selectedTTSEngine = buildSettings.tts_engine || 'f5';

  const portsToClear = [killPort(FLASK_PORT), killPort(F5_PORT)];
  if (selectedTTSEngine === 'qwen-fast') portsToClear.push(killPort(QWEN_FAST_PORT));
  await Promise.all(portsToClear);

  // Belt-and-braces: make sure the picker is fully gone before the main window
  // is created, so its teardown can't steal foreground focus back from it.
  if (pickerWindow && !pickerWindow.isDestroyed()) pickerWindow.destroy();
  pickerWindow = null;

  createMainWindow();
  rebuildTrayMenu();           // "Show HWUI" now enabled

  try {
    spawnService('flask', path.join(selectedBuild.path, 'app.py'), selectedBuild.path);
  } catch (e) {
    dialog.showErrorBox('HWUI Launcher: Flask spawn failed', e.message);
    quitApp();
    return;
  }

  const services = Array.isArray(selectedBuild.services) ? selectedBuild.services : [];
  if (selectedTTSEngine === 'qwen-fast') {
    try {
      const serverPath = buildSettings.qwen_tts_fast_server;
      if (!serverPath) throw new Error('qwen_tts_fast_server is not set in settings.json');
      spawnExternalPythonService('qwen3-tts-fast', serverPath);
    } catch (e) {
      logLine(`Qwen3-TTS Fast spawn failed (non-fatal): ${e.message}`);
    }
  } else if (services.includes('f5')) {
    try {
      spawnBatService('f5-tts', path.join(selectedBuild.path, 'Start_F5_XTTS.bat'), selectedBuild.path);
    } catch (e) {
      logLine(`F5-TTS spawn failed (non-fatal): ${e.message}`);
    }
  }
  if (services.includes('whisper')) {
    logLine('whisper is in-process with Flask — no subprocess spawn');
  }

  const scheme = detectScheme(selectedBuild.path);
  logLine(`Polling ${scheme}://127.0.0.1:${FLASK_PORT}/ ...`);
  const ready = await waitForFlask(scheme);
  if (!ready) {
    dialog.showErrorBox(
      'HWUI Launcher: Flask did not start',
      `No response on ${scheme}://127.0.0.1:${FLASK_PORT}/ within ${READY_TIMEOUT_MS/1000}s.\n\nSee ${LOG_PATH}`
    );
    quitApp();
    return;
  }

  logLine(`Flask ready — navigating window to ${scheme}://127.0.0.1:${FLASK_PORT}/`);
  if (mainWindow) {
    // Push keyboard focus into the page whenever it finishes loading. Without
    // this, after a programmatic loadURL() (or a reload) on an already-shown
    // window the renderer ends up WITHOUT keyboard focus on Windows: the page
    // renders and the mouse works, but typing does nothing until the user clicks
    // inside. Use a PERSISTENT 'did-finish-load' (not .once): the reload paths
    // we added (F5 / Ctrl+R / the in-window reload button / tray Reload) each
    // trigger a fresh load, and a one-shot handler wouldn't re-focus after them —
    // which is exactly how "can't type without clicking first" regressed. The
    // deferred second focus() lands after the renderer is actually interactive.
    mainWindow.webContents.on('did-finish-load', () => {
      // ⚠️ DO NOT REVERT: re-apply the saved per-build zoom on every load. Chromium
      // resets zoom level on a fresh document load, so without this the window snaps
      // back to 100% on every reload (F5/Ctrl+R) and every index⇄config navigation.
      // This line is the load-bearing bit that makes the persisted zoom actually
      // stick across loads.
      applyZoom();
      // Immediate attempt, then again at 300ms: at 60ms the renderer often isn't
      // interactive yet and the focus doesn't stick, so the later retry is what
      // actually lands keyboard focus in the page.
      forceFocusMain();
      setTimeout(forceFocusMain, 300);
    });
    mainWindow.loadURL(`${scheme}://127.0.0.1:${FLASK_PORT}/`);
  }
});
}  // end single-instance-lock else
