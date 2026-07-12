// ============================================================
// IN-PAGE DIALOGS — replacements for native alert/confirm/prompt.
// ⚠️ Native alert()/confirm()/prompt() stall the Electron renderer's keyboard
// input pipeline after dismissal. NEVER use them. Use these instead.
// (Defined here in utils.js, which index.html loads, so they're global to the
// chat page. config.html has its own inline copy — it loads no shared JS.)
// ============================================================
function hwuiToast(message, kind = 'success', ms = 2200) {
  let host = document.getElementById('hwui-toast-host');
  if (!host) {
    host = document.createElement('div');
    host.id = 'hwui-toast-host';
    host.style.cssText = 'position:fixed;bottom:20px;left:50%;transform:translateX(-50%);z-index:99999;display:flex;flex-direction:column;gap:8px;align-items:center;pointer-events:none;';
    document.body.appendChild(host);
  }
  const c = ({
    success: { bg:'#13361f', border:'#2a6a4a', fg:'#aef0c8' },
    error:   { bg:'#3a1414', border:'#8a2424', fg:'#ffb0b0' },
    info:    { bg:'#16263a', border:'#245080', fg:'#bcd8ff' },
  })[kind] || { bg:'#13361f', border:'#2a6a4a', fg:'#aef0c8' };
  const el = document.createElement('div');
  el.textContent = message;
  el.style.cssText = `background:${c.bg};border:1px solid ${c.border};color:${c.fg};border-radius:8px;padding:10px 16px;font-size:13px;max-width:480px;box-shadow:0 4px 16px rgba(0,0,0,0.5);opacity:0;transition:opacity .18s ease;white-space:pre-line;`;
  host.appendChild(el);
  requestAnimationFrame(() => { el.style.opacity = '1'; });
  setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 220); }, ms);
}

// hwuiConfirm(message[, opts]) → Promise<boolean>. Resolves true on confirm,
// false on cancel / backdrop click / Escape. opts: {confirmText, cancelText, danger}.
function hwuiConfirm(message, opts = {}) {
  return new Promise((resolve) => {
    const prevFocus = document.activeElement;
    const backdrop = document.createElement('div');
    backdrop.style.cssText = 'position:fixed;inset:0;z-index:100000;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,0.6);';
    const panel = document.createElement('div');
    panel.style.cssText = 'background:#1a1a2a;border:1px solid #444;border-radius:10px;padding:20px 22px;max-width:440px;width:calc(100% - 48px);box-shadow:0 8px 32px rgba(0,0,0,0.6);color:#ddd;font-size:14px;';
    const msg = document.createElement('div');
    msg.textContent = message;
    msg.style.cssText = 'white-space:pre-line;line-height:1.5;margin-bottom:18px;';
    const rowEl = document.createElement('div');
    rowEl.style.cssText = 'display:flex;justify-content:flex-end;gap:10px;';
    const cancelBtn = document.createElement('button');
    cancelBtn.textContent = opts.cancelText || 'Cancel';
    cancelBtn.style.cssText = 'background:#2a2a38;border:1px solid #555;color:#ccc;border-radius:6px;padding:7px 16px;font-size:13px;cursor:pointer;';
    const okBtn = document.createElement('button');
    okBtn.textContent = opts.confirmText || 'OK';
    okBtn.style.cssText = `background:${opts.danger ? '#5a1f1f' : '#1f5a3a'};border:1px solid ${opts.danger ? '#8a2424' : '#248050'};color:#fff;border-radius:6px;padding:7px 16px;font-size:13px;font-weight:600;cursor:pointer;`;
    rowEl.append(cancelBtn, okBtn);
    panel.append(msg, rowEl);
    backdrop.appendChild(panel);
    document.body.appendChild(backdrop);
    function cleanup(result) {
      document.removeEventListener('keydown', onKey, true);
      backdrop.remove();
      try { if (prevFocus && prevFocus.focus) prevFocus.focus(); } catch (_) {}
      resolve(result);
    }
    function onKey(e) {
      if (e.key === 'Escape') { e.preventDefault(); cleanup(false); }
      else if (e.key === 'Enter') { e.preventDefault(); cleanup(true); }
    }
    document.addEventListener('keydown', onKey, true);
    backdrop.addEventListener('mousedown', (e) => { if (e.target === backdrop) cleanup(false); });
    cancelBtn.addEventListener('click', () => cleanup(false));
    okBtn.addEventListener('click', () => cleanup(true));
    okBtn.focus();
  });
}

// hwuiPrompt(message[, defaultValue]) → Promise<string|null>. Resolves the entered
// text on OK/Enter, or null on cancel / backdrop click / Escape.
function hwuiPrompt(message, defaultValue = '', opts = {}) {
  return new Promise((resolve) => {
    const prevFocus = document.activeElement;
    const backdrop = document.createElement('div');
    backdrop.style.cssText = 'position:fixed;inset:0;z-index:100000;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,0.6);';
    const panel = document.createElement('div');
    panel.style.cssText = 'background:#1a1a2a;border:1px solid #444;border-radius:10px;padding:20px 22px;max-width:440px;width:calc(100% - 48px);box-shadow:0 8px 32px rgba(0,0,0,0.6);color:#ddd;font-size:14px;';
    const msg = document.createElement('div');
    msg.textContent = message;
    msg.style.cssText = 'white-space:pre-line;line-height:1.5;margin-bottom:12px;';
    const field = document.createElement('input');
    field.type = 'text';
    field.value = defaultValue || '';
    field.style.cssText = 'width:100%;box-sizing:border-box;background:#0e0e16;border:1px solid #555;color:#eee;border-radius:6px;padding:8px 10px;font-size:14px;margin-bottom:16px;';
    const rowEl = document.createElement('div');
    rowEl.style.cssText = 'display:flex;justify-content:flex-end;gap:10px;';
    const cancelBtn = document.createElement('button');
    cancelBtn.textContent = 'Cancel';
    cancelBtn.style.cssText = 'background:#2a2a38;border:1px solid #555;color:#ccc;border-radius:6px;padding:7px 16px;font-size:13px;cursor:pointer;';
    const okBtn = document.createElement('button');
    okBtn.textContent = opts.confirmText || 'OK';
    okBtn.style.cssText = 'background:#1f5a3a;border:1px solid #248050;color:#fff;border-radius:6px;padding:7px 16px;font-size:13px;font-weight:600;cursor:pointer;';
    rowEl.append(cancelBtn, okBtn);
    panel.append(msg, field, rowEl);
    backdrop.appendChild(panel);
    document.body.appendChild(backdrop);
    function cleanup(result) {
      document.removeEventListener('keydown', onKey, true);
      backdrop.remove();
      try { if (prevFocus && prevFocus.focus) prevFocus.focus(); } catch (_) {}
      resolve(result);
    }
    function onKey(e) {
      if (e.key === 'Escape') { e.preventDefault(); cleanup(null); }
      else if (e.key === 'Enter') { e.preventDefault(); cleanup(field.value); }
    }
    document.addEventListener('keydown', onKey, true);
    backdrop.addEventListener('mousedown', (e) => { if (e.target === backdrop) cleanup(null); });
    cancelBtn.addEventListener('click', () => cleanup(null));
    okBtn.addEventListener('click', () => cleanup(field.value));
    field.focus(); field.select();
  });
}

