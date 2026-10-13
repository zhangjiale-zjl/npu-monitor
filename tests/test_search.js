"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
const plain = value => JSON.parse(JSON.stringify(value));

class Element {
  constructor(tag = "div", className = "", text = "") {
    this.tag = tag; this.className = className; this.textContent = String(text);
    this.children = []; this.dataset = {}; this.attributes = {}; this.listeners = {};
    this.value = ""; this.hidden = false;
    this.classList = {toggle: (name, on) => { this.attributes[name] = on; }};
  }
  append(...items) { for (const item of items) { item.parentElement = this; this.children.push(item); } }
  prepend(...items) { for (const item of items) item.parentElement = this; this.children.unshift(...items); }
  replaceChildren(...items) { this.children = []; this.append(...items); }
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  contains(element) { return element === this || this.children.some(child => child.contains(element)); }
  querySelectorAll(selector) {
    return this.children.flatMap(child => [child, ...child.querySelectorAll(selector)])
      .filter(child => selector === ".model-filter" ? child.className === "model-filter"
        : child.className === "search-option");
  }
  focus() { this.listeners.focus?.(); }
  scrollIntoView() {}
}

function harness() {
  const elements = new Map(), listeners = {};
  const get = id => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  const nodes = [
    {id: "127.0.0.192:22", name: "Synthetic Alpha", address: "127.0.0.192", host: "alpha-alias", server_model: "Model Z", platform: "A2"},
    {id: "127.0.0.2:22", name: "Synthetic Beta", address: "127.0.0.2", host: "beta-alias", server_model: "Model 192", platform: "A3"},
    {id: "127.0.0.3:22", name: "Synthetic Gamma", address: "127.0.0.3", host: "gamma-alias", server_model: "Model A", platform: "A5"}
  ].map(node => ({...node, status: "ok", devices: [{npu_id: 0, chip_id: 0, health: "OK"}]}));
  const context = {state: {nodes, paused: false, demo: false, config: {interval: 5, model_order: [], node_order: {}}},
    FolderUI: {selected: "custom-folder", drag: null, current: () => null}, serviceDisconnected: false,
    selectedModel: "Model Z", lastRender: "",
    directoryClickSuppressedUntil: 0, performance: {now: () => 100},
    document: {activeElement: get("dashboard-search"), addEventListener: (name, handler) => { listeners[name] = handler; }},
    $: get, el: (...args) => new Element(...args), applyDirectoryWidth: () => {},
    modelSection: (model, members) => { const section = new Element(); section.dataset.model = model;
      section.nodeIds = members.map(node => node.id); return section; },
    api: () => { throw new Error("Search must stay local and never update config"); }};
  get("node-search-form").append(get("dashboard-search"), get("node-search-dropdown"));
  get("node-search-dropdown").append(get("node-search-options"));
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf("const fmt ="), source.indexOf("function meter(")), context);
  vm.runInContext(source.slice(source.indexOf("function ipv4Parts("), source.indexOf("async function saveDirectoryOrder(")), context);
  vm.runInContext(source.slice(source.indexOf("function render("), source.indexOf("async function poll(")), context);
  vm.runInContext("this.ui = NodeSearch;", context);
  context.renderDirectory = () => {};
  context.ui.init();
  const input = value => { get("dashboard-search").value = value; get("dashboard-search").listeners.input(); };
  const submit = () => get("node-search-form").listeners.submit({preventDefault() {}});
  const visibleIds = () => get("nodes").children.flatMap(section => plain(section.nodeIds));
  return {context, ui: context.ui, nodes, get, input, submit, visibleIds, listeners};
}

let h = harness();
const before = plain(h.context.state.config);
h.input("192");
assert.equal(h.ui.active(), false); // Typing previews matches; Search applies them.
assert.equal(h.get("node-search-dropdown").hidden, false);
assert.equal(h.get("node-search-options").children.length, 2);
assert.equal(h.get("node-search-preview-count").textContent, "匹配 2 个节点 · 点击“搜索”展示全部结果");
assert.deepEqual(plain(h.ui.candidates).map(node => node.id), [h.nodes[1].id, h.nodes[0].id]);
assert.match(h.get("node-search-options").children[0].children[1].textContent, /127\.0\.0\.2.*Model 192.*连接正常/);
h.submit();
assert.deepEqual(h.visibleIds(), [h.nodes[1].id, h.nodes[0].id]);
assert.equal(h.context.FolderUI.selected, null);
assert.equal(h.context.selectedModel, null);
assert.equal(h.get("summary").children[0].children[0].textContent, "2");
assert.equal(h.get("view-title").textContent, "搜索结果 · NPU 总览");
assert.equal(h.get("node-search-status").textContent, "“192” · 2 个匹配节点");
assert.equal(h.get("node-search-dropdown").hidden, true);
assert.deepEqual(plain(h.context.state.config), before);
h.context.state.nodes.push({...h.nodes[2], id: "127.0.0.192:2200", address: "127.0.0.192", server_model: "Model A"});
h.context.render();
assert.equal(h.visibleIds().length, 3); // Applied search stays active during live updates.
assert.equal(h.get("summary").children[0].children[0].textContent, "3");
h.get("node-search-clear").listeners.click();
assert.equal(h.ui.active(), false);
assert.equal(h.visibleIds().length, 4);
assert.equal(h.get("dashboard-search").value, "");

h = harness();
for (const [query, expected] of [["mOdEl Z", [h.nodes[0].id]], ["ＢＥＴＡ", [h.nodes[1].id]],
  ["192 a2", [h.nodes[0].id]], ["gamma-al", [h.nodes[2].id]], ["not-a-node", []]]) {
  h.input(query); h.submit();
  assert.deepEqual(h.visibleIds(), expected);
  assert.equal(h.get("search-empty").hidden, expected.length > 0);
}
h.input("<img src=x>"); h.submit();
assert.match(h.get("search-empty-description").textContent, /<img src=x>/); // Text remains literal.
h.get("search-show-all").listeners.click();
assert.equal(h.visibleIds().length, 3);
h.input("192");
h.ui.apply(h.nodes[0]);
assert.deepEqual(h.visibleIds(), [h.nodes[0].id]);
h.context.directoryButton("Model 192", 1, "Model 192").listeners.click();
assert.equal(h.ui.active(), false);
assert.deepEqual(h.visibleIds(), [h.nodes[1].id]);

h = harness();
h.input("192");
const key = value => h.get("dashboard-search").listeners.keydown({key: value, preventDefault() {}});
key("ArrowDown");
assert.equal(h.get("dashboard-search").attributes["aria-activedescendant"], "node-search-option-0");
key("Escape");
assert.equal(h.get("node-search-dropdown").hidden, true);
h.get("dashboard-search").focus();
assert.equal(h.get("node-search-dropdown").hidden, false);
assert.equal(h.get("node-search-options").children[0].attributes["aria-selected"], "false");
key("ArrowUp"); key("Enter");
assert.deepEqual(h.visibleIds(), [h.nodes[0].id]);
h.get("dashboard-search").listeners.compositionstart();
h.input("beta"); h.submit();
assert.deepEqual(h.visibleIds(), [h.nodes[0].id]);
h.get("dashboard-search").listeners.compositionend();
h.submit();
assert.deepEqual(h.visibleIds(), [h.nodes[1].id]);
h.input("");
assert.equal(h.ui.active(), false);
assert.equal(h.visibleIds().length, 3);
console.log("Search checks passed: fuzzy IP/model/name matching, all-result preview/apply, filtered stats, live updates, empty results, directory reset, exact option selection, keyboard and IME handling.");
