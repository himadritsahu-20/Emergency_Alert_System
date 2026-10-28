const canvas = document.querySelector('#map-canvas');
const context = canvas.getContext('2d');
const sourceSelect = document.querySelector('#source-select');
const languageSelect = document.querySelector('#native-language-select');
const messageInput = document.querySelector('#alert-message');
const placeButton = document.querySelector('#place-button');
const mapStage = document.querySelector('#map-stage');
const statusMessage = document.querySelector('#status-message');
const deliveryResults = document.querySelector('#delivery-results');
const colors = { bluetooth: '#4f8d37', '2g': '#dc8142', '3g': '#4d84c3', 'no route': '#a7584a', failed: '#b94b47' };

let simulationState;
let deliveries = [];
let placingDevice = false;
let mapZoom = 1;
let mapCenter = null;
let mapDrag = null;
let suppressMapClick = false;

async function request(path, body) {
  const response = await fetch(path, body ? {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  } : {});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Request failed');
  return result;
}

function setStatus(message, isError = false) {
  statusMessage.textContent = message;
  statusMessage.classList.toggle('error', isError);
}

function updateDeviceControls() {
  const selected = sourceSelect.value;
  sourceSelect.replaceChildren();
  for (const device of simulationState.devices) {
    const option = document.createElement('option');
    option.value = device.id;
    option.textContent = device.id;
    sourceSelect.append(option);
  }
  if (simulationState.devices.some((device) => device.id === selected)) sourceSelect.value = selected;
  const language = simulationState.devices.find((device) => device.id === sourceSelect.value)?.native_language;
  const selectedDevice = simulationState.devices.find((device) => device.id === sourceSelect.value);
  languageSelect.replaceChildren();
  for (const [code, name] of Object.entries(simulationState.native_languages)) {
    const option = document.createElement('option');
    option.value = code;
    option.textContent = name;
    languageSelect.append(option);
  }
  languageSelect.value = language || 'hi';
  document.querySelector('#phone-number').value = selectedDevice?.phone_number || '';
  document.querySelector('#device-count').textContent = `${simulationState.devices.length} DEVICES`;
}

function renderNetworkState() {
  const generations = simulationState.available_cellular_generations;
  document.querySelector('#toggle-2g').checked = generations.includes('2g');
  document.querySelector('#toggle-3g').checked = generations.includes('3g');
  const labels = generations.map((generation) => generation.toUpperCase());
  document.querySelector('#coverage-readout').textContent = labels.length ? `${labels.join(' + ')} ACTIVE` : 'CELLULAR OFFLINE';
  document.querySelector('.readout-dot').style.background = labels.length ? 'var(--green-deep)' : 'var(--orange)';
}

function getMapViewport() {
  const viewWidth = simulationState.width_m / mapZoom;
  const viewHeight = simulationState.height_m / mapZoom;
  if (!mapCenter) mapCenter = { x: simulationState.width_m / 2, y: simulationState.height_m / 2 };
  if (viewWidth <= simulationState.width_m) {
    mapCenter.x = Math.max(viewWidth / 2, Math.min(simulationState.width_m - viewWidth / 2, mapCenter.x));
  } else {
    mapCenter.x = simulationState.width_m / 2;
  }
  if (viewHeight <= simulationState.height_m) {
    mapCenter.y = Math.max(viewHeight / 2, Math.min(simulationState.height_m - viewHeight / 2, mapCenter.y));
  } else {
    mapCenter.y = simulationState.height_m / 2;
  }
  return {
    viewWidth,
    viewHeight,
    originX: mapCenter.x - viewWidth / 2,
    originY: mapCenter.y - viewHeight / 2,
  };
}

