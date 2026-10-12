"use strict";
const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="monitor-token"]').content;
let state = null;
let lastRender = "";
let polling = false;
let serviceDisconnected = false;
let noticeTimer;
const detailsOpen = new Set();

function el(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

async function api(path, data) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(path, {method: data === undefined ? "GET" : "POST",
      headers: {"X-Monitor-Token": token, "Content-Type": "application/json"},
      body: data === undefined ? undefined : JSON.stringify(data), signal: controller.signal});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `本地服务返回 ${response.status}`);
    return result;
  } finally { clearTimeout(timeout); }
}

function notify(text, error = false) {
  clearTimeout(noticeTimer);
  $("notice").textContent = text;
  $("notice").className = "notice" + (error ? " error" : "");
  $("notice").hidden = false;
  noticeTimer = setTimeout(() => { $("notice").hidden = true; }, 7000);
}

const fmt = (n, digits = 0) => n == null ? "—" : Number(n).toFixed(digits);
const timeText = (stamp) => stamp ? new Date(stamp).toLocaleTimeString("zh-CN", {hour12: false}) : "尚未采集";
const avg = (devices) => {
  const values = devices.map(d => d.aicore_percent).filter(v => v != null);
  return values.length ? values.reduce((a,b) => a+b, 0) / values.length : null;
};

function statusText(node) {
  if (node.status === "error") return "采集失败";
  if (node.status === "warning") return "输出待确认";
  if (node.status === "pending") return "连接中";
  if (node.devices.some(d => d.health !== "OK")) return "健康异常";
  return "连接正常";
}

function meter(value) {
  const bar = el("div", "meter" + (value == null ? " unavailable" : ""));
  const fill = el("div", "meter-fill");
  fill.style.setProperty("--fill", `${Math.min(100, Math.max(0, value || 0))}%`);
  bar.append(fill);
  return bar;
}

function memoryCell(data) {
  const cell = el("td", "memory-cell");
  const fallback = data.used_mb != null && data.total_mb != null ? `${fmt(data.used_mb)}/${fmt(data.total_mb)}` : "—";
  cell.append(el("span", "metric-number", data.percent == null ? fallback : `${fmt(data.percent, 1)}%`));
  cell.title = `已用 / 总量：${memoryAmounts(data)} GiB（${fmt(data.used_mb)} / ${fmt(data.total_mb)} MB）` + (data.percent == null ? "；占用比例未提供" : `；占用 ${fmt(data.percent, 1)}%`);
  cell.setAttribute("aria-label", cell.title);
  return cell;
}

function memoryAmounts(data) {
  return `${fmt(data.used_mb == null ? null : data.used_mb / 1024, 1)} / ${fmt(data.total_mb == null ? null : data.total_mb / 1024, 1)}`;
}

function deviceTable(devices, name) {
  const table = el("table", "device-table");
  table.setAttribute("aria-label", `${name} 每张 NPU 卡的状态`);
  const thead = el("thead");
  const heading = el("tr");
  ["NPU", "Health", "AICore", "HBM", "Memory"].forEach(text => {
    const th = el("th", "", text); th.scope = "col"; heading.append(th);
    if (text === "NPU") th.title = "NPU ID / Chip ID";
    if (["AICore", "HBM", "Memory"].includes(text)) th.append(el("span", "column-unit", "%"));
    if (["HBM", "Memory"].includes(text)) th.title = "内存占用率；悬停查看已用 / 总量，详细信息中可展开内存明细";
  });
  thead.append(heading);
  const tbody = el("tbody");
  devices.forEach(device => {
    const row = el("tr", device.health === "OK" ? "" : "unhealthy");
    const id = el("th", "device-id", `${device.npu_id}/${device.chip_id}`);
    id.scope = "row";
    id.title = `${device.name} · Bus ${device.bus_id} · ${fmt(device.temperature_c)}°C · ${fmt(device.power_w, 1)} W`;
    const health = el("td", "health-cell");
    health.append(el("span", "health-badge" + (device.health === "OK" ? " healthy" : " abnormal"), device.health || "—"));
    const utilization = el("td", "utilization-cell");
    utilization.append(el("div", "metric-values", device.aicore_percent == null ? "—" : `${fmt(device.aicore_percent, 1)}%`), meter(device.aicore_percent));
    row.append(id, health, utilization, memoryCell(device.hbm), memoryCell(device.memory));
    tbody.append(row);
  });
  table.append(thead, tbody);
  const wrap = el("div", "device-table-wrap");
  wrap.append(table);
  return wrap;
}

