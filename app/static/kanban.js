const csrfToken = document.querySelector('meta[name="csrf-token"]').content;
const errorBox = document.getElementById("todo-error");
const dialog = document.getElementById("todo-dialog");
const form = document.getElementById("todo-form");
const actionDialog = document.getElementById("action-dialog");
const actionForm = document.getElementById("action-form");
const actionError = document.getElementById("action-error");
const toolbar = document.getElementById("bulk-toolbar");
const selected = new Set();
let pendingAction = null;
const actionTitles = {
  move: "Verschieben", edit: "Bearbeiten", done: "Erledigen", delete: "Löschen"
};
const actionLabels = { create: "Angelegt", ...actionTitles };
const todoStatuses = ["planned", "in_progress", "done"];
const vmNames = new Map(
  [...document.querySelectorAll("#todo-vmid option")].filter(option => option.value)
    .map(option => [option.value, option.textContent])
);

async function requestTodos(url, method = "GET", body) {
  const headers = { "X-CSRF-Token": csrfToken };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(url, {
    method,
    headers,
    ...(body === undefined ? {} : { body: JSON.stringify(body) })
  });
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    throw new Error(result.error || "ToDo konnte nicht geladen werden.");
  }
  return response.status === 204 ? null : response.json();
}

function showError(message = "") {
  errorBox.textContent = message;
  errorBox.hidden = !message;
}

function makeTodoCard(todo) {
  const card = document.createElement("article");
  card.className = "kanban-card todo-card";
  card.draggable = true;
  card.dataset.id = todo.id;
  card.addEventListener("dragstart", event => {
    event.dataTransfer.setData("text/plain", todo.id);
  });

  const pick = document.createElement("input");
  pick.type = "checkbox";
  pick.className = "select-todo";
  pick.setAttribute("aria-label", `ToDo „${todo.title}“ auswählen`);
  pick.checked = selected.has(todo.id);
  pick.addEventListener("change", () => {
    if (pick.checked) selected.add(todo.id); else selected.delete(todo.id);
    updateToolbar();
  });
  const title = document.createElement("strong");
  title.textContent = todo.title;
  card.append(pick, title);
  if (todo.vmid) {
    const vm = document.createElement("span");
    vm.className = "badge type";
    vm.textContent = vmNames.get(String(todo.vmid)) || `VMID ${todo.vmid}`;
    card.append(vm);
  }
  if (todo.description) {
    const description = document.createElement("p");
    description.textContent = todo.description;
    card.append(description);
  }
  for (const entry of Array.isArray(todo.comments) ? todo.comments : []) {
    const note = document.createElement("p");
    note.className = "todo-comment";
    note.textContent = `${entry.author || ""}: ${entry.text || ""}`;
    card.append(note);
  }
  const history = Array.isArray(todo.history) ? todo.history : [];
  if (history.length) {
    const details = document.createElement("details");
    details.className = "todo-history";
    const summary = document.createElement("summary");
    summary.textContent = `Historie (${history.length})`;
    details.append(summary);
    for (const item of history) {
      const line = document.createElement("p");
      line.className = "todo-comment";
      const when = item.time ? new Date(item.time).toLocaleString("de-DE") : "";
      line.textContent = `${when} · ${item.user || ""} · ${actionLabels[item.action] || item.action || ""}: ${item.comment || ""}`;
      details.append(line);
    }
    card.append(details);
  }
  if (todo.created_at) {
    const created = document.createElement("small");
    created.textContent = `Erstellt: ${new Date(todo.created_at).toLocaleString("de-DE")}`;
    card.append(created);
  }

  const actions = document.createElement("div");
  actions.className = "todo-actions";
  const edit = document.createElement("button");
  edit.type = "button";
  edit.textContent = "Bearbeiten";
  edit.addEventListener("click", () => openDialog(todo));
  const remove = document.createElement("button");
  remove.type = "button";
  remove.textContent = "Löschen";
  remove.addEventListener("click", () => askAction("delete", [todo.id], todo.title, true));
  actions.append(edit, remove);
  card.append(actions);
  return card;
}

function renderTodos(todos) {
  for (const column of document.querySelectorAll(".todo-column")) {
    const cards = column.querySelector(".todo-cards");
    cards.replaceChildren();
    const entries = todos.filter(todo => todo.status === column.dataset.status);
    if (!entries.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "Keine ToDos.";
      cards.append(empty);
    } else {
      for (const todo of entries) cards.append(makeTodoCard(todo));
    }
  }
}

function updateToolbar() {
  document.getElementById("bulk-count").textContent = selected.size;
  toolbar.hidden = selected.size === 0;
}

function askAction(action, ids, name = "", single = false) {
  pendingAction = { action, ids, single };
  actionForm.reset();
  actionError.hidden = true;
  document.getElementById("action-title").textContent =
    `${actionTitles[action]}${single ? (name ? `: ${name}` : "") : ` (${ids.length} ToDos)`}`;
  for (const part of actionForm.querySelectorAll("[data-for]")) {
    part.hidden = part.dataset.for !== action;
  }
  actionDialog.showModal();
}

