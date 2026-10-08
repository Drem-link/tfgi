const form = document.querySelector("#catalog-form");
const message = document.querySelector("#catalog-message");
const uploadedFiles = document.querySelector("#uploaded-files");
const featureMode = document.querySelector("#feature-mode");
const geometryKind = document.querySelector("#geometry-kind");
const MAX_FILE_SIZE = 50 * 1024 * 1024;
let importBytes = null;
let importFormat = null;

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

async function loadDocuments() {
  const data = await request("/api/admin/documents");
  const selector = document.querySelector("#related-document");
  const editSelector = document.querySelector("#edit-document");
  const selected = selector.value;
  const selectedForEdit = editSelector.value;
  selector.replaceChildren(node("option", "Не связывать"));
  selector.firstElementChild.value = "";
  editSelector.replaceChildren(node("option", "Выберите документ…"));
  editSelector.firstElementChild.value = "";
  for (const document of data.documents) {
    const label = `${document.title}${document.inventory_number ? ` · ${document.inventory_number}` : ""}`;
    const relationOption = node("option", label);
    relationOption.value = document.id;
    selector.append(relationOption);
    const editOption = node("option", label);
    editOption.value = document.id;
    editSelector.append(editOption);
  }
  selector.value = selected;
  editSelector.value = selectedForEdit;
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

async function loadQuality() {
  const report = document.querySelector("#quality-report");
  const data = await request("/api/admin/quality");
  report.replaceChildren();
  for (const [key, label] of [
    ["unmapped_features", "Объекты без положения на глобусе"],
    ["unlinked_documents", "Документы без геологического объекта"],
    ["incomplete_documents", "Карточки с неполными основными данными"],
    ["duplicate_inventory_numbers", "Повторяющиеся фондовые номера"],
    ["duplicate_tgf_numbers", "Повторяющиеся номера ТГФ"],
  ]) {
    const items = data[key];
    report.append(node("p", `${label}: ${data.counts[key]}`, "quality-count"));
    for (const item of items.slice(0, 8)) {
      report.append(node("p", `${item.name || item.title || item.inventory_number || item.tgf_number}${item.source_crs ? ` · ${item.source_crs}` : ""}`));
    }
  }
}

async function loadAudit() {
  const log = document.querySelector("#audit-log");
  const data = await request("/api/admin/audit?limit=100");
  log.replaceChildren();
  for (const entry of data.entries) {
    const details = typeof entry.details === "string" ? entry.details : JSON.stringify(entry.details);
    log.append(node("p", `${entry.created_at} · ${entry.actor || "удалённый пользователь"} · ${entry.action} ${entry.entity_type} #${entry.entity_id ?? "пакет"} · ${details}`));
  }
  if (!data.entries.length) log.append(node("p", "Записей пока нет."));
}

async function previewImport() {
  const file = document.querySelector("#csv-import").files[0];
  const preview = document.querySelector("#import-preview");
  preview.replaceChildren();
  if (!file) {
    preview.append(node("p", "Сначала выберите CSV-файл."));
    return;
  }
  if (file.size > 5 * 1024 * 1024) {
    preview.append(node("p", "Размер файла не должен превышать 5 МБ."));
    return;
  }
  const fileFormat = file.name.toLowerCase().endsWith(".xlsx") ? "xlsx" : "csv";
  if (!file.name.toLowerCase().endsWith(".xlsx") && !file.name.toLowerCase().endsWith(".csv")) {
    preview.append(node("p", "Выберите файл .csv или .xlsx."));
    return;
  }
  importBytes = await file.arrayBuffer();
  importFormat = fileFormat;
  const response = await fetch(`/api/admin/import/${fileFormat}/preview`, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": fileFormat === "xlsx"
        ? "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        : "text/csv; charset=utf-8",
    },
    body: importBytes,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Не удалось проверить CSV.");
  preview.append(node("p", `Строк: ${data.count}. ${data.valid ? "Ошибок не найдено." : "Исправьте строки с ошибками."}`));
  for (const row of data.rows) {
    const errors = row.errors.length ? ` — ОШИБКИ: ${row.errors.join("; ")}` : " — готово";
    preview.append(node("p", `Строка ${row.row}: ${row.feature_name} · ${row.document_title} · ${row.coordinate_crs}${errors}`));
  }
  if (data.valid) {
    const commit = node("button", "Подтвердить импорт");
    commit.type = "button";
    commit.addEventListener("click", () => {
      commit.disabled = true;
      commitImport(preview).catch((error) => {
        preview.append(node("p", error.message));
      }).finally(() => { commit.disabled = false; });
    });
    preview.append(commit);
  }
}

async function commitImport(preview) {
  if (!importBytes || !importFormat || !window.confirm("Импортировать проверенные строки? Запись выполняется одной транзакцией.")) return;
  const response = await fetch(`/api/admin/import/${importFormat}/commit`, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": importFormat === "xlsx"
        ? "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        : "text/csv; charset=utf-8",
    },
    body: importBytes,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail?.message || data.detail || "Импорт не выполнен.");
  importBytes = null;
  importFormat = null;
  preview.replaceChildren(node(
    "p",
    `Импорт завершён: добавлено ${data.imported_documents}, пропущено повторов ${data.skipped_repeats}.`,
  ));
  await Promise.all([loadFeatures(), loadDocuments(), loadQuality(), loadAudit()]);
}