// ============================================================
// AUTHOR'S NOTE
// ============================================================
function openAuthorNote() {
  const modal = document.getElementById('author-note-modal');
  const textarea = document.getElementById('author-note');
  
  // Load existing note for current chat
  if (currentChatFilename) {
    const savedNote = localStorage.getItem(`author-note-${currentChatFilename}`) || '';
    textarea.value = savedNote;
  }
  
  modal.style.display = 'block';
  textarea.focus();
}

function closeAuthorNote() {
  const modal = document.getElementById('author-note-modal');
  modal.style.display = 'none';
}

function saveAuthorNote() {
  const note = document.getElementById('author-note').value;
  
  if (currentChatFilename) {
    localStorage.setItem(`author-note-${currentChatFilename}`, note);
    console.log('✅ Author note saved for', currentChatFilename);
  }
  
  closeAuthorNote();
}


// ============================================================
// CURRENT SITUATION MODAL — removed.
// The sidebar button + modal were redundant with the Current Situation
// field on the Config page. The field there (saveCurrentSituation /
// loadCurrentSituation in config.html) still uses the same backend
// endpoints: /get_current_situation and /save_current_situation.
// ============================================================


// ==================================================
// CHECK PRO LICENSE
// ==================================================

async function checkProLicense() {
    try {
        const response = await fetch('/check_license');
        const data = await response.json();
        return data.valid;
    } catch (error) {
        console.error('License check error:', error);
        return false;
    }
}

// ==================================================
// MEMORY MODAL
// ==================================================
function openMemoryModal() {
    const modal = document.getElementById('memory-modal');
    if (modal) {
        modal.style.display = 'block';
        window._memoryTab = 'personal';
        switchMemoryTab('personal');
    }
}

function closeMemoryModal() {
    const modal = document.getElementById('memory-modal');
    modal.style.display = 'none';
}

// ==================================================
// PROJECT MODAL
// ==================================================
function openProjectModal() {
    const modal = document.getElementById('project-modal');
    if (modal) {
        modal.style.display = 'block';
        loadProjects();
    }
}

function closeProjectModal() {
    const modal = document.getElementById('project-modal');
    modal.style.display = 'none';
}

// ============================================================
// OPENING LINE
// ============================================================
function openOpeningLine() {
  const modal = document.getElementById('opening-line-modal');
  const checkbox = document.getElementById('opening-line-enabled');
  const textarea = document.getElementById('opening-line-text');
  
  // Get current character name
  const charName = currentCharacter?.name || document.getElementById('character-select')?.value || 'Unknown';
  
  // Load current values for this CHARACTER (not chat)
  const enabled = localStorage.getItem(`opening-line-enabled-${charName}`) === 'true';
  const text = localStorage.getItem(`opening-line-${charName}`) || '';
  
  checkbox.checked = enabled;
  textarea.value = text;
  
  modal.style.display = 'block';
}

function closeOpeningLine() {
  const modal = document.getElementById('opening-line-modal');
  modal.style.display = 'none';
}

function saveOpeningLine() {
  const checkbox = document.getElementById('opening-line-enabled');
  const textarea = document.getElementById('opening-line-text');
  
  // Get current character name
  const charName = currentCharacter?.name || document.getElementById('character-select')?.value || 'Unknown';
  
  // Save for this CHARACTER (not chat)
  localStorage.setItem(`opening-line-enabled-${charName}`, checkbox.checked.toString());
  localStorage.setItem(`opening-line-${charName}`, textarea.value);
  
  console.log(`✅ Opening line saved for character: ${charName}`);
  
  closeOpeningLine();
  
  // If chat is empty, display the opening line now
  if (window.loadedChat && Array.isArray(window.loadedChat) && window.loadedChat.length === 0) {
    displayOpeningLineInChat();
  }
}

async function displayOpeningLineInChat() {
  // Get current character name
  const charName = 
    currentCharacter?.name || 
    document.getElementById('character-select')?.value || 
    localStorage.getItem('lastCharacter') || 
    'Unknown';
  
  console.log('🎬 displayOpeningLineInChat called for character:', charName);
  
  try {
    // Load from disk
    const res = await fetch(`/get_opening_lines/${charName}`);
    const data = await res.json();
    
    if (!data.enabled) {
      console.log('  ❌ Opening line not enabled');
      return;
    }
    
    const lines = data.lines || [];
    
    if (lines.length === 0) {
      console.log('  ❌ No opening lines available');
      return;
    }
    
    if (!window.loadedChat) {
      window.loadedChat = [];
    }
    
    if (window.loadedChat.length > 0) {
      console.log('  ❌ Chat not empty, skipping opening line');
      return;
    }
    
    // 🎲 Pick a random line
    const randomLine = lines[Math.floor(Math.random() * lines.length)];
    
    console.log(`  ✅ Picked random opening line (${lines.length} available)`);
    console.log(`  📝 Line: ${randomLine.substring(0, 50)}...`);
    
    // Create assistant message object
    const openingMessage = {
      role: 'assistant',
      content: randomLine,
      is_opening_line: true  // tells backend this isn't a real reply — don't treat as continuation
    };
    
    // Add to chat history
    window.loadedChat.push(openingMessage);
    window._chatDirty = true;  // stale-write guard: opener added locally

    // Render in chat UI
    renderChatMessages(window.loadedChat);
    
    // Auto-save
    autoSaveCurrentChat();
    
  } catch (err) {
    console.error('Failed to load opening line:', err);
  }
}

