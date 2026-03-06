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
        loadCharacterMemory();
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
      content: randomLine
    };
    
    // Add to chat history
    window.loadedChat.push(openingMessage);
    
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
          alert('Upload failed: ' + result.error);
          return;
        }
        
        console.log('✅ Document uploaded:', result.filename);
        
        // Reload documents list
        await loadProjectDocuments(projectName);
        
        // Clear input
        e.target.value = '';
        
      } catch (err) {
        console.error('❌ Upload error:', err);
        alert('Failed to upload document');
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
  if (!confirm(`Delete "${filename}"?`)) return;
  
  try {
    const res = await fetch(`/projects/${projectName}/documents/${filename}`, {
      method: 'DELETE'
    });
    
    const result = await res.json();
    
    if (result.error) {
      alert('Delete failed: ' + result.error);
      return;
    }
    
    console.log('🗑️ Document deleted:', filename);
    
    // Reload documents list
    await loadProjectDocuments(projectName);
    
  } catch (err) {
    console.error('❌ Delete error:', err);
    alert('Failed to delete document');
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
const TTS_START_THRESHOLD = 1;  // Start playing after this many sentences buffered

let lastTTSResponseText = '';  // 🔊 Stored for Replay button


function toggleTTS() {
  ttsEnabled = !ttsEnabled;
  const btn = document.getElementById('tts-toggle-btn');

  if (ttsEnabled) {
    btn.classList.add('active');
    console.log('🔊 TTS enabled');
    checkTTSStatus();
  } else {
    btn.classList.remove('active');
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
      document.getElementById('tts-toggle-btn')?.classList.remove('active');
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


// --- Called from streaming loop for each chunk ---
function bufferTextForTTS(chunk) {
  if (!ttsEnabled || !chunk) return;

  // Reset streaming flag at start of each new response
  if (ttsSentenceBuffer === '' && ttsQueue.length === 0 && !ttsProcessing) {
    ttsStreamingComplete = false;
  }

  // Fix contractions FIRST before apostrophes get stripped
  chunk = fixContractionsForTTS(chunk);

  // Normalise dashes to commas, strip ellipsis entirely
  chunk = chunk.replace(/\.{3}/g, ' . . . ').replace(/\u2026/g, ' . . . ').replace(/\.{2}/g, ' . . ');
  chunk = chunk.replace(/:/g, '. ');
  chunk = chunk.replace(/\s*\(\s*/g, ', ').replace(/\s*\)\s*/g, ', ');
  // Replace specific emojis with spoken words before catch-all strips them
  chunk = chunk.replace(/\u{1F4AF}/gu, 'one hundred percent');
  // Replace remaining emojis with full stop tight to preceding word — \s* eats the space
  chunk = chunk.replace(/(\w)\s*(?:[\u{1F000}-\u{1FFFF}]|[\u{2600}-\u{26FF}]|[\u{2700}-\u{27BF}]|[\uD800-\uDBFF][\uDC00-\uDFFF])+/gu, '$1.');
  chunk = chunk.replace(/(?:[\u{1F000}-\u{1FFFF}]|[\u{2600}-\u{26FF}]|[\u{2700}-\u{27BF}]|[\uD800-\uDBFF][\uDC00-\uDFFF])+/gu, '');

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
  const sentenceRegex = /[^.!?:;]+[.!?:;]+[)"'*_]*\s*/g;
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


function splitAndQueue(text) {
  const cleaned = text.trim()
    .replace(/\u{1F4AF}/gu, 'one hundred percent')
    .replace(/(\w)\s*(?:[\u{1F000}-\u{1FFFF}]|[\u{2600}-\u{26FF}]|[\u{2700}-\u{27BF}]|[\uD800-\uDBFF][\uDC00-\uDFFF])+/gu, '$1.')
    .replace(/(?:[\u{1F000}-\u{1FFFF}]|[\u{2600}-\u{26FF}]|[\u{2700}-\u{27BF}]|[\uD800-\uDBFF][\uDC00-\uDFFF])+/gu, '')
    .replace(/\*\*/g, '').replace(/\*/g, '').replace(/_/g, '')
    .replace(/^[-*•]\s+/, '').replace(/^\d+\.\s+/, '')
    .replace(/:/g, '. ')
    .replace(/\s*\(\s*/g, ', ').replace(/\s*\)\s*/g, ', ')
    .trim();

  if (cleaned.length === 0) return;

  // Ensure every sentence ends with punctuation — full stop unless it's already ? or !
  const withPunct = /[.!?]$/.test(cleaned) ? cleaned : cleaned + '.';

  // Send the whole sentence as one chunk — no word-boundary splitting
  if (withPunct.length > 2) {
    ttsQueue.push(withPunct);
    console.log(`📝 Queued: "${withPunct.substring(0, 50)}"`);
    if (!ttsProcessing && ttsQueue.length >= TTS_START_THRESHOLD) {
      console.log('🎬 Threshold reached, starting playback');
      processQueue();
    }
  }
}


// --- Called when streaming finishes ---
function flushTTSBuffer() {
  if (!ttsEnabled) return;

  // Flush anything left in the buffer (no newline at end, no punctuation)
  const remaining = ttsSentenceBuffer.trim()
    .replace(/(\w)\s*(?:[\u{1F000}-\u{1FFFF}]|[\u{2600}-\u{26FF}]|[\u{2700}-\u{27BF}]|[\uD800-\uDBFF][\uDC00-\uDFFF])+/gu, '$1.')
    .replace(/(?:[\u{1F000}-\u{1FFFF}]|[\u{2600}-\u{26FF}]|[\u{2700}-\u{27BF}]|[\uD800-\uDBFF][\uDC00-\uDFFF])+/gu, '')
    .replace(/\*\*/g, '').replace(/\*/g, '').replace(/_/g, '')
    .trim();

  if (remaining.length > 2) {
    const withPunct = /[.!?]$/.test(remaining) ? remaining : remaining + '.';
    ttsQueue.push(withPunct);
    console.log(`📝 Flushed: "${withPunct.substring(0, 50)}"`);
  }
  ttsSentenceBuffer = '';
  ttsStreamingComplete = true;

  if (!ttsProcessing && ttsQueue.length > 0) {
    console.log('🎬 Starting playback after flush');
    processQueue();
  }
}


// --- Sequential queue processor with prefetch ---
async function processQueue() {
  if (ttsProcessing) return;
  ttsProcessing = true;

  const btn = document.getElementById('tts-toggle-btn');
  if (btn) btn.classList.add('speaking');

  // Fetch a sentence from TTS server, return a blob URL or null
  async function fetchAudio(sentence) {
    try {
      const response = await fetch('/api/tts/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: sentence, voice: ttsVoice })
      });
      if (!response.ok) return null;
      const blob = await response.blob();
      return URL.createObjectURL(blob);
    } catch (err) {
      console.error('❌ TTS fetch error:', err);
      return null;
    }
  }

  // Check if queue is empty
  if (ttsQueue.length === 0) {
    ttsProcessing = false;
    if (btn) btn.classList.remove('speaking');
    return;
  }

  // Pre-fetch buffer: always keep 2-3 sentences generating ahead
  const prefetchBuffer = [];
  
  // Start fetching first 5 sentences immediately (aggressive buffering)
  const initialFetches = Math.min(2, ttsQueue.length);
  for (let i = 0; i < initialFetches; i++) {
    prefetchBuffer.push(fetchAudio(ttsQueue.shift()));
  }

  while (true) {
    if (!ttsEnabled) break;

    // Wait for the next audio in the buffer
    const audioUrl = prefetchBuffer.length > 0 ? await prefetchBuffer.shift() : null;
    
    // Immediately start fetching more to keep buffer full
    while (prefetchBuffer.length < 2 && ttsQueue.length > 0) {
      prefetchBuffer.push(fetchAudio(ttsQueue.shift()));
    }

    if (audioUrl) {
      // Play current sentence — while playing, poll for new queue items and prefetch them
      await new Promise((resolve) => {
        currentAudio = new Audio(audioUrl);
        currentAudio.playbackRate = 1.0;
        isPlayingAudio = true;

        // Poll during playback so next sentences start fetching immediately
        // rather than waiting until current sentence finishes
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
        break;  // Completely done
      } else {
        // Wait for more sentences from streaming
        await new Promise(r => setTimeout(r, 25));
        if (ttsQueue.length > 0) {
          // More sentences arrived - fetch them
          while (prefetchBuffer.length < 2 && ttsQueue.length > 0) {
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
    document.getElementById('tts-toggle-btn')?.classList.add('active');
  }

  const savedVoice = localStorage.getItem('tts-voice');
  if (savedVoice) ttsVoice = savedVoice;

  loadTTSVoices();

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
    console.log('🎤 Recording started...');

  } catch (err) {
    console.error('🎤 Mic access error:', err);
    alert('Could not access microphone. Check Brave permissions.');
  }
}

async function stopVoiceInput() {
  if (!isRecording || !mediaRecorder) return;

  isRecording = false;
  const micBtn = document.getElementById('mic-btn');
  micBtn.classList.remove('listening');

  return new Promise((resolve) => {
    mediaRecorder.onstop = async () => {
      console.log('🎤 Recording stopped, sending to Whisper...');

      const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
      const formData = new FormData();
      formData.append('audio', audioBlob, 'recording.webm');

      try {
        micBtn.classList.add('processing');

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
    document.getElementById('tts-toggle-btn')?.classList.add('active');
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
    const text = lastAssistant.content
      .replace(/\u{1F4AF}/gu, 'one hundred percent')
      .replace(/(\w)\s*(?:[\u{1F000}-\u{1FFFF}]|[\u{2600}-\u{26FF}]|[\u{2700}-\u{27BF}]|[\uD800-\uDBFF][\uDC00-\uDFFF])+/gu, '$1.')
      .replace(/(?:[\u{1F000}-\u{1FFFF}]|[\u{2600}-\u{26FF}]|[\u{2700}-\u{27BF}]|[\uD800-\uDBFF][\uDC00-\uDFFF])+/gu, '')
      .replace(/\*\*/g, '').replace(/\*/g, '').replace(/_/g, '')
      .replace(/\s*[\u2013\u2014]\s*/g, '. ').replace(/\s*--\s*/g, '. ')
      .replace(/\.{3}/g, ' . . . ').replace(/\u2026/g, ' . . . ')
      .replace(/:/g, '. ')
      .replace(/\s*\(\s*/g, ', ').replace(/\s*\)\s*/g, ', ')
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