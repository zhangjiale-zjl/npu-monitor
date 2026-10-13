"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const app = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
const source = fs.readFileSync(path.join(__dirname, "../static/folders.js"), "utf8");
const plain = value => JSON.parse(JSON.stringify(value));
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
};

function harness() {
  const requests = [], renders = [], notices = [], status = {};
  const save = deferred(), get = deferred();
  const nodes = [10, 2, 3].map(last => ({id: "127.0.0." + last + ":22",
    address: "127.0.0." + last, host: "127.0.0." + last, name: "Synthetic " + last}));
  const folders = [{id: "a", name: "Custom A", node_ids: [nodes[0].id, nodes[1].id]},
                   {id: "b", name: "Custom B", node_ids: [nodes[1].id]}];
  const context = {state: {nodes, demo: false, config: {folders, interval: 17, timeout: 19,
    nodes: [{host: "synthetic-node", password_set: true}], node_order: {}, model_order: []}},
    directorySaving: false, directoryOrderRevision: 0, directorySignature: "before", lastRender: "before",
    polling: false, serviceDisconnected: false, directoryClickSuppressedUntil: 0,
    $: id => id === "model-directory" ? {querySelectorAll: () => []} : status,
    applyDirectoryState: () => {}, cancelDirectoryHover: () => {}, cancelAnimationFrame: () => {},
    document: {body: {classList: {remove: () => {}}}}, performance: {now: () => 100},
    render: () => renders.push(plain(context.state.config.folders)),
    notify: (message, error) => notices.push({message, error}),
    api: (url, body) => { requests.push({url, body: body && plain(body)});
      return url === "/api/state" ? get.promise : save.promise; }};
  vm.createContext(context);
  vm.runInContext(app.slice(app.indexOf("function ipv4Parts("), app.indexOf("function markDirectorySelection(")), context);
  vm.runInContext(source + "\nthis.ui = FolderUI;", context);
  vm.runInContext(app.slice(app.indexOf("async function poll("), app.indexOf("function configNodeValue(")), context);
  return {context, ui: context.ui, save, get, requests, renders, notices, nodes, folders};
}

function pickerHarness() {
  const h = harness(), elements = new Map();
  const element = (tag = "div", className = "", text = "") => ({
    tag, className, textContent: text, children: [], dataset: {}, listeners: {},
    checked: false, disabled: false, hidden: false, value: "", open: false,
    setAttribute(name, value) { this[name] = value; },
    addEventListener(name, handler) { this.listeners[name] = handler; },
    append(...children) { for (const child of children) { child.parentElement = this; this.children.push(child); } },
    replaceChildren(...children) { this.children = []; this.append(...children); },
    querySelectorAll(selector) {
      const descendants = this.children.flatMap(child => [child, ...child.querySelectorAll("*")]);
      if (selector === "*") return descendants;
      if (selector === ".folder-node-option") return descendants.filter(child => child.className.includes("folder-node-option"));
      return descendants.filter(child => child.tag === "input"
        && (!selector.includes(":checked") || child.checked)
        && (!selector.includes(":disabled") || !child.disabled));
    },
    showModal() { this.open = true; }, close() { this.open = false; }
  });
  h.context.$ = id => {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  };
  h.context.el = element;
  h.context.window = {addEventListener: () => {}};
  h.context.document.addEventListener = () => {};
  h.nodes[0].server_model = "Synthetic A3";
  h.nodes[1].server_model = "Synthetic A2";
  h.nodes[2].server_model = "Synthetic A5";
  h.ui.init();
  h.ui.addNodes(h.folders[1]);
  return {...h, elements, input: h.context.$};
}