// ==================================================
// DOCUMENT MANAGEMENT
// ==================================================

// Setup document upload handler
function initDocumentUpload() {
  const uploadInput = document.getElementById('document-upload');
  if (uploadInput) {
    uploadInput.addEventListener('change', async function(e) {
      const file = e.target.files[0];
      if (!file) return;
      
      const projectName = document.getElementById('edit-project-name').value;
      
      const formData = new FormData();
      formData.append('file', file);
      
      try {
        const res = await fetch(`/projects/${projectName}/documents/upload`, {
          method: 'POST',
          body: formData
        });
        
        const result = await res.json();
        
        if (result.error) {
          hwuiToast('Upload failed: ' + result.error, 'error');
          return;
        }
        
        console.log('✅ Document uploaded:', result.filename);
        
        // Reload documents list
        await loadProjectDocuments(projectName);
        
        // Clear input
        e.target.value = '';
        
      } catch (err) {
        console.error('❌ Upload error:', err);
        hwuiToast('Failed to upload document', 'error');
      }
    });
  }
}

async function loadProjectDocuments(projectName) {
  try {
    const res = await fetch(`/projects/${projectName}/documents/list`);
    const data = await res.json();
    
    const listDiv = document.getElementById('documents-list');
    listDiv.innerHTML = '';
    
    if (data.documents.length === 0) {
      listDiv.innerHTML = '<p style="color: #666; font-size: 12px;">No documents uploaded yet.</p>';
      return;
    }
    
    data.documents.forEach(doc => {
      const docItem = document.createElement('div');
      docItem.className = 'document-item';
      
      const nameSpan = document.createElement('span');
      nameSpan.textContent = doc.filename;
      nameSpan.className = 'document-name';
      
      const sizeSpan = document.createElement('span');
      sizeSpan.textContent = formatFileSize(doc.size);
      sizeSpan.className = 'document-size';
      
      const deleteBtn = document.createElement('button');
      deleteBtn.textContent = '×';
      deleteBtn.className = 'document-delete-btn';
      deleteBtn.onclick = () => deleteProjectDocument(projectName, doc.filename);
      
      docItem.appendChild(nameSpan);
      docItem.appendChild(sizeSpan);
      docItem.appendChild(deleteBtn);
      
      listDiv.appendChild(docItem);
    });
    
  } catch (err) {
    console.error('❌ Failed to load documents:', err);
  }
}

async function deleteProjectDocument(projectName, filename) {
  if (!await hwuiConfirm(`Delete "${filename}"?`)) return;
  
  try {
    const res = await fetch(`/projects/${projectName}/documents/${filename}`, {
      method: 'DELETE'
    });
    
    const result = await res.json();
    
    if (result.error) {
      hwuiToast('Delete failed: ' + result.error, 'error');
      return;
    }
    
    console.log('🗑️ Document deleted:', filename);
    
    // Reload documents list
    await loadProjectDocuments(projectName);
    
  } catch (err) {
    console.error('❌ Delete error:', err);
    hwuiToast('Failed to delete document', 'error');
  }
}

function formatFileSize(bytes) {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
}


// ==================================================
// TTS (TEXT-TO-SPEECH) FUNCTIONALITY
// ==================================================

let ttsEnabled = false;
let isPlayingAudio = false;
let currentAudio = null;
let ttsVoice = localStorage.getItem('tts-voice') || 'af_heart';

// Hybrid queue — sentences accumulate here, playback starts after 3 are ready
let ttsQueue = [];
let ttsProcessing = false;
let ttsSentenceBuffer = '';
let ttsStreamingComplete = false;
// Code-block stripping state — persists across streaming chunks so a fenced
// ```...``` block that spans multiple chunks is fully suppressed. The tail
// holds back 1-2 trailing backticks at chunk boundaries in case they are the
// start of a ``` marker completed in the next chunk.
let ttsInsideCodeBlock = false;
let ttsBacktickTail = '';
const TTS_START_THRESHOLD = 1;  // Start playing after this many sentences buffered

let lastTTSResponseText = '';  // 🔊 Stored for Replay button


function toggleTTS() {
  ttsEnabled = !ttsEnabled;
  const btn = document.getElementById('tts-toggle-btn');

  if (ttsEnabled) {
    btn.classList.add('active');
    btn.style.background = 'rgba(76,175,80,0.4)';
    btn.style.borderColor = 'rgba(76,175,80,0.8)';
    btn.style.color = '#aaffaa';
    console.log('🔊 TTS enabled');
    checkTTSStatus();
  } else {
    btn.classList.remove('active');
    btn.style.background = 'rgba(255,255,255,0.08)';
    btn.style.borderColor = '#555';
    btn.style.color = '#ccc';
    stopAllAudio();
    console.log('🔇 TTS disabled');
  }

  localStorage.setItem('tts-enabled', ttsEnabled);
}


async function checkTTSStatus() {
  try {
    const res = await fetch('/api/tts/status');
    const data = await res.json();

    if (data.status === 'offline') {
      showTTSNotification('Kokoro TTS server not running. Start start_kokoro.bat', true);
      ttsEnabled = false;
      const _tb=document.getElementById('tts-toggle-btn'); if(_tb){_tb.classList.remove('active');_tb.style.background='rgba(255,255,255,0.08)';_tb.style.borderColor='#555';_tb.style.color='#ccc';}
    } else if (data.status === 'online') {
      console.log('✅ Kokoro TTS ready');
    }
  } catch (err) {
    console.error('❌ TTS status check failed:', err);
  }
}


function showTTSNotification(message, isError) {
  const existing = document.getElementById('tts-notification');
  if (existing) existing.remove();

  const notif = document.createElement('div');
  notif.id = 'tts-notification';
  notif.textContent = message;
  notif.style.cssText = `
    position: fixed;
    bottom: 80px;
    right: 20px;
    padding: 10px 16px;
    border-radius: 8px;
    font-size: 13px;
    z-index: 10000;
    color: #fff;
    background: ${isError ? '#5a1f1f' : '#1f3a5a'};
    border: 1px solid ${isError ? '#802424' : '#245080'};
    animation: fadeIn 0.3s ease;
  `;
  document.body.appendChild(notif);
  setTimeout(() => {
    notif.style.opacity = '0';
    notif.style.transition = 'opacity 0.3s ease';
    setTimeout(() => notif.remove(), 300);
  }, 5000);
}