function drawMap() {
  if (!simulationState) return;
  const width = canvas.width;
  const height = canvas.height;
  const { viewWidth, viewHeight, originX, originY } = getMapViewport();
  const scaleX = width / viewWidth;
  const scaleY = height / viewHeight;
  const pixelScale = width / canvas.getBoundingClientRect().width;
  const gridStep = viewWidth > 1200 ? 200 : viewWidth > 600 ? 100 : viewWidth > 250 ? 50 : 25;
  context.clearRect(0, 0, width, height);
  context.fillStyle = '#f9fbf6';
  context.fillRect(0, 0, width, height);

  context.lineWidth = pixelScale;
  for (let x = Math.floor(originX / gridStep) * gridStep; x <= originX + viewWidth; x += gridStep) {
    context.beginPath();
    context.strokeStyle = x % (gridStep * 2) === 0 ? '#e3eae2' : '#edf1eb';
    context.moveTo((x - originX) * scaleX, 0);
    context.lineTo((x - originX) * scaleX, height);
    context.stroke();
  }
  for (let y = Math.floor(originY / gridStep) * gridStep; y <= originY + viewHeight; y += gridStep) {
    context.beginPath();
    context.strokeStyle = y % (gridStep * 2) === 0 ? '#e3eae2' : '#edf1eb';
    context.moveTo(0, (y - originY) * scaleY);
    context.lineTo(width, (y - originY) * scaleY);
    context.stroke();
  }

  const devicesById = new Map(simulationState.devices.map((device) => [device.id, device]));
  for (const delivery of deliveries) {
    const sender = devicesById.get(sourceSelect.value);
    const receiver = devicesById.get(delivery.receiver_id);
    if (!sender || !receiver) continue;
    const start = { x: (sender.x - originX) * scaleX, y: (sender.y - originY) * scaleY };
    const end = { x: (receiver.x - originX) * scaleX, y: (receiver.y - originY) * scaleY };
    context.beginPath();
    context.setLineDash(delivery.status === 'acknowledged' ? [] : [5, 6]);
    context.moveTo(start.x, start.y);
    context.lineTo(end.x, end.y);
    context.strokeStyle = delivery.status === 'failed' ? colors.failed : colors[delivery.channel] || colors['no route'];
    context.lineWidth = (delivery.channel === 'bluetooth' ? 3 : 2) * pixelScale;
    context.globalAlpha = delivery.status === 'acknowledged' ? .8 : .55;
    context.stroke();
    context.setLineDash([]);
    context.globalAlpha = 1;
  }

  for (const device of simulationState.devices) {
    const x = (device.x - originX) * scaleX;
    const y = (device.y - originY) * scaleY;
    const selected = device.id === sourceSelect.value;
    const result = deliveries.find((delivery) => delivery.receiver_id === device.id);
    const fill = selected ? '#b7ef56' : result
      ? result.status === 'failed' ? colors.failed : colors[result.channel] || colors['no route']
      : '#fff';
    context.beginPath();
    context.arc(x, y, (selected ? 10 : 8) * pixelScale, 0, Math.PI * 2);
    context.fillStyle = fill;
    context.fill();
    context.lineWidth = (selected ? 3 : 2) * pixelScale;
    context.strokeStyle = selected ? '#142b35' : '#58716e';
    context.stroke();
    if (selected) {
      context.beginPath();
      context.arc(x, y, 16 * pixelScale, 0, Math.PI * 2);
      context.strokeStyle = 'rgba(20,43,53,.23)';
      context.lineWidth = pixelScale;
      context.stroke();
    }

    context.font = `600 ${12 * pixelScale}px Manrope, Trebuchet MS, sans-serif`;
    context.textBaseline = 'middle';
    const label = device.id;
    const labelWidth = context.measureText(label).width;
    const labelOffset = 14 * pixelScale;
    const labelX = x + labelOffset + labelWidth > width - 8 * pixelScale
      ? x - labelWidth - 15 * pixelScale
      : x + labelOffset;
    context.fillStyle = '#30484b';
    const labelY = selected ? y - 18 * pixelScale : y + 18 * pixelScale;
    context.fillText(label, labelX, labelY);
  }

  context.fillStyle = '#778782';
  context.font = `${10 * pixelScale}px "DM Mono", Courier New, monospace`;
  const labelStep = gridStep * 2;
  for (let x = Math.ceil(originX / labelStep) * labelStep; x <= originX + viewWidth; x += labelStep) {
    context.fillText(String(x), (x - originX) * scaleX + 4 * pixelScale, height - 8 * pixelScale);
  }
  document.querySelector('#zoom-readout').textContent = `${mapZoom}x · ${Math.round(viewWidth)} m view`;
  document.querySelector('#coordinate-readout').textContent = `VIEW ${Math.round(viewWidth)} x ${Math.round(viewHeight)} M`;
  canvas.classList.toggle('is-zoomed', mapZoom > 1);
}

