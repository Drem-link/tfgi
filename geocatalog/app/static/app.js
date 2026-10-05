const map = L.map("map").setView([61, 96], 3);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 18,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
}).addTo(map);

const results = document.querySelector("#results");
const message = document.querySelector("#message");
const count = document.querySelector("#result-count");
let layer;

function text(tag, value, className) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = value ?? "";
  return element;
}

function showDocuments(container, documents) {
  if (!documents.length) {
    container.append(text("p", "Связанных документов пока нет."));
    return;
  }
  for (const doc of documents) {
    const card = text("div", null, "doc");
    card.append(text("strong", doc.title));
    const metadata = [doc.inventory_number, doc.region, doc.year, doc.topic].filter(Boolean).join(" · ");
    if (metadata) card.append(text("p", metadata));
    if (doc.description) card.append(text("p", doc.description));
    if (doc.archive_reference) card.append(text("p", `Шифр/место хранения: ${doc.archive_reference}`));
    container.append(card);
  }
}

function renderFeature(feature, target = results) {
  const props = feature.properties;
  const card = text("article", null, "feature-card");
  card.append(text("h3", props.name));
  card.append(text("p", `Тип: ${props.kind} · документов: ${props.documents.length}`));
  if (props.metadata && Object.keys(props.metadata).length) {
    card.append(text("p", JSON.stringify(props.metadata)));
  }
  showDocuments(card, props.documents);
  target.append(card);
  return card;
}

function styleFor(feature) {
  return { color: feature.properties.kind === "area" ? "#a35d2b" : "#176c58", weight: 2, fillOpacity: 0.18 };
}

function popupHtml(feature) {
  const props = feature.properties;
  const docs = props.documents.length
    ? props.documents.map((doc) => {
      const facts = [doc.inventory_number, doc.region, doc.year, doc.topic]
        .filter(Boolean)
        .map(escapeHtml)
        .join(" · ");
      const description = doc.description ? `<p>${escapeHtml(doc.description)}</p>` : "";
      return `<section><strong>${escapeHtml(doc.title)}</strong>${facts ? `<p>${facts}</p>` : ""}${description}</section>`;
    }).join("")
    : "<p>Связанных документов нет.</p>";
  const metadata = Object.keys(props.metadata || {}).length
    ? `<p>${escapeHtml(JSON.stringify(props.metadata))}</p>`
    : "";
  return `<div class="map-popup"><strong>${escapeHtml(props.name)}</strong><p>Тип: ${escapeHtml(props.kind)}</p>${metadata}${docs}</div>`;
}

async function search() {
  message.textContent = "";
  const form = document.querySelector("#search-form");
  const params = new URLSearchParams(new FormData(form));
  for (const [key, value] of [...params.entries()]) if (!value) params.delete(key);
  const bounds = map.getBounds();
  if (map.getZoom() > 4) {
    params.set("bbox", [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()].join(","));
  }
  try {
    const response = await fetch(`/api/features?${params}`);
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "Не удалось выполнить поиск");
    if (layer) map.removeLayer(layer);
    results.replaceChildren();
    layer = L.geoJSON(body, {
      style: styleFor,
      pointToLayer: (feature, latlng) => L.circleMarker(latlng, {
        radius: 7, color: "#176c58", fillColor: "#d49838", fillOpacity: 0.9, weight: 2,
      }),
      onEachFeature: (feature, leafletLayer) => leafletLayer.bindPopup(popupHtml(feature)),
    }).addTo(map);
    for (const feature of body.features) renderFeature(feature);
    count.textContent = `${body.features.length} объектов`;
    if (!body.features.length) message.textContent = "Ничего не найдено. Каталог пока пуст или измените фильтры.";
  } catch (error) {
    message.textContent = error.message;
    count.textContent = "Ошибка";
  }
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

document.querySelector("#search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  search();
});
document.querySelector("#search-form").addEventListener("reset", () => setTimeout(search, 0));
map.on("moveend", search);
search();
