"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
const plain = value => JSON.parse(JSON.stringify(value));

function harness(mode) {
  const requests = [], listeners = {}, notices = [];
  const node = {host: "synthetic-node", user: "root", port: 22, auth_type: "key", identity_file: ""};
  const result = {hidden: true, className: "", textContent: ""};
  const button = {disabled: false, textContent: ""};
  const fieldset = {isConnected: true, dataset: {}, querySelectorAll: () => [],
    querySelector: selector => selector === ".test-node" ? button : result};
  const elements = {"default-key": {value: ""}, timeout: {value: "9", checkValidity: () => true},
    "save-config": {disabled: false}, "config-error": {hidden: true},
    "config-form": {addEventListener: (type, handler) => { listeners[type] = handler; }},
    "config-nodes": {querySelectorAll: selector => selector === ".config-node" ? [fieldset] : []},
    "settings-dialog": {close: () => {}}};
  const context = {settingsMode: mode, state: {connection_test_supported: true, auto_node_names: true,
    auth_password_supported: true, demo: false, config: {identity_file: "saved-default-key", timeout: 17,
      interval: 5, nodes: [{host: "existing-node"}], node_order: {}, model_order: []}},
    settingsNodeOrder: new Map(), settingsModelOrder: [], $: id => elements[id],
    clearConnectionTest: field => { field.dataset.testVersion = "1"; },
    configNodeValue: () => node, finishConfigDrag: () => {}, poll: async () => {},
    sshHostKeyHelp: () => null, fmt: value => String(value),
    notify: message => notices.push(message),
    api: async (url, body) => {
      requests.push({url, body: plain(body)});
      return {status: "ok", message: "Synthetic connection succeeded", ok: true};
    }};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf("async function testConfigNode("),
    source.indexOf('for (const id of ["default-key", "timeout"])')), context);
  vm.runInContext(source.slice(source.indexOf('$("config-form").addEventListener("submit"'),
    source.indexOf('$("interval-form").addEventListener("submit"')), context);
  return {context, fieldset, elements, requests, listeners, node, notices};
}

async function main() {
  let h = harness("add");
  h.elements.timeout.checkValidity = () => false;
  await h.context.testConfigNode(h.fieldset);
  assert.deepEqual(h.requests, [{url: "/api/test-connection", body: {node: h.node}}]);
  await h.listeners.submit({preventDefault: () => {}});
  assert.deepEqual(h.requests.at(-1), {url: "/api/nodes", body: {nodes: [h.node]}});
  assert.equal(h.elements["config-error"].hidden, true);

  h = harness("manage");
  h.elements["default-key"].value = "draft-default-key";
  h.elements.timeout.value = "23";
  await h.context.testConfigNode(h.fieldset);
  assert.deepEqual(h.requests[0], {url: "/api/test-connection",
    body: {node: h.node, identity_file: "draft-default-key", timeout: 23}});
  await h.listeners.submit({preventDefault: () => {}});
  assert.equal(h.requests.at(-1).url, "/api/config");
  assert.equal(h.requests.at(-1).body.identity_file, "draft-default-key");
  assert.equal(h.requests.at(-1).body.timeout, 23);
  h.elements["default-key"].value = "";
  await h.context.testConfigNode(h.fieldset);
  assert.equal(h.requests.at(-1).body.identity_file, "");
  console.log("Default key checks passed: add inherits saved defaults, append preserves server config, manage uses draft defaults.");
}

main().catch(error => { console.error(error); process.exitCode = 1; });
