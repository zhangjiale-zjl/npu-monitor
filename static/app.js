"use strict";
const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="monitor-token"]').content;
let state = null;
let lastRender = "";
let polling = false;
let serviceDisconnected = false;
let noticeTimer;
const detailsOpen = new Set();
let directorySignature = "";
let selectedModel = null;
let directoryCollapsed = false;
let directoryHovered = false;
let directoryFocused = false;
let directoryHoverTimer;
let directoryWidth = 168;
let directoryResize = null;
let directoryDrag = null;
let directorySaving = false;
let directoryOrderRevision = 0;
let directoryClickSuppressedUntil = 0;
const directoryDesktopMedia = window.matchMedia("(min-width: 761px)");
const directoryHoverMedia = window.matchMedia("(min-width: 761px) and (hover: hover) and (pointer: fine)");
try {
  directoryCollapsed = localStorage.getItem("npu-monitor-directory-collapsed") === "true";
  const width = Number(localStorage.getItem("npu-monitor-directory-width"));
  if (Number.isFinite(width) && width >= 144 && width <= 420) directoryWidth = width;
} catch (_) {}

function directoryMaxWidth() {
  return Math.max(144, Math.min(420, document.documentElement.clientWidth - 380));
}

function applyDirectoryWidth() {
  const width = Math.min(directoryWidth, directoryMaxWidth());
  $("model-sidebar").style.setProperty("--directory-width", `${width}px`);
  const handle = $("directory-resize");
  handle.setAttribute("aria-valuemax", String(directoryMaxWidth()));
  handle.setAttribute("aria-valuenow", String(width));
  handle.setAttribute("aria-valuetext", `${width} 像素`);
}

function saveDirectoryWidth(width) {
  directoryWidth = Math.max(144, Math.min(directoryMaxWidth(), Math.round(width)));
  applyDirectoryWidth();
  try { localStorage.setItem("npu-monitor-directory-width", String(directoryWidth)); } catch (_) {}
}

function applyDirectoryState() {
  const floating = directoryCollapsed && directoryHoverMedia.matches
    && (directoryHovered || directoryFocused || directoryResize !== null || directoryDrag !== null || directorySaving);
  $("model-sidebar").classList.toggle("collapsed", directoryCollapsed);
  $("model-sidebar").classList.toggle("floating-open", floating);
  $("model-directory-content").hidden = directoryCollapsed && !floating;
  $("directory-hover-hint").hidden = !floating;
  $("directory-resize").hidden = !directoryDesktopMedia.matches || (directoryCollapsed && !floating);
  const toggle = $("sidebar-toggle");
  const label = floating ? "固定展开目录" : directoryCollapsed ? "展开目录" : "收起目录";
  toggle.setAttribute("aria-expanded", String(!directoryCollapsed || floating));
  toggle.setAttribute("aria-label", label);
  toggle.title = label;
  toggle.querySelector("span").textContent = floating ? "固定" : directoryCollapsed ? "›" : "‹";
}

function cancelDirectoryHover() {
  clearTimeout(directoryHoverTimer);
  directoryHoverTimer = null;
  directoryHovered = false;
}

$("sidebar-toggle").addEventListener("click", () => {
  if (directoryDrag || directorySaving || FolderUI.drag) return;
  finishDirectoryResize();
  directoryCollapsed = !directoryCollapsed;
  cancelDirectoryHover();
  directoryFocused = false;
  try { localStorage.setItem("npu-monitor-directory-collapsed", String(directoryCollapsed)); } catch (_) {}
  applyDirectoryState();
});
$("model-sidebar").addEventListener("pointerenter", event => {
  if (event.pointerType !== "mouse" || !directoryHoverMedia.matches || !directoryCollapsed) return;
  cancelDirectoryHover();
  directoryHoverTimer = setTimeout(() => {
    directoryHoverTimer = null;
    if (!directoryCollapsed || !directoryHoverMedia.matches) return;
    directoryHovered = true;
    applyDirectoryState();
  }, 500);
});
$("model-sidebar").addEventListener("pointerleave", () => {
  if (directoryResize || directoryDrag || FolderUI.drag) return;
  cancelDirectoryHover();
  applyDirectoryState();
});
$("model-sidebar").addEventListener("focusin", event => {
  directoryFocused = event.target.matches(":focus-visible");
  applyDirectoryState();
});
$("model-sidebar").addEventListener("focusout", event => {
  if ($("model-sidebar").contains(event.relatedTarget)) return;
  directoryFocused = false;
  applyDirectoryState();
});
directoryHoverMedia.addEventListener("change", () => {
  finishDirectoryResize();
  cancelDirectoryHover();
  directoryFocused = false;
  applyDirectoryState();
});
const directoryResizeHandle = $("directory-resize");
directoryResizeHandle.addEventListener("pointerdown", event => {
  if (event.button !== 0 || !directoryDesktopMedia.matches || directoryResize || directoryDrag || directorySaving || FolderUI.drag) return;
  event.preventDefault();
  clearTimeout(directoryHoverTimer);
  directoryHoverTimer = null;
  directoryResize = {pointerId: event.pointerId, startX: event.clientX,
    startWidth: $("model-sidebar").querySelector(".sidebar-panel").getBoundingClientRect().width};
  directoryResizeHandle.setPointerCapture(event.pointerId);
  document.body.classList.add("directory-resizing");
  applyDirectoryState();
});
directoryResizeHandle.addEventListener("pointermove", event => {
  if (!directoryResize || event.pointerId !== directoryResize.pointerId) return;
  directoryWidth = Math.max(144, Math.min(directoryMaxWidth(),
    Math.round(directoryResize.startWidth + event.clientX - directoryResize.startX)));
  applyDirectoryWidth();
});

