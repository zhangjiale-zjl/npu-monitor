"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

// Verify automatic saves and stale polling with synthetic state; no browser or network.
const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
const plain = value => value === undefined ? undefined : JSON.parse(JSON.stringify(value));
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
};

function harness() {
  const handles = [{disabled: false}, {disabled: false}];
  let focused = null;
  const rows = ["Model Z", "Model A"].map(model => ({dataset: {model},
    querySelector: () => ({focus: () => { focused = model; }})}));
  const status = {textContent: ""};
  const directory = {querySelectorAll: selector => selector === ".directory-model" ? rows : handles};
  const renders = [], notices = [], requests = [];
  const save = deferred(), get = deferred();
  const config = {model_order: ["Model A", "Model Z"], interval: 17, nodes: [{host: "synthetic-node"}],
    node_order: {"Model A": ["synthetic-node:22"]}};
  const context = {state: {config, demo: false}, FolderUI: {drag: null}, directorySaving: false, directoryOrderRevision: 0,
    directorySignature: "before", lastRender: "before", polling: false, serviceDisconnected: false,
    $: id => id === "model-directory" ? directory : status,
    applyDirectoryState: () => {}, render: () => renders.push(plain(context.state.config)),
    notify: (message, error = false) => notices.push({message, error}),
    api: (url, body) => { requests.push({url, body: plain(body)}); return url === "/api/state" ? get.promise : save.promise; }};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf("async function saveDirectoryOrder("), source.indexOf("function moveDirectoryDrag(")), context);
  vm.runInContext(source.slice(source.indexOf("async function poll("), source.indexOf("function configNodeValue(")), context);
  return {context, save, get, requests, notices, renders, handles, status, focused: () => focused};
}

async function main() {
  let h = harness();
  const pending = h.context.saveDirectoryOrder("Model Z", true);
  assert.equal(h.context.directorySaving, true);
  assert.equal(h.handles.every(handle => handle.disabled), true);
  assert.deepEqual(plain(h.context.state.config.model_order), ["Model Z", "Model A"]);
  assert.deepEqual(h.requests, [{url: "/api/model-order", body: {model_order: ["Model Z", "Model A"]}}]);
  h.save.resolve({ok: true, model_order: ["Model Z", "Model A"]});
  await pending;
  assert.equal(h.context.directorySaving, false);
  assert.equal(h.focused(), "Model Z");
  assert.deepEqual(plain(h.context.state.config.node_order), {"Model A": ["synthetic-node:22"]});
  assert.equal(h.context.state.config.interval, 17);
  assert.equal(h.notices.at(-1).error, false);

  h = harness();
  const failed = h.context.saveDirectoryOrder("Model Z");
  h.save.reject(new Error("synthetic save failure"));
  await failed;
  assert.deepEqual(plain(h.context.state.config.model_order), ["Model A", "Model Z"]);
  assert.equal(h.context.directorySaving, false);
  assert.equal(h.notices.at(-1).error, true);
  assert.match(h.status.textContent, /保存失败/);

  h = harness();
  const oldPoll = h.context.poll();
  const autoSave = h.context.saveDirectoryOrder("Model Z");
  h.get.resolve({config: {model_order: ["Model A", "Model Z"]}, demo: false});
  await oldPoll;
  assert.deepEqual(plain(h.context.state.config.model_order), ["Model Z", "Model A"]);
  assert.equal(h.context.state.config.interval, 17);
  assert.equal(h.context.polling, false);
  await h.context.poll();
  assert.equal(h.requests.filter(request => request.url === "/api/state").length, 1);
  h.save.resolve({ok: true, model_order: ["Model Z", "Model A"]});
  await autoSave;
  assert.deepEqual(plain(h.context.state.config.model_order), ["Model Z", "Model A"]);

  h = harness();
  h.get.reject(new Error("Synthetic service disconnected"));
  await h.context.poll();
  assert.equal(h.context.serviceDisconnected, true);
  assert.equal(h.renders.length, 1); // Render unavailable process occupancy immediately.
  h.context.api = async () => ({config: h.context.state.config, demo: false});
  await h.context.poll();
  assert.equal(h.context.serviceDisconnected, false);
  assert.equal(h.renders.length, 2);
  console.log("Directory ordering checks passed: automatic save, independent node order, failure rollback, focus and stale polling.");
}

main().catch(error => { console.error(error); process.exitCode = 1; });