function stopAllAudio() {
  if (replayTimeout) {
    clearTimeout(replayTimeout);
    replayTimeout = null;
  }
  if (currentAudio) {
    currentAudio.pause();
    currentAudio = null;
  }
  ttsQueue = [];
  ttsProcessing = false;
  isPlayingAudio = false;
  ttsSentenceBuffer = '';
  ttsStreamingComplete = false;
  ttsInsideCodeBlock = false;
  ttsBacktickTail = '';

  const btn = document.getElementById('tts-toggle-btn');
  if (btn) btn.classList.remove('speaking');

  const replayBtn = document.querySelector('.replay-btn');
  if (replayBtn) {
    replayBtn.style.background = '';
    replayBtn.style.borderColor = '';
  }
}


// --- Contraction apostrophe protector for TTS ---
// Normalises smart/curly apostrophes to straight ones so F5 receives
// contractions intact (e.g. "don't" not "dont") before the pipeline strips them.
function fixContractionsForTTS(text) {
  text = text.replace(/\u2019/g, "'").replace(/\u2018/g, "'");
  text = text.replace(/\bisn't\b/gi, 'isnt');
  return text;
}


// Strip fenced ```...``` and inline `...` code from a streaming chunk so the
// TTS engine never reads code aloud. State (`ttsInsideCodeBlock`,
// `ttsBacktickTail`) persists across chunks because a code block can span
// many chunks and the ``` fence itself can split at a chunk boundary.
function stripCodeForTTS(chunk) {
  // Re-attach any backticks held back from the previous chunk
  chunk = ttsBacktickTail + chunk;
  ttsBacktickTail = '';

  // If the chunk ends in 1 or 2 trailing backticks, hold them back — they
  // might be the leading bytes of a ``` fence completed in the next chunk.
  const trail = chunk.match(/`{1,2}$/);
  if (trail) {
    ttsBacktickTail = trail[0];
    chunk = chunk.slice(0, -trail[0].length);
  }

  let out = '';
  let i = 0;
  while (i < chunk.length) {
    // Triple-backtick fence — toggle block state
    if (chunk[i] === '`' && chunk[i + 1] === '`' && chunk[i + 2] === '`') {
      ttsInsideCodeBlock = !ttsInsideCodeBlock;
      i += 3;
      // On opener, skip optional language tag up to (and including) newline
      if (ttsInsideCodeBlock) {
        while (i < chunk.length && chunk[i] !== '\n') i++;
        if (i < chunk.length) i++;  // consume the newline
      }
      continue;
    }
    // Inside a fenced block — drop everything
    if (ttsInsideCodeBlock) { i++; continue; }
    // Inline code: single backtick → next single backtick on same chunk
    if (chunk[i] === '`') {
      const close = chunk.indexOf('`', i + 1);
      if (close === -1) {
        // No close yet — hold the opening ` and following text for next chunk
        ttsBacktickTail = chunk.substring(i) + ttsBacktickTail;
        return out;
      }
      i = close + 1;
      continue;
    }
    out += chunk[i];
    i++;
  }
  return out;
}


// Remove links so TTS never speaks a URL or a bare-domain citation.
// ⚠️ Must run BEFORE any "(" → ". " paren-to-pause conversion — otherwise the
// wrapped citation's parens are gone before we can recognise the form, and the
// bare domain inside leaks through and gets read aloud.
//   • Citation links from search-preview models look like ([openai.com](https://…)) —
//     the whole wrapped form (outer parens included) is dropped.
//   • A plain [text](url): if the visible text is a bare domain (no spaces, has a
//     dotted TLD) it's dropped ENTIRELY — reading "openai.com" mid-sentence is
//     noise. Real prose labels ("the documentation") or a plain word ("OpenAI",
//     no dot) are KEPT, since speaking them is useful.
function stripLinksForTTS(text) {
  if (!text) return text;
  var _isDomain = function (t) { return /^\s*[^\s]+\.[a-z]{2,}[^\s]*\s*$/i.test(t); };
  return text
    .replace(/<a\b[^>]*>[\s\S]*?<\/a>/gi, '')                 // full <a>…</a> blocks
    .replace(/\s*\(\s*\[[^\]]+\]\([^)]*\)\s*\)/g, '')         // wrapped citation ([text](url)) → drop whole
    .replace(/\[([^\]]+)\]\([^)]*\)/g, function (_, t) { return _isDomain(t) ? '' : t; })   // [text](url)
    .replace(/\[([^\]]+)\]\([^)]*$/g, function (_, t) { return _isDomain(t) ? '' : t; })    // unclosed (chunk split mid-URL)
    .replace(/\]\([^)]*\)/g, '')                              // orphaned ](url) fragment
    .replace(/https?:\/\/[^\s\])"'>]+/g, '')                  // bare URLs
    .replace(/www\.[^\s\])"'>]+/g, '')                        // bare www URLs
    .replace(/[\u{1F517}🔗]/gu, '');               // link emoji (all variants)
}