function finishDirectoryResize(event) {
  if (!directoryResize || (event && event.pointerId !== directoryResize.pointerId)) return;
  const pointerId = directoryResize.pointerId;
  directoryResize = null;
  if (directoryResizeHandle.hasPointerCapture(pointerId)) directoryResizeHandle.releasePointerCapture(pointerId);
  document.body.classList.remove("directory-resizing");
  directoryHovered = !!event && $("model-sidebar").contains(document.elementFromPoint(event.clientX, event.clientY));
  saveDirectoryWidth(directoryWidth);
  applyDirectoryState();
}

for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) {
  directoryResizeHandle.addEventListener(type, finishDirectoryResize);
}
directoryResizeHandle.addEventListener("keydown", event => {
  const width = Number(directoryResizeHandle.getAttribute("aria-valuenow"));
  const next = {ArrowLeft: width - 16, ArrowRight: width + 16, Home: 144, End: directoryMaxWidth()}[event.key];
  if (next === undefined) return;
  event.preventDefault();
  saveDirectoryWidth(next);
});
directoryResizeHandle.addEventListener("dblclick", () => saveDirectoryWidth(168));
window.addEventListener("resize", () => {
  finishDirectoryResize();
  applyDirectoryWidth();
  applyDirectoryState();
});
applyDirectoryWidth();
applyDirectoryState();

function el(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

async function api(path, data, timeoutMs = 10000) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
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
const utilizationValue = device => device.utilization_percent ?? device.aicore_percent;
const utilizationLabel = devices => devices.some(device => device.utilization_label === "NPU Util") ? "NPU Util" : "AICore";
const deviceId = device => device.chip_id == null ? String(device.npu_id) : `${device.npu_id}/${device.chip_id}`;
const deviceColumn = devices => devices.some(device => device.chip_id != null) ? "NPU / Die" : "NPU ID";
const dieCountText = devices => {
  const known = devices.filter(device => device.chip_id != null).length;
  return known === devices.length ? String(known) : known ? `${known}+?` : "—";
};
const dieSummary = devices => devices.some(device => device.chip_id != null) ? ` · ${dieCountText(devices)} die` : "";
const avg = (devices) => {
  const values = devices.map(utilizationValue).filter(v => v != null);
  return values.length ? values.reduce((a,b) => a+b, 0) / values.length : null;
};
const cardCount = devices => new Set(devices.map(device => device.npu_id)).size;

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
  table.setAttribute("aria-label", `${name} NPU 状态`);
  const thead = el("thead");
  const heading = el("tr");
  const usageLabel = utilizationLabel(devices);
  [deviceColumn(devices), "Health", usageLabel, "HBM", "Memory"].forEach(text => {
    const th = el("th", "", text); th.scope = "col"; heading.append(th);
    if (text === "NPU / Die") th.title = "NPU ID（卡号）/ Chip ID（卡内 die）；Phy-ID 见悬停信息";
    if (text === "NPU ID") th.title = "NPU ID（卡号）；当前输出未提供 Chip / die 编号";
    if ([usageLabel, "HBM", "Memory"].includes(text)) th.append(el("span", "column-unit", "%"));
    if (["HBM", "Memory"].includes(text)) th.title = "内存占用率；悬停查看已用 / 总量，详细信息中可展开内存明细";
  });
  thead.append(heading);
  const tbody = el("tbody");
  const sorted = [...devices].sort((a, b) => a.npu_id - b.npu_id || a.chip_id - b.chip_id);
  sorted.forEach((device, index) => {
    const row = el("tr", device.health === "OK" ? "" : "unhealthy");
    if (index && device.npu_id !== sorted[index - 1].npu_id) row.classList.add("card-start");
    const id = el("th", "device-id", deviceId(device));
    id.scope = "row";
    id.title = `${device.name} · NPU ${device.npu_id}` + (device.chip_id == null ? "" : ` / die ${device.chip_id}`)
      + (device.phy_id == null ? "" : ` · Phy-ID ${device.phy_id}`)
      + (device.device_id == null ? "" : ` · Device ${device.device_id}`)
      + ` · Bus ${device.bus_id} · ${fmt(device.temperature_c)}°C · ${fmt(device.power_w, 1)} W`;
    const health = el("td", "health-cell");
    health.append(el("span", "health-badge" + (device.health === "OK" ? " healthy" : " abnormal"), device.health || "—"));
    const utilization = el("td", "utilization-cell");
    const usage = utilizationValue(device);
    utilization.append(el("div", "metric-values", usage == null ? "—" : `${fmt(usage, 1)}%`), meter(usage));
    row.append(id, health, utilization, memoryCell(device.hbm), memoryCell(device.memory));
    tbody.append(row);
  });
  table.append(thead, tbody);
  const wrap = el("div", "device-table-wrap");
  wrap.append(table);
  return wrap;
}

