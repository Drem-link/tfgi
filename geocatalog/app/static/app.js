const canvas = document.querySelector("#globe");
const context = canvas.getContext("2d");
const results = document.querySelector("#results");
const message = document.querySelector("#message");
const count = document.querySelector("#result-count");
const featureCount = document.querySelector("#feature-count");
const tooltip = document.querySelector("#globe-tooltip");
let features = [];
let rotation = { longitude: 38, latitude: 54 };
let zoom = 1;
let targetZoom = 1;
let dragging = false;
let lastPointer = null;
let dragOrigin = null;
let didDrag = false;
let lastFrame = performance.now();
let hoveredFeature = null;
let globeGeometry = null;
let countries = [];
let selectedFeature = null;
const selectionPanel = document.querySelector("#globe-selection");
const selectionTitle = document.querySelector("#selection-title");
const selectionLocation = document.querySelector("#selection-location");
const selectionDocuments = document.querySelector("#selection-documents");

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
  card.tabIndex = 0;
  card.setAttribute("role", "button");
  card.setAttribute("aria-label", `${props.name}, показать на глобусе`);
  card.append(text("h3", props.name));
  card.append(text("p", `${kindLabel(props.kind)} · документов: ${props.documents.length}`));
  if (props.metadata && Object.keys(props.metadata).length) {
    card.append(text("p", JSON.stringify(props.metadata)));
  }
  showDocuments(card, props.documents);
  card.addEventListener("click", () => focusFeature(feature));
  card.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      focusFeature(feature);
    }
  });
  target.append(card);
}

function kindLabel(kind) {
  return ({ well: "Скважина", area: "Площадь", site: "Участок", other: "Объект" })[kind] || kind;
}

function featureCoordinates(feature) {
  const geometry = feature.geometry;
  if (geometry.type === "Point") return [geometry.coordinates];
  if (geometry.type === "MultiPoint") return geometry.coordinates;
  return [];
}

function renderSelection(feature) {
  selectedFeature = feature;
  selectionTitle.textContent = feature.properties.name;
  const coordinates = featureCoordinates(feature)[0] || firstPolygonCoordinate(feature.geometry);
  selectionLocation.textContent = coordinates
    ? `${coordinates[1].toFixed(4)}° с. ш., ${coordinates[0].toFixed(4)}° в. д.`
    : "";
  selectionDocuments.replaceChildren();
  showDocuments(selectionDocuments, feature.properties.documents);
  selectionPanel.hidden = false;
}

