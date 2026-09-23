const $ = (selector) => document.querySelector(selector);
const preview = $('#preview');
const ACTIVE = new Set(['starting', 'running', 'stopping']);
let selectedSource = 'h3';
let studioStatus = null;
let busy = false;
let polling = false;
let online = false;
let sessionId = null;
let currentURL = null;
let thumbnailURL = null;
let revision = 0;
const playedURLs = new Set();

function isActive() { return ACTIVE.has(studioStatus?.state); }
function localError(message) { $('#videoNotice').textContent = message; }
function resetPlayback(id) {
  sessionId = id || null; currentURL = null; playedURLs.clear();
  preview.removeAttribute('src'); preview.load();
  $('#videoEmpty').classList.remove('hidden'); $('#videoNotice').textContent = '';
}
function playNextClip() {
  const segments = studioStatus?.media?.segments || [];
  const available = new Set(segments.map(segment => segment.url));
  for (const url of playedURLs) if (!available.has(url)) playedURLs.delete(url);
  const next = segments.find(segment => !playedURLs.has(segment.url));
  if (!next) { currentURL = null; return; }
  currentURL = next.url; playedURLs.add(next.url);
  preview.src = next.url; preview.load(); $('#videoEmpty').classList.add('hidden');
  preview.play().catch(() => localError('Press play to resume the preview.'));
}
function readiness() {
  const status = studioStatus || {};
  const caps = status.capabilities || {};
  const keys = status.credentials || {};
  let issue = '';
  if (!online) issue = 'Cannot reach the local server. Restart it to continue.';
  else if (!caps.ffmpeg) issue = 'Install FFmpeg to start the preview.';
  else if (selectedSource !== 'test_signal') {
    if (!caps.h3) issue = 'Install requirements.txt to generate video.';
    else if (!keys.REACTOR_API_KEY || !keys.XAI_API_KEY) issue = 'Add your API keys above.';
    else if (selectedSource === 'h3' && !status.show) issue = 'Choose a reference image.';
    else if (selectedSource === 'fast_h3' && !$('#premise').value.trim()) issue = 'Describe a scene to start FastH3.';
    else if (selectedSource === 'fast_h3' && $('#useStartingFrame').checked && !status.show) issue = 'Choose a starting image.';
  }
  $('#setupMessage').textContent = issue;
  return !issue;
}
function renderControls() {
  const locked = busy || isActive();
  const x = studioStatus?.x || {};
  const sending = ['connecting', 'sending'].includes(x.state);
  document.querySelectorAll('#destinationForm input, #saveDestination').forEach(node => { node.disabled = busy || !online || sending; });
  $('#removeDestination').disabled = busy || !online || sending || !x.configured;
  $('#sendToX').disabled = busy || !online || sending || !x.configured || studioStatus?.state !== 'running' || !studioStatus?.media?.segments?.length;
  $('#stopSending').disabled = busy || !online || !sending;
  $('#streamKeyState').textContent = x.hasStreamKey ? 'Saved' : 'Missing';
  $('#xStreamKey').placeholder = x.hasStreamKey ? 'Saved — leave blank to keep' : 'Paste from X Live Studio';
  $('#destinationStatus').textContent = !online ? 'Local server unavailable. Check the running process before assuming transmission stopped.'
    : x.error || (x.state === 'sending' ? 'Sending video · verify reception in X Live Studio'
    : x.state === 'connecting' ? 'Connecting to X…' : x.configured ? 'Destination saved · not sending' : 'Add your X source to send video.');
  $('#generationSettings').hidden = selectedSource === 'test_signal';
  $('#imageLabel').textContent = selectedSource === 'fast_h3' ? 'Starting image (optional)' : 'Reference image';
  $('#showFile').setAttribute('aria-label', selectedSource === 'fast_h3' ? 'Choose starting image' : 'Choose reference image');
  $('#premise').placeholder = selectedSource === 'fast_h3'
    ? 'Describe the scene, camera movement, and sound.' : 'Describe what happens in your stream.';
  $('#startingFrameRow').hidden = selectedSource !== 'fast_h3';
  $('#usageNote').textContent = selectedSource !== 'test_signal'
    ? 'Uses Reactor and xAI credits until you stop.' : 'No API usage.';
  document.querySelectorAll('#model, #showFile, #premise, #useStartingFrame, #credentialsForm input, #saveKeys')
    .forEach(node => { node.disabled = locked || !online; });
  document.querySelectorAll('[data-clear-key]').forEach(node => {
    node.disabled = locked || !online || !studioStatus?.credentials?.[node.dataset.clearKey];
  });
  const ready = readiness();
  $('#start').disabled = locked || !ready;
  $('#stop').disabled = busy || !online || !isActive();
  $('#start').textContent = studioStatus?.state === 'starting' ? 'Starting…' : 'Start preview';
}
function render(status) {
  online = true; studioStatus = status;
  if (!$('#xServer').dataset.edited && document.activeElement !== $('#xServer')) $('#xServer').value = status.x?.serverUrl || '';
  if (sessionId !== (status.sessionId || null)) resetPlayback(status.sessionId);
  if (isActive()) { selectedSource = status.source; $('#model').value = selectedSource; }
  const keys = status.credentials || {};
  const missing = [];
  for (const [key, label, id] of [['XAI_API_KEY', 'xAI', '#xaiKeyState'], ['REACTOR_API_KEY', 'Reactor', '#reactorKeyState']]) {
    $(id).textContent = keys[key] ? 'Saved · not verified' : 'Missing';
    if (!keys[key]) missing.push(label);
  }
  $('#credentialWarning').textContent = missing.length ? 'Add ' + missing.join(' and ') + ' API keys to generate video.' : '';
  if (status.show) $('#showMeta').textContent = 'Image ready';
  $('#emptyMessage').textContent = isActive() ? 'Preparing your preview…' : 'Your preview will appear here.';
  if (status.state === 'failed') localError('Generation stopped unexpectedly. Check the server logs, then try again.');
  if (selectedSource === status.source && !currentURL && status.media?.segments?.length) playNextClip();
  renderControls();
}
async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({ error: 'Invalid server response.' }));
  if (!response.ok) throw new Error(data.error || 'Request failed.');
  return data;
}
async function refresh() {
  if (polling || busy) return;
  polling = true; const observed = revision;
  try { const status = await api('/api/status'); if (observed === revision) render(status); }
  catch { if (observed === revision) { online = false; renderControls(); } }
  finally { polling = false; }
}
async function mutate(path, body) {
  if (busy) return;
  revision++; busy = true; renderControls(); $('#videoNotice').textContent = '';
  try { render(await api(path, {method:'POST', headers:{'Content-Type':'application/json','X-Studio-Control':'1'}, body:JSON.stringify(body)})); }
  catch (error) { localError(error.message); }
  finally { busy = false; renderControls(); }
}
$('#model').addEventListener('change', () => {
  selectedSource = $('#model').value;
  if (studioStatus?.source !== selectedSource) resetPlayback(studioStatus?.sessionId);
  renderControls();
});
$('#start').addEventListener('click', () => mutate('/api/start', {
  source:selectedSource, duration:null, confirmPaid:selectedSource !== 'test_signal', premise:$('#premise').value,
  useStartingFrame:selectedSource === 'fast_h3' && $('#useStartingFrame').checked
}));
$('#premise').addEventListener('input', renderControls);
$('#useStartingFrame').addEventListener('change', renderControls);
$('#stop').addEventListener('click', () => mutate('/api/stop', {}));
preview.addEventListener('ended', () => { currentURL = null; playNextClip(); });
preview.addEventListener('error', () => { currentURL = null; localError('Could not load a preview clip. Waiting for the next frame.'); });