function renderResults(report) {
  deliveries = report.deliveries;
  const acknowledged = deliveries.filter((delivery) => delivery.acknowledged).length;
  const retryCount = deliveries.reduce((total, delivery) => total + delivery.retries, 0);
  document.querySelector('#delivery-count').textContent = `${acknowledged} / ${deliveries.length} ACK`;
  document.querySelector('#report-summary').textContent = `${report.payload_bytes} byte alert · ${retryCount} resends · ${Math.round(report.failure_rate * 100)}% packet failure · sent from ${report.sender_id}`;
  deliveryResults.replaceChildren();

    if (!deliveries.length) {
    const empty = document.createElement('div');
    empty.className = 'empty-report';
    empty.textContent = 'No recipient devices on the map.';
    deliveryResults.append(empty);
      document.querySelector('#received-preview').innerHTML = '<div class="preview-empty">No recipient devices to preview.</div>';
  }

  for (const [index, delivery] of deliveries.entries()) {
    const row = document.createElement('div');
    row.className = 'result-row';
    row.style.animationDelay = `${index * 35}ms`;
    const name = document.createElement('span');
    name.className = 'receiver-name';
    name.textContent = delivery.receiver_id;
    const deviceCell = document.createElement('div');
    deviceCell.className = 'receiver-cell';
    const language = document.createElement('small');
    language.className = 'receiver-language';
    language.textContent = delivery.native_language_name || 'Language unavailable';
    deviceCell.append(name, language);
    const route = document.createElement('span');
    route.className = `route-chip ${delivery.status === 'no route' ? 'route-none' : `route-${delivery.channel}`}`;
    route.textContent = delivery.status === 'no route' ? 'NO ROUTE' : delivery.channel.toUpperCase();
    const state = document.createElement('span');
    state.className = `delivery-state state-${delivery.status === 'acknowledged' ? 'ack' : delivery.status === 'failed' ? 'failed' : 'offline'}`;
    state.textContent = delivery.status === 'acknowledged'
      ? `ACK · ${delivery.attempts}x`
      : delivery.status === 'failed' ? `FAILED · ${delivery.attempts}x` : 'NO ACK';
    const distance = document.createElement('span');
    distance.className = 'result-distance';
    distance.textContent = delivery.distance_m === undefined ? '--' : `${delivery.distance_m} m`;
    const time = document.createElement('span');
    time.className = 'result-time';
    time.textContent = delivery.estimated_seconds === undefined ? 'offline' : formatTime(delivery.estimated_seconds);
    const inspect = document.createElement('button');
    inspect.type = 'button';
    inspect.className = 'inspect-button';
    inspect.textContent = 'View';
    inspect.disabled = delivery.status === 'no route';
    inspect.setAttribute('aria-label', `View message received by ${delivery.receiver_id}`);
    inspect.addEventListener('click', () => renderReceivedPreview(delivery, report.alert_message));
    const receiverLink = document.createElement('a');
    receiverLink.className = 'receiver-link';
    receiverLink.textContent = 'Open';
    receiverLink.href = `/receiver.html?device_id=${encodeURIComponent(delivery.receiver_id)}`;
    receiverLink.target = '_blank';
    receiverLink.rel = 'noopener';
    receiverLink.setAttribute('aria-label', `Open Android receiver for ${delivery.receiver_id}`);
    receiverLink.setAttribute('aria-disabled', String(delivery.status !== 'acknowledged'));
    if (delivery.status !== 'acknowledged') receiverLink.removeAttribute('href');
    row.append(deviceCell, route, state, distance, time, inspect, receiverLink);
    deliveryResults.append(row);
  }
  const firstReachable = deliveries.find((delivery) => delivery.status !== 'no route');
  if (firstReachable) renderReceivedPreview(firstReachable, report.alert_message);
    else document.querySelector('#received-preview').innerHTML = '<div class="preview-empty">No message reached a recipient.</div>';
  drawMap();
}

