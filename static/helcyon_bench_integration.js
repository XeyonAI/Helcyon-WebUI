(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.HelcyonBenchPromptTransfer = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const PENDING_KEY = 'hwui_helcyon_bench_pending_prompt';
  const CONTEXT_KEY = 'hwui_helcyon_bench_context';
  const ASSOCIATIONS_KEY = 'hwui_helcyon_bench_capture_associations';
  const FORM_STATE_KEY = 'hwui_helcyon_bench_form_state';
  const DRAFT_KEY = 'hwui_chat_input_draft';
  const PANEL_HIDDEN_KEY = 'hwui_helcyon_bench_hidden_panels';
  const LIVE_ASSOCIATION_STATES = new Set([
    'queued',
    'loaded_into_chat',
    'submitted',
    'generating',
    'ready_to_capture'
  ]);
  const TERMINAL_ASSOCIATION_STATES = new Set([
    'error',
    'cancelled',
    'invalidated'
  ]);

  function read(storage, key) {
    try { return JSON.parse(storage.getItem(key) || 'null'); }
    catch (_) { storage.removeItem(key); return null; }
  }

  function createPayload(details, target) {
    return {
      association_id: String(details.association_id || (
        typeof crypto !== 'undefined' && crypto.randomUUID
          ? crypto.randomUUID()
          : `association-${Date.now()}-${Math.random().toString(16).slice(2)}`
      )),
      benchmark_id: String(details.benchmark_id || ''),
      benchmark_name: String(details.benchmark_name || ''),
      test_id: String(details.test_id || ''),
      test_title: String(details.test_title || ''),
      prompt_number: Number(details.prompt_number) || 0,
      category: String(details.category || ''),
      prompt_text: String(details.prompt_text || ''),
      original_prompt: String(details.original_prompt || details.prompt_text || ''),
      candidate_model_slot: String(details.candidate_model_slot || 'A'),
      candidate_model_name: String(details.candidate_model_name || ''),
      run_id: String(details.run_id || (
        typeof crypto !== 'undefined' && crypto.randomUUID
          ? crypto.randomUUID()
          : `bench-${Date.now()}`
      )),
      target_chat_filename: String(target.chat || ''),
      target_model: String(target.model || ''),
      created_at: new Date().toISOString()
    };
  }

  function associationKey(payload) {
    return [
      payload.run_id,
      payload.benchmark_id,
      payload.test_id,
      payload.candidate_model_slot
    ].join('::');
  }

  function saveAssociation(storage, payload) {
    const associations = read(storage, ASSOCIATIONS_KEY) || {};
    associations[associationKey(payload)] = payload;
    storage.setItem(ASSOCIATIONS_KEY, JSON.stringify(associations));
    return payload;
  }

  function associationStatus(association) {
    // status is the freshest client-side field (the chat page advances it
    // without re-mirroring generation_status), and it is what every badge
    // renders. Cleanup must read the same field the display reads, or a
    // record like {status:'error', generation_status:'submitted'} passes
    // every liveness check while rendering as a permanent Error badge.
    return String(
      association?.status || association?.generation_status || ''
    );
  }

  function isTerminalAssociation(association) {
    if (!association) return false;
    const marked = [association.status, association.generation_status]
      .map(value => String(value || ''))
      .some(value => TERMINAL_ASSOCIATION_STATES.has(value));
    if (!marked) return false;
    // A wrong-chat detour invalidation keeps its pre-detour state and is
    // reclaimed on returning to the target chat, so it is not terminal.
    return !recoverableTransferStatus(association);
  }

  function modelIdentity(value) {
    return String(value || '')
      .trim()
      .replaceAll('\\', '/')
      .split('/')
      .pop()
      .replace(/\.gguf$/i, '')
      .toLocaleLowerCase();
  }

  function associationMatchesTarget(association, target) {
    if (!association || !target) return false;
    const targetChat = String(target.chat || '');
    const targetModel = modelIdentity(target.model);
    const boundModel = modelIdentity(association.target_model);
    const chatIdKnown = Boolean(association.chat_id && target.chat_id);
    if (chatIdKnown) {
      // chat_id is rename-proof; prefer it over the filename it was bound to
      // at queue/registration time so a background auto-rename can't make a
      // still-live association look like it belongs to a different chat.
      if (association.chat_id !== target.chat_id) return false;
    } else {
      const boundChat = String(
        association.resolved_chat_filename || association.target_chat_filename || ''
      );
      if (targetChat && boundChat !== targetChat) return false;
    }
    if (targetModel && boundModel !== targetModel) return false;
    return Boolean(targetChat || targetModel || chatIdKnown);
  }

  function removeAssociations(storage, predicate) {
    const associations = read(storage, ASSOCIATIONS_KEY) || {};
    const removedIds = new Set();
    const removedResponseKeys = new Set();
    const removedRunIds = new Set();
    Object.entries(associations).forEach(([key, association]) => {
      if (!association || !predicate(association)) return;
      if (association.association_id) removedIds.add(association.association_id);
      removedResponseKeys.add(responseKey(association));
      if (association.run_id) removedRunIds.add(association.run_id);
      delete associations[key];
    });
    if (!removedIds.size) return 0;
    storage.setItem(ASSOCIATIONS_KEY, JSON.stringify(associations));
    const context = read(storage, CONTEXT_KEY);
    const removedCurrentContext = Boolean(
      context && removedIds.has(context.association_id)
    );
    if (removedCurrentContext) {
      storage.removeItem(CONTEXT_KEY);
    }
    const pending = read(storage, PENDING_KEY);
    if (pending && removedIds.has(pending.association_id)) {
      storage.removeItem(PENDING_KEY);
    }
    const draft = read(storage, DRAFT_KEY);
    if (
      draft
      && removedRunIds.has(draft.benchmark_run_id)
      && (!context || removedCurrentContext)
    ) {
      storage.removeItem(DRAFT_KEY);
    }
    const hidden = read(storage, PANEL_HIDDEN_KEY) || {};
    removedIds.forEach(associationId => { delete hidden[associationId]; });
    storage.setItem(PANEL_HIDDEN_KEY, JSON.stringify(hidden));
    const formState = read(storage, FORM_STATE_KEY);
    if (formState && typeof formState === 'object') {
      formState.associations = associations;
      formState.responses = formState.responses || {};
      formState.response_meta = formState.response_meta || {};
      removedResponseKeys.forEach(key => {
        if (!String(formState.responses[key] || '').trim()) {
          delete formState.response_meta[key];
        }
      });
      if (removedIds.has(formState.active_association_id)) {
        delete formState.active_association_id;
      }
      if (
        removedResponseKeys.has(formState.last_focused_response_key)
        && !String(formState.responses[formState.last_focused_response_key] || '').trim()
      ) {
        delete formState.last_focused_response_key;
        formState.focus_response_on_return = false;
      }
      storage.setItem(FORM_STATE_KEY, JSON.stringify(formState));
    }
    return removedIds.size;
  }

  function pruneAssociationsForTarget(storage, target) {
    return removeAssociations(storage, association => (
      associationStatus(association) !== 'captured'
      && !associationMatchesTarget(association, target)
    ));
  }

  function retireAssociation(storage, associationId) {
    if (!associationId) return false;
    return removeAssociations(
      storage,
      association => association.association_id === associationId
    ) > 0;
  }

  function queue(storage, details, target) {
    if (!target || !target.chat) return { ok: false, reason: 'no-chat' };
    if (!target.model) return { ok: false, reason: 'no-model' };
    const draft = read(storage, DRAFT_KEY);
    if (draft && String(draft.text || '').trim()) {
      return { ok: false, reason: 'draft-exists' };
    }
    const payload = {
      ...createPayload(details, target),
      status: 'queued'
    };
    storage.setItem(PENDING_KEY, JSON.stringify(payload));
    saveAssociation(storage, payload);
    return { ok: true, payload };
  }

  function consume(storage, currentChat, input) {
    const payload = read(storage, PENDING_KEY);
    if (!payload) return { ok: false, reason: 'none' };
    if (!currentChat || payload.target_chat_filename !== currentChat) {
      return { ok: false, reason: 'chat-mismatch', payload };
    }
    if (!input || String(input.value || '').trim()) {
      return { ok: false, reason: 'input-not-empty', payload };
    }
    input.value = payload.prompt_text;
    const context = {
      ...payload,
      status: 'loaded_into_chat',
      active_chat_matches: true
    };
    storage.setItem(CONTEXT_KEY, JSON.stringify(context));
    saveAssociation(storage, context);
    setPanelHidden(storage, context.association_id, false);
    storage.setItem(DRAFT_KEY, JSON.stringify({
      text: payload.prompt_text,
      chat: currentChat,
      character: '',
      updatedAt: Date.now(),
      benchmark_run_id: payload.run_id
    }));
    storage.removeItem(PENDING_KEY);
    return { ok: true, payload };
  }

  function handleChatChanged(storage, currentChat) {
    const context = read(storage, CONTEXT_KEY);
    if (!context) return { active: false };
    if (context.target_chat_filename === currentChat) {
      const activeContext = { ...context, active_chat_matches: true };
      // Returning to the target chat undoes a wrong-chat invalidation — the
      // detour must not permanently destroy an unsent transferred prompt.
      if (
        activeContext.status === 'invalidated' &&
        activeContext.status_before_invalidation
      ) {
        activeContext.status = activeContext.status_before_invalidation;
        delete activeContext.status_before_invalidation;
        delete activeContext.invalidated_at;
      }
      storage.setItem(CONTEXT_KEY, JSON.stringify(activeContext));
      saveAssociation(storage, activeContext);
      return { active: true, context: activeContext };
    }
    const inactiveContext = {
      ...context,
      active_chat_matches: false,
      chat_changed_at: new Date().toISOString(),
      status: context.status === 'loaded_into_chat' ? 'invalidated' : context.status,
      ...(context.status === 'loaded_into_chat'
        ? { status_before_invalidation: 'loaded_into_chat' }
        : {}),
      invalidated_at: new Date().toISOString()
    };
    storage.setItem(CONTEXT_KEY, JSON.stringify(inactiveContext));
    saveAssociation(storage, inactiveContext);
    return { active: false, reason: 'chat-mismatch', context: inactiveContext };
  }

  function rebindLoadedTransfer(storage, fromChat, toChat, promptText) {
    const context = read(storage, CONTEXT_KEY);
    if (
      !context ||
      !fromChat ||
      !toChat ||
      fromChat === toChat ||
      context.target_chat_filename !== fromChat ||
      context.status !== 'loaded_into_chat' ||
      context.source_user_message_id ||
      String(context.prompt_text || '').trim() !== String(promptText || '').trim()
    ) {
      return null;
    }
    const rebound = {
      ...context,
      target_chat_filename: toChat,
      active_chat_matches: true,
      chat_changed_at: new Date().toISOString()
    };
    delete rebound.chat_id;
    delete rebound.resolved_chat_filename;
    delete rebound.status_before_invalidation;
    delete rebound.invalidated_at;
    storage.setItem(CONTEXT_KEY, JSON.stringify(rebound));
    saveAssociation(storage, rebound);
    const draft = read(storage, DRAFT_KEY);
    if (draft && String(draft.text || '').trim() === String(promptText || '').trim()) {
      storage.setItem(DRAFT_KEY, JSON.stringify({ ...draft, chat: toChat }));
    }
    return rebound;
  }

  function recoverableTransferStatus(association) {
    if (!association) return false;
    if (['queued', 'loaded_into_chat'].includes(association.status)) return true;
    // A wrong-chat detour marks a loaded prompt invalidated; the exact-chat +
    // exact-text match below is what makes reclaiming it safe.
    return (
      association.status === 'invalidated' &&
      ['queued', 'loaded_into_chat'].includes(association.status_before_invalidation)
    );
  }

  function markSubmitted(storage, currentChat, promptText, source = {}) {
    let context = read(storage, CONTEXT_KEY);
    if (!context || context.target_chat_filename !== currentChat) {
      // The chat input trims on send, so compare trimmed text on both sides —
      // otherwise a prompt stored with outer whitespace can never be reclaimed.
      const submittedText = String(promptText || '').trim();
      context = Object.values(read(storage, ASSOCIATIONS_KEY) || {})
        .filter(association =>
          association &&
          recoverableTransferStatus(association) &&
          association.target_chat_filename === currentChat &&
          String(association.prompt_text || '').trim() === submittedText
        )
        .sort((left, right) =>
          (Date.parse(right.created_at || '') || 0) -
          (Date.parse(left.created_at || '') || 0)
        )[0] || null;
      if (context) storage.setItem(CONTEXT_KEY, JSON.stringify(context));
    }
    if (!context || context.target_chat_filename !== currentChat) return false;
    const submitted = {
      ...context,
      status: 'submitted',
      active_chat_matches: true,
      submitted_prompt_text: String(promptText || ''),
      submitted_at: source.submitted_at || new Date().toISOString(),
      source_user_message_id: String(source.source_user_message_id || ''),
      source_user_position: Number.isInteger(source.source_user_position)
        ? source.source_user_position
        : -1,
      chat_id: String(source.chat_id || context.chat_id || ''),
      character: String(source.character || '')
    };
    delete submitted.status_before_invalidation;
    delete submitted.invalidated_at;
    storage.setItem(CONTEXT_KEY, JSON.stringify(submitted));
    saveAssociation(storage, submitted);
    setPanelHidden(storage, submitted.association_id, false);
    return submitted;
  }

  function updateAssociation(storage, associationId, updates) {
    const associations = read(storage, ASSOCIATIONS_KEY) || {};
    const key = Object.keys(associations).find(
      item => associations[item] && associations[item].association_id === associationId
    );
    if (!key) return null;
    associations[key] = { ...associations[key], ...updates };
    storage.setItem(ASSOCIATIONS_KEY, JSON.stringify(associations));
    const context = read(storage, CONTEXT_KEY);
    if (context && context.association_id === associationId) {
      storage.setItem(CONTEXT_KEY, JSON.stringify({ ...context, ...updates }));
    }
    return associations[key];
  }

  function getAssociation(storage, runId, benchmarkId, testId, slot) {
    const associations = read(storage, ASSOCIATIONS_KEY) || {};
    return associations[[runId, benchmarkId, testId, slot].join('::')] || null;
  }

  function getAssociationById(storage, associationId) {
    if (!associationId) return null;
    return Object.values(read(storage, ASSOCIATIONS_KEY) || {}).find(
      association => association && association.association_id === associationId
    ) || null;
  }

  function getAssociations(storage) {
    return read(storage, ASSOCIATIONS_KEY) || {};
  }

  function getContext(storage) {
    return read(storage, CONTEXT_KEY);
  }

  function candidateChatNeedsRotation(storage, runId, benchmarkId, slot, currentChat) {
    if (!runId || !benchmarkId || !slot || !currentChat) return false;
    return Object.values(read(storage, ASSOCIATIONS_KEY) || {}).some(association => {
      if (!association || association.invalidated_reason === 'cleared') return false;
      if (association.status === 'invalidated' || association.generation_status === 'invalidated') {
        return false;
      }
      const boundChat = association.resolved_chat_filename || association.target_chat_filename || '';
      const wasSubmitted = Boolean(association.source_user_message_id) || [
        'submitted',
        'generating',
        'ready_to_capture',
        'captured',
        'cancelled',
        'error'
      ].includes(association.status || association.generation_status || '');
      return (
        wasSubmitted &&
        association.run_id === runId &&
        association.benchmark_id === benchmarkId &&
        association.candidate_model_slot !== slot &&
        boundChat === currentChat
      );
    });
  }

  function dismissContext(storage, associationId) {
    const context = read(storage, CONTEXT_KEY);
    if (!context || (associationId && context.association_id !== associationId)) {
      return false;
    }
    storage.removeItem(CONTEXT_KEY);
    return true;
  }

  function restoreContext(storage, association) {
    if (!association || !association.association_id) return null;
    storage.setItem(CONTEXT_KEY, JSON.stringify(association));
    return association;
  }

  function isPanelHidden(storage, associationId) {
    const hidden = read(storage, PANEL_HIDDEN_KEY) || {};
    return Boolean(associationId && hidden[associationId]);
  }

  function setPanelHidden(storage, associationId, hidden) {
    if (!associationId) return false;
    const states = read(storage, PANEL_HIDDEN_KEY) || {};
    if (hidden) states[associationId] = true;
    else delete states[associationId];
    storage.setItem(PANEL_HIDDEN_KEY, JSON.stringify(states));
    return Boolean(hidden);
  }

  function responseKey(association) {
    return [
      association && association.benchmark_id,
      association && association.test_id,
      association && association.candidate_model_slot
    ].join(':');
  }

  function resetCurrentRun(storage, state, benchmarkId) {
    const packId = String(benchmarkId || '');
    const next = state && typeof state === 'object' ? { ...state } : {};
    if (!packId) return next;
    const prefix = `${packId}:`;
    next.responses = { ...(next.responses || {}) };
    next.response_meta = { ...(next.response_meta || {}) };
    Object.keys(next.responses).forEach(key => {
      if (key.startsWith(prefix)) delete next.responses[key];
    });
    Object.keys(next.response_meta).forEach(key => {
      if (key.startsWith(prefix)) delete next.response_meta[key];
    });
    const activeAssociation = getAssociationById(storage, next.active_association_id)
      || Object.values(next.associations || {}).find(
        association => association?.association_id === next.active_association_id
      );
    removeAssociations(
      storage,
      association => association.benchmark_id === packId
    );
    next.associations = getAssociations(storage);
    next.run_ids = { ...(next.run_ids || {}) };
    delete next.run_ids[packId];
    if (activeAssociation?.benchmark_id === packId) delete next.active_association_id;
    if (String(next.last_focused_response_key || '').startsWith(prefix)) {
      delete next.last_focused_response_key;
      next.focus_response_on_return = false;
    }
    if (next.selected_pack === packId) {
      delete next.active_prompt_test_id;
      delete next.active_prompt_title;
      delete next.active_prompt_number;
      delete next.current_candidate_slot;
    }
    storage.setItem(FORM_STATE_KEY, JSON.stringify(next));
    return next;
  }

  function sanitizeAssociations(map) {
    const cleaned = {};
    Object.entries(map && typeof map === 'object' ? map : {}).forEach(([key, value]) => {
      // Drop legacy/malformed records without an association_id — they can
      // never be registered, refreshed, or captured, only mislead recovery.
      if (value && typeof value === 'object' && value.association_id) {
        cleaned[key] = value;
      }
    });
    return cleaned;
  }

  function mergeSessionState(local, server, target) {
    const localState = local && typeof local === 'object' ? { ...local } : {};
    const serverState = server && typeof server === 'object' ? { ...server } : {};
    const merged = { ...localState, ...serverState };
    // Associations advance in this browser's localStorage first (the chat page
    // updates them without pushing the session), so per-key the local copy is
    // never older than the server mirror.
    merged.associations = {
      ...sanitizeAssociations(serverState.associations),
      ...sanitizeAssociations(localState.associations)
    };
    // The server session lags local edits by a debounce; never let an empty
    // server value erase a non-empty local response draft.
    const localResponses = localState.responses && typeof localState.responses === 'object'
      ? localState.responses
      : {};
    const serverResponses = serverState.responses && typeof serverState.responses === 'object'
      ? serverState.responses
      : {};
    merged.responses = { ...serverResponses };
    Object.entries(localResponses).forEach(([key, value]) => {
      if (String(value || '').trim() && !String(serverResponses[key] || '').trim()) {
        merged.responses[key] = value;
      }
    });
    const localMeta = localState.response_meta && typeof localState.response_meta === 'object'
      ? localState.response_meta
      : {};
    merged.response_meta = {
      ...(serverState.response_meta && typeof serverState.response_meta === 'object'
        ? serverState.response_meta
        : {})
    };
    Object.entries(localMeta).forEach(([key, value]) => {
      if (value && merged.response_meta[key] === undefined) {
        merged.response_meta[key] = value;
      }
    });
    // Older session snapshots could retain capture metadata and a terminal
    // association after their response text had already been cleared. Retire
    // that contradictory state during hydration so an empty slot starts empty.
    const retiredAssociationIds = new Set();
    Object.keys(merged.response_meta).forEach(key => {
      if (!String(merged.responses[key] || '').trim()) {
        delete merged.response_meta[key];
      }
    });
    const enforceTarget = Boolean(target && typeof target === 'object');
    const completeTarget = Boolean(target?.chat && target?.model);
    Object.entries(merged.associations).forEach(([key, association]) => {
      const status = associationStatus(association);
      const hasResponse = String(merged.responses[responseKey(association)] || '').trim();
      // Terminal on either field means the tracked turn can never be captured
      // again — retire it outright, even when a manually pasted response
      // remains in the slot (the response text itself is never touched here).
      const terminal = isTerminalAssociation(association);
      const recoverable = (
        !terminal
        && LIVE_ASSOCIATION_STATES.has(status)
        && (!enforceTarget || (
          completeTarget && associationMatchesTarget(association, target)
        ))
      );
      if (terminal || (!hasResponse && !recoverable)) {
        if (association.association_id) retiredAssociationIds.add(association.association_id);
        delete merged.associations[key];
      }
    });
    const activeAssociationStillExists = Object.values(merged.associations).some(
      association => association?.association_id === merged.active_association_id
    );
    if (
      retiredAssociationIds.has(merged.active_association_id)
      || (merged.active_association_id && !activeAssociationStillExists)
    ) {
      delete merged.active_association_id;
    }
    if (
      merged.last_focused_response_key
      && !String(merged.responses[merged.last_focused_response_key] || '').trim()
    ) {
      delete merged.last_focused_response_key;
      merged.focus_response_on_return = false;
    }
    ['model_a', 'model_b'].forEach(key => {
      if (!String(merged[key] || '').trim() && String(localState[key] || '').trim()) {
        merged[key] = localState[key];
      }
    });
    return merged;
  }

  function invalidateAssociationsForResponseKeys(storage, keys) {
    const wanted = new Set((keys || []).filter(Boolean));
    if (!wanted.size) return 0;
    const associations = read(storage, ASSOCIATIONS_KEY) || {};
    let changed = 0;
    Object.entries(associations).forEach(([key, association]) => {
      if (!association || !wanted.has(responseKey(association))) return;
      if (association.status === 'invalidated' && association.invalidated_reason === 'cleared') return;
      associations[key] = {
        ...association,
        status: 'invalidated',
        generation_status: 'invalidated',
        invalidated_at: new Date().toISOString(),
        invalidated_reason: 'cleared'
      };
      delete associations[key].status_before_invalidation;
      changed += 1;
    });
    if (changed) storage.setItem(ASSOCIATIONS_KEY, JSON.stringify(associations));
    const context = read(storage, CONTEXT_KEY);
    if (context && wanted.has(responseKey(context))) {
      storage.removeItem(CONTEXT_KEY);
    }
    return changed;
  }

  function applyAssociationToSession(state, association, captured) {
    const next = state && typeof state === 'object' ? { ...state } : {};
    if (!association) return next;
    const key = responseKey(association);
    const existingMeta = next.response_meta && typeof next.response_meta === 'object'
      ? next.response_meta[key]
      : null;
    if (
      !captured
      && association.status === 'submitted'
      && existingMeta
      && String(existingMeta.origin || '').includes('capture')
      && existingMeta.association_id
      && association.association_id
      && existingMeta.association_id !== association.association_id
    ) {
      next.responses = { ...(next.responses || {}) };
      next.response_meta = { ...(next.response_meta || {}) };
      delete next.responses[key];
      delete next.response_meta[key];
    }
    next.selected_view = 'test';
    next.selected_category = String(association.category || next.selected_category || '');
    next.selected_pack = String(association.benchmark_id || next.selected_pack || '');
    next.selected_pack_name = String(association.benchmark_name || next.selected_pack_name || '');
    next.active_prompt_test_id = String(association.test_id || next.active_prompt_test_id || '');
    next.active_prompt_title = String(association.test_title || next.active_prompt_title || '');
    next.active_prompt_number = Number(association.prompt_number) || next.active_prompt_number || 0;
    next.current_candidate_slot = String(association.candidate_model_slot || next.current_candidate_slot || '');
    next.run_ids = { ...(next.run_ids || {}) };
    if (association.benchmark_id && association.run_id) {
      next.run_ids[association.benchmark_id] = association.run_id;
    }
    if (captured && typeof captured.response_text === 'string') {
      next.responses = { ...(next.responses || {}), [key]: captured.response_text };
      next.response_meta = {
        ...(next.response_meta || {}),
        [key]: {
          origin: 'captured',
          association_id: association.association_id,
          assistant_message_id: captured.assistant_message_id || '',
          captured_at: association.captured_at || new Date().toISOString()
        }
      };
      next.last_focused_response_key = key;
      next.focus_response_on_return = true;
    }
    return next;
  }

  function returnUrl(association) {
    const query = new URLSearchParams({ tab: 'helcyon-bench' });
    if (association && association.association_id) {
      query.set('association', association.association_id);
    }
    return `/config?${query.toString()}`;
  }

  return {
    PENDING_KEY,
    CONTEXT_KEY,
    ASSOCIATIONS_KEY,
    FORM_STATE_KEY,
    DRAFT_KEY,
    PANEL_HIDDEN_KEY,
    createPayload,
    associationStatus,
    isTerminalAssociation,
    queue,
    consume,
    handleChatChanged,
    rebindLoadedTransfer,
    associationMatchesTarget,
    pruneAssociationsForTarget,
    retireAssociation,
    markSubmitted,
    updateAssociation,
    getAssociation,
    getAssociationById,
    getAssociations,
    getContext,
    candidateChatNeedsRotation,
    dismissContext,
    restoreContext,
    isPanelHidden,
    setPanelHidden,
    responseKey,
    resetCurrentRun,
    sanitizeAssociations,
    mergeSessionState,
    invalidateAssociationsForResponseKeys,
    applyAssociationToSession,
    returnUrl
  };
});