// --- Called from streaming loop for each chunk ---
function bufferTextForTTS(chunk) {
  if (!ttsEnabled || !chunk) return;

  // Reset code-block state at start of each new response — must happen
  // BEFORE stripCodeForTTS so stale state from a prior response can't gut
  // the first chunk of the next one.
  if (ttsSentenceBuffer === '' && ttsQueue.length === 0 && !ttsProcessing) {
    ttsStreamingComplete = false;
    ttsInsideCodeBlock = false;
    ttsBacktickTail = '';
  }

  // Strip fenced and inline code BEFORE any other transforms — code may
  // contain parens, dashes, emoji-like Unicode that other passes would mangle
  // into spoken artefacts.
  chunk = stripCodeForTTS(chunk);
  if (!chunk) return;

  // Strip links FIRST via the shared helper (wrapped citations, bare-domain link
  // text, <a> tags, bare URLs) — MUST run before the "(" → ". " conversion below,
  // else a citation's parens vanish and the bare domain leaks into speech.
  chunk = stripLinksForTTS(chunk)
               .replace(/\n*[\u{1F517}\uD83D\uDD17]*\s*Source:[^\n]*/gu, '') // Source: lines (all emoji variants)
               .replace(/<[^>]+>/g, ' ')                    // remaining HTML tags
               .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')    // markdown links [text](url) → text only
               .replace(/\[([^\]]+)\]\([^)]*$/g, '$1')     // unclosed markdown link (chunk split mid-URL)
               .replace(/\]\([^)]*\)/g, '')                 // orphaned ](url) fragment
               .replace(/https?:\/\/[^\s\])"'>]+/g, '')    // bare URLs (broader terminator set)
               .replace(/www\.[^\s\])"'>]+/g, '')           // bare www URLs
               .replace(/[\u{1F517}\uD83D\uDD17]/gu, ''); // link emoji (all variants)

  // Fix contractions FIRST before apostrophes get stripped
  chunk = fixContractionsForTTS(chunk);

  // Normalise dashes, strip ellipsis to single pause, no stacking dots
  chunk = chunk.replace(/\.{3}/g, '. ').replace(/\u2026/g, '. ').replace(/\.{2}/g, '. ');
  chunk = chunk.replace(/\s*\(\s*/g, '. ').replace(/\s*\)\s*/g, '. ');
  chunk = chunk.replace(/^>\s*/gm, '').replace(/\s*>\s*/g, '. ');  // strip > list markers
  // Replace specific emojis with spoken words before catch-all strips them
  chunk = chunk.replace(/\u{1F4AF}/gu, 'one hundred percent');
  // Replace remaining emojis with full stop tight to preceding word — \s* eats the space
  chunk = chunk.replace(/(\w)\s*(?:[\u{1F000}-\u{1FFFF}\u{1F900}-\u{1F9FF}\u{1FA00}-\u{1FAFF}]|[\u{2600}-\u{26FF}]|[\u{2700}-\u{27BF}]|[\uD800-\uDBFF][\uDC00-\uDFFF])+/gu, '$1.');
  chunk = chunk.replace(/(?:[\u{1F000}-\u{1FFFF}\u{1F900}-\u{1F9FF}\u{1FA00}-\u{1FAFF}]|[\u{2600}-\u{26FF}]|[\u{2700}-\u{27BF}]|[\uD800-\uDBFF][\uDC00-\uDFFF])+/gu, '');

  ttsSentenceBuffer += chunk;

  // Split on newlines first — each line is a reliable boundary
  const lines = ttsSentenceBuffer.split('\n');
  ttsSentenceBuffer = lines.pop(); // keep last incomplete line in buffer

  for (const line of lines) {
    // For each complete line, also split on sentence punctuation within it
    splitAndQueue(line);
  }

  // Also check the current incomplete line for sentence endings
  // so we don't wait for a newline to start playing
  // Emoji counted as sentence terminator — model often ends a sentence with an emoji
  // instead of punctuation; without this the sentence stays in the buffer unqueued,
  // gets merged with the next line, and F5 gets a run-on chunk with no prosody break.
  const sentenceRegex = /[^.!?]+(?:[.!?]+|(?:[\u{1F000}-\u{1FFFF}\u{1F300}-\u{1FAFF}\u{1F900}-\u{1F9FF}\u{1FA00}-\u{1FAFF}\u2600-\u27BF])+)[)"'*_]*\s*/gu;
  let match;
  let lastIndex = 0;
  while ((match = sentenceRegex.exec(ttsSentenceBuffer)) !== null) {
    splitAndQueue(match[0]);
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex > 0) {
    ttsSentenceBuffer = ttsSentenceBuffer.substring(lastIndex);
  }
}


// Max characters per TTS chunk — engine-aware
// F5 sounds better with longer chunks (more pacing context)
// Chatterbox needs short chunks to keep latency low
let TTS_MAX_CHUNK_LENGTH = 300; // default to F5-friendly until engine is known

async function initTTSEngine() {
  try {
    const res = await fetch('/api/tts/engine');
    const data = await res.json();
    const engine = data.engine || 'f5';
    TTS_MAX_CHUNK_LENGTH = (engine === 'chatterbox') ? 150 : 300;
    console.log(`🔊 TTS engine: ${engine} — chunk length: ${TTS_MAX_CHUNK_LENGTH}`);
  } catch (e) {
    console.warn('Could not fetch TTS engine, using default chunk length');
  }
}

function splitAndQueue(text) {
  // Strip links and source lines before any TTS processing. stripLinksForTTS runs
  // FIRST so wrapped citations / bare-domain link text never reach speech.
  text = stripLinksForTTS(text)                                 // links via shared helper
             .replace(/<[^>]+>/g, ' ')                          // remaining HTML tags
             .replace(/\n*[\u{1F517}\*]*\s*Source:[^\n]*/gu, '') // Source: lines
             .replace(/https?:\/\/\S+/g, '')                 // bare URLs
             .replace(/[\u{1F517}]/gu, '');                    // link emoji
  const cleaned = text.trim()
    .replace(/\u{1F4AF}/gu, 'one hundred percent')
    .replace(/(\w)\s*(?:[\u{1F000}-\u{1FFFF}\u{1F900}-\u{1F9FF}\u{1FA00}-\u{1FAFF}]|[\u{2600}-\u{26FF}]|[\u{2700}-\u{27BF}]|[\uD800-\uDBFF][\uDC00-\uDFFF])+/gu, '$1.')
    .replace(/(?:[\u{1F000}-\u{1FFFF}\u{1F900}-\u{1F9FF}\u{1FA00}-\u{1FAFF}]|[\u{2600}-\u{26FF}]|[\u{2700}-\u{27BF}]|[\uD800-\uDBFF][\uDC00-\uDFFF])+/gu, '')
    .replace(/\*\*/g, '').replace(/\*/g, '').replace(/_/g, '')
    .replace(/^[-*•>]\s+/, '').replace(/^\d+\.\s+/, '').replace(/\s*>\s*/g, '. ')
    .replace(/\s*\(\s*/g, '. ').replace(/\s*\)\s*/g, '. ')
    .trim();

  if (cleaned.length === 0) return;

  // Ensure every sentence ends with punctuation
  const withPunct = /[.!?]$/.test(cleaned) ? cleaned : cleaned + '.';

  // If chunk is within limit, queue it directly
  if (withPunct.length <= TTS_MAX_CHUNK_LENGTH) {
    if (withPunct.length > 2) {
      ttsQueue.push(withPunct);
      console.log(`📝 Queued: "${withPunct.substring(0, 50)}"`);
      if (!ttsProcessing && ttsQueue.length >= TTS_START_THRESHOLD) {
        console.log('🎬 Threshold reached, starting playback');
        processQueue();
      }
    }
    return;
  }

  // Chunk is too long — split at commas/dashes first, then word boundaries
  const subChunks = [];
  let remaining = withPunct;

  while (remaining.length > TTS_MAX_CHUNK_LENGTH) {
    // Try to split at last comma or dash before the limit
    let splitAt = -1;
    const searchStr = remaining.substring(0, TTS_MAX_CHUNK_LENGTH);
    const commaIdx = searchStr.lastIndexOf(',');
    const dashIdx  = searchStr.lastIndexOf(' — ');
    const spaceIdx = searchStr.lastIndexOf(' ');

    if (commaIdx > TTS_MAX_CHUNK_LENGTH * 0.4) {
      splitAt = commaIdx + 1; // include the comma
    } else if (dashIdx > TTS_MAX_CHUNK_LENGTH * 0.4) {
      splitAt = dashIdx + 3;
    } else if (spaceIdx > TTS_MAX_CHUNK_LENGTH * 0.4) {
      splitAt = spaceIdx;
    } else {
      splitAt = TTS_MAX_CHUNK_LENGTH; // hard cut as last resort
    }

    subChunks.push(remaining.substring(0, splitAt).trim());
    remaining = remaining.substring(splitAt).trim();
  }

  if (remaining.length > 2) subChunks.push(remaining);

  // Queue each sub-chunk
  for (const sub of subChunks) {
    if (sub.length <= 2) continue;
    const subWithPunct = /[.!?]$/.test(sub) ? sub : sub + '.';
    ttsQueue.push(subWithPunct);
    console.log(`📝 Queued (sub): "${subWithPunct.substring(0, 50)}"`);
  }

  if (!ttsProcessing && ttsQueue.length >= TTS_START_THRESHOLD) {
    console.log('🎬 Threshold reached, starting playback');
    processQueue();
  }
}