function sparkline(history, label = "AICore") {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 600 40");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `最近 120 次采集的平均 ${label} 利用率，范围 0 到 100 百分比`);
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

function sshHostKeyHelp(message, node = {}) {
  if (/REMOTE HOST IDENTIFICATION HAS CHANGED|Offending .* key in/i.test(message)) {
    return {summary: "SSH 主机指纹与本机记录不一致。",
      instruction: "请向节点管理员核对服务器指纹，并更新该节点的本机记录后重试。"};
  }
  if (/No \S+ host key is known for .*strict checking/i.test(message)) {
    if (node.auth_type === "password") {
      return {summary: "SSH 主机指纹验证未通过。",
        instruction: "密码登录支持首次自动登记指纹。请重启更新后的本地服务再试；仍出现此错误时，检查 SSH 配置中的跳板机指纹设置。"};
    }
    const host = node.host || "节点地址";
    const user = node.user || "root";
    const port = node.port || 22;
    return {summary: "SSH 主机指纹尚未登记，连接尚未进入认证阶段。",
      instruction: "在运行工具的同一台机器、同一账号下执行下方命令，核对服务器指纹后输入 yes，再返回测试连接。",
      command: `ssh -o StrictHostKeyChecking=ask -p ${port} -l ${user} ${host}`};
  }
  if (/Host key verification failed/i.test(message)) {
    return {summary: "SSH 主机指纹验证失败。",
      instruction: "请检查原始错误，核对该节点的服务器指纹及本机 SSH 主机指纹记录。"};
  }
  return null;
}

function nodeCard(node) {
  const card = el("article", "node-card");
  card.dataset.nodeId = node.id;
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
  update.append(el("div", "", node.collecting ? "正在采集…" : timeText(node.updated_at)), el("div", "", `${cardCount(node.devices)} 卡${dieSummary(node.devices)} · 均 ${fmt(avg(node.devices), 1)}%`));
  const edit = el("button", "edit-node subtle", "编辑");
  edit.setAttribute("aria-label", `编辑节点 ${node.name}`);
  edit.addEventListener("click", () => openSettings({id: node.id}));
  right.append(update, edit);
  head.append(FolderUI.nodeHandle(node), identity, right);
  const body = el("div", "node-body");
  if (node.error || node.warning) {
    const message = node.error || node.warning;
    const hostKey = sshHostKeyHelp(message, config || node);
    const summary = hostKey?.summary || message;
    const alert = el("div", "node-alert" + (node.error ? " error" : ""));
    const text = el("div", "alert-message", summary);
    text.title = message;
    alert.append(text);
    if (hostKey) {
      alert.append(el("p", "ssh-host-key-help", hostKey.instruction));
      if (hostKey.command) alert.append(el("code", "ssh-host-key-command", hostKey.command));
    }
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
    table.setAttribute("aria-label", `${node.name} NPU 内存用量`);
    const thead = el("thead"); const heading = el("tr");
    [deviceColumn(node.devices), "HBM（GiB）", "Memory（GiB）"].forEach(text => {
      const th = el("th", "", text); th.scope = "col"; heading.append(th);
    });
    thead.append(heading);
    const tbody = el("tbody");
    node.devices.forEach(device => {
      const row = el("tr");
      const id = el("th", "device-id", deviceId(device)); id.scope = "row";
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
    const label = node.history.at(-1)?.label || utilizationLabel(node.devices);
    const caption = el("p", "", `平均 ${label}`);
    caption.append(el("br"), document.createTextNode(`最近 ${node.history.length} 次采集`));
    plot.append(caption, sparkline(node.history, label));
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
    [deviceColumn(node.processes), "PID", "进程名称", "显存（MB）", "容器 PID"].forEach(text => tr.append(el("th", "", text)));
    thead.append(tr); table.append(thead);
    const tbody = el("tbody");
    node.processes.forEach(process => {
      const row = el("tr");
      [deviceId(process), process.pid, process.name, fmt(process.memory_mb), process.container_pid ?? "—"].forEach(text => row.append(el("td", "", text)));
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

function ipv4Parts(address) {
  if (!/^\d{1,3}(?:\.\d{1,3}){3}$/.test(address)) return null;
  const parts = address.split(".").map(Number);
  return parts.every(part => part <= 255) ? parts : null;
}

function ipAddressParts(value) {
  let address = value.replace(/^\[|\]$/g, "").split("%")[0];
  const ipv4 = ipv4Parts(address);
  if (ipv4) return {family: 4, parts: ipv4};
  if (!address.includes(":")) return null;
  if (address.includes(".")) {
    const tail = ipv4Parts(address.slice(address.lastIndexOf(":") + 1));
    if (!tail) return null;
    address = address.slice(0, address.lastIndexOf(":") + 1)
      + ((tail[0] << 8) | tail[1]).toString(16) + ":" + ((tail[2] << 8) | tail[3]).toString(16);
  }
  const halves = address.split("::");
  if (halves.length > 2) return null;
  const left = halves[0] ? halves[0].split(":") : [];
  const right = halves.length === 2 && halves[1] ? halves[1].split(":") : [];
  const missing = 8 - left.length - right.length;
  if ((halves.length === 1 && missing !== 0) || (halves.length === 2 && missing < 1)
      || [...left, ...right].some(part => !/^[0-9a-f]{1,4}$/i.test(part))) return null;
  return {family: 6, parts: [...left.map(part => parseInt(part, 16)),
    ...Array(missing).fill(0), ...right.map(part => parseInt(part, 16))]};
}

function compareNodeAddresses(a, b) {
  const left = String(a.address || a.host || "");
  const right = String(b.address || b.host || "");
  const leftIp = ipAddressParts(left), rightIp = ipAddressParts(right);
  if (leftIp && rightIp) {
    if (leftIp.family !== rightIp.family) return leftIp.family - rightIp.family;
    for (let index = 0; index < leftIp.parts.length; index++) {
      if (leftIp.parts[index] !== rightIp.parts[index]) return leftIp.parts[index] - rightIp.parts[index];
    }
  } else {
    if (leftIp || rightIp) return leftIp ? -1 : 1;
    const result = left.localeCompare(right, "en", {numeric: true, sensitivity: "base"});
    if (result) return result;
  }
  const port = node => node.ssh_port || node.port || Number(String(node.id || "").match(/:(\d+)$/)?.[1]) || 22;
  return port(a) - port(b)
    || String(a.id || a.host || "").localeCompare(String(b.id || b.host || ""), "en", {numeric: true});
}

function compareModelNames(a, b) {
  if (a === b) return 0;
  const pending = name => ["型号未识别", "待识别机型"].includes(name);
  return pending(a) !== pending(b) ? pending(a) ? 1 : -1
    : a.localeCompare(b, "en", {sensitivity: "base"});
}

function modelGroups(nodes) {
  const groups = new Map();
  nodes.forEach(node => {
    const model = node.server_model || "型号未识别";
    if (!groups.has(model)) groups.set(model, []);
    groups.get(model).push(node);
  });
  const orders = state?.config?.node_order || {};
  for (const [model, members] of groups) {
    const rank = new Map((Array.isArray(orders[model]) ? orders[model] : []).map((id, index) => [id, index]));
    members.sort((a, b) => ((rank.get(a.id) ?? Infinity) - (rank.get(b.id) ?? Infinity)) || compareNodeAddresses(a, b));
  }
  const modelRank = new Map((state?.config?.model_order || []).map((model, index) => [model, index]));
  return [...groups].sort(([a], [b]) => ((modelRank.get(a) ?? Infinity) - (modelRank.get(b) ?? Infinity))
    || compareModelNames(a, b));
}

function markDirectorySelection() {
  $("model-directory").querySelectorAll(".model-filter").forEach(button => {
    const selected = FolderUI.selected !== null ? button.dataset.folder === FolderUI.selected
      : !button.dataset.folder && button.dataset.model === (selectedModel ?? "");
    button.classList.toggle("selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
}

function directoryButton(label, count, model, folderId = null) {
  const button = el("button", "model-filter");
  button.type = "button";
  button.dataset.model = model ?? "";
  button.dataset.folder = folderId ?? "";
  button.setAttribute("aria-label", `${label}，${count} 个节点`);
  button.setAttribute("aria-controls", "nodes");
  button.append(el("span", "model-label", label), el("span", "model-count", count));
  button.title = `${label} · ${count} 个节点`;
  button.addEventListener("click", () => {
    if (performance.now() < directoryClickSuppressedUntil) return;
    selectedModel = model;
    FolderUI.selected = folderId;
    render();
  });
  return button;
}

async function saveDirectoryOrder(model, focus = false, folderId = null) {
  if (folderId) {
    const order = [...$("model-directory").querySelectorAll(".directory-folder")].map(row => row.dataset.folder);
    try { await FolderUI.save({action: "reorder", folder_ids: order}, "目录顺序已自动保存。"); }
    catch (error) { notify(error.message, true); }
    if (focus) FolderUI.focusRow(folderId);
    return;
  }
  const previous = [...(state.config.model_order || [])];
  const order = [...$("model-directory").querySelectorAll(".directory-model")].map(row => row.dataset.model);
  directorySaving = true;
  directoryOrderRevision++;
  state.config.model_order = order;
  $("model-directory").querySelectorAll(".directory-order-handle").forEach(handle => { handle.disabled = true; });
  applyDirectoryState();
  render();
  try {
    const saved = await api("/api/model-order", {model_order: order});
    state.config.model_order = saved.model_order;
    $("directory-order-status").textContent = "机型顺序已自动保存。";
    notify(state.demo ? "演示机型顺序已自动应用。" : "机型顺序已自动保存。");
  } catch (error) {
    state.config.model_order = previous;
    $("directory-order-status").textContent = "机型顺序保存失败，请重试。";
    notify(`机型顺序保存失败：${error.message}，请重试。`, true);
  } finally {
    directorySaving = false;
    directorySignature = "";
    lastRender = "";
    render();
    if (focus) {
      const row = [...$("model-directory").querySelectorAll(".directory-model")].find(entry => entry.dataset.model === model);
      row?.querySelector(".directory-order-handle").focus();
    }
    applyDirectoryState();
  }
}

function moveDirectoryDrag() {
  const drag = directoryDrag;
  if (!drag?.moved) return;
  const columns = getComputedStyle(drag.list).gridTemplateColumns.split(" ").length;
  const before = [...drag.list.children].find(row => {
    if (row === drag.row) return false;
    const bounds = row.getBoundingClientRect();
    if (columns > 1 && drag.clientY >= bounds.top && drag.clientY <= bounds.bottom) return drag.clientX < bounds.left + bounds.width / 2;
    return drag.clientY < bounds.top + bounds.height / 2;
  });
  if (before !== drag.row.nextElementSibling) drag.list.insertBefore(drag.row, before || null);
}

function scrollDirectoryDrag() {
  const drag = directoryDrag;
  if (!drag) return;
  const content = $("model-directory-content");
  const bounds = content.getBoundingClientRect();
  const speed = drag.clientY < bounds.top + 25 ? -8 : drag.clientY > bounds.bottom - 25 ? 8 : 0;
  if (speed) { content.scrollTop += speed; moveDirectoryDrag(); }
  drag.frame = requestAnimationFrame(scrollDirectoryDrag);
}

function finishDirectoryDrag(event) {
  if (!directoryDrag || (event && event.pointerId !== directoryDrag.pointerId)) return;
  const {row, list, pointerId, moved, initialRows, frame} = directoryDrag;
  directoryDrag = null;
  cancelAnimationFrame(frame);
  row.classList.remove("reordering");
  document.body.classList.remove("directory-sorting");
  if (list.hasPointerCapture(pointerId)) list.releasePointerCapture(pointerId);
  if (moved) directoryClickSuppressedUntil = performance.now() + 350;
  if (event) directoryHovered = $("model-sidebar").contains(document.elementFromPoint(event.clientX, event.clientY));
  if (!moved) { lastRender = ""; applyDirectoryState(); return; }
  const changed = moved && initialRows.some((entry, index) => list.children[index] !== entry);
  if (changed && event?.type === "pointerup") void saveDirectoryOrder(row.dataset.model, false, row.dataset.folder);
  else {
    initialRows.forEach(entry => list.append(entry));
    directorySignature = "";
    lastRender = "";
    render();
    applyDirectoryState();
  }
}

function directoryModel(model, count, list, folderId = null) {
  const row = el("div", folderId ? "directory-folder" : "directory-model");
  row.dataset.model = model;
  row.dataset.folder = folderId ?? "";
  const handle = el("button", "directory-order-handle", "⋮⋮");
  handle.type = "button";
  handle.title = folderId ? "拖动调整目录顺序，松开自动保存；↑↓调整顺序" : "拖动调整机型顺序，松开自动保存；↑↓调整顺序";
  handle.setAttribute("aria-label", folderId ? `调整自定义目录 ${model} 的顺序` : `调整目录机型 ${model} 的顺序`);
  row.append(handle, directoryButton(model, count, folderId ? null : model, folderId));
  row.addEventListener("pointerdown", event => {
    if (event.button !== 0 || directoryDrag || directorySaving || directoryResize || FolderUI.drag
        || (event.pointerType === "touch" && !handle.contains(event.target))) return;
    event.preventDefault();
    directoryDrag = {row, list, pointerId: event.pointerId, startX: event.clientX, startY: event.clientY,
      clientX: event.clientX, clientY: event.clientY, moved: false, initialRows: [...list.children]};
    applyDirectoryState();
  });
  handle.addEventListener("keydown", event => {
    if (directorySaving || directoryDrag) return;
    if (event.key === "ArrowUp" && row.previousElementSibling) {
      event.preventDefault(); list.insertBefore(row, row.previousElementSibling);
    } else if (event.key === "ArrowDown" && row.nextElementSibling) {
      event.preventDefault(); list.insertBefore(row.nextElementSibling, row);
    } else return;
    void saveDirectoryOrder(model, true, folderId);
  });
  return row;
}

$("model-directory").addEventListener("pointermove", event => {
  if (!directoryDrag || event.pointerId !== directoryDrag.pointerId) return;
  const drag = directoryDrag;
  drag.clientX = event.clientX;
  drag.clientY = event.clientY;
  if (!drag.moved && Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) < 5) return;
  if (!drag.moved) {
    drag.moved = true;
    drag.list.setPointerCapture(event.pointerId);
    drag.row.classList.add("reordering");
    document.body.classList.add("directory-sorting");
    drag.frame = requestAnimationFrame(scrollDirectoryDrag);
  }
  moveDirectoryDrag();
});
for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) $("model-directory").addEventListener(type, finishDirectoryDrag);
for (const type of ["pointerup", "pointercancel"]) document.addEventListener(type, finishDirectoryDrag);
window.addEventListener("blur", () => finishDirectoryDrag());

function renderDirectory(groups) {
  if (directoryDrag || directorySaving || FolderUI.drag) return;
  const signature = JSON.stringify([groups.map(([model, nodes]) => [model, nodes.length]), state.config.folders,
    state.nodes.map(node => [node.id, node.name, node.status])]);
  if (signature !== directorySignature) {
    directorySignature = signature;
    const list = el("div", "model-filters");
    groups.forEach(([model, nodes]) => list.append(directoryModel(model, nodes.length, list)));
    $("model-directory").replaceChildren(directoryButton("全部节点", state.nodes.length, null),
      FolderUI.sidebar(), el("div", "directory-section-title", "机器型号"), list);
    $("directory-empty").hidden = groups.length > 0;
  }
  markDirectorySelection();
}

function modelSection(model, nodes) {
  const section = el("section", "model-group");
  if (nodes.length > 2) section.classList.add("model-wide");
  else if (nodes.length === 2) section.classList.add("model-double");
  section.setAttribute("aria-label", `${model} 节点分区`);
  const heading = el("div", "model-heading");
  heading.append(el("h3", "", model));
  const devices = nodes.flatMap(node => node.devices);
  const cards = nodes.reduce((sum, node) => sum + cardCount(node.devices), 0);
  const failed = nodes.filter(node => node.status === "error" || node.status === "warning").length;
  heading.append(el("span", "", `${nodes.length} 节点 · ${cards} 卡${dieSummary(devices)}${failed ? ` · ${failed} 采集异常` : ""}`));
  const grid = el("div", "nodes-grid");
  grid.append(...nodes.map(nodeCard));
  section.append(heading, grid);
  return section;
}

function render() {
  if (!state || FolderUI.drag) return;
  const allGroups = modelGroups(state.nodes);
  const folder = FolderUI.current();
  if (selectedModel !== null && !allGroups.some(([model]) => model === selectedModel)) selectedModel = null;
  const signature = JSON.stringify([state.nodes, state.paused, state.config, selectedModel, FolderUI.selected]);
  if (signature === lastRender) return;
  lastRender = signature;
  $("demo-label").hidden = !state.demo;
  $("pause").textContent = state.paused ? "继续采集" : "暂停采集";
  $("refresh").disabled = state.paused;
  $("poll-state").textContent = state.paused ? "采集已暂停" : `每 ${state.config.interval} 秒采集`;
  if (document.activeElement !== $("interval")) $("interval").value = state.config.interval;
  const groups = folder ? [[folder.name, FolderUI.members(folder)]]
    : selectedModel === null ? allGroups : allGroups.filter(([model]) => model === selectedModel);
  const nodes = groups.flatMap(([, members]) => members);
  const devices = nodes.flatMap(n => n.devices);
  const online = nodes.filter(n => n.status === "ok").length;
  renderDirectory(allGroups);
  $("view-title").textContent = `${folder?.name ?? selectedModel ?? "全部节点"} · NPU 总览`;
  $("view-description").textContent = state.paused ? "采集已暂停，展示最近一次结果" : `${online} / ${nodes.length} 节点采集正常 · ${folder ? "自定义目录" : selectedModel === null ? "按服务器型号分区" : "当前机型"}`;
  $("summary").replaceChildren();
  const cards = nodes.reduce((sum, node) => sum + cardCount(node.devices), 0);
  for (const [label,value] of [["节点", nodes.length], ["卡 / die", `${cards} / ${dieCountText(devices)}`], ["Health 非 OK", devices.filter(d => d.health !== "OK").length], ["采集异常", nodes.filter(n => n.status === "error" || n.status === "warning").length]]) {
    const item = el("div", "summary-item", label); item.prepend(el("strong", "", value)); $("summary").append(item);
  }
  $("nodes").replaceChildren(...groups.map(([model, members]) => modelSection(model, members)));
  $("empty").hidden = state.nodes.length > 0 || !!folder;
  $("folder-empty").hidden = !folder || nodes.length > 0;
  applyDirectoryWidth();
}

async function poll() {
  if (polling || directorySaving || FolderUI.drag) return;
  polling = true;
  const revision = directoryOrderRevision;
  try {
    const next = await api("/api/state");
    if (revision !== directoryOrderRevision || FolderUI.drag) return;
    state = next;
    if (serviceDisconnected) notify("本地服务已连接。");
    serviceDisconnected = false;
    $("server-dot").className = "dot";
    $("connection-text").textContent = "本地服务已连接";
    render();
  } catch (error) {
    if (revision !== directoryOrderRevision) return;
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

function configNodeValue(fieldset) {
  const node = {};
  fieldset.querySelectorAll("[data-field]").forEach(input => {
    const field = input.dataset.field;
    if (field !== "password") node[field] = field === "port" ? (input.value ? Number(input.value) : null) : input.value.trim();
  });
  node.user = node.user || "root";
  node.port = node.port ?? 22;
  const password = fieldset.querySelector('[data-field="password"]').value;
  if (node.auth_type === "password" && password !== "") node.password = password;
  return node;
}

function clearConnectionTest(fieldset) {
  fieldset.dataset.testVersion = String(Number(fieldset.dataset.testVersion || 0) + 1);
  fieldset.querySelector(".node-test-result").hidden = true;
}

async function testConfigNode(fieldset) {
  const button = fieldset.querySelector(".test-node");
  const result = fieldset.querySelector(".node-test-result");
  clearConnectionTest(fieldset);
  const version = fieldset.dataset.testVersion;
  button.disabled = true;
  button.textContent = "测试中…";
  result.hidden = false;
  result.className = "node-test-result pending";
  result.textContent = "正在测试 SSH 连接与宿主机 npu-smi info…";
  try {
    const manage = settingsMode === "manage";
    const fields = [...fieldset.querySelectorAll("[data-field]:not(:disabled)")];
    if (manage) fields.push($("timeout"));
    const invalid = fields.find(input => !input.checkValidity());
    if (invalid) { invalid.reportValidity(); throw new Error("请检查该节点的连接信息和采集超时。"); }
    if (!state.connection_test_supported) throw new Error("请重新启动更新后的监控工具，再测试连接。");
    const timeout = manage ? Number($("timeout").value) : state.config.timeout;
    const node = configNodeValue(fieldset);
    const defaults = manage ? {identity_file: $("default-key").value.trim(), timeout} : {};
    const response = await api("/api/test-connection", {node, ...defaults}, (timeout + 30) * 1000);
    if (!fieldset.isConnected || fieldset.dataset.testVersion !== version) return;
    result.className = "node-test-result " + response.status;
    const hostKey = response.status === "error" ? sshHostKeyHelp(response.detail || "", node) : null;
    result.textContent = (hostKey?.summary || response.message) + (response.status === "error" && response.exit_code != null ? ` · 返回码 ${response.exit_code}` : "")
      + (response.duration_ms == null ? "" : ` · ${fmt(response.duration_ms / 1000, 1)} 秒`)
      + (hostKey ? `\n${hostKey.instruction}` + (hostKey.command ? `\n${hostKey.command}` : "") : "")
      + (response.detail ? `\n${response.detail}` : "");
  } catch (error) {
    if (fieldset.isConnected && fieldset.dataset.testVersion === version) {
      result.className = "node-test-result error";
      result.textContent = error.name === "AbortError" ? "连接测试未完成，请检查本地服务并重试。" : error.message;
    }
  } finally { button.disabled = false; button.textContent = "测试连接"; }
}

for (const id of ["default-key", "timeout"]) $(id).addEventListener("input", () => {
  $("config-nodes").querySelectorAll(".config-node").forEach(clearConnectionTest);
});

let settingsMode = "manage";
let configGroups = new Map();
let configDrag = null;
let settingsNodeOrder = new Map();
let settingsModelOrder = [];

function announceNodeOrder(row) {
  const list = row.parentElement;
  const ids = [...list.children].map(entry => entry.querySelector(".config-node").dataset.id).filter(Boolean);
  if (ids.length) settingsNodeOrder.set(list.dataset.model, ids);
  const position = [...row.parentElement.children].indexOf(row) + 1;
  $("config-order-status").textContent = `${row.querySelector(".config-node-name").textContent} 已调整至当前机型第 ${position} 个节点，保存后生效。`;
}

function finishConfigDrag(event) {
  if (!configDrag || (event && event.pointerId !== configDrag.pointerId)) return;
  const {row, list, pointerId, moved, initialRows, announce, frame} = configDrag;
  configDrag = null;
  cancelAnimationFrame(frame);
  row.classList.remove("reordering");
  document.body.classList.remove("config-reordering");
  if (list.hasPointerCapture(pointerId)) list.releasePointerCapture(pointerId);
  if (moved && row.isConnected && initialRows.some((entry, index) => list.children[index] !== entry)) announce(row);
}

function moveConfigDrag() {
  const drag = configDrag;
  if (!drag?.moved) return;
  const before = [...drag.list.children].find(row => {
    if (row === drag.row) return false;
    const bounds = (drag.heading ? row.querySelector(drag.heading) : row).getBoundingClientRect();
    return drag.clientY < bounds.top + bounds.height / 2;
  });
  if (before !== drag.row.nextElementSibling) drag.list.insertBefore(drag.row, before || null);
}

function scrollConfigDrag() {
  const drag = configDrag;
  if (!drag) return;
  const body = $("settings-dialog").querySelector(".dialog-body");
  const bounds = body.getBoundingClientRect();
  const speed = drag.clientY < bounds.top + 35 ? -Math.min(12, (bounds.top + 35 - drag.clientY) / 3)
    : drag.clientY > bounds.bottom - 35 ? Math.min(12, (drag.clientY - bounds.bottom + 35) / 3) : 0;
  if (speed) { body.scrollTop += speed; moveConfigDrag(); }
  drag.frame = requestAnimationFrame(scrollConfigDrag);
}

function configOrderHandle(handle, row, list, announce, heading = null) {
  handle.addEventListener("pointerdown", event => {
    if (event.button !== 0 || configDrag || $("save-config").disabled) return;
    event.preventDefault();
    configDrag = {row, list, announce, heading, pointerId: event.pointerId, startY: event.clientY,
      clientY: event.clientY, moved: false, initialRows: [...list.children]};
    list.setPointerCapture(event.pointerId);
  });
  handle.addEventListener("keydown", event => {
    if ($("save-config").disabled || configDrag) return;
    if (event.key === "ArrowUp" && row.previousElementSibling) {
      event.preventDefault(); list.insertBefore(row, row.previousElementSibling);
    } else if (event.key === "ArrowDown" && row.nextElementSibling) {
      event.preventDefault(); list.insertBefore(row.nextElementSibling, row);
    } else return;
    handle.focus();
    announce(row);
  });
}

$("config-nodes").addEventListener("pointermove", event => {
  if (!configDrag || event.pointerId !== configDrag.pointerId) return;
  const drag = configDrag;
  drag.clientY = event.clientY;
  if (!drag.moved && Math.abs(event.clientY - drag.startY) < 5) return;
  if (!drag.moved) {
    drag.moved = true;
    drag.row.classList.add("reordering");
    document.body.classList.add("config-reordering");
    drag.frame = requestAnimationFrame(scrollConfigDrag);
  }
  moveConfigDrag();
});
for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) $("config-nodes").addEventListener(type, finishConfigDrag);

function announceModelOrder(section) {
  settingsModelOrder = [...$("config-nodes").children].map(group => group.dataset.model).filter(Boolean);
  const position = settingsModelOrder.indexOf(section.dataset.model) + 1;
  $("config-order-status").textContent = `${section.dataset.model} 已调整至第 ${position} 个机型，保存后生效。`;
}

$("reset-model-order").addEventListener("click", () => {
  if ($("save-config").disabled) return;
  finishConfigDrag();
  const list = $("config-nodes");
  [...list.children].sort((a, b) => compareModelNames(a.dataset.model, b.dataset.model)).forEach(section => list.append(section));
  settingsModelOrder = [];
  $("config-order-status").textContent = "机型已恢复字母升序，保存后生效。";
});

function configGroup(model) {
  if (configGroups.has(model)) return configGroups.get(model);
  const section = el("section", "config-group");
  section.dataset.model = model;
  section.setAttribute("aria-label", `${model} 节点配置`);
  const heading = el("div", "config-group-heading");
  const title = el("div", "config-group-title");
  const handle = el("button", "model-order-handle", "⋮⋮");
  handle.type = "button";
  handle.title = "拖动调整机型顺序；↑↓调整顺序";
  handle.setAttribute("aria-label", `调整机型 ${model} 的顺序`);
  title.append(handle, el("h3", "", model));
  heading.append(title);
  const list = el("div", "config-group-nodes");
  list.dataset.model = model;
  const sort = el("button", "subtle reset-node-order", "按 IP 排序");
  sort.type = "button";
  sort.title = "恢复该机型的默认 IP 升序，保存后生效";
  sort.addEventListener("click", () => {
    if ($("save-config").disabled) return;
    finishConfigDrag();
    const value = row => {
      const fieldset = row.querySelector(".config-node");
      const host = fieldset.querySelector('[data-field="host"]').value.trim();
      return {host, address: host === fieldset.dataset.originalHost ? fieldset.dataset.address : host,
        id: fieldset.dataset.id, port: Number(fieldset.querySelector('[data-field="port"]').value || 22)};
    };
    [...list.children].sort((a, b) => compareNodeAddresses(value(a), value(b))).forEach(row => list.append(row));
    settingsNodeOrder.delete(model);
    $("config-order-status").textContent = `${model} 已恢复 IP 升序，保存后生效。`;
  });
  heading.append(sort);
  section.append(heading, list);
  $("config-nodes").append(section);
  configGroups.set(model, list);
  configOrderHandle(handle, section, $("config-nodes"), announceModelOrder, ".config-group-heading");
  return list;
}

function addConfigNode(node = {}, {model = "待识别机型", open = true} = {}) {
  const fragment = $("config-node-template").content.cloneNode(true);
  const fieldset = fragment.querySelector("fieldset");
  fieldset.dataset.id = node.id || "";
  fieldset.dataset.originalHost = node.host || "";
  fieldset.dataset.address = state.nodes.find(live => live.id === node.id)?.address || node.address || node.host || "";
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
    const note = fieldset.querySelector(".auth-note");
    note.hidden = !byPassword && !node.password_set;
    note.textContent = byPassword ? "密码以明文保存在本机配置文件。" + (node.password_set ? "留空保留已有密码。" : "")
      : "保存为密钥登录后，将清除该节点已保存的密码。";
    password.disabled = !byPassword;
    password.required = byPassword && !node.password_set;
    fieldset.querySelector('[data-field="identity_file"]').disabled = byPassword;
  }
  auth.addEventListener("change", updateAuth);
  updateAuth();
  fieldset.querySelector(".test-node").addEventListener("click", () => testConfigNode(fieldset));
  fieldset.addEventListener("input", () => clearConnectionTest(fieldset));
  fieldset.addEventListener("change", () => clearConnectionTest(fieldset));
  if (settingsMode === "add") {
    fieldset.querySelector(".remove-node").hidden = true;
    $("config-nodes").append(fragment);
    return fieldset;
  }
  const list = configGroup(model);
  const row = el("div", "config-row");
  const handle = el("button", "node-order-handle", "⋮⋮");
  handle.type = "button";
  handle.title = "拖动调整机型内顺序；↑↓调整顺序";
  handle.setAttribute("aria-label", `调整节点 ${node.name || "新节点"} 的顺序`);
  const entry = el("details", "config-entry");
  const summary = el("summary", "config-node-name", node.name || "新节点");
  entry.append(summary, fragment);
  entry.open = open;
  row.append(handle, entry);
  list.append(row);
  $("config-empty").hidden = true;
  fieldset.addEventListener("input", () => {
    if (!node.id) summary.textContent = fieldset.querySelector('[data-field="host"]').value.trim() || "新节点";
  });
  configOrderHandle(handle, row, list, announceNodeOrder);
  fieldset.querySelector(".remove-node").addEventListener("click", () => {
    finishConfigDrag();
    row.remove();
    if (!list.children.length) { list.parentElement.remove(); configGroups.delete(model); }
    $("config-empty").hidden = !!$("config-nodes").querySelector(".config-node");
  });
  return fieldset;
}

function openSettings({add = false, id = null} = {}) {
  if (!state) { notify("请先启动本地服务。", true); return; }
  finishConfigDrag();
  settingsMode = add ? "add" : "manage";
  configGroups = new Map();
  settingsNodeOrder = new Map(Object.entries(state.config.node_order || {}).map(([model, ids]) => [model, [...ids]]));
  settingsModelOrder = [...(state.config.model_order || [])];
  $("settings-title").textContent = add ? "添加节点" : "管理节点";
  $("settings-description").textContent = add ? "填写连接信息，可先测试连接再添加。"
    : "默认按机型、IP 升序；拖动机型或节点左侧滑块调整顺序，保存后生效。";
  $("config-nodes-title").textContent = add ? "节点信息" : "监控节点";
  $("add-node").hidden = add;
  $("reset-model-order").hidden = add;
  $("connection-defaults").hidden = add;
  $("connection-defaults").open = false;
  $("save-config").textContent = add ? "添加节点" : "保存配置";
  $("default-key").value = state.config.identity_file;
  $("timeout").value = state.config.timeout;
  $("config-nodes").replaceChildren();
  let target;
  if (add) target = addConfigNode();
  else {
    const config = new Map(state.config.nodes.map(node => [node.id, node]));
    for (const [model, nodes] of modelGroups(state.nodes)) for (const node of nodes) {
      const fieldset = addConfigNode(config.get(node.id), {model, open: node.id === id});
      if (node.id === id) target = fieldset;
    }
  }
  $("config-empty").hidden = add || state.config.nodes.length > 0;
  $("config-order-status").textContent = "";
  $("config-error").hidden = true;
  $("config-note").textContent = state.demo ? "演示模式：配置不写入文件" : "保存到启动时指定的配置文件";
  $("settings-dialog").showModal();
  $("settings-dialog").querySelector(".dialog-body").scrollTop = 0;
  if (target) {
    target.scrollIntoView({block: "nearest"});
    target.querySelector('[data-field="host"]').focus();
  }
}

$("settings").addEventListener("click", () => openSettings());
$("new-node").addEventListener("click", () => openSettings({add: true}));
$("empty-settings").addEventListener("click", () => openSettings({add: true}));
for (const id of ["close-settings", "cancel-settings"]) $(id).addEventListener("click", () => $("settings-dialog").close());
$("add-node").addEventListener("click", () => {
  const fieldset = addConfigNode();
  fieldset.scrollIntoView({block: "nearest"});
  fieldset.querySelector('[data-field="host"]').focus();
});
$("settings-dialog").addEventListener("close", () => {
  finishConfigDrag();
  $("config-nodes").replaceChildren();
  configGroups.clear();
});
window.addEventListener("blur", () => finishConfigDrag());

$("config-form").addEventListener("submit", async event => {
  event.preventDefault();
  if ($("save-config").disabled) return;
  finishConfigDrag();
  const inputs = [...$("config-nodes").querySelectorAll("[data-field]:not(:disabled)")];
  if (settingsMode === "manage") inputs.push($("timeout"));
  const invalid = inputs.find(input => !input.checkValidity());
  if (invalid) {
    const details = invalid.closest("details");
    if (details) details.open = true;
    invalid.scrollIntoView({block: "nearest"});
    invalid.reportValidity();
    return;
  }
  const nodes = [...$("config-nodes").querySelectorAll(".config-node")].map(configNodeValue);
  $("save-config").disabled = true;
  try {
    if (nodes.some(node => node.auth_type === "password") && !state.auth_password_supported) {
      throw new Error("请重新启动更新后的监控工具，再使用密码登录。");
    }
    if (!state.auto_node_names) throw new Error("请重启本地监控服务后使用自动命名与节点去重。");
    const config = {...state.config};
    delete config.folders;
    const saved = settingsMode === "add" ? await api("/api/nodes", {nodes})
      : await api("/api/config", {...config, nodes, timeout: Number($("timeout").value),
        identity_file: $("default-key").value.trim(), node_order: Object.fromEntries(settingsNodeOrder),
        model_order: settingsModelOrder});
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
    const config = {...state.config};
    delete config.folders;
    await api("/api/config", {...config, interval: Number($("interval").value)});
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
FolderUI.init();
poll();
setInterval(poll, 1000);