function actionBody({ action, ids, single }) {
  const comment = document.getElementById("action-comment").value;
  const body = { comment };
  if (!single) Object.assign(body, { ids, action });
  if (action === "move") body.status = document.getElementById("action-status").value;
  if (action === "done") body.status = "done";
  if (action === "edit") {
    const title = document.getElementById("action-todo-title").value;
    const description = document.getElementById("action-todo-description").value;
    const vmid = document.getElementById("action-todo-vmid").value;
    if (title.trim()) body.title = title;
    if (description.trim()) body.description = description;
    if (vmid) body.vmid = vmid === "__none__" ? null : vmid;
  }
  return body;
}

document.getElementById("cancel-action").addEventListener("click", () => actionDialog.close());
actionForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (!document.getElementById("action-comment").value.trim()) {
    actionError.textContent = "Ein Kommentar ist erforderlich.";
    actionError.hidden = false;
    return;
  }
  const body = actionBody(pendingAction);
  try {
    if (pendingAction.single) {
      const url = `/api/kanban/todos/${encodeURIComponent(pendingAction.ids[0])}`;
      if (pendingAction.action === "delete") await requestTodos(url, "DELETE", { comment: body.comment });
      else await requestTodos(url, "PUT", body);
    } else {
      const result = await requestTodos("/api/kanban/todos/bulk", "POST", body);
      const failed = result.results.filter(r => !r.ok);
      if (failed.length) showError(`${failed.length} ToDo(s) nicht gefunden.`);
    }
    for (const id of pendingAction.ids) selected.delete(id);
    updateToolbar();
    actionDialog.close();
    await loadTodos();
  } catch (error) {
    actionError.textContent = error.message;
    actionError.hidden = false;
  }
});

for (const button of toolbar.querySelectorAll("[data-bulk]")) {
  button.addEventListener("click", () => askAction(button.dataset.bulk, [...selected]));
}
document.getElementById("bulk-clear").addEventListener("click", () => {
  selected.clear();
  updateToolbar();
  for (const box of document.querySelectorAll(".select-todo, .select-column")) box.checked = false;
});
for (const column of document.querySelectorAll(".todo-column")) {
  column.querySelector(".select-column").addEventListener("change", event => {
    for (const card of column.querySelectorAll(".todo-card")) {
      card.querySelector(".select-todo").checked = event.target.checked;
      if (event.target.checked) selected.add(card.dataset.id); else selected.delete(card.dataset.id);
    }
    updateToolbar();
  });
}

async function loadTodos() {
  try {
    const all = await requestTodos("/api/kanban/todos");
    const known = new Set(all.map(todo => todo.id));
    for (const id of [...selected]) if (!known.has(id)) selected.delete(id);
    renderTodos(all);
    updateToolbar();
    showError();
  } catch (error) {
    showError(error.message);
  }
}

function openDialog(todo = null) {
  form.dataset.todoId = todo ? todo.id : "";
  document.getElementById("todo-dialog-title").textContent =
    todo ? "ToDo bearbeiten" : "ToDo hinzufügen";
  form.elements.title.value = todo ? todo.title : "";
  form.elements.description.value = todo ? todo.description : "";
  form.elements.vmid.value = todo && todo.vmid ? String(todo.vmid) : "";
  form.elements.comment.value = "";
  form.elements.comment.required = Boolean(todo);
  form.elements.comment.parentElement.hidden = !todo;
  dialog.showModal();
}

document.getElementById("add-todo").addEventListener("click", () => openDialog());
document.getElementById("cancel-todo").addEventListener("click", () => dialog.close());
form.addEventListener("submit", async event => {
  event.preventDefault();
  const id = form.dataset.todoId;
  const payload = {
    title: form.elements.title.value,
    description: form.elements.description.value,
    vmid: form.elements.vmid.value || null,
    comment: form.elements.comment.value
  };
  if (!payload.comment.trim()) {
    showError("Ein Kommentar ist erforderlich.");
    return;
  }
  try {
    await requestTodos(
      id ? `/api/kanban/todos/${encodeURIComponent(id)}` : "/api/kanban/todos",
      id ? "PUT" : "POST",
      payload
    );
    dialog.close();
    await loadTodos();
  } catch (error) {
    showError(error.message);
  }
});

for (const column of document.querySelectorAll(".todo-column")) {
  column.addEventListener("dragover", event => event.preventDefault());
  column.addEventListener("drop", async event => {
    event.preventDefault();
    const id = event.dataTransfer.getData("text/plain");
    if (!id || !todoStatuses.includes(column.dataset.status)) return;
    askAction("move", [id], "", true);
    document.getElementById("action-status").value = column.dataset.status;
  });
}

loadTodos();