// --- Called when streaming finishes ---
function flushTTSBuffer() {
  if (!ttsEnabled) return;

  // Flush anything left in the buffer (no newline at end, no punctuation)
  const remaining = ttsSentenceBuffer.trim()
    .replace(/(\w)\s*(?:[\u{1F000}-\u{1FFFF}\u{1F900}-\u{1F9FF}\u{1FA00}-\u{1FAFF}]|[\u{2600}-\u{26FF}]|[\u{2700}-\u{27BF}]|[\uD800-\uDBFF][\uDC00-\uDFFF])+/gu, '$1.')
    .replace(/(?:[\u{1F000}-\u{1FFFF}\u{1F900}-\u{1F9FF}\u{1FA00}-\u{1FAFF}]|[\u{2600}-\u{26FF}]|[\u{2700}-\u{27BF}]|[\uD800-\uDBFF][\uDC00-\uDFFF])+/gu, '')
    .replace(/\*\*/g, '').replace(/\*/g, '').replace(/_/g, '')
    .trim();

  if (remaining.length > 2) {
    const withPunct = /[.!?]$/.test(remaining) ? remaining : remaining + '.';
    ttsQueue.push(withPunct);
    console.log(`📝 Flushed: "${withPunct.substring(0, 50)}"`);
  }
  ttsSentenceBuffer = '';
  // Drop any held-back backtick fragments — never spoken aloud regardless
  // of whether the closing ``` ever arrived.
  ttsBacktickTail = '';
  ttsInsideCodeBlock = false;

  // Delay setting streamingComplete so the queue processor's poll loop
  // has time to pick up any last-queued sentences before seeing "done"
  // Fixes last sentence cutoff when it arrives just before stream end
  setTimeout(() => {
    ttsStreamingComplete = true;
    console.log('✅ TTS streaming marked complete');
    if (!ttsProcessing && ttsQueue.length > 0) {
      console.log('🎬 Starting playback after flush');
      processQueue();
    }
  }, 150);
}


// --- Sequential queue processor with prefetch ---
async function processQueue() {
  if (ttsProcessing) return;
  ttsProcessing = true;

  const btn = document.getElementById('tts-toggle-btn');
  if (btn) btn.classList.add('speaking');

  // Fetch a sentence from TTS server, return a blob URL or null.
  // Retries once on transient failure so a single F5 hiccup doesn't drop a sentence.
  async function fetchAudio(sentence, firstChunk = false) {
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const response = await fetch('/api/tts/generate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: sentence, voice: ttsVoice, first_chunk: firstChunk })
        });
        if (response.ok) {
          const blob = await response.blob();
          return URL.createObjectURL(blob);
        }
        console.warn(`⚠️ TTS fetch ${response.status} (attempt ${attempt + 1}): "${sentence.substring(0, 40)}"`);
      } catch (err) {
        console.warn(`⚠️ TTS fetch error (attempt ${attempt + 1}):`, err);
      }
      if (attempt === 0) await new Promise(r => setTimeout(r, 200));
    }
    console.error('❌ TTS fetch failed after retry');
    return null;
  }

  // Check if queue is empty
  if (ttsQueue.length === 0) {
    ttsProcessing = false;
    if (btn) btn.classList.remove('speaking');
    return;
  }

  // Pre-fetch buffer: always keep 3 sentences generating ahead
  const prefetchBuffer = [];

  // Start fetching first sentence immediately — play it as soon as ready,
  // chunk 2 will be generating in parallel during playback of chunk 1.
  // first_chunk=true tells F5 server to use nfe_step=20 for faster first-byte latency.
  const initialFetches = Math.min(1, ttsQueue.length);
  for (let i = 0; i < initialFetches; i++) {
    prefetchBuffer.push(fetchAudio(ttsQueue.shift(), true));  // first chunk — fast path
  }

  while (true) {
    if (!ttsEnabled) break;

    // Wait for the next audio in the buffer
    const audioUrl = prefetchBuffer.length > 0 ? await prefetchBuffer.shift() : null;

    // Immediately start fetching more to keep buffer full
    while (prefetchBuffer.length < 3 && ttsQueue.length > 0) {
      prefetchBuffer.push(fetchAudio(ttsQueue.shift()));
    }

    if (audioUrl) {
      // Play current sentence — while playing, poll for new queue items and prefetch them
      await new Promise((resolve) => {
        currentAudio = new Audio(audioUrl);
        currentAudio.playbackRate = 1.0;
        isPlayingAudio = true;

        // Poll during playback so next sentences start fetching immediately
        const prefetchInterval = setInterval(() => {
          while (prefetchBuffer.length < 3 && ttsQueue.length > 0) {
            prefetchBuffer.push(fetchAudio(ttsQueue.shift()));
          }
        }, 50);

        currentAudio.onended = () => {
          clearInterval(prefetchInterval);
          URL.revokeObjectURL(audioUrl);
          isPlayingAudio = false;
          currentAudio = null;
          resolve();
        };
        currentAudio.onerror = () => {
          clearInterval(prefetchInterval);
          URL.revokeObjectURL(audioUrl);
          isPlayingAudio = false;
          currentAudio = null;
          resolve();
        };
        currentAudio.play().catch(() => {
          clearInterval(prefetchInterval);
          isPlayingAudio = false;
          currentAudio = null;
          resolve();
        });
      });

      // Keep buffer topped up after each sentence plays
      while (prefetchBuffer.length < 3 && ttsQueue.length > 0) {
        prefetchBuffer.push(fetchAudio(ttsQueue.shift()));
      }
    }

    // Check if we're done
    if (prefetchBuffer.length === 0 && ttsQueue.length === 0) {
      if (ttsStreamingComplete) {
        break;
      } else {
        // Wait for more sentences from streaming
        await new Promise(r => setTimeout(r, 25));
        if (ttsQueue.length > 0) {
          while (prefetchBuffer.length < 3 && ttsQueue.length > 0) {
            prefetchBuffer.push(fetchAudio(ttsQueue.shift()));
          }
        } else if (ttsStreamingComplete) {
          break;
        }
      }
    }
  }

  ttsProcessing = false;
  isPlayingAudio = false;
  if (btn) btn.classList.remove('speaking');

  const replayBtn = document.querySelector('.replay-btn');
  if (replayBtn) {
    replayBtn.style.background = '';
    replayBtn.style.borderColor = '';
  }
  console.log('✅ TTS queue complete');
}


