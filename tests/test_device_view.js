"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
class Element {
  constructor(tag, className = "", text = "") {
    this.tag = tag; this.className = className; this.text = String(text); this.children = [];
    this.attributes = {}; this.style = {setProperty: () => {}};
    this.classList = {add: name => { this.className += " " + name; }};
  }
  append(...items) { this.children.push(...items); }
  setAttribute(name, value) { this.attributes[name] = value; }
}
const context = {el: (...args) => new Element(...args), document: {
  createElementNS: (_, tag) => new Element(tag)}};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf("const fmt ="), source.indexOf("function statusText(")), context);
vm.runInContext(source.slice(source.indexOf("function meter("), source.indexOf("function detail(")), context);
vm.runInContext("this.view = {avg, deviceId, deviceColumn, dieCountText, utilizationLabel};", context);

const a5 = Array.from({length: 8}, (_, npu_id) => ({npu_id, chip_id: null, name: "Ascend950PR",
  health: npu_id === 6 ? "Critical" : "OK", utilization_label: "NPU Util", utilization_percent: npu_id === 4 ? 100 : 0,
  aicore_percent: null, bus_id: "synthetic-bus", power_w: 120, temperature_c: 30,
  memory: {used_mb: 0, total_mb: 0, percent: null}, hbm: {used_mb: 1024, total_mb: 32768, percent: 3.12}}));
const a3 = [{...a5[0], chip_id: 0, utilization_label: "AICore", utilization_percent: 10, aicore_percent: 10},
            {...a5[0], chip_id: 1, utilization_label: "AICore", utilization_percent: 20, aicore_percent: 20}];
assert.equal(context.view.avg(a5), 12.5);
assert.equal(context.view.avg(a3), 15);
assert.equal(context.view.avg([{aicore_percent: 25}]), 25);
assert.equal(context.view.deviceId(a5[4]), "4");
assert.equal(context.view.deviceId(a3[1]), "0/1");
assert.equal(context.view.deviceColumn(a5), "NPU ID");
assert.equal(context.view.dieCountText(a5), "—");
assert.equal(context.view.dieCountText(a3), "2");
assert.equal(context.view.dieCountText([...a5, ...a3]), "2+?");

function table(devices) { return context.deviceTable(devices, "Synthetic node").children[0]; }
let rendered = table(a5);
assert.deepEqual(rendered.children[0].children[0].children.map(cell => cell.text), ["NPU ID", "Health", "NPU Util", "HBM-Usage"]);
assert.equal(rendered.children[0].children[0].children[3].children[0].text, "MB");
const rows = rendered.children[1].children;
assert.equal(rows.length, 8);
assert.equal(rows[4].children[0].text, "4");
assert.equal(rows[4].children[2].children[0].text, "100.0%");
assert.equal(rows[0].children[2].children[0].text, "0.0%");
assert.equal(rows[0].children.length, 4);
assert.equal(rows[0].children[3].children[0].text, "1024/32768");
assert.match(rows[0].children[3].title, /1\.0\/32\.0 GiB.*3\.1%/);
assert.match(rows[0].children[2].title, /当前采样 NPU Util：0\.0%/);
assert.match(rows[6].className, /unhealthy/);
assert.doesNotMatch(rows[0].children[0].title, /die|null/);
rendered = table(a3);
assert.equal(rendered.children[0].children[0].children[2].text, "AICore");
assert.equal(rendered.children[1].children[1].children[0].text, "0/1");
const missing = table([{...a5[0], utilization_percent: null}]);
assert.equal(missing.children[1].children[0].children[2].children[0].text, "—");
assert.equal(context.hbmCell({used_mb: 0, total_mb: 65536, percent: 0}).children[0].text, "0/65536");
assert.equal(context.hbmCell({used_mb: 0, total_mb: 0, percent: null}).children[0].text, "—");
assert.match(context.hbmCell({used_mb: 0, total_mb: 0, percent: null}).title, /未提供有效总容量/);
assert.equal(context.hbmCell({used_mb: null, total_mb: null, percent: null}).children[0].text, "—");
assert.equal(context.hbmCell({used_mb: null, total_mb: 65536, percent: null}).children[0].text, "—/65536");
assert.equal(context.hbmCell({used_mb: 1024, total_mb: null, percent: null}).children[0].text, "1024/—");
assert.equal(context.hbmCell({used_mb: 65536, total_mb: 65536, percent: 100}).children[0].text, "65536/65536");
assert.equal(context.hbmCell({used_mb: 0.5, total_mb: 8.5, percent: 5.88}).children[0].text, "0.5/8.5");
assert.match(context.hbmCell({used_mb: 0.5, total_mb: 8.5, percent: 5.88}).title, /0\.5 \/ 8\.5 MB/);
assert.equal(context.hbmAmounts({used_mb: 1024, total_mb: 65536}, 1024, 1), "1.0/64.0");
assert.match(context.sparkline([{value: 0}, {value: 100}], "NPU Util").attributes["aria-label"], /NPU Util/);
console.log("Device view checks passed: HBM used/total MB, zero/unknown capacity, no Memory column, actual sampled utilization, A5 NPU Util, A3 dies and trend labels.");