const visualIconMarkup = {
  warning: '<path d="M32 7 59 55H5L32 7Z"/><path d="M32 22v15m0 8v1"/>',
  flood: '<path d="M7 22 22 10l14 12v18H7V22Z"/><path d="M4 47c6-6 12 6 18 0s12 6 18 0 12 6 18 0"/><path d="M4 56c6-6 12 6 18 0s12 6 18 0 12 6 18 0"/>',
  fire: '<path d="M33 7c3 14-8 15-3 25 3-7 9-8 10-16 11 10 17 20 12 31-4 9-13 13-23 11-12-2-19-12-16-23 2-8 8-13 11-19 1 8 4 11 6 12 4-7 4-13 3-21Z"/>',
  storm: '<path d="M17 39h31a11 11 0 0 0 0-22 16 16 0 0 0-30-2 12 12 0 0 0-1 24Z"/><path d="m31 42-7 11h9l-3 9 12-15h-9l4-5"/>',
  earthquake: '<path d="m8 30 24-20 24 20v27H8V30Z"/><path d="m34 24-7 11 8 3-7 13 14-15-8-3 4-9"/>',
  evacuate: '<circle cx="20" cy="17" r="6"/><path d="m18 25-6 15 10 5 5 12m-11-16 13-2 8 8m-17-7-8 12m23-25h20m-9-9 9 9-9 9"/>',
  high_ground: '<path d="m5 54 18-27 9 13 8-12 19 26H5Z"/><path d="M32 38V9m-9 9 9-9 9 9"/>',
  shelter: '<path d="m5 30 27-22 27 22M12 27v29h40V27M25 56V39h14v17"/>',
  medical: '<path d="M24 8h16v16h16v16H40v16H24V40H8V24h16V8Z"/>',
  no_cross: '<path d="m10 20 8-8h28l8 8v24l-8 8H18l-8-8V20Z"/><path d="m18 18 28 28"/>',
  call: '<path d="M18 8h28a7 7 0 0 1 7 7v34a7 7 0 0 1-7 7H18a7 7 0 0 1-7-7V15a7 7 0 0 1 7-7Z"/><path d="M23 17h18M27 48h10"/>',
  safe: '<path d="M32 6 54 14v17c0 14-9 23-22 29C19 54 10 45 10 31V14L32 6Z"/><path d="m21 32 8 8 15-17"/>',
};

function renderReceivedPreview(delivery, originalMessage) {
  const preview = document.querySelector('#received-preview');
  preview.replaceChildren();
  const heading = document.createElement('div');
  heading.className = 'preview-heading';
  const title = document.createElement('h3');
  title.textContent = `${delivery.receiver_id} · ${delivery.native_language_name}`;
  const badge = document.createElement('span');
  badge.className = `preview-badge ${delivery.recognized_scenario ? 'is-localized' : 'is-fallback'}`;
  badge.textContent = delivery.recognized_scenario
    ? `${delivery.scenario_name.toUpperCase()} SCENARIO`
    : 'GENERIC SCENARIO';
  heading.append(title, badge);
  const text = document.createElement('p');
  text.className = 'received-text';
  text.lang = delivery.native_language;
  text.textContent = delivery.received_text || originalMessage || 'No text message was delivered.';
  const visual = document.createElement('div');
  visual.className = 'visual-message';
  visual.setAttribute('aria-label', 'Visual alert sequence');
  for (const [index, icon] of (delivery.visual_steps || []).entries()) {
    const step = document.createElement('div');
    step.className = 'visual-step';
    const picture = document.createElement('span');
    picture.className = `visual-icon icon-${icon}`;
    picture.setAttribute('role', 'img');
    picture.setAttribute('aria-label', icon.replaceAll('_', ' '));
    picture.innerHTML = `<svg viewBox="0 0 64 64" aria-hidden="true">${visualIconMarkup[icon] || visualIconMarkup.warning}</svg>`;
    step.append(picture);
    visual.append(step);
    if (index < delivery.visual_steps.length - 1) {
      const arrow = document.createElement('span');
      arrow.className = 'visual-arrow';
      arrow.setAttribute('aria-hidden', 'true');
      arrow.textContent = '→';
      visual.append(arrow);
    }
  }
  preview.append(heading, text, visual);
  const smsStatus = document.createElement('p');
  smsStatus.className = 'sms-delivery-status';
  smsStatus.textContent = delivery.sms_status === 'not requested'
    ? 'Real SMS not requested.'
    : `SMS: ${delivery.sms_status}`;
  const pushStatus = document.createElement('p');
  pushStatus.className = 'sms-delivery-status';
  pushStatus.textContent = `Android push: ${delivery.push_status || 'not subscribed'}`;
  preview.append(smsStatus, pushStatus);
}

