const list = document.querySelector("#users-list");
const message = document.querySelector("#users-message");

function element(tag, value, className) {
  const node = document.createElement(tag);
  if (value !== undefined) node.textContent = value;
  if (className) node.className = className;
  return node;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  if (response.status === 401) {
    location.assign("/login");
    throw new Error("Сессия завершена, войдите снова.");
  }
  const body = response.status === 204 ? null : await response.json();
  if (!response.ok) throw new Error(body?.detail || "Запрос не выполнен.");
  return body;
}

function field(label, control) {
  const wrapper = element("label");
  wrapper.append(element("span", label), control);
  return wrapper;
}

function renderUser(user) {
  const card = element("article", undefined, "user-row");
  card.append(element("h3", user.username));
  card.append(element("p", `Создан: ${new Date(user.created_at).toLocaleString("ru-RU")}`, "user-row-meta"));

  const role = document.createElement("select");
  for (const [value, label] of [["viewer", "Просмотр"], ["admin", "Администратор"]]) {
    const option = element("option", label);
    option.value = value;
    option.selected = user.role === value;
    role.append(option);
  }
  const active = document.createElement("select");
  for (const [value, label] of [["true", "Активна"], ["false", "Отключена"]]) {
    const option = element("option", label);
    option.value = value;
    option.selected = user.is_active === (value === "true");
    active.append(option);
  }
  const save = element("button", "Сохранить права");
  save.type = "button";
  save.addEventListener("click", async () => {
    try {
      await api(`/api/admin/users/${user.id}`, {
        method: "PATCH",
        body: JSON.stringify({ role: role.value, active: active.value === "true" }),
      });
      message.textContent = `Права пользователя ${user.username} обновлены.`;
      await loadUsers();
    } catch (error) {
      message.textContent = error.message;
    }
  });
  const controls = element("div", undefined, "user-row-controls");
  controls.append(field("Роль", role), field("Статус", active), save);
  card.append(controls);

  const password = document.createElement("input");
  password.type = "password";
  password.minLength = 8;
  password.maxLength = 1024;
  password.required = true;
  password.autocomplete = "new-password";
  password.placeholder = "Новый пароль (минимум 8 символов)";
  const reset = element("button", "Сбросить пароль");
  reset.type = "button";
  reset.className = "secondary";
  reset.addEventListener("click", async () => {
    if (!password.reportValidity()) return;
    try {
      await api(`/api/admin/users/${user.id}`, {
        method: "PATCH",
        body: JSON.stringify({ password: password.value }),
      });
      password.value = "";
      message.textContent = `Пароль пользователя ${user.username} обновлён; его активные сессии отозваны.`;
    } catch (error) {
      message.textContent = error.message;
    }
  });
  const passwordForm = element("div", undefined, "password-reset");
  passwordForm.append(password, reset);
  card.append(passwordForm);
  return card;
}

async function loadUsers() {
  try {
    const data = await api("/api/admin/users");
    list.replaceChildren(...data.users.map(renderUser));
  } catch (error) {
    message.textContent = error.message;
  }
}

document.querySelector("#create-user-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  try {
    const user = await api("/api/admin/users", {
      method: "POST",
      body: JSON.stringify({
        username: form.get("username"),
        password: form.get("password"),
        role: form.get("role"),
      }),
    });
    event.currentTarget.reset();
    message.textContent = `Создан пользователь ${user.username}. Передай ему пароль лично.`;
    await loadUsers();
  } catch (error) {
    message.textContent = error.message;
  }
});

document.querySelector("#refresh-users").addEventListener("click", loadUsers);
loadUsers();
