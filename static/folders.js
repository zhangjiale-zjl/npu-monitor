"use strict";

const FolderUI = {
  selected: null,
  expanded: new Set(),
  drag: null,
  editing: null,
  editingNode: null,
  addingFolder: null,

  folders() { return state?.config?.folders || []; },
  current() {
    const folder = this.folders().find(item => item.id === this.selected);
    if (!folder) this.selected = null;
    return folder || null;
  },
  members(folder) {
    const ids = new Set(folder.node_ids);
    return state.nodes.filter(node => ids.has(node.id)).sort(compareNodeAddresses);
  },
  refresh() { directorySignature = ""; lastRender = ""; render(); },
  focusRow(id) {
    [...$("model-directory").querySelectorAll(".directory-folder")].find(row => row.dataset.folder === id)
      ?.querySelector(".directory-order-handle").focus();
  },
  async save(data, message) {
    if (directorySaving) throw new Error("目录正在保存，请稍后重试。");
    directorySaving = true;
    directoryOrderRevision++;
    $("model-directory").querySelectorAll("button").forEach(button => { button.disabled = true; });
    applyDirectoryState();
    try {
      const saved = await api("/api/folders", data);
      state.config.folders = saved.folders;
      $("directory-order-status").textContent = message;
      notify(state.demo ? "演示模式：" + message : message);
      return saved;
    } finally {
      directorySaving = false;
      this.refresh();
      applyDirectoryState();
    }
  },
  sidebar() {
    const section = el("div", "custom-directories");
    const heading = el("div", "directory-section-heading");
    const add = el("button", "folder-new", "＋");
    add.type = "button";
    add.title = "新建自定义目录";
    add.setAttribute("aria-label", "新建自定义目录");
    add.addEventListener("click", () => this.edit());
    heading.append(el("span", "directory-section-title", "自定义目录"), add);
    section.append(heading);
    const list = el("div", "folder-list");
    for (const folder of this.folders()) {
      const nodes = this.members(folder);
      const row = directoryModel(folder.name, nodes.length, list, folder.id);
      const expand = el("button", "folder-expand", this.expanded.has(folder.id) ? "⌄" : "›");
      expand.type = "button";
      expand.setAttribute("aria-expanded", String(this.expanded.has(folder.id)));
      expand.setAttribute("aria-label", "展开或收起目录 " + folder.name + " 的节点");
      expand.addEventListener("pointerdown", event => event.stopPropagation());
      expand.addEventListener("click", () => {
        if (this.expanded.has(folder.id)) this.expanded.delete(folder.id);
        else this.expanded.add(folder.id);
        this.refresh();
      });
      row.insertBefore(expand, row.children[1]);
      const addNodes = el("button", "folder-add-nodes", "＋");
      addNodes.type = "button";
      addNodes.setAttribute("aria-label", "向目录 " + folder.name + " 添加节点");
      addNodes.title = "选择一个或多个节点加入目录";
      addNodes.addEventListener("pointerdown", event => event.stopPropagation());
      addNodes.addEventListener("click", () => this.addNodes(folder));
      const more = el("button", "folder-more", "⋯");
      more.type = "button";
      more.setAttribute("aria-label", "设置目录 " + folder.name);
      more.title = "重命名或删除目录";
      more.addEventListener("pointerdown", event => event.stopPropagation());
      more.addEventListener("click", () => this.edit(folder));
      row.append(addNodes, more);
      if (this.expanded.has(folder.id)) {
        const members = el("div", "folder-members");
        members.setAttribute("role", "list");
        if (!nodes.length) members.append(el("p", "folder-no-members", "点击目录旁 ＋ 选择节点"));
        for (const node of nodes) {
          const member = el("div", "folder-member");
          member.setAttribute("role", "listitem");
          const name = el("button", "folder-member-name", node.name);
          name.type = "button";
          name.title = node.name;
          name.addEventListener("pointerdown", event => event.stopPropagation());
          name.addEventListener("click", () => {
            NodeSearch.clear(false);
            this.selected = folder.id;
            selectedModel = null;
            this.refresh();
            [...$("nodes").querySelectorAll(".node-card")].find(card => card.dataset.nodeId === node.id)
              ?.scrollIntoView({block: "nearest"});
          });
          const remove = el("button", "folder-member-remove", "×");
          remove.type = "button";
          remove.setAttribute("aria-label", "将节点 " + node.name + " 移出目录 " + folder.name);
          remove.title = "从此目录移出，其他目录的归属保留";
          remove.addEventListener("pointerdown", event => event.stopPropagation());
          remove.addEventListener("click", async () => {
            try { await this.save({action: "remove", folder_id: folder.id, node_id: node.id}, "节点已移出此目录。"); }
            catch (error) { notify(error.message, true); }
          });
          member.append(this.nodeHandle(node, true), name, remove);
          members.append(member);
        }
        row.append(members);
      }
      list.append(row);
    }
    if (!this.folders().length) list.append(el("p", "folder-no-members", "创建目录后，可选择或拖入节点"));
    section.append(list);
    return section;
  },
  edit(folder = null) {
    if (!state || directorySaving || this.drag) return;
    this.editing = folder?.id || null;
    $("folder-dialog-title").textContent = folder ? "设置目录" : "新建目录";
    $("folder-name").value = folder?.name || "";
    $("folder-save").textContent = folder ? "保存名称" : "创建目录";
    $("folder-delete").hidden = !folder;
    $("folder-delete-help").hidden = !folder;
    $("folder-error").hidden = true;
    $("folder-dialog").showModal();
    $("folder-name").focus();
  },
  editNode(node) {
    if (directorySaving || this.drag) return;
    this.editingNode = node.id;
    $("node-folders-title").textContent = "节点目录";
    $("node-folders-description").textContent = node.name;
    const options = this.folders().map(folder => {
      const label = el("label", "node-folder-option");
      const checkbox = el("input");
      checkbox.type = "checkbox";
      checkbox.dataset.folder = folder.id;
      checkbox.checked = folder.node_ids.includes(node.id);
      label.append(checkbox, el("span", "", folder.name));
      return label;
    });
    $("node-folder-options").replaceChildren(...options);
    $("node-folders-empty").hidden = options.length > 0;
    $("node-folders-save").disabled = options.length === 0;
    $("node-folders-error").hidden = true;
    $("node-folders-dialog").showModal();
  },
  addNodes(folder) {
    if (directorySaving || this.drag) return;
    this.addingFolder = folder.id;
    $("folder-nodes-title").textContent = "添加节点 · " + folder.name;
    $("folder-nodes-search").value = "";
    const members = new Set(folder.node_ids);
    const options = [...state.nodes].sort(compareNodeAddresses).map(node => {
      const label = el("label", "node-folder-option folder-node-option");
      label.dataset.search = NodeSearch.text(node);
      const checkbox = el("input");
      checkbox.type = "checkbox";
      checkbox.dataset.node = node.id;
      checkbox.checked = members.has(node.id);
      checkbox.disabled = checkbox.checked;
      checkbox.addEventListener("change", () => this.updateNodeSelection());
      const identity = el("span", "folder-node-identity");
      identity.append(el("span", "", node.name), el("small", "", node.server_model || "型号未识别"));
      label.append(checkbox, identity);
      if (checkbox.disabled) label.append(el("small", "folder-node-added", "已加入"));
      return label;
    });
    $("folder-node-options").replaceChildren(...options);
    $("folder-nodes-empty").hidden = options.length > 0;
    $("folder-nodes-error").hidden = true;
    this.filterNodeOptions();
    $("folder-nodes-dialog").showModal();
  },
  filterNodeOptions() {
    const query = $("folder-nodes-search").value;
    const options = [...$("folder-node-options").querySelectorAll(".folder-node-option")];
    for (const option of options) option.hidden = !NodeSearch.matchesText(option.dataset.search, query);
    const count = options.filter(option => !option.hidden).length;
    $("folder-nodes-results").textContent = "显示 " + count + " / " + options.length + " 个节点";
    $("folder-nodes-no-results").hidden = count > 0 || options.length === 0;
    this.updateNodeSelection();
  },
  updateNodeSelection() {
    const available = [...$("folder-node-options").querySelectorAll("input:not(:disabled)")];
    const count = available.filter(input => input.checked).length;
    $("folder-nodes-count").textContent = "已选择 " + count + " 个节点";
    $("folder-nodes-save").disabled = directorySaving || count === 0;
    $("folder-nodes-select-all").disabled = directorySaving || !available.some(input => !input.parentElement.hidden);
    $("folder-nodes-clear").disabled = directorySaving || count === 0;
  },
  nodeHandle(node, small = false) {
    const handle = el("button", small ? "node-folder-handle compact" : "node-folder-handle", "⋮⋮");
    handle.type = "button";
    handle.title = "拖动加入目录；点击选择多个目录";
    handle.setAttribute("aria-label", "节点 " + node.name + " 的目录归属：拖动加入，点击选择");
    handle.addEventListener("click", () => {
      if (performance.now() >= directoryClickSuppressedUntil) this.editNode(node);
    });
    handle.addEventListener("pointerdown", event => {
      if (event.button !== 0 || directorySaving || directoryDrag || directoryResize || this.drag) return;
      event.stopPropagation();
      this.drag = {handle, node, pointerId: event.pointerId, startX: event.clientX, startY: event.clientY,
        clientX: event.clientX, clientY: event.clientY, moved: false, target: null, frame: null, ghost: null};
      handle.setPointerCapture(event.pointerId);
    });
    handle.addEventListener("pointermove", event => this.moveNode(event));
    for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) {
      handle.addEventListener(type, event => this.finishNode(event));
    }
    return handle;
  },
  moveNode(event) {
    const drag = this.drag;
    if (!drag || event.pointerId !== drag.pointerId) return;
    drag.clientX = event.clientX;
    drag.clientY = event.clientY;
    if (!drag.moved && Math.hypot(drag.clientX - drag.startX, drag.clientY - drag.startY) < 5) return;
    event.preventDefault();
    if (!drag.moved) {
      drag.moved = true;
      drag.ghost = el("div", "node-drag-ghost", drag.node.name + " · 加入目录");
      document.body.append(drag.ghost);
      document.body.classList.add("node-assigning");
      this.scrollNode();
    }
    drag.ghost.style.left = drag.clientX + 12 + "px";
    drag.ghost.style.top = drag.clientY + 12 + "px";
    this.highlightTarget();
    const bounds = $("model-sidebar").querySelector(".sidebar-panel").getBoundingClientRect();
    const inside = drag.clientX >= bounds.left && drag.clientX <= bounds.right
      && drag.clientY >= bounds.top && drag.clientY <= bounds.bottom;
    if (directoryCollapsed && directoryHoverMedia.matches) {
      if (inside && !directoryHovered && !directoryHoverTimer) {
        directoryHoverTimer = setTimeout(() => {
          directoryHoverTimer = null;
          if (this.drag === drag) { directoryHovered = true; applyDirectoryState(); this.highlightTarget(); }
        }, 500);
      } else if (!inside) { cancelDirectoryHover(); applyDirectoryState(); }
    }
  },
  highlightTarget() {
    const drag = this.drag;
    if (!drag) return;
    const target = document.elementFromPoint(drag.clientX, drag.clientY)?.closest(".directory-folder");
    const folder = target && this.folders().find(item => item.id === target.dataset.folder);
    drag.target = folder?.id || null;
    $("model-directory").querySelectorAll(".directory-folder").forEach(row => {
      row.classList.toggle("folder-drop-target", row.dataset.folder === drag.target);
    });
    if (drag.ghost) drag.ghost.textContent = drag.node.name + (folder ? " → " + folder.name : " · 加入目录");
  },
  scrollNode() {
    const drag = this.drag;
    if (!drag?.moved) return;
    const content = $("model-directory-content");
    const bounds = content.getBoundingClientRect();
    if (drag.clientX >= bounds.left && drag.clientX <= bounds.right
        && drag.clientY >= bounds.top && drag.clientY <= bounds.bottom) {
      const speed = drag.clientY < bounds.top + 25 ? -8 : drag.clientY > bounds.bottom - 25 ? 8 : 0;
      if (speed) { content.scrollTop += speed; this.highlightTarget(); }
    }
    drag.frame = requestAnimationFrame(() => this.scrollNode());
  },
  finishNode(event) {
    const drag = this.drag;
    if (!drag || (event && event.pointerId !== drag.pointerId)) return;
    this.drag = null;
    cancelAnimationFrame(drag.frame);
    drag.ghost?.remove();
    if (drag.handle.hasPointerCapture(drag.pointerId)) drag.handle.releasePointerCapture(drag.pointerId);
    document.body.classList.remove("node-assigning");
    $("model-directory").querySelectorAll(".folder-drop-target").forEach(row => row.classList.remove("folder-drop-target"));
    if (drag.moved) directoryClickSuppressedUntil = performance.now() + 350;
    cancelDirectoryHover();
    if (event?.type === "pointerup" && drag.moved && drag.target) {
      void this.save({action: "assign", folder_id: drag.target, node_id: drag.node.id}, "节点已加入目录。")
        .catch(error => notify(error.message, true));
    } else { if (drag.moved) this.refresh(); applyDirectoryState(); }
  },
  init() {
    $("folder-show-all").addEventListener("click", () => {
      NodeSearch.clear(false);
      this.selected = null; selectedModel = null; this.refresh();
    });
    for (const id of ["folder-close", "folder-cancel"]) {
      $(id).addEventListener("click", () => $("folder-dialog").close());
    }
    for (const id of ["node-folders-close", "node-folders-cancel"]) {
      $(id).addEventListener("click", () => $("node-folders-dialog").close());
    }
    for (const id of ["folder-nodes-close", "folder-nodes-cancel"]) {
      $(id).addEventListener("click", () => $("folder-nodes-dialog").close());
    }
    $("folder-nodes-search").addEventListener("input", () => this.filterNodeOptions());
    for (const id of ["folder-nodes-select-all", "folder-nodes-clear"]) {
      $(id).addEventListener("click", () => {
        $("folder-node-options").querySelectorAll("input:not(:disabled)").forEach(input => {
          if (id === "folder-nodes-clear") input.checked = false;
          else if (!input.parentElement.hidden) input.checked = true;
        });
        this.updateNodeSelection();
      });
    }
    $("folder-nodes-form").addEventListener("submit", async event => {
      event.preventDefault();
      if (directorySaving) return;
      const node_ids = [...$("folder-node-options").querySelectorAll("input:checked:not(:disabled)")]
        .map(input => input.dataset.node);
      if (!node_ids.length) return;
      $("folder-nodes-save").disabled = true;
      $("folder-nodes-select-all").disabled = true;
      $("folder-nodes-clear").disabled = true;
      $("folder-nodes-error").hidden = true;
      try {
        await this.save({action: "assign_many", folder_id: this.addingFolder, node_ids}, "所选节点已加入目录。");
        this.expanded.add(this.addingFolder);
        this.refresh();
        $("folder-nodes-dialog").close();
      } catch (error) {
        $("folder-nodes-error").textContent = error.message;
        $("folder-nodes-error").hidden = false;
      } finally { this.updateNodeSelection(); }
    });
    $("folder-form").addEventListener("submit", async event => {
      event.preventDefault();
      if (!$("folder-name").reportValidity() || directorySaving) return;
      $("folder-save").disabled = true;
      $("folder-delete").disabled = true;
      $("folder-error").hidden = true;
      try {
        const saved = await this.save({action: this.editing ? "rename" : "create",
          folder_id: this.editing, name: $("folder-name").value.trim()},
          this.editing ? "目录名称已保存。" : "目录已创建，点击旁边 ＋ 添加节点。");
        if (!this.editing) { this.expanded.add(saved.folder_id); this.refresh(); }
        $("folder-dialog").close();
      } catch (error) {
        $("folder-error").textContent = error.message; $("folder-error").hidden = false;
      } finally { $("folder-save").disabled = false; $("folder-delete").disabled = false; }
    });
    $("folder-delete").addEventListener("click", async () => {
      if (!this.editing || directorySaving) return;
      $("folder-delete").disabled = true;
      try {
        await this.save({action: "delete", folder_id: this.editing}, "目录已删除，全部节点已保留。");
        this.expanded.delete(this.editing);
        $("folder-dialog").close();
      } catch (error) {
        $("folder-error").textContent = error.message; $("folder-error").hidden = false;
      } finally { $("folder-delete").disabled = false; }
    });
    $("node-folders-form").addEventListener("submit", async event => {
      event.preventDefault();
      if (directorySaving) return;
      $("node-folders-save").disabled = true;
      $("node-folders-error").hidden = true;
      try {
        const folder_ids = [...$("node-folder-options").querySelectorAll("input:checked")]
          .map(input => input.dataset.folder);
        await this.save({action: "set_membership", node_id: this.editingNode, folder_ids}, "节点目录归属已保存。");
        $("node-folders-dialog").close();
      } catch (error) {
        $("node-folders-error").textContent = error.message; $("node-folders-error").hidden = false;
      } finally { $("node-folders-save").disabled = this.folders().length === 0; }
    });
    window.addEventListener("blur", () => this.finishNode());
    document.addEventListener("keydown", event => {
      if (event.key === "Escape" && this.drag) { event.preventDefault(); this.finishNode(); }
    });
  }
};