function formatTime(seconds) {
  if (seconds < 1) return `${Math.max(1, Math.round(seconds * 1000))} ms`;
  return `${seconds.toFixed(seconds < 10 ? 2 : 1)} s`;
}

async function refreshState() {
  simulationState = await request('/api/state');
  updateDeviceControls();
  renderNetworkState();
  document.querySelector('#coordinate-readout').textContent = `MAP ${simulationState.width_m} x ${simulationState.height_m} M`;
  drawMap();
}

async function updateNetworks() {
  const active = [];
  if (document.querySelector('#toggle-2g').checked) active.push('2g');
  if (document.querySelector('#toggle-3g').checked) active.push('3g');
  try {
    simulationState = await request('/api/networks', { available_cellular_generations: active });
    renderNetworkState();
    deliveries = [];
    document.querySelector('#delivery-count').textContent = '-- / --';
    document.querySelector('#report-summary').textContent = 'Coverage changed. Run the broadcast again.';
    deliveryResults.innerHTML = '<div class="empty-report"><span class="empty-mark" aria-hidden="true">&#8594;</span><span>Delivery results will appear here.</span></div>';
    drawMap();
    setStatus('Cellular coverage updated.');
  } catch (error) {
    setStatus(error.message, true);
  }
}

document.querySelector('#send-button').addEventListener('click', async () => {
  const payloadBytes = new TextEncoder().encode(messageInput.value).length;
  try {
    const report = await request('/api/deliver', {
      sender_id: sourceSelect.value,
      payload_bytes: payloadBytes,
      alert_message: messageInput.value,
      failure_rate: Number(document.querySelector('#failure-rate').value) / 100,
      max_retries: Number(document.querySelector('#retry-limit').value),
      send_sms: document.querySelector('#send-real-sms').checked,
    });
    renderResults(report);
    const acknowledged = report.deliveries.filter((delivery) => delivery.acknowledged).length;
    const failed = report.deliveries.filter((delivery) => delivery.status === 'failed').length;
    const summary = `${acknowledged} acknowledged, ${failed} failed after retries, ${report.deliveries.length - acknowledged - failed} without route.`;
    const smsAccepted = report.deliveries.filter((delivery) => delivery.sms_status === 'accepted by gateway').length;
    const smsFailed = report.deliveries.filter((delivery) => delivery.sms_status && delivery.sms_status !== 'not requested' && delivery.sms_status !== 'accepted by gateway' && !delivery.sms_status.startsWith('not sent')).length;
    const pushSent = report.deliveries.filter((delivery) => delivery.push_status?.startsWith('sent to')).length;
    setStatus(document.querySelector('#send-real-sms').checked
      ? `${summary} SMS accepted: ${smsAccepted}; SMS issues: ${smsFailed}; Android push sent: ${pushSent}.`
      : `${summary} Android push sent: ${pushSent}.`);
  } catch (error) {
    setStatus(error.message, true);
  }
});