featureMode.addEventListener("change", () => {
  updateFormSections();
  loadFeatures().catch((error) => { message.textContent = error.message; });
});
document.querySelector("#csv-import").addEventListener("change", () => {
  importBytes = null;
  importFormat = null;
  document.querySelector("#import-preview").replaceChildren();
});
geometryKind.addEventListener("change", updateFormSections);
document.querySelector("#refresh-files").addEventListener("click", () => {
  loadFiles().catch((error) => { message.textContent = error.message; });
});
document.querySelector("#preview-import").addEventListener("click", () => {
  previewImport().catch((error) => {
    document.querySelector("#import-preview").replaceChildren(node("p", error.message));
  });
});
document.querySelector("#refresh-quality").addEventListener("click", () => {
  loadQuality().catch((error) => { message.textContent = error.message; });
});
document.querySelector("#refresh-audit").addEventListener("click", () => {
  loadAudit().catch((error) => { message.textContent = error.message; });
});
document.querySelector("#edit-document").addEventListener("change", async (event) => {
  const editor = document.querySelector("#document-json");
  if (!event.target.value) {
    editor.value = "";
    return;
  }
  try {
    const document = await request(`/api/admin/documents/${event.target.value}`);
    delete document.id;
    editor.value = JSON.stringify(document, null, 2);
  } catch (error) {
    message.textContent = error.message;
  }
});
document.querySelector("#save-document").addEventListener("click", async () => {
  const id = document.querySelector("#edit-document").value;
  if (!id) {
    message.textContent = "Выберите документ для редактирования.";
    return;
  }
  let fields;
  try {
    fields = JSON.parse(document.querySelector("#document-json").value);
  } catch {
    message.textContent = "Карточка должна быть корректным JSON.";
    return;
  }
  try {
    await request(`/api/admin/documents/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(fields),
    });
    message.textContent = "Карточка сохранена.";
    await Promise.all([loadDocuments(), loadQuality(), loadAudit()]);
  } catch (error) {
    message.textContent = error.message;
  }
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
    await Promise.all([loadFeatures(), loadDocuments(), loadFiles(), loadQuality(), loadAudit()]);
  } catch (error) {
    message.textContent = error.message;
  } finally {
    submit.disabled = false;
  }
});

updateFormSections();
Promise.all([loadFeatures(), loadDocuments(), loadFiles(), loadQuality(), loadAudit()]).catch((error) => {
  message.textContent = error.message;
});