// ============================================================
// HWUI Free build — Pro-upgrade modal
// Shared by the Theme Editor and the Benchmark workspace (both loaded on
// this page). Locked actions stay visible; clicking them opens this modal
// instead of performing the action.
// ============================================================
(function () {
  const GUMROAD_URL = 'https://xeyonai.gumroad.com/l/bsmupk';

  function buildProModal() {
    const modal = document.createElement('div');
    modal.id = 'hwui-pro-modal';
    modal.className = 'modal';
    modal.innerHTML =
      '<div class="modal-content" style="max-width:420px;">' +
        '<div class="modal-header">' +
          '<h3 id="hwui-pro-modal-title">Upgrade to HWUI Pro</h3>' +
          '<span class="close" onclick="closeHwuiProModal()">&times;</span>' +
        '</div>' +
        '<div class="modal-body" style="padding:22px; text-align:center;">' +
          '<div style="font-size:46px; margin-bottom:12px;">⭐</div>' +
          '<p id="hwui-pro-modal-message" style="color:#ccc; font-size:14px; line-height:1.6; margin:0 0 20px;"></p>' +
          '<p style="color:#888; font-size:12px; margin:0 0 18px;">One-time payment. No subscription. Yours forever.</p>' +
          '<button onclick="window.open(\'' + GUMROAD_URL + '\', \'_blank\')" ' +
            'style="background:linear-gradient(135deg,#667eea,#764ba2); border:none; color:#fff; ' +
            'font-weight:600; font-size:14px; padding:10px 26px; border-radius:8px; cursor:pointer;">' +
            'Get HWUI Pro' +
          '</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(modal);
    modal.addEventListener('click', function (event) {
      if (event.target === modal) closeHwuiProModal();
    });
    return modal;
  }

  window.showHwuiProModal = function (title, message) {
    const modal = document.getElementById('hwui-pro-modal') || buildProModal();
    document.getElementById('hwui-pro-modal-title').textContent = title || 'Upgrade to HWUI Pro';
    document.getElementById('hwui-pro-modal-message').textContent =
      message || 'This feature is available in HWUI Pro.';
    modal.style.display = 'block';
  };

  window.closeHwuiProModal = function () {
    const modal = document.getElementById('hwui-pro-modal');
    if (modal) modal.style.display = 'none';
  };
})();