async function main() {
  let h = harness();
  assert.deepEqual(plain(h.ui.members(h.folders[0])).map(node => node.address), ["127.0.0.2", "127.0.0.10"]);
  assert.equal(h.ui.members(h.folders[1])[0].id, h.nodes[1].id);
  h.ui.selected = "a";
  assert.equal(h.ui.current().name, "Custom A");
  h.ui.selected = "deleted-folder";
  assert.equal(h.ui.current(), null);
  assert.equal(h.ui.selected, null);

  h = harness();
  const oldPoll = h.context.poll();
  const before = plain(h.context.state.config);
  const saved = h.ui.save({action: "assign", folder_id: "b", node_id: h.nodes[0].id}, "Saved");
  assert.equal(h.context.directorySaving, true);
  assert.deepEqual(h.requests.at(-1), {url: "/api/folders",
    body: {action: "assign", folder_id: "b", node_id: h.nodes[0].id}});
  await assert.rejects(h.ui.save({action: "create", name: "Concurrent"}, "Saved"), /正在保存/);
  h.get.resolve({config: {folders: []}, nodes: []});
  await oldPoll;
  assert.deepEqual(plain(h.context.state.config), before);
  const result = [...plain(h.folders)];
  result[1].node_ids.push(h.nodes[0].id);
  h.save.resolve({folders: result});
  await saved;
  assert.equal(h.context.directorySaving, false);
  assert.equal(h.context.state.config.interval, 17);
  assert.deepEqual(plain(h.context.state.config.nodes), before.nodes);
  assert.deepEqual(plain(h.ui.members(h.ui.folders()[1])).map(node => node.address), ["127.0.0.2", "127.0.0.10"]);

  h = harness();
  const failed = h.ui.save({action: "rename", folder_id: "a", name: "Renamed"}, "Saved");
  h.save.reject(new Error("Synthetic save failure"));
  await assert.rejects(failed, /Synthetic save failure/);
  assert.deepEqual(plain(h.context.state.config.folders), h.folders);
  assert.equal(h.context.directorySaving, false);
  assert.equal(h.renders.length, 1);

  h = harness();
  let pending;
  const saveAction = h.ui.save;
  h.ui.save = function (...args) { pending = saveAction.apply(this, args); return pending; };
  h.ui.drag = {node: h.nodes[0], pointerId: 7, moved: true, target: "b", ghost: null, frame: null,
    handle: {hasPointerCapture: () => false}};
  h.ui.finishNode({pointerId: 7, type: "pointerup"});
  assert.equal(h.ui.drag, null);
  assert.deepEqual(h.requests[0], {url: "/api/folders",
    body: {action: "assign", folder_id: "b", node_id: h.nodes[0].id}});
  h.save.resolve({folders: h.folders});
  await pending;
  h = harness();
  h.ui.drag = {node: h.nodes[0], pointerId: 7, moved: false, ghost: null, frame: null,
    handle: {hasPointerCapture: () => false}};
  h.ui.finishNode({pointerId: 7, type: "pointerup"});
  assert.equal(h.requests.length, 0);
  assert.equal(h.renders.length, 0); // A normal click keeps its button alive to open membership selection.

  h = pickerHarness();
  let inputs = h.input("folder-node-options").querySelectorAll("input");
  assert.deepEqual(inputs.map(input => input.dataset.node), [h.nodes[1].id, h.nodes[2].id, h.nodes[0].id]);
  assert.equal(inputs[0].checked, true);
  assert.equal(inputs[0].disabled, true);
  assert.equal(h.input("folder-nodes-save").disabled, true);
  assert.equal(h.input("folder-nodes-dialog").open, true);
  inputs[1].checked = true;
  inputs[1].listeners.change();
  assert.equal(h.input("folder-nodes-count").textContent, "已选择 1 个节点");
  let submit = h.input("folder-nodes-form").listeners.submit({preventDefault() {}});
  assert.deepEqual(h.requests[0], {url: "/api/folders", body: {
    action: "assign_many", folder_id: "b", node_ids: [h.nodes[2].id]}});
  h.save.resolve({folders: h.folders});
  await submit;
  assert.equal(h.input("folder-nodes-dialog").open, false);
  assert.equal(h.ui.expanded.has("b"), true);

  h = pickerHarness();
  inputs = h.input("folder-node-options").querySelectorAll("input");
  h.input("folder-nodes-search").value = "a5 127.0.0.3";
  h.input("folder-nodes-search").listeners.input();
  assert.deepEqual(inputs.map(input => input.parentElement.hidden), [true, false, true]);
  h.input("folder-nodes-select-all").listeners.click();
  assert.deepEqual(inputs.map(input => input.checked), [true, true, false]);
  h.input("folder-nodes-search").value = "SYNTHETIC A3";
  h.input("folder-nodes-search").listeners.input();
  assert.deepEqual(inputs.map(input => input.parentElement.hidden), [true, true, false]);
  h.input("folder-nodes-select-all").listeners.click();
  assert.deepEqual(inputs.map(input => input.checked), [true, true, true]);
  h.input("folder-nodes-search").value = "no-matching-ip";
  h.input("folder-nodes-search").listeners.input();
  assert.equal(h.input("folder-nodes-no-results").hidden, false);
  assert.equal(h.input("folder-nodes-select-all").disabled, true);
  assert.equal(h.input("folder-nodes-count").textContent, "已选择 2 个节点");
  submit = h.input("folder-nodes-form").listeners.submit({preventDefault() {}});
  assert.deepEqual(h.requests[0].body.node_ids, [h.nodes[2].id, h.nodes[0].id]);
  h.save.reject(new Error("Synthetic batch save failure"));
  await submit;
  assert.equal(h.input("folder-nodes-dialog").open, true);
  assert.equal(h.input("folder-nodes-error").hidden, false);
  assert.equal(h.input("folder-nodes-error").textContent, "Synthetic batch save failure");
  assert.equal(h.input("folder-nodes-save").disabled, false);
  assert.deepEqual(plain(h.context.state.config.folders), h.folders);
  h.input("folder-nodes-clear").listeners.click();
  assert.deepEqual(inputs.map(input => input.checked), [true, false, false]);
  assert.equal(h.input("folder-nodes-save").disabled, true);

  h = pickerHarness();
  h.context.state.nodes = [];
  h.ui.addNodes(h.folders[1]);
  assert.equal(h.input("folder-nodes-empty").hidden, false);
  assert.equal(h.input("folder-nodes-save").disabled, true);
  assert.equal(h.input("folder-nodes-select-all").disabled, true);
  assert.equal(h.input("folder-nodes-no-results").hidden, true);
  assert.equal(h.requests.length, 0);
  console.log("Custom directory checks passed: shared nodes, atomic batch additions, IP/model search, persistent filtered selections, failed saves and drag/click behavior.");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