async function saveCredentials(updates) {
  if (busy || isActive()) return;
  const feedback = $('#credentialFeedback');
  if (!Object.keys(updates).length) { feedback.textContent = 'Enter a key to save.'; return; }
  revision++; busy = true; renderControls(); feedback.classList.remove('error');
  try {
    render(await api('/api/credentials', {method:'POST', headers:{'Content-Type':'application/json','X-Studio-Control':'1'}, body:JSON.stringify(updates)}));
    feedback.textContent = 'Keys saved.';
  } catch (error) { feedback.textContent = error.message; feedback.classList.add('error'); }
  finally { $('#xaiKey').value = ''; $('#reactorKey').value = ''; busy = false; renderControls(); }
}
$('#credentialsForm').addEventListener('submit', event => {
  event.preventDefault(); const updates = {};
  if ($('#xaiKey').value.trim()) updates.XAI_API_KEY = $('#xaiKey').value.trim();
  if ($('#reactorKey').value.trim()) updates.REACTOR_API_KEY = $('#reactorKey').value.trim();
  saveCredentials(updates);
});
document.querySelectorAll('[data-clear-key]').forEach(button => button.addEventListener('click', () => saveCredentials({[button.dataset.clearKey]:''})));
$('#xServer').addEventListener('input', () => { $('#xServer').dataset.edited = 'true'; });
async function saveDestination(value) {
  if (busy) return;
  revision++; busy = true; renderControls(); $('#destinationFeedback').textContent = '';
  try {
    const status = await api('/api/x/destination', {method:'POST', headers:{'Content-Type':'application/json','X-Studio-Control':'1'}, body:JSON.stringify(value)});
    delete $('#xServer').dataset.edited; $('#xServer').value = status.x.serverUrl; render(status);
  } catch (error) { $('#destinationFeedback').textContent = error.message; }
  finally { $('#xStreamKey').value = ''; busy = false; renderControls(); }
}
$('#destinationForm').addEventListener('submit', event => {
  event.preventDefault(); saveDestination({serverUrl:$('#xServer').value.trim(), streamKey:$('#xStreamKey').value.trim()});
});
$('#removeDestination').addEventListener('click', () => saveDestination({serverUrl:'', streamKey:''}));
$('#sendToX').addEventListener('click', () => {
  if (window.confirm('Send the current preview to your saved X source? An active broadcast using this source may show it immediately.')) mutate('/api/x/start', {confirmSend:true});
});
$('#stopSending').addEventListener('click', () => mutate('/api/x/stop', {}));
$('#showFile').addEventListener('change', async event => {
  const file = event.target.files[0];
  if (!file || busy || isActive()) return;
  if (!['image/png','image/jpeg','image/webp'].includes(file.type) || file.size > 10 * 1024 * 1024) {
    localError('Choose a PNG, JPEG or WebP image under 10 MB.'); event.target.value = ''; return;
  }
  revision++; busy = true; renderControls();
  try {
    render(await api('/api/show', {method:'POST', headers:{'Content-Type':file.type,'X-Studio-Control':'1','X-Show-Premise':encodeURIComponent($('#premise').value)},body:file}));
    if (thumbnailURL) URL.revokeObjectURL(thumbnailURL);
    thumbnailURL = URL.createObjectURL(file);
    const image = document.createElement('img'); image.src = thumbnailURL; image.alt = 'Reference image';
    $('#showThumb').replaceChildren(image);
    if (selectedSource === 'fast_h3') $('#useStartingFrame').checked = true;
  } catch (error) { localError(error.message); }
  finally { busy = false; event.target.value = ''; renderControls(); }
});
window.addEventListener('beforeunload', () => { if (thumbnailURL) URL.revokeObjectURL(thumbnailURL); });
refresh(); window.setInterval(refresh, 1000);
