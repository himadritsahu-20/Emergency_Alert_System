self.addEventListener('push', (event) => {
  let message;
  try {
    message = event.data ? event.data.json() : {};
  } catch {
    message = { title: 'Emergency alert', body: event.data?.text() || '' };
  }
  const title = message.title || 'Emergency alert';
  const options = {
    body: message.body || message.text || 'Open Relay Lab to view this alert.',
    tag: message.id ? `relay-${message.id}` : 'relay-emergency-alert',
    renotify: true,
    requireInteraction: true,
    vibrate: [250, 120, 250],
    data: {
      url: message.url || '/',
      message,
    },
  };
  event.waitUntil((async () => {
    await self.registration.showNotification(title, options);
    const clients = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const client of clients) client.postMessage({ type: 'relay-alert', message });
  })());
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = new URL(event.notification.data?.url || '/', self.location.origin).href;
  event.waitUntil((async () => {
    const clients = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    const existing = clients.find((client) => new URL(client.url).origin === self.location.origin);
    if (existing) {
      if (existing.url !== target) await existing.navigate(target);
      existing.postMessage({ type: 'relay-alert', message: event.notification.data?.message });
      await existing.focus();
    } else {
      await self.clients.openWindow(target);
    }
  })());
});
