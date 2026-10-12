"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

// Test pure ordering rules with synthetic loopback addresses; no browser or network.
const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
const context = {state: {config: {node_order: {}}}};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf("function ipv4Parts("), source.indexOf("function markDirectorySelection(")), context);
const plain = value => JSON.parse(JSON.stringify(value));
const node = (address, model = "Model A2", port = 22, host = address) => ({address, host,
  id: address.includes(":") ? `[${address}]:${port}` : `${address}:${port}`, port, server_model: model});
const addresses = values => values.slice().sort(context.compareNodeAddresses).map(value => value.address);

assert.deepEqual(addresses([node("127.0.0.10"), node("127.0.0.2"), node("127.0.0.1")]),
  ["127.0.0.1", "127.0.0.2", "127.0.0.10"]);
assert.deepEqual(addresses([node("127.0.1.1"), node("127.0.0.255"), node("127.0.0.2")]),
  ["127.0.0.2", "127.0.0.255", "127.0.1.1"]);
assert.deepEqual(addresses([node("127.0.0.10", "Model A2", 22, "alias-a"), node("127.0.0.2", "Model A2", 22, "alias-z")]),
  ["127.0.0.2", "127.0.0.10"]);
assert.deepEqual(addresses([node("::10"), node("::2"), node("::1")]), ["::1", "::2", "::10"]);
assert.deepEqual(addresses([node("2001:db8:1::"), node("2001:db8::ffff"), node("2001:db8:0:1::")]),
  ["2001:db8::ffff", "2001:db8:0:1::", "2001:db8:1::"]);
assert.deepEqual(addresses([node("::ffff:127.0.0.10"), node("::ffff:127.0.0.2")]),
  ["::ffff:127.0.0.2", "::ffff:127.0.0.10"]);
assert.equal(context.compareNodeAddresses(node("::1"), node("0:0:0:0:0:0:0:1")) > 0, false);
assert.deepEqual(addresses([node("node10"), node("node2"), node("127.0.0.1")]), ["127.0.0.1", "node2", "node10"]);
assert.deepEqual([node("127.0.0.1", "Model A2", 2200), node("127.0.0.1")].sort(context.compareNodeAddresses).map(value => value.port), [22, 2200]);
assert.deepEqual([{address: "127.0.0.1", host: "alias-first", id: "alias-first:2200"},
  {address: "127.0.0.1", host: "alias-last", id: "alias-last:22"}].sort(context.compareNodeAddresses).map(value => value.id),
  ["alias-last:22", "alias-first:2200"]);

const nodes = [node("127.0.0.10", "Model Z"), node("127.0.0.3", "Model B"),
  node("127.0.0.1", "Model A"), node("127.0.0.2", "Model B"), node("127.0.0.1", "Model B"),
  node("127.0.0.2", "Model Z"), node("127.0.0.4", null)];
const original = JSON.stringify(nodes);
assert.deepEqual(plain(context.modelGroups([node("127.0.0.1", "Model800T"), node("127.0.0.2", "Model8009000")])).map(([model]) => model),
  ["Model8009000", "Model800T"]);
let groups = plain(context.modelGroups(nodes));
assert.deepEqual(groups.map(([model]) => model), ["Model A", "Model B", "Model Z", "型号未识别"]);
assert.deepEqual(groups[1][1].map(value => value.address), ["127.0.0.1", "127.0.0.2", "127.0.0.3"]);
assert.equal(JSON.stringify(nodes), original);
context.state.config.node_order = {"Model B": ["127.0.0.3:22", "127.0.0.1:22"]};
groups = plain(context.modelGroups(nodes));
assert.deepEqual(groups[1][1].map(value => value.address), ["127.0.0.3", "127.0.0.1", "127.0.0.2"]);
assert.deepEqual(groups[2][1].map(value => value.address), ["127.0.0.2", "127.0.0.10"]);
context.state.config.node_order = {};
assert.deepEqual(plain(context.modelGroups(nodes))[1][1].map(value => value.address), ["127.0.0.1", "127.0.0.2", "127.0.0.3"]);
console.log("Node ordering checks passed: model names, numeric IPv4/IPv6, aliases, manual override and reset.");
