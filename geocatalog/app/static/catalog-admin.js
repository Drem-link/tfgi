const form = document.querySelector("#catalog-form");
const message = document.querySelector("#catalog-message");
const uploadedFiles = document.querySelector("#uploaded-files");
const featureMode = document.querySelector("#feature-mode");
const geometryKind = document.querySelector("#geometry-kind");
const MAX_FILE_SIZE = 50 * 1024 * 1024;

function node(tag, value, className) {
  const item = document.createElement(tag);
  if (value !== undefined) item.textContent = value;
  if (className) item.className = className;
  return item;
}

function updateFormSections() {
  const existing = featureMode.value === "existing";
  document.querySelector("#existing-feature-fields").hidden = !existing;
  document.querySelector("#new-feature-fields").hidden = existing;
  const area = geometryKind.value === "area";
  document.querySelector("#point-fields").hidden = area;
  document.querySelector("#area-fields").hidden = !area;
  document.querySelector("#feature-name").required = !existing;
  document.querySelector('[name="longitude"]').required = !existing && !area;
  document.querySelector('[name="latitude"]').required = !existing && !area;
  document.querySelector("#polygon-vertices").required = !existing && area;
}

async function request(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", ...options });
  if (response.status === 401) {
    location.assign("/login");
    throw new Error("Сессия завершена; войдите снова.");
  }
  if (response.status === 204) return null;
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || "Запрос не выполнен.");
  return body;
}

async function loadFeatures() {
  const data = await request("/api/admin/features");
  const selector = document.querySelector("#feature-id");
  selector.replaceChildren();
  for (const feature of data.features) {
    const option = node("option", `${feature.name} · ${feature.kind}`);
    option.value = feature.id;
    selector.append(option);
  }
  selector.required = featureMode.value === "existing";
  document.querySelector("#existing-feature-fields").hidden =
    featureMode.value !== "existing" || data.features.length === 0;
  if (featureMode.value === "existing" && !data.features.length) {
    message.textContent = "В каталоге ещё нет объектов — сначала создайте новый.";
    featureMode.value = "new";
    updateFormSections();
  }
}

function readableSize(bytes) {
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} КБ`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
}

async function loadFiles() {
  const data = await request("/api/admin/files");
  uploadedFiles.replaceChildren();
  if (!data.files.length) {
    uploadedFiles.append(node("p", "Файлов пока нет.", "help"));
    return;
  }
  for (const file of data.files) {
    const card = node("article", undefined, "uploaded-file");
    const link = node("a", `${file.original_filename} · ${readableSize(file.size_bytes)}`);
    link.href = `/api/files/${encodeURIComponent(file.id)}`;
    link.setAttribute("download", "");
    card.append(link, node("p", `Документ: ${file.document_title}`), node("p", `Объект: ${file.feature_name}`));
    const remove = node("button", "Удалить файл");
    remove.type = "button";
    remove.className = "secondary";
    remove.addEventListener("click", async () => {
      if (!window.confirm(`Удалить вложение «${file.original_filename}»?`)) return;
      try {
        await request(`/api/admin/files/${file.id}`, { method: "DELETE" });
        message.textContent = `Файл ${file.original_filename} удалён.`;
        await loadFiles();
      } catch (error) {
        message.textContent = error.message;
      }
    });
    card.append(remove);
    uploadedFiles.append(card);
  }
}

featureMode.addEventListener("change", () => {
  updateFormSections();
  loadFeatures().catch((error) => { message.textContent = error.message; });
});
geometryKind.addEventListener("change", updateFormSections);
document.querySelector("#refresh-files").addEventListener("click", () => {
  loadFiles().catch((error) => { message.textContent = error.message; });
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  message.textContent = "";
  const fileInput = document.querySelector("#files");
  if (!fileInput.files.length || fileInput.files.length > 10) {
    message.textContent = "Выбери от 1 до 10 файлов.";
    return;
  }
  if (Array.from(fileInput.files).some((file) => file.size > MAX_FILE_SIZE)) {
    message.textContent = "Размер каждого файла не должен превышать 50 МБ.";
    return;
  }

  const submit = document.querySelector("#submit-upload");
  submit.disabled = true;
  message.textContent = "Проверка и сохранение файлов…";
  try {
    const result = await request("/api/admin/documents", {
      method: "POST",
      body: new FormData(form),
    });
    message.textContent =
      `Создан объект «${result.feature_name}», документ и вложений: ${result.files.length}.`;
    form.reset();
    updateFormSections();
    await Promise.all([loadFeatures(), loadFiles()]);
  } catch (error) {
    message.textContent = error.message;
  } finally {
    submit.disabled = false;
  }
});

updateFormSections();
Promise.all([loadFeatures(), loadFiles()]).catch((error) => {
  message.textContent = error.message;
});
