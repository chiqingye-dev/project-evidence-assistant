/* Local reading presentation only. It never fetches sources or calls a model. */
(() => {
  'use strict';
  const node = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  };
  function inline(parent, value) {
    if (!/(`[^`]+`|\*\*[^*]+\*\*)/.test(value)) { parent.textContent = value; return; }
    for (const piece of value.split(/(`[^`]+`|\*\*[^*]+\*\*)/)) {
      parent.append(piece.startsWith('`') && piece.endsWith('`') ? node('code', piece.slice(1, -1))
        : piece.startsWith('**') && piece.endsWith('**') ? node('strong', piece.slice(2, -2)) : node('span', piece));
    }
  }
  const cells = line => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(item => item.trim());
  const separator = line => /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
  let diagramId = 0;

  function diagram(parent, source) {
    const figure = node('figure', undefined, 'learning-diagram');
    figure.append(node('figcaption', '关系示意 · 窄屏可在图内横向滚动；结合本块引用核对'));
    const original = node('details', undefined, 'diagram-source');
    original.append(node('summary', '查看图表原文'), node('pre', source));
    const allowed = /^(?:flowchart\s+(?:LR|RL|TB|TD|BT)|graph\s+(?:LR|RL|TB|TD|BT)|sequenceDiagram|stateDiagram-v2|erDiagram)\b/.test(source.trim());
    const unsafe = /%%\{|^\s*---|\bclick\b|(?:https?|javascript|data):|<\/?[a-z]|@\{|\b(?:image|icon)\s*:/im.test(source);
    if (!allowed || unsafe || source.length > 1200) {
      figure.append(node('p', '此图暂以原文显示，请结合引用阅读。', 'field-help'), original);
      parent.append(figure); return;
    }
    const frame = node('iframe', undefined, 'learning-diagram-frame');
    frame.setAttribute('sandbox', 'allow-scripts');
    frame.setAttribute('referrerpolicy', 'no-referrer');
    frame.setAttribute('title', '源码关系示意图');
    frame.setAttribute('loading', 'lazy');
    const origin = globalThis.location?.origin;
    if (!/^https?:\/\/(?:127\.0\.0\.1|localhost)(?::\d+)?$/.test(origin || '')) {
      figure.append(original); parent.append(figure); return;
    }
    const resource = `${origin}/static/vendor/mermaid-12.3.0.min.js`;
    // Mermaid's strict SVG text renderer drops bare '>' inside labels.
    // Encode only quoted display text; leave graph arrows and stored source intact.
    const displaySource = source.replace(/"(?:\\.|[^"\\])*"/g, label => label.replace(/>/g, '&gt;'));
    const data = JSON.stringify(displaySource).replace(/</g, '\\u003c');
    const identifier = `lesson-diagram-${++diagramId}`;
    const onSize = event => {
      const message = event.data;
      if (event.source !== frame.contentWindow || message?.type !== 'learning-diagram-size'
        || message.id !== identifier || !Number.isFinite(message.height)) return;
      frame.style.height = `${Math.max(90, Math.min(600, message.height))}px`;
      globalThis.removeEventListener?.('message', onSize);
    };
    globalThis.addEventListener?.('message', onSize);
    globalThis.setTimeout?.(() => globalThis.removeEventListener?.('message', onSize), 15000);
    // Both the library and generated SVG live in an opaque-origin iframe.
    // The policy allows only the local library, inline setup, styles and data images.
    frame.srcdoc = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline' ${resource}; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; font-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'">
<style>body{margin:0;padding:10px;font:14px sans-serif;color:#173e35;background:#fff}#diagram{overflow:auto}svg{max-width:100%;height:auto}pre{white-space:pre-wrap}</style>
<script src="${resource}"></script></head><body><div id="diagram" role="img" aria-label="源码关系示意图"></div>
<script>(async()=>{const source=${data};const target=document.getElementById('diagram');try{mermaid.initialize({startOnLoad:false,securityLevel:'strict',htmlLabels:false,maxTextSize:1200,maxEdges:40,suppressErrorRendering:true,theme:'neutral',fontFamily:'sans-serif'});const result=await mermaid.render('learning-diagram',source);target.innerHTML=result.svg;const svg=target.querySelector('svg');if(svg)svg.style.minWidth=Math.min(620,svg.viewBox.baseVal.width)+'px';}catch{target.textContent='图表未能渲染，可查看下方图表原文。';}requestAnimationFrame(()=>parent.postMessage({type:'learning-diagram-size',id:'${identifier}',height:Math.ceil(target.getBoundingClientRect().height)+28},'*'));})();</script></body></html>`;
    figure.append(frame, original); parent.append(figure);
  }

  function render(parent, value) {
    if (typeof value !== 'string') return;
    const lines = value.replace(/\r\n?/g, '\n').split('\n');
    for (let i = 0; i < lines.length;) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }
      const fence = /^\s*```([\w-]*)\s*$/.exec(line);
      if (fence) {
        const start = i++; const body = [];
        while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) body.push(lines[i++]);
        if (i === lines.length) { parent.append(node('pre', lines.slice(start).join('\n'))); break; }
        i++;
        if (fence[1].toLowerCase() === 'mermaid') diagram(parent, body.join('\n'));
        else { const pre = node('pre', undefined, 'learning-code'); pre.append(node('code', body.join('\n'))); parent.append(pre); }
        continue;
      }
      if (line.includes('|') && i + 1 < lines.length && separator(lines[i + 1])) {
        const scroll = node('div', undefined, 'learning-table-scroll');
        const table = node('table', undefined, 'learning-table'); const head = node('thead'); const row = node('tr');
        const headings = cells(line);
        for (const text of headings) { const cell = node('th'); cell.setAttribute('scope', 'col'); inline(cell, text); row.append(cell); }
        head.append(row); table.append(head); i += 2; const body = node('tbody');
        while (i < lines.length && lines[i].trim() && lines[i].includes('|') && !/^\s*```/.test(lines[i])) {
          const values = cells(lines[i]);
          if (values.length !== headings.length) break;
          const tr = node('tr'); for (const text of values) { const cell = node('td'); inline(cell, text); tr.append(cell); }
          body.append(tr); i++;
        }
        table.append(body); scroll.append(table); parent.append(scroll); continue;
      }
      if (/^\s*(?:[-*]\s+|\d+\.\s+)/.test(line)) {
        const ordered = /^\s*\d+\./.test(line); const list = node(ordered ? 'ol' : 'ul');
        const pattern = ordered ? /^\s*\d+\.\s+(.+)$/ : /^\s*[-*]\s+(.+)$/;
        while (i < lines.length && pattern.test(lines[i])) { const li = node('li'); inline(li, lines[i++].replace(pattern, '$1')); list.append(li); }
        parent.append(list); continue;
      }
      const heading = /^(#{2,5})\s+(.+)$/.exec(line);
      if (heading) { const h = node('h5'); inline(h, heading[2]); parent.append(h); i++; continue; }
      const paragraph = [line]; i++;
      while (i < lines.length && lines[i].trim() && !/^\s*(?:```|#{2,5}\s|[-*]\s|\d+\.\s)/.test(lines[i])
        && !(lines[i].includes('|') && i + 1 < lines.length && separator(lines[i + 1]))) paragraph.push(lines[i++]);
      const p = node('p', undefined, 'learning-prose'); inline(p, paragraph.join('\n')); parent.append(p);
    }
  }
  globalThis.LearningContent = Object.freeze({render});
})();