function sparkline(history) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 600 40");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "最近 120 次采集的平均 AICore 利用率，范围 0 到 100 百分比");
  svg.classList.add("sparkline");
  for (const y of [5, 35]) {
    const line = document.createElementNS(ns, "line");
    for (const [k,v] of Object.entries({x1:0,x2:600,y1:y,y2:y})) line.setAttribute(k,v);
    svg.append(line);
  }
  let segment = [];
  const draw = () => {
    if (segment.length) {
      const line = document.createElementNS(ns, "polyline");
      // A first sample occupies a visible point instead of an empty chart.
      if (segment.length === 1) segment.push(segment[0]);
      line.setAttribute("points", segment.join(" "));
      svg.append(line);
    }
    segment = [];
  };
  history.forEach((sample, index) => {
    if (sample.value == null) { draw(); return; }
    segment.push(`${index * 600 / Math.max(1, history.length - 1)},${35 - sample.value * .3}`);
  });
  draw();
  return svg;
}

function detail(nodeName, key, label) {
  const element = el("details");
  const id = JSON.stringify([nodeName, key]);
  element.open = detailsOpen.has(id);
  element.append(el("summary", "", label));
  element.addEventListener("toggle", () => {
    // Disconnected elements must not overwrite the new element's open state.
    if (!element.isConnected) return;
    if (element.open) detailsOpen.add(id); else detailsOpen.delete(id);
  });
  return element;
}

