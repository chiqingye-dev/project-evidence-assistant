// Rendering checks use synthetic text; they do not measure model quality.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.attributes = {}; this.textContent = ''; }
  append(...children) { this.children.push(...children); }
  setAttribute(key, value) { this.attributes[key] = value; }
}
const context = vm.createContext({document: {createElement: tag => new Element(tag)}, location: {origin: 'http://127.0.0.1:8766'}});
vm.runInContext(fs.readFileSync('static/learning-content.js', 'utf8'), context);
const all = element => [element, ...element.children.flatMap(all)];
const render = text => { const root = new Element('article'); context.LearningContent.render(root, text); return all(root); };
let nodes = render('**关键条件**是 `queueFull`。\n\n- 首项\n- 次项\n\n| 方案 | 代价 |\n| --- | --- |\n| 直接发送 | 双写窗口 |');
assert.equal(nodes.filter(n => n.tagName === 'strong').length, 1);
assert.equal(nodes.filter(n => n.tagName === 'code').length, 1);
assert.equal(nodes.filter(n => n.tagName === 'li').length, 2);
assert.equal(nodes.filter(n => n.tagName === 'table').length, 1);
assert.equal(nodes.filter(n => n.tagName === 'td').length, 2);
console.log('PASS: paragraphs, emphasis, inline code, lists and comparisons render as local DOM nodes');
nodes = render('<img src=x onerror=alert(1)>\n\n[link](javascript:alert(1))\n\n![secret](https://example.invalid/x)');
assert.equal(nodes.some(n => ['img', 'script', 'a'].includes(n.tagName)), false);
assert.ok(nodes.some(n => n.textContent.includes('<img')));
console.log('PASS: raw HTML, image syntax and links remain text and cannot fetch or execute');
nodes = render('```mermaid\nflowchart LR\nA[请求] --> B[校验]\n```');
const frame = nodes.find(n => n.tagName === 'iframe');
assert.equal(frame.attributes.sandbox, 'allow-scripts');
assert.ok(frame.srcdoc.includes('connect-src \'none\''));
assert.ok(frame.srcdoc.includes('securityLevel:\'strict\''));
assert.ok(frame.srcdoc.includes('http://127.0.0.1:8766/static/vendor/mermaid-12.3.0.min.js'));
assert.ok(!frame.attributes.sandbox.includes('same-origin'));
console.log('PASS: diagrams use the pinned local library in an opaque sandbox with connections disabled');
for (const graph of ['flowchart LR\nA[节点]\nclick A "https://example.invalid"', '%%{init: {}}%%\nflowchart LR\nA-->B', 'flowchart LR\nA[<img src=x>]', 'mindmap\nroot((知识))', '---\nconfig: x\n---\nflowchart LR\nA-->B']) {
  nodes = render('```mermaid\n'+graph+'\n```');
  assert.equal(nodes.some(n => n.tagName === 'iframe'), false);
  assert.ok(nodes.some(n => n.tagName === 'pre' && n.textContent === graph));
}
console.log('PASS: directives, clickable links, HTML, remote resources and unsupported diagrams fall back to unchanged text');
nodes = render('```java\n<script>notExecutable()</script>\n```\n\n```mermaid\nflowchart LR\nA-->B');
assert.equal(nodes.some(n => n.tagName === 'iframe'), false);
assert.equal(nodes.some(n => n.tagName === 'script'), false);
assert.ok(nodes.some(n => n.tagName === 'code' && n.textContent.includes('<script>')));
console.log('PASS: code and incomplete fences remain readable without executing or swallowing the answer');