sourceSelect.addEventListener('change', () => {
  const selectedDevice = simulationState.devices.find((device) => device.id === sourceSelect.value);
  if (selectedDevice) languageSelect.value = selectedDevice.native_language;
  document.querySelector('#phone-number').value = selectedDevice?.phone_number || '';
  deliveries = [];
  document.querySelector('#delivery-count').textContent = '-- / --';
  document.querySelector('#report-summary').textContent = 'Source changed. Run the broadcast again.';
  deliveryResults.innerHTML = '<div class="empty-report"><span class="empty-mark" aria-hidden="true">&#8594;</span><span>Delivery results will appear here.</span></div>';
    document.querySelector('#received-preview').innerHTML = '<div class="preview-empty">Run the broadcast again to refresh received messages.</div>';
  drawMap();
});

document.querySelector('#save-phone').addEventListener('click', async () => {
  try {
    simulationState = await request('/api/devices/phone', {
      device_id: sourceSelect.value,
      phone_number: document.querySelector('#phone-number').value.trim(),
    });
    updateDeviceControls();
    setStatus('Device phone number saved.');
  } catch (error) {
    setStatus(error.message, true);
  }
});

document.querySelector('#preview-alert').addEventListener('click', async () => {
  try {
    const result = await request('/api/preview', {
      alert_message: messageInput.value,
      native_language: languageSelect.value,
    });
    const preview = document.querySelector('#scenario-preview');
    preview.replaceChildren();
    const title = document.createElement('strong');
    title.textContent = `${result.scenario_name} · ${result.native_language_name}`;
    const text = document.createElement('p');
    text.lang = result.native_language;
    text.textContent = result.short_text;
    const evidence = document.createElement('small');
    evidence.textContent = result.recognized
      ? `Matched: ${result.matched_terms.join(', ')}`
      : 'No scenario keywords matched; generic emergency wording shown.';
    preview.append(title, text, evidence);
    setStatus('Scenario preview ready.');
  } catch (error) {
    setStatus(error.message, true);
  }
});

languageSelect.addEventListener('change', async () => {
  try {
    simulationState = await request('/api/devices/language', {
      device_id: sourceSelect.value,
      native_language: languageSelect.value,
    });
    updateDeviceControls();
    deliveries = [];
    document.querySelector('#delivery-count').textContent = '-- / --';
    document.querySelector('#report-summary').textContent = 'Device language changed. Run the broadcast again.';
    deliveryResults.innerHTML = '<div class="empty-report"><span class="empty-mark" aria-hidden="true">&#8594;</span><span>Delivery results will appear here.</span></div>';
    document.querySelector('#received-preview').innerHTML = '<div class="preview-empty">Run the broadcast again to refresh received messages.</div>';
    drawMap();
    setStatus(`${sourceSelect.value} language set to ${simulationState.native_languages[languageSelect.value]}.`);
  } catch (error) {
    setStatus(error.message, true);
  }
});

messageInput.addEventListener('input', () => {
  document.querySelector('#byte-count').textContent = new TextEncoder().encode(messageInput.value).length;
});

document.querySelector('#toggle-2g').addEventListener('change', updateNetworks);
document.querySelector('#toggle-3g').addEventListener('change', updateNetworks);

placeButton.addEventListener('click', () => {
  placingDevice = !placingDevice;
  placeButton.setAttribute('aria-pressed', String(placingDevice));
  mapStage.classList.toggle('is-placing', placingDevice);
  setStatus(placingDevice ? 'Click an open point on the map to place a device.' : 'Device placement cancelled.');
});