function nodeCard(node) {
  const card = el("article", "node-card");
  const head = el("div", "node-head");
  const identity = el("div", "node-identity");
  const title = el("h4", "", node.name);
  const badHealth = node.devices.some(d => d.health !== "OK");
  title.append(el("span", "status-pill " + (badHealth ? "error" : node.status), statusText(node)));
  const config = state.config.nodes.find(n => n.id === node.id);
  const models = [...new Set(node.devices.map(device => device.name))].join(" / ");
  identity.append(title, el("div", "node-address", (config?.user ? `${config.user}@` : "") + node.host + (config?.port ? `:${config.port}` : "") + (models ? ` · ${models}` : "")));
  title.title = node.platform_error || `环境型号：${node.platform || "尚未识别"}`;
  const right = el("div", "node-head-right");
  const update = el("div", "node-update");
  update.append(el("div", "", node.collecting ? "正在采集…" : timeText(node.updated_at)), el("div", "", `${node.devices.length} 芯片 · 平均 ${fmt(avg(node.devices), 1)}%`));
  const edit = el("button", "edit-node subtle", "编辑");
  edit.setAttribute("aria-label", `编辑节点 ${node.name}`);
  edit.addEventListener("click", () => openSettings({id: node.id}));
  right.append(update, edit);
  head.append(identity, right);
  const body = el("div", "node-body");
  if (node.error || node.warning) {
    const message = node.error || node.warning;
    const summary = /REMOTE HOST IDENTIFICATION HAS CHANGED|Host key verification failed/.test(message) ? "SSH 主机指纹不匹配，请核对该节点的服务器指纹。" : message;
    const alert = el("div", "node-alert" + (node.error ? " error" : ""));
    const text = el("div", "alert-message", summary);
    text.title = message;
    alert.append(text);
    alert.append(el("small", "", `返回码：${node.exit_code == null ? "未取得" : node.exit_code} · 最近成功：${timeText(node.last_success_at)}`));
    body.append(alert);
  } else if (!node.devices.length) {
    body.append(el("div", "node-alert", "正在建立 SSH 连接，等待第一份 npu-smi info 输出…"));
  }
  if (node.devices.length) {
    body.append(deviceTable(node.devices, node.name));
  }
  const details = el("div", "node-details");
  const extra = detail(node.id, "extra", `详细信息 · ${node.processes.length} 条进程`);
  extra.append(el("p", "no-process", node.server_model
    ? `服务器型号：${node.server_model} · 来源：${node.server_model_source === "sysfs" ? "系统 DMI 文件" : node.server_model_source === "demo" ? "演示数据" : "dmidecode"}`
    : (node.server_model_error || "等待服务器型号查询")));
  if (node.devices.length) {
    const memory = detail(node.id, "memory", "内存明细 · 已用 / 总量");
    const table = el("table");
    table.setAttribute("aria-label", `${node.name} 每张 NPU 卡的内存用量`);
    const thead = el("thead"); const heading = el("tr");
    ["NPU / Chip", "HBM（GiB）", "Memory（GiB）"].forEach(text => {
      const th = el("th", "", text); th.scope = "col"; heading.append(th);
    });
    thead.append(heading);
    const tbody = el("tbody");
    node.devices.forEach(device => {
      const row = el("tr");
      const id = el("th", "device-id", `${device.npu_id}/${device.chip_id}`); id.scope = "row";
      row.append(id);
      [device.hbm, device.memory].forEach(data => {
        const cell = el("td", "", memoryAmounts(data));
        cell.title = `${fmt(data.used_mb)} / ${fmt(data.total_mb)} MB`;
        row.append(cell);
      });
      tbody.append(row);
    });
    table.append(thead, tbody);
    const wrap = el("div", "table-wrap"); wrap.append(table); memory.append(wrap);
    extra.append(memory);
  }
  if (node.history.length > 1) {
    const plot = el("div", "node-plot");
    const caption = el("p", "", "平均 AICore");
    caption.append(el("br"), document.createTextNode(`最近 ${node.history.length} 次采集`));
    plot.append(caption, sparkline(node.history));
    extra.append(plot);
  }
  if (node.platform_error) extra.append(el("p", "no-process", node.platform_error));
  if (node.board_raw) {
    const board = detail(node.id, "board", "查看环境型号查询结果");
    board.append(el("pre", "", node.board_raw));
    extra.append(board);
  }
  const processes = detail(node.id, "processes", `查看进程 · ${node.processes.length} 条`);
  if (node.processes.length) {
    const table = el("table");
    const thead = el("thead"); const tr = el("tr");
    ["NPU / Chip", "PID", "进程名称", "显存（MB）", "容器 PID"].forEach(text => tr.append(el("th", "", text)));
    thead.append(tr); table.append(thead);
    const tbody = el("tbody");
    node.processes.forEach(process => {
      const row = el("tr");
      [`${process.npu_id} / ${process.chip_id}`, process.pid, process.name, fmt(process.memory_mb), process.container_pid ?? "—"].forEach(text => row.append(el("td", "", text)));
      tbody.append(row);
    });
    table.append(tbody);
    const wrap = el("div", "table-wrap"); wrap.append(table); processes.append(wrap);
  } else {
    processes.append(el("p", "no-process", "当前输出未列出可识别的进程；完整信息见原始输出。"));
  }
  const raw = detail(node.id, "raw", `查看原始输出 · npu-smi info${node.version ? " · " + node.version : ""}`);
  raw.append(el("pre", "", node.raw || node.error || "尚未收到输出"));
  extra.append(processes, raw);
  details.append(extra);
  card.append(head, body, details);
  return card;
}

