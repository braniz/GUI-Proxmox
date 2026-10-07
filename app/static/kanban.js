const csrfToken = document.querySelector('meta[name="csrf-token"]').content;
const errorBox = document.getElementById("todo-error");
const dialog = document.getElementById("todo-dialog");
const form = document.getElementById("todo-form");
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

  const title = document.createElement("strong");
  title.textContent = todo.title;
  card.append(title);
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
  remove.addEventListener("click", async () => {
    if (!window.confirm(`ToDo „${todo.title}“ löschen?`)) return;
    try {
      await requestTodos(`/api/kanban/todos/${encodeURIComponent(todo.id)}`, "DELETE");
      await loadTodos();
    } catch (error) {
      showError(error.message);
    }
  });
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

async function loadTodos() {
  try {
    renderTodos(await requestTodos("/api/kanban/todos"));
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
    vmid: form.elements.vmid.value || null
  };
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
    try {
      await requestTodos(`/api/kanban/todos/${encodeURIComponent(id)}`, "PUT", {
        status: column.dataset.status
      });
      await loadTodos();
    } catch (error) {
      showError(error.message);
    }
  });
}

loadTodos();