canvas.addEventListener('click', async (event) => {
  if (suppressMapClick) {
    suppressMapClick = false;
    return;
  }
  const bounds = canvas.getBoundingClientRect();
  const { viewWidth, viewHeight, originX, originY } = getMapViewport();
  const x = Math.round(originX + (event.clientX - bounds.left) / bounds.width * viewWidth);
  const y = Math.round(originY + (event.clientY - bounds.top) / bounds.height * viewHeight);
  if (placingDevice) {
    try {
      const nextId = `Device ${simulationState.devices.length + 1}`;
      const result = await request('/api/devices', {
        id: nextId,
        x,
        y,
        native_language: languageSelect.value,
      });
      simulationState = result.state;
      updateDeviceControls();
      sourceSelect.value = nextId;
      languageSelect.value = result.device.native_language;
      deliveries = [];
      placingDevice = false;
      placeButton.setAttribute('aria-pressed', 'false');
      mapStage.classList.remove('is-placing');
      drawMap();
      setStatus(`${nextId} placed at ${x}, ${y} m and set as alert source.`);
    } catch (error) {
      setStatus(error.message, true);
    }
    return;
  }

  const clickedDevice = simulationState.devices.find((device) => {
    const dx = device.x - x;
    const dy = device.y - y;
    return Math.hypot(dx * canvas.width / bounds.width / viewWidth, dy * canvas.height / bounds.height / viewHeight) < 22;
  });
  if (clickedDevice) {
    sourceSelect.value = clickedDevice.id;
    sourceSelect.dispatchEvent(new Event('change'));
  }
});

canvas.addEventListener('pointerdown', (event) => {
  if (event.button !== 0 || placingDevice || mapZoom <= 1) return;
  getMapViewport();
  mapDrag = { x: event.clientX, y: event.clientY, centerX: mapCenter.x, centerY: mapCenter.y };
  suppressMapClick = false;
  canvas.setPointerCapture(event.pointerId);
});

canvas.addEventListener('pointermove', (event) => {
  if (!mapDrag) return;
  const bounds = canvas.getBoundingClientRect();
  const { viewWidth, viewHeight } = getMapViewport();
  const deltaX = event.clientX - mapDrag.x;
  const deltaY = event.clientY - mapDrag.y;
  if (Math.hypot(deltaX, deltaY) > 3) suppressMapClick = true;
  mapCenter = {
    x: mapDrag.centerX - deltaX / bounds.width * viewWidth,
    y: mapDrag.centerY - deltaY / bounds.height * viewHeight,
  };
  canvas.classList.add('is-panning');
  drawMap();
});

function stopMapDrag() {
  mapDrag = null;
  canvas.classList.remove('is-panning');
}

canvas.addEventListener('pointerup', stopMapDrag);
canvas.addEventListener('pointercancel', stopMapDrag);

document.querySelector('#reset-button').addEventListener('click', async () => {
  try {
    simulationState = await request('/api/reset', {});
    deliveries = [];
    updateDeviceControls();
    renderNetworkState();
    document.querySelector('#delivery-count').textContent = '-- / --';
    document.querySelector('#report-summary').textContent = 'Run a broadcast to see per-device routes.';
    deliveryResults.innerHTML = '<div class="empty-report"><span class="empty-mark" aria-hidden="true">&#8594;</span><span>Delivery results will appear here.</span></div>';
    placingDevice = false;
    placeButton.setAttribute('aria-pressed', 'false');
    mapStage.classList.remove('is-placing');
    drawMap();
    setStatus('Scenario reset.');
  } catch (error) {
    setStatus(error.message, true);
  }
});

document.querySelector('#byte-count').textContent = new TextEncoder().encode(messageInput.value).length;
document.querySelector('#failure-rate').addEventListener('input', (event) => {
  document.querySelector('#failure-rate-value').value = `${event.target.value}%`;
});
document.querySelector('#zoom-level').addEventListener('input', (event) => {
  mapZoom = Number(event.target.value);
  drawMap();
});
document.querySelector('#zoom-out').addEventListener('click', () => {
  const zoomLevel = document.querySelector('#zoom-level');
  zoomLevel.value = Math.max(Number(zoomLevel.min), Number(zoomLevel.value) - Number(zoomLevel.step));
  zoomLevel.dispatchEvent(new Event('input'));
});
document.querySelector('#zoom-in').addEventListener('click', () => {
  const zoomLevel = document.querySelector('#zoom-level');
  zoomLevel.value = Math.min(Number(zoomLevel.max), Number(zoomLevel.value) + Number(zoomLevel.step));
  zoomLevel.dispatchEvent(new Event('input'));
});
window.addEventListener('resize', drawMap);
refreshState().catch((error) => setStatus(error.message, true));