function modelGroups(nodes) {
  const groups = new Map();
  nodes.forEach(node => {
    const model = node.server_model || "型号未识别";
    if (!groups.has(model)) groups.set(model, []);
    groups.get(model).push(node);
  });
  return [...groups].sort(([a, left], [b, right]) => a === "型号未识别" ? 1 : b === "型号未识别" ? -1
    : right.length - left.length || a.localeCompare(b, "zh-CN", {numeric: true}));
}

function modelSection(model, nodes) {
  const section = el("section", "model-group");
  if (nodes.length > 2) section.classList.add("model-wide");
  else if (nodes.length === 2) section.classList.add("model-double");
  section.setAttribute("aria-label", `${model} 节点分区`);
  const heading = el("div", "model-heading");
  heading.append(el("h3", "", model));
  const chips = nodes.reduce((sum, node) => sum + node.devices.length, 0);
  const failed = nodes.filter(node => node.status === "error" || node.status === "warning").length;
  heading.append(el("span", "", `${nodes.length} 节点 · ${chips} 芯片${failed ? ` · ${failed} 采集异常` : ""}`));
  const grid = el("div", "nodes-grid");
  grid.append(...nodes.map(nodeCard));
  section.append(heading, grid);
  return section;
}

function render() {
  if (!state) return;
  const signature = JSON.stringify([state.nodes, state.paused, state.config]);
  if (signature === lastRender) return;
  lastRender = signature;
  $("demo-label").hidden = !state.demo;
  $("pause").textContent = state.paused ? "继续采集" : "暂停采集";
  $("refresh").disabled = state.paused;
  $("poll-state").textContent = state.paused ? "采集已暂停" : `每 ${state.config.interval} 秒采集`;
  if (document.activeElement !== $("interval")) $("interval").value = state.config.interval;
  const nodes = state.nodes;
  const devices = nodes.flatMap(n => n.devices);
  const online = nodes.filter(n => n.status === "ok").length;
  const groups = modelGroups(nodes);
  $("view-title").textContent = "全部节点 · NPU 总览";
  $("view-description").textContent = state.paused ? "采集已暂停，按机型展示最近一次结果" : `${online} / ${nodes.length} 节点采集正常 · 按服务器型号分区`;
  $("summary").replaceChildren();
  for (const [label,value] of [["节点", nodes.length], ["NPU / 芯片", devices.length], ["Health 非 OK", devices.filter(d => d.health !== "OK").length], ["采集异常", nodes.filter(n => n.status === "error" || n.status === "warning").length]]) {
    const item = el("div", "summary-item", label); item.prepend(el("strong", "", value)); $("summary").append(item);
  }
  $("nodes").replaceChildren(...groups.map(([model, members]) => modelSection(model, members)));
  $("empty").hidden = state.nodes.length > 0;
}

async function poll() {
  if (polling) return;
  polling = true;
  try {
    state = await api("/api/state");
    if (serviceDisconnected) notify("本地服务已连接。");
    serviceDisconnected = false;
    $("server-dot").className = "dot";
    $("connection-text").textContent = "本地服务已连接";
    render();
  } catch (error) {
    serviceDisconnected = true;
    $("server-dot").className = "dot error";
    $("connection-text").textContent = "本地服务已断开";
    $("poll-state").textContent = "数据已停止更新";
    $("notice").textContent = `本地服务无法连接：${error.message}。请启动工具或刷新页面。`;
    $("notice").className = "notice error";
    $("notice").hidden = false;
    lastRender = "";
  } finally { polling = false; }
}