function resizeCanvas() {
  const bounds = canvas.getBoundingClientRect();
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.round(bounds.width * dpr);
  canvas.height = Math.round(bounds.height * dpr);
  context.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function project(coordinates, geometry) {
  const [longitude, latitude] = coordinates;
  const lat = latitude * Math.PI / 180;
  const dLon = (longitude - rotation.longitude) * Math.PI / 180;
  const centerLat = rotation.latitude * Math.PI / 180;
  const x = Math.cos(lat) * Math.sin(dLon);
  const y = Math.sin(lat) * Math.cos(centerLat) - Math.cos(lat) * Math.cos(dLon) * Math.sin(centerLat);
  const z = Math.sin(lat) * Math.sin(centerLat) + Math.cos(lat) * Math.cos(dLon) * Math.cos(centerLat);
  return { x: geometry.x + geometry.radius * x, y: geometry.y - geometry.radius * y, z };
}

function drawGrid(geometry) {
  context.save();
  context.beginPath();
  context.arc(geometry.x, geometry.y, geometry.radius, 0, Math.PI * 2);
  context.clip();

  const gradient = context.createRadialGradient(
    geometry.x - geometry.radius * 0.32, geometry.y - geometry.radius * 0.42, geometry.radius * 0.05,
    geometry.x, geometry.y, geometry.radius * 1.2,
  );
  gradient.addColorStop(0, "#182445");
  gradient.addColorStop(0.67, "#0c142c");
  gradient.addColorStop(1, "#070b1c");
  context.fillStyle = gradient;
  context.fillRect(geometry.x - geometry.radius, geometry.y - geometry.radius, geometry.radius * 2, geometry.radius * 2);

  context.lineWidth = 0.8;
  context.strokeStyle = "rgba(157, 160, 211, .22)";
  for (let latitude = -80; latitude <= 80; latitude += 20) {
    traceGridLine(geometry, (step) => [step, latitude], -180, 180, 2);
  }
  for (let longitude = -180; longitude < 180; longitude += 20) {
    traceGridLine(geometry, (step) => [longitude, step], -90, 90, 2);
  }

  context.beginPath();
  context.ellipse(geometry.x, geometry.y - geometry.radius * 0.78, geometry.radius * 0.17, geometry.radius * 0.035, 0, 0, Math.PI * 2);
  context.strokeStyle = "rgba(157, 160, 211, .16)";
  context.stroke();
  context.restore();

  context.beginPath();
  context.arc(geometry.x, geometry.y, geometry.radius, 0, Math.PI * 2);
  context.lineWidth = 2;
  context.strokeStyle = "#9790e6";
  context.shadowColor = "rgba(117, 111, 215, .32)";
  context.shadowBlur = 14;
  context.stroke();
  context.shadowBlur = 0;
}

function countryPolygons(geometry) {
  if (geometry.type === "Polygon") return [geometry.coordinates];
  if (geometry.type === "MultiPolygon") return geometry.coordinates;
  return [];
}

function interpolateCoordinate(start, end, fraction) {
  const deltaLongitude = ((end[0] - start[0] + 540) % 360) - 180;
  let longitude = start[0] + deltaLongitude * fraction;
  if (longitude > 180) longitude -= 360;
  if (longitude < -180) longitude += 360;
  return [longitude, start[1] + (end[1] - start[1]) * fraction];
}

function horizonPoint(start, end, geometry) {
  let visibleFraction = 0;
  let hiddenFraction = 1;
  const startVisible = project(start, geometry).z > 0;
  for (let step = 0; step < 14; step += 1) {
    const middle = (visibleFraction + hiddenFraction) / 2;
    const point = project(interpolateCoordinate(start, end, middle), geometry);
    if ((point.z > 0) === startVisible) visibleFraction = middle;
    else hiddenFraction = middle;
  }
  return project(interpolateCoordinate(start, end, (visibleFraction + hiddenFraction) / 2), geometry);
}

function projectCountryRing(ring, geometry) {
  const chains = [];
  let chain = [];
  const append = (coordinates) => {
    const point = project(coordinates, geometry);
    if (point.z <= 0) return;
    chain.push([point.x, point.y]);
  };
  const finish = () => {
    if (chain.length > 1) chains.push(chain);
    chain = [];
  };

  for (let index = 0; index < ring.length - 1; index += 1) {
    const start = ring[index];
    const end = ring[index + 1];
    const deltaLongitude = ((end[0] - start[0] + 540) % 360) - 180;
    const steps = Math.max(1, Math.ceil(Math.max(Math.abs(deltaLongitude), Math.abs(end[1] - start[1])) / 3));
    let previous = start;
    let previousVisible = project(previous, geometry).z > 0;
    if (previousVisible && chain.length === 0) append(previous);

    for (let step = 1; step <= steps; step += 1) {
      const current = interpolateCoordinate(start, end, step / steps);
      const currentVisible = project(current, geometry).z > 0;
      if (previousVisible && currentVisible) {
        append(current);
      } else if (previousVisible && !currentVisible) {
        const edge = horizonPoint(previous, current, geometry);
        chain.push([edge.x, edge.y]);
        finish();
      } else if (!previousVisible && currentVisible) {
        const edge = horizonPoint(previous, current, geometry);
        chain.push([edge.x, edge.y]);
        append(current);
      }
      previous = current;
      previousVisible = currentVisible;
    }
  }
  finish();
  return chains;
}

function drawCountryPolygon(polygon, geometry) {
  const visibleRings = polygon.map((ring) => projectCountryRing(ring, geometry));
  context.beginPath();
  for (const chains of visibleRings) {
    for (const chain of chains) {
      context.moveTo(chain[0][0], chain[0][1]);
      for (let index = 1; index < chain.length; index += 1) {
        context.lineTo(chain[index][0], chain[index][1]);
      }
      context.closePath();
    }
  }
  context.fillStyle = "#263653";
  context.fill("evenodd");

  context.beginPath();
  for (const chains of visibleRings) {
    for (const chain of chains) {
      context.moveTo(chain[0][0], chain[0][1]);
      for (let index = 1; index < chain.length; index += 1) {
        context.lineTo(chain[index][0], chain[index][1]);
      }
    }
  }
  context.strokeStyle = "rgba(177, 194, 219, .48)";
  context.lineWidth = 0.65;
  context.stroke();
}

function drawCountries(geometry) {
  context.save();
  context.beginPath();
  context.arc(geometry.x, geometry.y, geometry.radius, 0, Math.PI * 2);
  context.clip();
  for (const country of countries) {
    for (const polygon of countryPolygons(country.geometry)) {
      drawCountryPolygon(polygon, geometry);
    }
  }
  context.restore();
}

function traceGridLine(geometry, coordinateAt, start, end, step) {
  context.beginPath();
  let drawing = false;
  for (let value = start; value <= end; value += step) {
    const point = project(coordinateAt(value), geometry);
    if (point.z > 0) {
      if (!drawing) context.moveTo(point.x, point.y);
      else context.lineTo(point.x, point.y);
      drawing = true;
    } else {
      drawing = false;
    }
  }
  context.stroke();
}

function drawRing(ring, geometry, fill, stroke) {
  let started = false;
  context.beginPath();
  for (const coordinates of ring) {
    const point = project(coordinates, geometry);
    if (point.z <= 0) {
      started = false;
      continue;
    }
    if (!started) context.moveTo(point.x, point.y);
    else context.lineTo(point.x, point.y);
    started = true;
  }
  if (fill) {
    context.closePath();
    context.fillStyle = fill;
    context.fill();
  }
  context.strokeStyle = stroke;
  context.lineWidth = 1.5;
  context.stroke();
}

function drawFeatureGeometry(feature, geometry) {
  const geo = feature.geometry;
  const area = feature.properties.kind === "area";
  const stroke = area ? "#d79a77" : "#f4c8aa";
  if (geo.type === "Polygon") {
    for (const ring of geo.coordinates) drawRing(ring, geometry, area ? "rgba(215, 154, 119, .12)" : null, stroke);
  } else if (geo.type === "MultiPolygon") {
    for (const polygon of geo.coordinates) {
      for (const ring of polygon) drawRing(ring, geometry, area ? "rgba(215, 154, 119, .12)" : null, stroke);
    }
  } else {
    for (const coordinates of featureCoordinates(feature)) {
      const point = project(coordinates, geometry);
      if (point.z <= 0) continue;
      const radius = feature === hoveredFeature ? 7 : 4.5;
      context.beginPath();
      context.arc(point.x, point.y, radius, 0, Math.PI * 2);
      context.fillStyle = "#f2c5a5";
      context.shadowColor = "rgba(242, 197, 165, .8)";
      context.shadowBlur = feature === hoveredFeature ? 18 : 8;
      context.fill();
      context.shadowBlur = 0;
      context.strokeStyle = "#f6d6bd";
      context.lineWidth = 1;
      context.stroke();
    }
  }
}

function drawGlobe(now) {
  const dt = Math.min((now - lastFrame) / 1000, 0.05);
  lastFrame = now;
  if (!dragging && !selectedFeature && document.visibilityState === "visible") {
    rotation.longitude = (rotation.longitude + dt * 2.2) % 360;
  }
  zoom += (targetZoom - zoom) * Math.min(1, dt * 5);

  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  context.clearRect(0, 0, width, height);
  const radius = Math.max(80, Math.min(width * 0.43, height * 0.43) * zoom);
  globeGeometry = { x: width / 2, y: height / 2, radius };
  drawGrid(globeGeometry);
  drawCountries(globeGeometry);
  for (const feature of features) drawFeatureGeometry(feature, globeGeometry);
  window.requestAnimationFrame(drawGlobe);
}

function focusFeature(feature) {
  const coords = featureCoordinates(feature)[0] || firstPolygonCoordinate(feature.geometry);
  if (!coords) return;
  rotation.longitude = coords[0];
  rotation.latitude = coords[1];
  targetZoom = Math.max(targetZoom, 1.38);
  hoveredFeature = feature;
  renderSelection(feature);
  const card = [...results.children].find((item) => item.querySelector("h3")?.textContent === feature.properties.name);
  card?.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function firstPolygonCoordinate(geometry) {
  if (geometry.type === "Polygon") return geometry.coordinates[0]?.[0];
  if (geometry.type === "MultiPolygon") return geometry.coordinates[0]?.[0]?.[0];
  return null;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

function coordinatesOf(feature) {
  const coords = featureCoordinates(feature);
  if (coords.length) return coords;
  const coord = firstPolygonCoordinate(feature.geometry);
  return coord ? [coord] : [];
}

function featureAt(x, y) {
  if (!globeGeometry) return null;
  for (const feature of features) {
    for (const coordinates of coordinatesOf(feature)) {
      const point = project(coordinates, globeGeometry);
      if (point.z > 0 && Math.hypot(point.x - x, point.y - y) < 14) return feature;
    }
  }
  return null;
}

function updateTooltip(feature) {
  if (!feature) {
    tooltip.hidden = true;
    return;
  }
  tooltip.textContent = `${feature.properties.name} · ${feature.properties.documents.length} док.`;
  tooltip.hidden = false;
}

canvas.addEventListener("pointerdown", (event) => {
  dragging = true;
  lastPointer = { x: event.clientX, y: event.clientY };
  dragOrigin = { x: event.clientX, y: event.clientY };
  didDrag = false;
  canvas.setPointerCapture(event.pointerId);
  canvas.classList.add("dragging");
});
canvas.addEventListener("pointermove", (event) => {
  if (dragging && lastPointer) {
    if (Math.hypot(event.clientX - dragOrigin.x, event.clientY - dragOrigin.y) > 4) didDrag = true;
    const scale = 90 / Math.max(globeGeometry?.radius || 1, 1);
    rotation.longitude -= (event.clientX - lastPointer.x) * scale;
    rotation.latitude = Math.max(-82, Math.min(82, rotation.latitude + (event.clientY - lastPointer.y) * scale));
    lastPointer = { x: event.clientX, y: event.clientY };
    tooltip.hidden = true;
    return;
  }
  const bounds = canvas.getBoundingClientRect();
  hoveredFeature = featureAt(event.clientX - bounds.left, event.clientY - bounds.top);
  canvas.classList.toggle("has-target", Boolean(hoveredFeature));
  updateTooltip(hoveredFeature);
});
canvas.addEventListener("pointerup", (event) => {
  if (!dragging) return;
  if (dragOrigin && Math.hypot(event.clientX - dragOrigin.x, event.clientY - dragOrigin.y) > 4) didDrag = true;
  dragging = false;
  lastPointer = null;
  dragOrigin = null;
  canvas.classList.remove("dragging");
  if (!didDrag) {
    const bounds = canvas.getBoundingClientRect();
    const feature = featureAt(event.clientX - bounds.left, event.clientY - bounds.top);
    if (feature) focusFeature(feature);
  }
});
canvas.addEventListener("pointercancel", () => {
  dragging = false;
  lastPointer = null;
  dragOrigin = null;
  canvas.classList.remove("dragging");
});
canvas.addEventListener("wheel", (event) => {
  event.preventDefault();
  targetZoom = Math.max(0.72, Math.min(1.65, targetZoom - Math.sign(event.deltaY) * 0.06));
}, { passive: false });
canvas.addEventListener("pointerleave", () => {
  if (!dragging) {
    hoveredFeature = null;
    tooltip.hidden = true;
    canvas.classList.remove("has-target");
  }
});
window.addEventListener("resize", resizeCanvas);
window.addEventListener("keydown", (event) => {
  if (event.key === "/" && !["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)) {
    event.preventDefault();
    document.querySelector("#search-form input[name=q]").focus();
  }
});

async function search() {
  message.textContent = "";
  const params = new URLSearchParams(new FormData(document.querySelector("#search-form")));
  for (const [key, value] of [...params.entries()]) if (!value) params.delete(key);
  try {
    const response = await fetch(`/api/features?${params}`, { credentials: "same-origin" });
    if (response.status === 401) {
      location.assign("/login");
      return;
    }
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "Не удалось выполнить поиск");
    features = body.features;
    results.replaceChildren();
    for (const feature of features) renderFeature(feature);
    count.textContent = `${features.length} объектов`;
    featureCount.textContent = `${features.length} ОБЪЕКТОВ`;
    if (!features.length) message.textContent = "Ничего не найдено или измените фильтры.";
  } catch (error) {
    message.textContent = error.message;
    count.textContent = "Ошибка";
  }
}

document.querySelector("#search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  search();
});
document.querySelector("#search-form").addEventListener("reset", () => setTimeout(search, 0));
document.querySelector("#logout").addEventListener("click", () => document.querySelector("#logout-form").requestSubmit());
document.querySelector("#close-selection").addEventListener("click", () => {
  selectionPanel.hidden = true;
  selectedFeature = null;
  targetZoom = 1;
});
fetch("/static/ne_110m_admin_0_countries.geojson")
  .then((response) => {
    if (!response.ok) throw new Error(`Не удалось загрузить офлайн-карту стран (${response.status})`);
    return response.json();
  })
  .then((data) => {
    if (!Array.isArray(data.features)) throw new Error("Файл офлайн-карты стран имеет неверный формат");
    countries = data.features;
  })
  .catch((error) => {
    message.textContent = error.message;
  });
fetch("/api/session", { credentials: "same-origin" })
  .then((response) => {
    if (response.status === 401) {
      location.assign("/login");
      return null;
    }
    if (!response.ok) throw new Error("Не удалось получить профиль пользователя");
    return response.json();
  })
  .then((session) => {
    if (session?.role === "admin") document.querySelector("#user-admin-link").hidden = false;
  })
  .catch((error) => {
    message.textContent = error.message;
  });
resizeCanvas();
window.requestAnimationFrame(drawGlobe);
search();
