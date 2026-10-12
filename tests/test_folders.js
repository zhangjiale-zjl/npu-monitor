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
  console.log("Custom directory checks passed: shared nodes, numeric IP order, atomic actions, stale polls, failed saves and drag/click behavior.");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
