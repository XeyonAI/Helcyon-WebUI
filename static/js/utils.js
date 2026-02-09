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
// MEMORY MODAL
// ==================================================
function openMemoryModal() {
  const modal = document.getElementById('memory-modal');
  modal.style.display = 'block';
  loadCharacterMemory();
}

function closeMemoryModal() {
  const modal = document.getElementById('memory-modal');
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