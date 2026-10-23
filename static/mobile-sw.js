/*
 * HWUI mobile service worker: Web Push for Random Check-ins.
 *
 * Served at /mobile-sw.js and registered with scope /mobile. The server
 * (checkin_notifications.py) pushes a small JSON payload
 *   { type, id, title, body, chat, project }
 * after a check-in has been saved. Every push shows a notification (Chrome
 * requires it for userVisibleOnly subscriptions), tagged with the check-in ID
 * so a repeat replaces rather than stacks. Tapping opens /mobile on the
 * originating project/chat, reusing an open HWUI mobile window when there is one.
 */
const HWUI_PUSH_ICON = '/static/icons/hwui-192.png';
const HWUI_PUSH_BADGE = '/static/icons/hwui-badge-96.png';
const HWUI_PUSH_TITLE_MAX = 64;
const HWUI_PUSH_BODY_MAX = 180;
const HWUI_MOBILE_PATH = '/mobile';

function hwuiPushTruncate(value, max) {
  const text = String(value == null ? '' : value).replace(/\s+/g, ' ').trim();
  if (text.length <= max) return text;
  return text.slice(0, max - 1).trimEnd() + '…';
}

// Same query shape mobile.html's mobileDeepLinkFromUrl() reads.
function hwuiMobileChatUrl(chat, project) {
  if (!chat) return HWUI_MOBILE_PATH;
  const params = new URLSearchParams();
  params.set('chat', chat);
  params.set('project', project || '');
  return HWUI_MOBILE_PATH + '?' + params.toString();
}

function hwuiNotificationFromPush(data) {
  let payload = null;
  try { payload = data ? data.json() : null; } catch (_) { payload = null; }
  if (!payload || typeof payload !== 'object') payload = {};
  const text = key => (typeof payload[key] === 'string' ? payload[key] : '');
  const chat = text('chat').slice(0, 500);
  const project = text('project').slice(0, 200);
  const id = text('id').slice(0, 200);
  return {
    title: hwuiPushTruncate(payload.title, HWUI_PUSH_TITLE_MAX) || 'HWUI',
    options: {
      body: hwuiPushTruncate(payload.body, HWUI_PUSH_BODY_MAX) || 'New message',
      tag: id || 'hwui',
      icon: HWUI_PUSH_ICON,
      badge: HWUI_PUSH_BADGE,
      data: { type: text('type') || 'unknown', id, chat, project, url: hwuiMobileChatUrl(chat, project) },
    },
  };
}

// Only ever open /mobile on this origin, whatever the notification carries.
function hwuiNotificationTarget(data) {
  const origin = self.location.origin;
  try {
    const url = new URL((data && data.url) || HWUI_MOBILE_PATH, origin);
    if (url.origin === origin && url.pathname === HWUI_MOBILE_PATH) return url.href;
  } catch (_) {}
  return new URL(HWUI_MOBILE_PATH, origin).href;
}

async function hwuiOpenFromNotification(data) {
  const target = hwuiNotificationTarget(data);
  const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
  const existing = windows.find(client => {
    try { return new URL(client.url).pathname === HWUI_MOBILE_PATH; } catch (_) { return false; }
  });
  if (existing) {
    const focused = await existing.focus();
    if (data && data.chat) {
      (focused || existing).postMessage({ type: 'hwui-open-chat', chat: data.chat, project: data.project || '' });
    }
    return focused || existing;
  }
  return self.clients.openWindow(target);
}

// ── Launch/resume resilience ────────────────────────────────────────────────
// Opening the installed app often races the phone's network / Tailscale VPN
// coming up, and Chrome then shows its own "offline" error page until the user
// refreshes by hand. Navigations to /mobile are therefore retried briefly, and
// if HWUI still cannot be reached a tiny "Connecting" page (generated here,
// never cached) keeps retrying on its own and loads the real app the moment the
// server answers. Only navigations are handled; every other request, and every
// successful navigation, goes straight to the network exactly as before, so no
// stale copy of the app or of an offline state can ever be served.
const HWUI_NAV_ATTEMPTS = 3;
const HWUI_NAV_RETRY_MS = 700;

const HWUI_CONNECTING_HTML = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="theme-color" content="#000000">
<title>HWUI</title><style>
html,body{height:100%;margin:0;background:#000;color:#ccc;font:16px system-ui,sans-serif}
body{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;text-align:center;padding:24px}
.dot{width:12px;height:12px;border-radius:50%;background:#4a9;animation:p 1.2s ease-in-out infinite}
@keyframes p{0%,100%{opacity:.25}50%{opacity:1}} small{color:#777}
</style></head><body><div class="dot"></div><div id="m">Connecting to HWUI&hellip;</div>
<small>Make sure HWUI is running and Tailscale is connected. This page retries automatically.</small>
<script>
let delay=1000,busy=false,timer=null;
async function attempt(){
  if(busy)return;busy=true;clearTimeout(timer);
  try{
    const r=await fetch('/manifest.webmanifest',{cache:'no-store'});
    if(r.ok){location.reload();return;}
  }catch(e){}
  busy=false;
  timer=setTimeout(attempt,delay);delay=Math.min(delay*2,8000);
}
function now(){delay=1000;attempt();}
document.addEventListener('visibilitychange',()=>{if(!document.hidden)now();});
window.addEventListener('online',now);window.addEventListener('pageshow',now);
timer=setTimeout(attempt,1000);
</script></body></html>`;

async function hwuiFetchNavigation(request) {
  for (let attempt = 0; attempt < HWUI_NAV_ATTEMPTS; attempt++) {
    try {
      const response = await fetch(request);
      // Tailscale Serve answers 502/503/504 when nothing is listening behind it.
      if (response.status < 502 || response.status > 504) return response;
    } catch (_) {}
    if (attempt < HWUI_NAV_ATTEMPTS - 1) await new Promise(r => setTimeout(r, HWUI_NAV_RETRY_MS));
  }
  return new Response(HWUI_CONNECTING_HTML, {
    status: 200,
    headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' },
  });
}

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.mode !== 'navigate' || request.method !== 'GET') return;
  event.respondWith(hwuiFetchNavigation(request));
});

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));

self.addEventListener('push', event => {
  const { title, options } = hwuiNotificationFromPush(event.data);
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(hwuiOpenFromNotification(event.notification.data || {}));
});

// The browser rotated this phone's subscription: re-register it so check-ins
// keep arriving without the user having to re-enable notifications.
self.addEventListener('pushsubscriptionchange', event => {
  event.waitUntil((async () => {
    const old = event.oldSubscription || null;
    let fresh = event.newSubscription || null;
    if (!fresh) {
      const key = old && old.options && old.options.applicationServerKey;
      if (!key) return;
      fresh = await self.registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
    }
    await fetch('/api/push/subscriptions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ subscription: fresh.toJSON(), replaces: old ? old.endpoint : '' }),
    });
  })().catch(() => {}));
});
