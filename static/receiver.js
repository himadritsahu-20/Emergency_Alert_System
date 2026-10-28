const params = new URLSearchParams(window.location.search);
const deviceId = params.get('device_id');
const status = document.querySelector('#receiver-status');
const messageList = document.querySelector('#receiver-messages');
const pushButton = document.querySelector('#enable-push');
let polling = false;
let serviceWorkerRegistration;
const receivedMessageIds = new Set();

const iconGlyphs = {
  warning: '⚠️', flood: '🌊', fire: '🔥', storm: '⛈️', earthquake: '🏚️',
  evacuate: '🚶', high_ground: '⛰️', shelter: '🏠', medical: '✚',
  no_cross: '🚫', call: '📞', safe: '✓',
};

function showReceivedMessage(message) {
  if (!message || !message.id || receivedMessageIds.has(message.id)) return;
  receivedMessageIds.add(message.id);
  const item = document.createElement('article');
  item.className = 'receiver-message';
  const heading = document.createElement('h2');
  heading.textContent = message.scenario_title;
  const body = document.createElement('p');
  body.lang = message.native_language;
  body.textContent = message.text;
  const pictograms = document.createElement('div');
  pictograms.className = 'receiver-pictograms';
  pictograms.setAttribute('aria-label', 'Visual alert sequence');
  for (const icon of message.visual_steps) {
    const symbol = document.createElement('span');
    symbol.setAttribute('role', 'img');
    symbol.setAttribute('aria-label', icon.replaceAll('_', ' '));
    symbol.textContent = iconGlyphs[icon] || '⚠️';
    pictograms.append(symbol);
  }
  const time = document.createElement('time');
  time.dateTime = message.received_at;
  time.textContent = new Date(message.received_at).toLocaleString();
  item.prepend(heading, body, pictograms, time);
  messageList.querySelector('.receiver-empty')?.remove();
  messageList.prepend(item);

}

async function pollInbox() {
  if (polling || !deviceId) return;
  polling = true;
  try {
    const response = await fetch(`/api/inbox?device_id=${encodeURIComponent(deviceId)}`, { cache: 'no-store' });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not read device inbox');
    for (const message of result.messages) showReceivedMessage(message);
    if (result.messages.length) status.textContent = `${result.messages.length} new alert${result.messages.length === 1 ? '' : 's'} received.`;
  } catch (error) {
    status.textContent = error.message;
  } finally {
    polling = false;
  }
}

async function startReceiver() {
  if (!deviceId) {
    status.textContent = 'No device selected. Open the receiver link from a device row.';
    return;
  }
  try {
    const stateResponse = await fetch('/api/state', { cache: 'no-store' });
    const state = await stateResponse.json();
    const device = state.devices.find((entry) => entry.id === deviceId);
    if (!device) throw new Error('This device is not in the current simulation.');
    document.querySelector('#receiver-title').textContent = device.id;
    document.documentElement.lang = device.native_language;
    status.textContent = `${device.native_language_name} receiver. Enable push once to receive alerts while this page is closed.`;
    if (!window.isSecureContext) {
      status.textContent = 'Inbox active. Browser push requires HTTPS; this page can still show alerts while open.';
    } else if (!('PushManager' in window)) {
      status.textContent = 'Inbox active. This browser does not support Web Push; use SMS delivery instead.';
    }
    if ('serviceWorker' in navigator && 'PushManager' in window && window.isSecureContext) {
      const registration = await navigator.serviceWorker.getRegistration('/');
      const savedSubscription = await registration?.pushManager.getSubscription();
      if (savedSubscription && Notification.permission === 'granted') {
        serviceWorkerRegistration = registration;
        pushButton.textContent = 'Push notifications enabled';
        pushButton.disabled = true;
        status.textContent = 'Push enabled. New alerts can appear while this page is closed.';
      }
    }
    await pollInbox();
    window.setInterval(pollInbox, 15000);
  } catch (error) {
    status.textContent = error.message;
  }
}

async function enablePushNotifications() {
  try {
    if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) {
      throw new Error('This browser does not support push notifications. Use Twilio SMS instead.');
    }
    if (!window.isSecureContext) {
      throw new Error('Browser push requires HTTPS. Open the trusted HTTPS address on this Android device.');
    }
    const permission = await Notification.requestPermission();
    if (permission !== 'granted') throw new Error('Notification permission was not granted.');

    const configResponse = await fetch('/api/push/config', { cache: 'no-store' });
    const config = await configResponse.json();
    if (!config.enabled || !config.public_key) {
      throw new Error('Server push is not configured. Add VAPID keys and VAPID_SUBJECT to root .env.');
    }

    serviceWorkerRegistration = await navigator.serviceWorker.register('/service-worker.js');
    await navigator.serviceWorker.ready;
    let subscription = await serviceWorkerRegistration.pushManager.getSubscription();
    if (!subscription) {
      const publicKey = config.public_key.replace(/-/g, '+').replace(/_/g, '/');
      const applicationServerKey = Uint8Array.from(
        atob(publicKey + '='.repeat((4 - publicKey.length % 4) % 4)),
        (character) => character.charCodeAt(0),
      );
      subscription = await serviceWorkerRegistration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey,
      });
    }

    const result = await fetch('/api/push/subscribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ device_id: deviceId, subscription: subscription.toJSON() }),
    });
    const response = await result.json();
    if (!result.ok) throw new Error(response.error || 'Could not register this device for push.');
    pushButton.textContent = 'Push notifications enabled';
    pushButton.disabled = true;
    status.textContent = 'Push enabled. New alerts can appear while this page is closed.';
  } catch (error) {
    status.textContent = error.message;
  }
}

navigator.serviceWorker?.addEventListener('message', (event) => {
  if (event.data?.type === 'relay-alert') showReceivedMessage(event.data.message);
});

pushButton.addEventListener('click', enablePushNotifications);

startReceiver();
