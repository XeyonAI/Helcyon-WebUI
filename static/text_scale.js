// Font-only theme scaling. This deliberately does not use CSS zoom: zoom
// changes the geometry of the whole page, including padding, controls and
// fixed-position layout. Explicit font sizes keep their existing ratios;
// inherited text continues to follow its already-scaled parent.
(function () {
  'use strict';

  const root = document.documentElement;
  const BASE_SIZE_VAR = '--hwui-font-size-base';
  const MARKER = 'data-hwui-font-scale';
  const SKIP_TAGS = new Set(['BR', 'HR', 'IMG', 'LINK', 'META', 'SCRIPT', 'STYLE', 'SVG', 'VIDEO']);
  let scheduled = false;

  function schedule() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      applyToCurrentDom();
    });
  }

  function applyToCurrentDom() {
    const body = document.body;
    if (!body) return;

    // Capture each authored/computed font size once. The rule below then
    // multiplies only that value, so margins, padding, borders and dimensions
    // remain untouched by the setting.
    const elements = [body, ...body.querySelectorAll('*')];
    for (const element of elements) {
      if (!(element instanceof HTMLElement) || SKIP_TAGS.has(element.tagName)) continue;
      if (element.hasAttribute(MARKER)) continue;

      const computed = getComputedStyle(element);
      const size = parseFloat(computed.fontSize);
      if (!Number.isFinite(size)) continue;

      const parent = element.parentElement;
      const parentSize = parent ? parseFloat(getComputedStyle(parent).fontSize) : NaN;
      const hasOwnSize = element === body ||
        Boolean(element.style.fontSize) ||
        !Number.isFinite(parentSize) ||
        Math.abs(size - parentSize) > 0.01;

      // Elements that inherit their parent's font size need no override: the
      // parent's scaled size already flows into them naturally.
      if (!hasOwnSize) continue;
      element.style.setProperty(BASE_SIZE_VAR, `${size}px`);
      element.setAttribute(MARKER, '');
    }
  }

  function start() {
    const style = document.createElement('style');
    style.id = 'hwui-font-scale-style';
    style.textContent = `[${MARKER}] { font-size: calc(var(${BASE_SIZE_VAR}) * var(--app-text-scale, 1)) !important; }`;
    document.head.appendChild(style);
    applyToCurrentDom();

    // Chat messages, menus and modal contents are often created after load.
    // Mark only newly-added elements; the browser applies scale changes to all
    // existing marked elements automatically through the custom property.
    new MutationObserver((mutations) => {
      if (mutations.some(mutation => mutation.addedNodes.length > 0)) schedule();
    }).observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();

// Editable prompt fields use one contenteditable layout for both rendering
// and selection. The original textarea remains hidden but authoritative:
// existing save/load and payload code continues to read its exact .value.
(function () {
  'use strict';

  const PROMPT_EDITOR_SELECTOR = [
    'textarea[data-hwui-prompt-editor]',
    '#author-note',
    '#edit-project-instructions',
    '#edit-project-rp-opener',
    '.opening-line-item textarea',
    '.bench-pack-editor-prompt textarea'
  ].join(',');
  const INDENT = '  ';
  const BULLET_RE = /^(\s*(?:[-*+•]|\d+[.)])\s+)(.*)$/;
  const enhanced = new WeakSet();
  const refreshers = new Set();

  function lineStart(value, position) {
    const newline = value.lastIndexOf('\n', Math.max(0, position - 1));
    return newline === -1 ? 0 : newline + 1;
  }

  function lineEnd(value, position) {
    const newline = value.indexOf('\n', position);
    return newline === -1 ? value.length : newline;
  }

  function indentationToRemove(line) {
    return line.startsWith('\t') ? 1 : Math.min(INDENT.length, (line.match(/^ */) || [''])[0].length);
  }

  function indentationEdit(value, start, end, outdent) {
    const blockStart = lineStart(value, start);
    const endProbe = end > start && end > 0 && value[end - 1] === '\n' ? end - 1 : end;
    const blockEnd = lineEnd(value, endProbe);
    const lines = value.slice(blockStart, blockEnd).split('\n');
    let changed = 0;
    const updatedLines = lines.map((line) => {
      if (!outdent) {
        changed += INDENT.length;
        return INDENT + line;
      }
      const remove = indentationToRemove(line);
      changed += remove;
      return line.slice(remove);
    });
    const replacement = updatedLines.join('\n');
    const nextValue = value.slice(0, blockStart) + replacement + value.slice(blockEnd);

    let nextStart;
    let nextEnd;
    if (start === end) {
      const removedBeforeCaret = outdent
        ? Math.min(changed, Math.max(0, start - blockStart))
        : 0;
      nextStart = nextEnd = start + (outdent ? -removedBeforeCaret : INDENT.length);
    } else if (!outdent) {
      nextStart = start + INDENT.length;
      nextEnd = end + changed;
    } else {
      let removedBeforeStart = 0;
      let removedBeforeEnd = 0;
      let offset = blockStart;
      lines.forEach((line, index) => {
        const remove = indentationToRemove(line);
        if (offset < start) removedBeforeStart += Math.min(remove, start - offset);
        if (offset < end) removedBeforeEnd += remove;
        offset += line.length + (index < lines.length - 1 ? 1 : 0);
      });
      nextStart = start - removedBeforeStart;
      nextEnd = end - removedBeforeEnd;
    }

    return { value: nextValue, start: nextStart, end: nextEnd };
  }

  function lineElements(editor) {
    return Array.from(editor.children).filter((element) => element.nodeType === Node.ELEMENT_NODE);
  }

  function serializeEditor(editor) {
    const elements = lineElements(editor);
    if (!elements.length) return '';
    return elements.map((element) => element.textContent || '').join('\n');
  }

  function serializeLooseEditor(editor) {
    if (!editor.childNodes.length) return '';
    return Array.from(editor.childNodes).map((node) => node.textContent || '').join('\n');
  }

  function lineForNode(editor, node) {
    let current = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
    while (current && current.parentElement !== editor) current = current.parentElement;
    return current && current.parentElement === editor ? current : null;
  }

  function offsetWithinLine(line, node, offset) {
    const range = document.createRange();
    range.selectNodeContents(line);
    try {
      range.setEnd(node, offset);
    } catch (_) {
      range.setEnd(line, line.childNodes.length);
    }
    return range.toString().length;
  }

  function editorOffset(editor, node, offset) {
    const elements = lineElements(editor);
    const line = lineForNode(editor, node);
    if (!line) return 0;
    const index = elements.indexOf(line);
    if (index < 0) return 0;
    let before = 0;
    for (let i = 0; i < index; i += 1) before += (elements[i].textContent || '').length + 1;
    return before + offsetWithinLine(line, node, offset);
  }

  function editorSelection(editor) {
    const selection = window.getSelection();
    if (!selection || !selection.rangeCount || !editor.contains(selection.anchorNode)) {
      const value = serializeEditor(editor);
      return { start: value.length, end: value.length };
    }
    const anchor = editorOffset(editor, selection.anchorNode, selection.anchorOffset);
    const focus = editorOffset(editor, selection.focusNode, selection.focusOffset);
    return anchor <= focus ? { start: anchor, end: focus } : { start: focus, end: anchor };
  }

  function textPoint(line, targetOffset) {
    const walker = document.createTreeWalker(line, NodeFilter.SHOW_TEXT);
    let remaining = targetOffset;
    let node;
    while ((node = walker.nextNode())) {
      if (remaining <= node.nodeValue.length) return [node, remaining];
      remaining -= node.nodeValue.length;
    }
    return [line, line.childNodes.length];
  }

  function setEditorSelection(editor, start, end) {
    const elements = lineElements(editor);
    if (!elements.length) return;
    const value = serializeEditor(editor);
    const clamp = (position) => Math.max(0, Math.min(value.length, position));
    const positions = [clamp(start), clamp(end)];
    const points = positions.map((position) => {
      let remaining = position;
      for (let i = 0; i < elements.length; i += 1) {
        const length = (elements[i].textContent || '').length;
        if (remaining <= length || i === elements.length - 1) return textPoint(elements[i], remaining);
        remaining -= length + 1;
      }
      return [elements[elements.length - 1], elements[elements.length - 1].childNodes.length];
    });
    const range = document.createRange();
    range.setStart(points[0][0], points[0][1]);
    range.setEnd(points[1][0], points[1][1]);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  }

  function prefixWidth(editor, prefix) {
    const sample = document.createElement('span');
    const computed = getComputedStyle(editor);
    sample.style.position = 'absolute';
    sample.style.visibility = 'hidden';
    sample.style.whiteSpace = 'pre';
    sample.style.font = computed.font;
    sample.style.letterSpacing = computed.letterSpacing;
    sample.textContent = prefix;
    editor.parentElement.appendChild(sample);
    const width = Math.ceil(sample.getBoundingClientRect().width);
    sample.remove();
    return Math.max(1, width);
  }

  function decorateLine(editor, line) {
    const text = line.textContent || '';
    line.className = 'hwui-prompt-editor-line';
    line.style.removeProperty('--hwui-hanging-indent');
    line.style.paddingLeft = '';
    line.style.textIndent = '';
    const match = text.match(BULLET_RE);
    if (!match) return;
    line.classList.add('hwui-prompt-editor-bullet-line');
    const width = prefixWidth(editor, match[1]);
    line.style.setProperty('--hwui-hanging-indent', `${width}px`);
    line.style.paddingLeft = `${width}px`;
    line.style.textIndent = `-${width}px`;
  }

  function decorateLines(editor) {
    lineElements(editor).forEach((line) => decorateLine(editor, line));
  }

  function renderEditor(editor, value, selection = null) {
    editor.replaceChildren();
    value.split('\n').forEach((text) => {
      const line = document.createElement('div');
      line.className = 'hwui-prompt-editor-line';
      line.textContent = text;
      if (!text) line.appendChild(document.createElement('br'));
      editor.appendChild(line);
    });
    editor.classList.toggle('is-empty', value.length === 0);
    decorateLines(editor);
    if (selection) {
      editor.focus();
      setEditorSelection(editor, selection.start, selection.end);
    }
  }

  function copyOuterLayout(source, shell, computed) {
    shell.style.width = computed.flex !== '0 1 auto' ? 'auto' : '100%';
    shell.style.maxWidth = computed.maxWidth;
    shell.style.flex = computed.flex;
    shell.style.flexGrow = computed.flexGrow;
    shell.style.flexShrink = computed.flexShrink;
    shell.style.flexBasis = computed.flexBasis;
    shell.style.minWidth = computed.minWidth;
    shell.style.margin = computed.margin;
    source.style.margin = '0';
  }

  function copyEditorMetrics(source, editor, computed) {
    [
      'fontFamily', 'fontSize', 'fontStyle', 'fontVariant', 'fontWeight',
      'lineHeight', 'letterSpacing', 'wordSpacing', 'textTransform', 'tabSize'
    ].forEach((property) => {
      editor.style[property] = computed[property];
    });
    editor.style.color = computed.color;
    editor.style.backgroundColor = computed.backgroundColor;
    editor.style.padding = computed.padding;
    editor.style.border = computed.border;
    editor.style.borderRadius = computed.borderRadius;
    editor.style.boxSizing = computed.boxSizing;
    editor.style.minHeight = computed.minHeight;
    editor.style.height = computed.height === 'auto' ? '' : computed.height;
    editor.style.maxHeight = computed.maxHeight;
    editor.style.resize = computed.resize;
    editor.style.overflow = computed.overflow;
    editor.style.whiteSpace = 'pre-wrap';
  }

  function enhance(source) {
    if (!(source instanceof HTMLTextAreaElement) || enhanced.has(source) || source.disabled) return;
    enhanced.add(source);
    const initial = getComputedStyle(source);
    const shell = document.createElement('div');
    shell.className = 'hwui-prompt-editor-shell';
    copyOuterLayout(source, shell, initial);
    source.parentNode.insertBefore(shell, source);
    shell.appendChild(source);

    const editor = document.createElement('div');
    editor.className = 'hwui-prompt-editor-editor';
    editor.contentEditable = 'true';
    editor.spellcheck = source.spellcheck;
    editor.setAttribute('role', 'textbox');
    editor.setAttribute('aria-multiline', 'true');
    if (source.getAttribute('aria-label')) editor.setAttribute('aria-label', source.getAttribute('aria-label'));
    editor.dataset.placeholder = source.placeholder || '';
    copyEditorMetrics(source, editor, initial);
    shell.insertBefore(editor, source);

    source.classList.add('hwui-prompt-editor-source');
    source.setAttribute('aria-hidden', 'true');
    source.tabIndex = -1;
    source.style.position = 'absolute';
    source.style.width = '1px';
    source.style.height = '1px';
    source.style.minHeight = '0';
    source.style.maxHeight = '0';
    source.style.opacity = '0';
    source.style.pointerEvents = 'none';
    source.style.overflow = 'hidden';

    let lastValue = source.value;
    const syncFromEditor = () => {
      const elements = lineElements(editor);
      const isSimpleStructure = elements.length === editor.childNodes.length &&
        elements.every((element) => element.tagName === 'DIV');
      const value = isSimpleStructure ? serializeEditor(editor) : serializeLooseEditor(editor);
      if (source.value !== value) {
        source.value = value;
        lastValue = value;
        source.dispatchEvent(new Event('input', { bubbles: true }));
      }
      editor.classList.toggle('is-empty', value.length === 0);
      if (!isSimpleStructure) {
        const selection = editorSelection(editor);
        renderEditor(editor, value, selection);
      } else {
        decorateLines(editor);
      }
    };

    const applyEditorEdit = (value, start, end) => {
      source.value = value;
      lastValue = value;
      renderEditor(editor, value, { start, end });
      source.dispatchEvent(new Event('input', { bubbles: true }));
    };

    editor.addEventListener('input', syncFromEditor);
    editor.addEventListener('paste', (event) => {
      event.preventDefault();
      const pasted = event.clipboardData?.getData('text/plain') || '';
      const selection = editorSelection(editor);
      const current = serializeEditor(editor);
      const nextValue = current.slice(0, selection.start) + pasted + current.slice(selection.end);
      const nextCaret = selection.start + pasted.length;
      applyEditorEdit(nextValue, nextCaret, nextCaret);
    });
    editor.addEventListener('keydown', (event) => {
      const selection = editorSelection(editor);
      const current = serializeEditor(editor);
      if (event.key === 'Tab') {
        event.preventDefault();
        const edit = indentationEdit(current, selection.start, selection.end, event.shiftKey);
        applyEditorEdit(edit.value, edit.start, edit.end);
      } else if (event.key === 'Enter') {
        event.preventDefault();
        const nextValue = current.slice(0, selection.start) + '\n' + current.slice(selection.end);
        applyEditorEdit(nextValue, selection.start + 1, selection.start + 1);
      }
    });

    const refreshValue = () => {
      if (!source.isConnected) {
        refreshers.delete(refreshValue);
        return;
      }
      if (source.value !== lastValue) {
        lastValue = source.value;
        const selection = document.activeElement === editor ? editorSelection(editor) : null;
        renderEditor(editor, source.value, selection);
      }
    };
    refreshers.add(refreshValue);

    renderEditor(editor, source.value);
    if (typeof ResizeObserver === 'function') {
      new ResizeObserver(() => decorateLines(editor)).observe(editor);
    }
  }

  function scan(root = document) {
    if (!root.querySelectorAll) return;
    root.querySelectorAll(PROMPT_EDITOR_SELECTOR).forEach(enhance);
  }

  function start() {
    scan();
    window.setInterval(() => refreshers.forEach((refresh) => refresh()), 250);
    new MutationObserver((mutations) => {
      mutations.forEach((mutation) => mutation.addedNodes.forEach((node) => {
        if (node.nodeType === Node.ELEMENT_NODE) {
          if (node.matches && node.matches(PROMPT_EDITOR_SELECTOR)) enhance(node);
          scan(node);
        }
      }));
    }).observe(document.body, { childList: true, subtree: true });
    window.addEventListener('resize', () => scan());
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
