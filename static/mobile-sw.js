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