function addConfigNode(node = {}) {
  const fragment = $("config-node-template").content.cloneNode(true);
  const fieldset = fragment.querySelector("fieldset");
  fieldset.dataset.id = node.id || "";
  fieldset.querySelector("legend").textContent = node.name || "新节点 · 自动识别环境型号";
  fieldset.querySelectorAll("[data-field]").forEach(input => {
    const field = input.dataset.field;
    input.value = field === "password" ? "" : field === "auth_type" ? (node.auth_type || "key") : (node[field] ?? "");
  });
  const auth = fieldset.querySelector('[data-field="auth_type"]');
  const password = fieldset.querySelector('[data-field="password"]');
  password.placeholder = node.password_set ? "已设置；留空保留已有密码" : "输入 SSH 登录密码";
  function updateAuth() {
    const byPassword = auth.value === "password";
    fieldset.querySelector(".password-field").hidden = !byPassword;
    password.disabled = !byPassword;
    password.required = byPassword && !node.password_set;
    fieldset.querySelector('[data-field="identity_file"]').disabled = byPassword;
  }
  auth.addEventListener("change", updateAuth);
  updateAuth();
  fieldset.querySelector(".remove-node").addEventListener("click", () => fieldset.remove());
  $("config-nodes").append(fragment);
  return fieldset;
}

function openSettings({add = false, id = null} = {}) {
  if (!state) { notify("请先启动本地服务。", true); return; }
  $("default-key").value = state.config.identity_file;
  $("npu-path").value = state.config.npu_smi_path || "npu-smi";
  $("timeout").value = state.config.timeout;
  $("config-nodes").replaceChildren();
  state.config.nodes.forEach(addConfigNode);
  let target;
  if (add || !state.config.nodes.length) target = addConfigNode();
  if (id) target = [...$("config-nodes").querySelectorAll("fieldset")].find(fieldset => fieldset.dataset.id === id);
  $("config-error").hidden = true;
  $("config-note").textContent = state.demo ? "演示模式：配置不写入文件" : "保存到启动时指定的配置文件";
  $("settings-dialog").showModal();
  if (target) target.querySelector('[data-field="host"]').focus();
}

$("settings").addEventListener("click", () => openSettings());
$("new-node").addEventListener("click", () => openSettings({add: true}));
$("empty-settings").addEventListener("click", () => openSettings({add: true}));
for (const id of ["close-settings", "cancel-settings"]) $(id).addEventListener("click", () => $("settings-dialog").close());
$("add-node").addEventListener("click", () => addConfigNode());

$("config-form").addEventListener("submit", async event => {
  event.preventDefault();
  const nodes = [...$("config-nodes").querySelectorAll("fieldset")].map(fieldset => {
    const node = {};
    fieldset.querySelectorAll("[data-field]").forEach(input => {
      const field = input.dataset.field;
      if (field === "password") {
        if (node.auth_type === "password" && input.value !== "") node.password = input.value;
      } else {
        node[field] = field === "port" ? (input.value ? Number(input.value) : null) : input.value.trim();
      }
    });
    return node;
  });
  $("save-config").disabled = true;
  try {
    if (nodes.some(node => node.auth_type === "password") && !state.auth_password_supported) {
      throw new Error("请重新启动更新后的监控工具，再使用密码登录。");
    }
    if (!state.auto_node_names) throw new Error("请重启本地监控服务后使用自动命名与节点去重。");
    const saved = await api("/api/config", {...state.config, timeout: Number($("timeout").value), identity_file: $("default-key").value.trim(), npu_smi_path: $("npu-path").value.trim(), nodes});
    $("settings-dialog").close();
    const merged = saved.duplicates_removed ? ` 已合并 ${saved.duplicates_removed} 个重复节点，保留首次配置。` : "";
    notify((state.demo ? "演示配置已应用。" : "节点配置已保存。") + merged);
    await poll();
  } catch (error) {
    $("config-error").textContent = error.message;
    $("config-error").hidden = false;
  } finally { $("save-config").disabled = false; }
});

$("interval-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (!state) return;
  try {
    await api("/api/config", {...state.config, interval: Number($("interval").value)});
    notify("监控周期已应用。");
    await poll();
  } catch (error) { notify(error.message, true); }
});

async function control(action) {
  try { await api("/api/control", {action}); await poll(); }
  catch (error) { notify(error.message, true); }
}
$("refresh").addEventListener("click", () => control("refresh"));
$("pause").addEventListener("click", () => control(state?.paused ? "resume" : "pause"));
poll();
setInterval(poll, 1000);