// --- Voice Selection ---
function setTTSVoice(voiceName) {
  ttsVoice = voiceName;
  localStorage.setItem('tts-voice', voiceName);
  const charName = document.getElementById('character-select')?.value;
  if (charName) {
    localStorage.setItem(`tts-voice-${charName}`, voiceName);
    console.log('🔊 TTS voice saved for', charName, ':', voiceName);
    // Save to server so mobile picks it up automatically
    fetch(`/character_voice/${encodeURIComponent(charName)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ voice: voiceName })
    }).catch(e => console.warn('Server voice save failed:', e));
  }
  // Warm up the newly selected voice so first response is fast
  warmupTTSVoice(voiceName);
}


// --- Warmup TTS with the current voice so GPU is hot and ready ---
function warmupTTSVoice(voice) {
  if (!voice) return;
  console.log(`🔥 Warming up TTS voice: ${voice}...`);
  fetch('/api/tts/warmup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ voice: voice })
  }).then(() => {
    console.log(`✅ TTS warmup complete for: ${voice}`);
  }).catch(err => {
    console.log('⚠️ TTS warmup failed (non-critical):', err);
  });
}


async function loadTTSVoices() {
  try {
    const res = await fetch('/api/tts/voices');
    const data = await res.json();

    const select = document.getElementById('tts-voice-select');
    if (!select) return;

    select.innerHTML = '';

    data.voices.forEach(voice => {
      const option = document.createElement('option');
      option.value = voice.name;
      option.textContent = voice.label;
      if (voice.name === ttsVoice) option.selected = true;
      select.appendChild(option);
    });

  } catch (err) {
    console.error('Failed to load TTS voices:', err);
  }
}


// Restore TTS state on page load
window.addEventListener('DOMContentLoaded', () => {
  const saved = localStorage.getItem('tts-enabled');
  if (saved === 'true') {
    ttsEnabled = true;
    const _ta=document.getElementById('tts-toggle-btn'); if(_ta){_ta.classList.add('active');_ta.style.background='rgba(76,175,80,0.4)';_ta.style.borderColor='rgba(76,175,80,0.8)';_ta.style.color='#aaffaa';}
  }

  const savedVoice = localStorage.getItem('tts-voice');
  if (savedVoice) ttsVoice = savedVoice;

  loadTTSVoices();
  initTTSEngine();

  // Warmup with current voice so first response is fast
  if (ttsVoice) warmupTTSVoice(ttsVoice);
});

// ==================================================
// VOICE INPUT (Hold-to-Talk) - Offline via Whisper
// ==================================================

let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;

async function startVoiceInput() {
  if (isRecording) {
    stopVoiceInput();
    return;
  }

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRecorder = new MediaRecorder(stream);
    audioChunks = [];

    mediaRecorder.ondataavailable = (event) => {
      if (event.data.size > 0) {
        audioChunks.push(event.data);
      }
    };

    mediaRecorder.start();
    isRecording = true;

    const micBtn = document.getElementById('mic-btn');
    micBtn.classList.add('listening');
    micBtn.style.background = '#5a0f0f';
    micBtn.style.borderColor = '#ff4444';
    micBtn.style.color = '#ff8888';
    console.log('🎤 Recording started...');

  } catch (err) {
    console.error('🎤 Mic access error:', err);
    hwuiToast('Could not access microphone. Check Brave permissions.', 'info');
  }
}

async function stopVoiceInput() {
  if (!isRecording || !mediaRecorder) return;

  isRecording = false;
  const micBtn = document.getElementById('mic-btn');
  micBtn.classList.remove('listening');
  micBtn.style.background = 'rgba(255,255,255,0.08)';
  micBtn.style.borderColor = '#555';
  micBtn.style.color = '#ccc';

  return new Promise((resolve) => {
    mediaRecorder.onstop = async () => {
      console.log('🎤 Recording stopped, sending to Whisper...');

      const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
      const formData = new FormData();
      formData.append('audio', audioBlob, 'recording.webm');

      try {
        micBtn.classList.add('processing');
        micBtn.style.background = '#1a3a1a';
        micBtn.style.borderColor = '#44aa44';
        micBtn.style.color = '#88ff88';

        const response = await fetch('/api/whisper/transcribe', {
          method: 'POST',
          body: formData
        });

        const data = await response.json();

        if (data.transcript) {
          const userInput = document.getElementById('user-input');
          userInput.value = data.transcript;
          console.log('✅ Transcript:', data.transcript);
          sendPrompt();
        } else {
          console.error('❌ No transcript returned:', data);
        }

      } catch (err) {
        console.error('❌ Whisper request failed:', err);
      } finally {
        micBtn.classList.remove('processing');
        micBtn.style.background = 'rgba(255,255,255,0.08)';
        micBtn.style.borderColor = '#555';
        micBtn.style.color = '#ccc';
        // Stop all mic tracks to release the mic
        mediaRecorder.stream.getTracks().forEach(t => t.stop());
      }

      resolve();
    };

    mediaRecorder.stop();
  });
}
// Keyboard shortcut for mic (press M to toggle)
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && document.activeElement !== document.getElementById('user-input')) {
    startVoiceInput();
  }
});

async function loadStickyDocsState(projectName) {
  try {
    const res = await fetch(`/projects/get-sticky-docs/${projectName}`);
    const data = await res.json();
    
    const btn = document.getElementById('sticky-docs-btn');
    if (!btn) return;
    
    if (data.sticky_docs) {
      btn.textContent = 'ON';
      btn.style.background = 'rgba(40, 167, 69, 0.3)';
      btn.style.borderColor = '#28a745';
      btn.style.color = '#28a745';
    } else {
      btn.textContent = 'OFF';
      btn.style.background = '#3a3b3f';
      btn.style.borderColor = '#555';
      btn.style.color = '#aaa';
    }

    // Show/update pinned doc indicator
    updatePinnedDocIndicator(data.sticky_doc_file);

  } catch (err) {
    console.error('Failed to load sticky docs state:', err);
  }
}

function updatePinnedDocIndicator(pinnedFile) {
  // Remove any existing indicator
  const existing = document.getElementById('pinned-doc-indicator');
  if (existing) existing.remove();

  if (!pinnedFile) return;

  // Find the sticky docs toggle container to insert after it
  const btn = document.getElementById('sticky-docs-btn');
  if (!btn) return;
  const container = btn.closest('div[style]');
  if (!container) return;

  const indicator = document.createElement('div');
  indicator.id = 'pinned-doc-indicator';
  indicator.style.cssText = 'display:flex; align-items:center; gap:8px; margin-top:8px; padding:6px 10px; background:rgba(40,167,69,0.1); border:1px solid rgba(40,167,69,0.3); border-radius:4px; font-size:12px; color:#28a745;';
  indicator.innerHTML = `<span>📌</span><span style="flex:1;">Pinned: <strong>${pinnedFile}</strong></span>`;
  container.insertAdjacentElement('afterend', indicator);
}
//============================== Toggle sticky docs===========================
async function toggleStickyDocs() {
  if (!currentEditingProject) return;
  
  try {
    const res = await fetch(`/projects/toggle-sticky-docs/${currentEditingProject}`, {
      method: 'POST'
    });
    const data = await res.json();
    
    const btn = document.getElementById('sticky-docs-btn');
    if (!btn) return;
    
    if (data.sticky_docs) {
      btn.textContent = 'ON';
      btn.style.background = 'rgba(40, 167, 69, 0.3)';
      btn.style.borderColor = '#28a745';
      btn.style.color = '#28a745';
      console.log('📌 Sticky docs enabled for:', currentEditingProject);
    } else {
      btn.textContent = 'OFF';
      btn.style.background = '#3a3b3f';
      btn.style.borderColor = '#555';
      btn.style.color = '#aaa';
      console.log('📌 Sticky docs disabled for:', currentEditingProject);
    }

    // Update pinned doc indicator (will be null if just turned off, since toggle clears it)
    updatePinnedDocIndicator(data.sticky_doc_file);

  } catch (err) {
    console.error('Failed to toggle sticky docs:', err);
  }
}


// --- Replay / Stop last assistant message ---
let replayTimeout = null;

function replayLastAudio() {
  // If audio is playing or a replay is queued, stop everything
  if (isPlayingAudio || ttsProcessing || replayTimeout) {
    if (replayTimeout) {
      clearTimeout(replayTimeout);
      replayTimeout = null;
    }
    stopAllAudio();
    return;
  }

  if (!ttsEnabled) {
    ttsEnabled = true;
    const _ta=document.getElementById('tts-toggle-btn'); if(_ta){_ta.classList.add('active');_ta.style.background='rgba(76,175,80,0.4)';_ta.style.borderColor='rgba(76,175,80,0.8)';_ta.style.color='#aaffaa';}
    localStorage.setItem('tts-enabled', true);
  }

  const messages = window.loadedChat || [];
  const lastAssistant = [...messages].reverse().find(m => m.role === 'assistant');

  if (!lastAssistant) {
    showTTSNotification('Nothing to replay yet', true);
    return;
  }

  stopAllAudio();

  const replayBtn = document.querySelector('.replay-btn');
  if (replayBtn) {
    replayBtn.style.background = 'rgba(255, 210, 50, 0.3)';
    replayBtn.style.borderColor = 'rgba(255, 210, 50, 0.6)';
  }

  replayTimeout = setTimeout(() => {
    replayTimeout = null;
    const text = stripLinksForTTS(lastAssistant.content)   // strip links BEFORE the "(" → ". " conversion below
      .replace(/\u{1F4AF}/gu, 'one hundred percent')
      .replace(/(\w)\s*(?:[\u{1F000}-\u{1FFFF}\u{1F900}-\u{1F9FF}\u{1FA00}-\u{1FAFF}]|[\u{2600}-\u{26FF}]|[\u{2700}-\u{27BF}]|[\uD800-\uDBFF][\uDC00-\uDFFF])+/gu, '$1.')
      .replace(/(?:[\u{1F000}-\u{1FFFF}\u{1F900}-\u{1F9FF}\u{1FA00}-\u{1FAFF}]|[\u{2600}-\u{26FF}]|[\u{2700}-\u{27BF}]|[\uD800-\uDBFF][\uDC00-\uDFFF])+/gu, '')
      .replace(/\*\*/g, '').replace(/\*/g, '').replace(/_/g, '')
      .replace(/\s*[\u2013\u2014]\s*/g, '. ').replace(/\s*--\s*/g, '. ')
      .replace(/\.{3}/g, '. ').replace(/\u2026/g, '. ')
      .replace(/\s*\(\s*/g, '. ').replace(/\s*\)\s*/g, '. ')
      ;

    const lines = text.split('\n');
    for (const line of lines) {
      const t = line.trim();
      if (t.length > 2) {
        splitAndQueue(t);
      }
    }
    ttsStreamingComplete = true;
    if (ttsQueue.length > 0) processQueue();
  }, 150);
}