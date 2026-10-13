const $ = (selector) => document.querySelector(selector);
const preview = $('#preview');
const ACTIVE = new Set(['starting', 'running', 'stopping']);
let selectedSource = 'fast_h3';
let studioStatus = null;
let busy = false;
let polling = false;
let online = false;
let sessionId = null;
let currentURL = null;
let thumbnailURL = null;
let revision = 0;
let storyLoaded = false;
let storySessionId = null;
let creativeDraft = {images:[], audios:[]};
const directionFields = {camera:'cameraDirection', sound:'soundDirection', music:'musicDirection', dialogue:'dialogueDirection'};
const speechFields = ['dialogueLanguage', 'dialogueSpeaker', 'dialogueVoice', 'exactDialogue'];
const playedURLs = new Set();

function isActive() { return ACTIVE.has(studioStatus?.state); }
function localError(message) { $('#videoNotice').textContent = message; }
function resetPlayback(id) {
  sessionId = id || null; currentURL = null; playedURLs.clear();
  preview.removeAttribute('src'); preview.load();
  $('#videoEmpty').classList.remove('hidden'); $('#videoNotice').textContent = '';
}
function playNextClip() {
  if (isActive() && studioStatus?.source === 'fast_h3' && ['warming','rebuffering'].includes(studioStatus?.model?.buffer?.phase)) return;
  const segments = studioStatus?.media?.segments || [];
  const available = new Set(segments.map(segment => segment.url));
  for (const url of playedURLs) if (!available.has(url)) playedURLs.delete(url);
  if (!playedURLs.size && segments.length) {
    for (const segment of segments.slice(0,-1)) playedURLs.add(segment.url);
  }
  const next = playedURLs.size ? segments.find(segment => !playedURLs.has(segment.url)) : segments.at(-1);
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
  if (online && isActive()) { $('#setupMessage').textContent = ''; return true; }
  if (!online) issue = 'Cannot reach the local server. Restart it to continue.';
  else if (!caps.ffmpeg) issue = 'Install FFmpeg to start the preview.';
  else if (selectedSource !== 'test_signal') {
    if (!caps.h3) issue = 'Install requirements.txt to generate video.';
    else if (!keys.REACTOR_API_KEY || !keys.XAI_API_KEY) issue = 'Add your API keys above.';
    else if (selectedSource === 'h3' && !status.show) issue = 'Choose a reference image.';
    else if (selectedSource === 'fast_h3' && !$('#premise').value.trim()) issue = 'Write a story premise to start FastH3-Omni.';
    else if (selectedSource === 'fast_h3' && $('#useStartingFrame').checked && !status.show) issue = 'Choose a reference image.';
    else if (selectedSource === 'fast_h3') issue = creativeIssue();
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
  $('#sendToX').disabled = busy || !online || sending || !x.configured || studioStatus?.state !== 'running' || !studioStatus?.media?.segments?.length
    || ['warming','rebuffering'].includes(studioStatus?.model?.buffer?.phase);
  $('#stopSending').disabled = busy || !online || !sending;
  $('#streamKeyState').textContent = x.hasStreamKey ? 'Saved' : 'Missing';
  $('#xStreamKey').placeholder = x.hasStreamKey ? 'Saved — leave blank to keep' : 'Paste from X Live Studio';
  $('#destinationStatus').textContent = !online ? 'Local server unavailable. Check the running process before assuming transmission stopped.'
    : x.error || (x.state === 'sending' ? 'Sending video · verify reception in X Live Studio'
    : x.state === 'connecting' ? 'Connecting to X…' : x.configured ? 'Destination saved · not sending' : 'Add your X source to send video.');
  $('#generationSettings').hidden = selectedSource === 'test_signal';
  $('#imageLabel').textContent = selectedSource === 'fast_h3' ? 'Opening reference (optional)' : 'Reference image';
  $('#showFile').setAttribute('aria-label', 'Choose reference image');
  $('#sendChat').disabled = busy || !online || studioStatus?.state !== 'running' || studioStatus?.source === 'test_signal';
  $('#startingFrameRow').hidden = selectedSource !== 'fast_h3';
  $('#usageNote').textContent = selectedSource !== 'test_signal'
    ? 'Uses Reactor and xAI credits until you stop.' : 'No API usage.';
  document.querySelectorAll('#model, #generationSettings input, #generationSettings textarea, #generationSettings select, #generationSettings button, #credentialsForm input, #saveKeys')
    .forEach(node => { node.disabled = locked || !online; });
  const soundtrack = creativeDraft.audios.some(item => item.kind === 'soundtrack');
  $('#soundtrackNote').hidden = !soundtrack;
  for (const id of ['soundDirection', 'musicDirection', 'dialogueDirection']) $("#" + id).disabled = locked || !online || soundtrack;
  $('#dialogueMode').disabled = locked || !online || soundtrack;
  $('#speechSettings').hidden = soundtrack || $('#dialogueMode').value === 'off';
  $('#scriptedSpeechRow').hidden = $('#dialogueMode').value === 'exact';
  $('#exactSpeechRow').hidden = $('#dialogueMode').value !== 'exact';
  const words = Math.min(12, Math.max(1, Math.floor((Number($('#clipSeconds').value) - 1.5) * 2)));
  $('#speechBudget').textContent = `One speaker; up to ${words} words per scene. Finish the line with sentence punctuation.`;
  document.querySelectorAll('[data-clear-key]').forEach(node => {
    node.disabled = locked || !online || !studioStatus?.credentials?.[node.dataset.clearKey];
  });
  const ready = readiness();
  $('#start').disabled = locked || !ready;
  $('#saveStory').disabled = locked || !online || selectedSource !== 'fast_h3' || !$('#premise').value.trim();
  $('#stop').disabled = busy || !online || !isActive();
  $('#start').textContent = studioStatus?.state === 'starting' ? 'Starting…' : 'Start preview';
}
function render(status) {
  online = true; studioStatus = status;
  if (!$('#xServer').dataset.edited && document.activeElement !== $('#xServer')) $('#xServer').value = status.x?.serverUrl || '';
  if (sessionId !== (status.sessionId || null)) resetPlayback(status.sessionId);
  if (isActive()) { selectedSource = status.source; $('#model').value = selectedSource; }
  if (status.story && (!storyLoaded || storySessionId !== status.sessionId)) {
    $('#premise').value = status.story.premise;
    $('#visualStyle').value = status.story.style;
    $('#clipSeconds').value = String(status.story.clipSeconds);
    $('#bufferSeconds').value = String(status.story.bufferSeconds ?? 16);
    $('#continueScenes').checked = status.story.continueScenes;
    $('#useStartingFrame').checked = status.story.useStartingFrame;
    const creative = status.story.creative || {};
    for (const [field, id] of Object.entries(directionFields)) $('#' + id).value = creative[field] || '';
    $('#dialogueMode').value = creative.dialogueMode || 'auto';
    for (const field of speechFields) $('#' + field).value = creative[field] || (field === 'dialogueLanguage' ? 'English' : '');
    creativeDraft = {images:(creative.images || []).map(item => ({...item})), audios:(creative.audios || []).map(item => ({...item}))};
    renderReferenceCards();
    renderKeyframes(status.assets || [], creative.firstFrame || '', creative.lastFrame || '');
    storyLoaded = true; storySessionId = status.sessionId;
  }
  if (!storyLoaded) renderKeyframes(status.assets || []);
  $('#legacyReference').hidden = !status.show && !$('#useStartingFrame').checked;
  const keys = status.credentials || {};
  const missing = [];
  for (const [key, label, id] of [['XAI_API_KEY', 'xAI', '#xaiKeyState'], ['REACTOR_API_KEY', 'Reactor', '#reactorKeyState']]) {
    $(id).textContent = keys[key] ? 'Saved' : 'Missing';
    if (!keys[key]) missing.push(label);
  }
  $('#credentialWarning').textContent = missing.length ? 'Add ' + missing.join(' and ') + ' API keys to generate video.' : '';
  if (status.show) $('#showMeta').textContent = 'Image ready';
  $('#emptyMessage').textContent = isActive() ? 'Preparing your preview…' : 'Your preview will appear here.';
  if (status.state === 'failed') localError('Generation stopped unexpectedly. Check the server logs, then try again.');
  if (selectedSource === status.source && !currentURL && status.media?.segments?.length) playNextClip();
  const model = status.model || {};
  const buffer = model.buffer || {};
  const waiting = ['warming','rebuffering'].includes(buffer.phase);
  $('#bufferStatus').textContent = !isActive() || status.source === 'test_signal' ? ''
    : buffer.phase === 'warming' ? `Building playback reserve · ${buffer.readySeconds || 0}s of ${buffer.targetSeconds || 0}s ready`
    : buffer.phase === 'rebuffering' ? `Rebuilding playback reserve · ${buffer.readySeconds || 0}s of ${buffer.targetSeconds || 0}s ready`
    : buffer.targetSeconds ? `${buffer.readySeconds || 0}s ready ahead` : '';
  if (isActive() && typeof model.generationRate === 'number') {
    $('#bufferStatus').textContent += ` · Model generation ${model.generationRate.toFixed(2)}× realtime`;
    if (model.generationRate < 1) $('#bufferStatus').textContent += ' — slower than playback; the reserve will eventually run out.';
  }
  $('#bufferStatus').classList.toggle('warning', waiting || model.generationRate < 1);
  $('#currentPromptDetails').hidden = !model.currentPrompt;
  $('#currentPrompt').textContent = model.currentPrompt || '';
  const testSignal = isActive() && status.source === 'test_signal';
  $('#currentScene').textContent = testSignal ? 'Test signal' : model.currentScene
    || (isActive() ? (model.videoReceived ? 'Preparing the next scene…' : 'Writing the opening scene…') : 'Your story starts here.');
  $('#modelStatus').textContent = testSignal ? 'Synthetic video · silent audio'
    : !isActive() ? 'FastH3-Omni'
    : !model.connected ? 'Connecting to FastH3-Omni…'
    : model.videoReceived && model.audioReceived ? 'Video and audio arriving'
    : 'Generating video and audio…';
  const titles = model.comingUp?.length ? model.comingUp : ['No scenes queued yet.'];
  $('#comingUp').replaceChildren(...titles.map(title => { const item = document.createElement('li'); item.textContent = title; return item; }));
  $('#queueStatus').textContent = isActive() && !testSignal
    ? `${model.generationQueued || 0} generating · ${model.playoutQueued || 0} ready` : '';
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
  try {
    const status = await api(path, {method:'POST', headers:{'Content-Type':'application/json','X-Studio-Control':'1'}, body:JSON.stringify(body)});
    render(status); return status;
  }
  catch (error) { localError(error.message); }
  finally { busy = false; renderControls(); }
}
$('#model').addEventListener('change', () => {
  selectedSource = $('#model').value;
  if (studioStatus?.source !== selectedSource) resetPlayback(studioStatus?.sessionId);
  renderControls();
});
function storySettings() { return {
  premise:$('#premise').value,
  useStartingFrame:selectedSource === 'fast_h3' && $('#useStartingFrame').checked,
  style:$('#visualStyle').value.trim() || 'Natural motion, clear subjects, coherent camera and sound.',
  clipSeconds:Number($('#clipSeconds').value), continueScenes:$('#continueScenes').checked,
  bufferSeconds:Number($('#bufferSeconds').value),
  creative:{...Object.fromEntries(Object.entries(directionFields).map(([field, id]) => [field, $('#' + id).value.trim()])),
    ...Object.fromEntries(speechFields.map(field => [field, $('#' + field).value.trim()])), dialogueMode:$('#dialogueMode').value,
    images:creativeDraft.images, audios:creativeDraft.audios, firstFrame:$('#firstFrame').value, lastFrame:$('#lastFrame').value}
}; }

function creativeIssue() {
  if ($('#dialogueMode').value === 'exact' && !$('#exactDialogue').value.trim() && !creativeDraft.audios.some(item => item.kind === 'soundtrack')) return 'Write the exact spoken line or choose scripted dialogue.';
  if (creativeDraft.images.length > 9) return 'Use at most 9 image references.';
  const maxAudio = $('#continueScenes').checked ? 2 : 3;
  if (creativeDraft.audios.length > maxAudio) return `Use at most ${maxAudio} audio references with these continuation settings.`;
  if ([...creativeDraft.images, ...creativeDraft.audios].some(item => !item.description.trim())) return 'Describe the role of every reference.';
  if ((creativeDraft.audios.length || $('#firstFrame').value || $('#lastFrame').value) && !creativeDraft.images.length) return 'Add an image reference for audio or opening keyframes.';
  if (creativeDraft.images.length && $('#useStartingFrame').checked) return 'Disable the saved opening appearance reference to use numbered images.';
  if (creativeDraft.audios.filter(item => item.kind === 'soundtrack').length > 1) return 'Choose only one complete soundtrack.';
  return '';
}
function renderReferenceCards() {
  for (const field of ['images', 'audios']) {
    const cards = creativeDraft[field].map((item, index) => {
      const card = document.createElement('div'); card.className = 'reference-card';
      const header = document.createElement('header');
      const title = document.createElement('span'); title.textContent = `${field === 'images' ? 'Picture' : 'Audio'} ${index + 1}`;
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'text-button'; remove.textContent = 'Remove';
      remove.setAttribute('aria-label', `Remove ${title.textContent}`);
      remove.addEventListener('click', () => { creativeDraft[field].splice(index, 1); renderReferenceCards(); renderControls(); });
      header.append(title, remove);
      const role = document.createElement('select'); role.setAttribute('aria-label', `${title.textContent} role`);
      const choices = field === 'images' ? [['person','Character'],['object','Object'],['scene','Location / look']] : [['voice','Voice'],['ambience','Ambience'],['soundtrack','Complete soundtrack']];
      for (const [value, label] of choices) role.add(new Option(label, value));
      role.value = item.kind; role.addEventListener('change', () => { item.kind = role.value; renderControls(); });
      const description = document.createElement('textarea'); description.rows = 2; description.maxLength = 300;
      description.setAttribute('aria-label', `${title.textContent} description`); description.value = item.description;
      description.placeholder = field === 'images' ? 'Who or what is shown? E.g. Aki, the chef in a navy bandana.' : 'Whose voice, which ambience, or what soundtrack is this?';
      description.addEventListener('input', () => { item.description = description.value; renderControls(); });
      const media = document.createElement(field === 'images' ? 'img' : 'audio');
      media.src = '/api/assets/' + item.id;
      if (field === 'images') media.alt = title.textContent + ' reference';
      else { media.controls = true; media.preload = 'none'; }
      card.append(header, media, role, description); return card;
    });
    $('#' + (field === 'images' ? 'imageReferences' : 'audioReferences')).replaceChildren(...cards);
  }
}
function renderKeyframes(assets, first = $('#firstFrame').value, last = $('#lastFrame').value) {
  for (const [field, selected] of [['firstFrame',first],['lastFrame',last]]) {
    const options = [new Option('Not pinned', '')];
    assets.filter(asset => asset.type === 'image').forEach((asset, index) => options.push(new Option(`Uploaded image ${index + 1}`, asset.id)));
    $('#' + field).replaceChildren(...options); $('#' + field).value = selected;
  }
}
async function uploadReference(event, target) {
  const file = event.target.files[0];
  if (!file || busy || isActive()) return;
  const type = target === 'audios' ? 'audio' : 'image';
  if (file.size > (type === 'image' ? 10 : 20) * 1024 * 1024) { localError('Reference file is too large.'); event.target.value = ''; return; }
  if ((target === 'images' && creativeDraft.images.length >= 9) || (target === 'audios' && creativeDraft.audios.length >= ($('#continueScenes').checked ? 2 : 3))) {
    localError('Remove a reference before adding another.'); event.target.value = ''; return;
  }
  revision++; busy = true; renderControls();
  try {
    const status = await api('/api/assets', {method:'POST', headers:{'X-Studio-Control':'1','X-Asset-Type':type,'Content-Type':'application/octet-stream'}, body:file});
    const asset = status.uploadedAsset;
    if (target === 'images' || target === 'audios') {
      creativeDraft[target].push({id:asset.id, kind:target === 'images' ? 'person' : 'voice', description:''});
      if (target === 'images') $('#useStartingFrame').checked = false;
      renderReferenceCards();
    }
    renderKeyframes(status.assets, target === 'firstFrame' ? asset.id : $('#firstFrame').value, target === 'lastFrame' ? asset.id : $('#lastFrame').value);
    render(status);
  } catch (error) { localError(error.message); }
  finally { busy = false; event.target.value = ''; renderControls(); }
}
for (const [id, target] of [['imageReferenceFile','images'],['audioReferenceFile','audios'],['firstFrameFile','firstFrame'],['lastFrameFile','lastFrame']]) {
  $('#' + id).addEventListener('change', event => uploadReference(event, target));
}
for (const id of ['continueScenes', 'firstFrame', 'lastFrame']) $('#' + id).addEventListener('change', renderControls);
for (const id of ['dialogueMode','clipSeconds','bufferSeconds']) $('#' + id).addEventListener('change', renderControls);
$('#exactDialogue').addEventListener('input', renderControls);
$('#inspectPrompt').addEventListener('click', async () => {
  try { const result = await api('/api/prompt-preview', {method:'POST',headers:{'Content-Type':'application/json','X-Studio-Control':'1'},body:JSON.stringify(storySettings())}); $('#promptPreview').textContent = result.prompt; }
  catch (error) { $('#promptPreview').textContent = error.message; }
});
$('#start').addEventListener('click', () => mutate('/api/start', {
  source:selectedSource, duration:null, confirmPaid:selectedSource !== 'test_signal', ...storySettings()
}));
$('#saveStory').addEventListener('click', async () => {
  $('#storyFeedback').textContent = '';
  if (await mutate('/api/story', storySettings())) $('#storyFeedback').textContent = 'Show saved on this computer.';
});
$('#premise').addEventListener('input', renderControls);
$('#useStartingFrame').addEventListener('change', renderControls);
$('#stop').addEventListener('click', () => mutate('/api/stop', {}));
$('#chatForm').addEventListener('submit', async event => {
  event.preventDefault();
  if (busy) return;
  const feedback = $('#chatFeedback');
  feedback.textContent = '';
  try {
    render(await api('/api/chat', {method:'POST', headers:{'Content-Type':'application/json','X-Studio-Control':'1'},
      body:JSON.stringify({author:$('#chatAuthor').value.trim(), text:$('#chatText').value.trim()})}));
    feedback.textContent = 'Queued for moderation and scene writing; watch the worker log for the result.';
    $('#chatText').value = '';
  } catch (error) { feedback.textContent = error.message; }
});
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
