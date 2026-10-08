const canvas = document.querySelector("#globe");
const context = canvas.getContext("2d");
const results = document.querySelector("#results");
const message = document.querySelector("#message");
const count = document.querySelector("#result-count");
const featureCount = document.querySelector("#feature-count");
const tooltip = document.querySelector("#globe-tooltip");
let features = [];
let rotation = { longitude: 150.8, latitude: 59.56 };
let zoom = 1;
let targetZoom = 1;
let dragging = false;
let lastPointer = null;
let dragOrigin = null;
let didDrag = false;
let lastFrame = performance.now();
let lastReadout = 0;
let hoveredFeature = null;
let hoveredGroup = null;
let markerGroups = [];
let lastActivatedGroup = "";
let groupSelectionIndex = 0;
let searchRequestSequence = 0;
let globeGeometry = null;
let landPoints = [];
let selectedFeature = null;
const selectionPanel = document.querySelector("#globe-selection");
const selectionTitle = document.querySelector("#selection-title");
const selectionLocation = document.querySelector("#selection-location");
const selectionDocuments = document.querySelector("#selection-documents");
const selectionEyebrow = selectionPanel.querySelector(".eyebrow");
const savedSearches = document.querySelector("#saved-searches");

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
    const metadata = [
      doc.inventory_number, doc.tgf_number && `ТГФ ${doc.tgf_number}`,
      doc.region, doc.year, doc.document_type,
      doc.work_year_start && doc.work_year_end
        ? `работы ${doc.work_year_start}–${doc.work_year_end}` : null,
      doc.topic,
    ].filter(Boolean).join(" · ");
    if (metadata) card.append(text("p", metadata));
    const archival = [doc.authors, doc.coauthors, doc.executor_org, doc.created_place,
      doc.minerals, doc.archive_disk_number, doc.material_composition,
      doc.electronic_copy_status].filter(Boolean).join(" · ");
    if (archival) card.append(text("p", archival));
    if (doc.description) card.append(text("p", doc.description));
    if (doc.archive_reference) card.append(text("p", `Шифр/место хранения: ${doc.archive_reference}`));
    let efgiUrl;
    try {
      efgiUrl = new URL(doc.efgi_url);
    } catch {
      efgiUrl = null;
    }
    if (efgiUrl && ["http:", "https:"].includes(efgiUrl.protocol)) {
      const link = text("a", `ЕФГИ${doc.efgi_id ? ` · ${doc.efgi_id}` : ""}`, "file-download");
      link.href = efgiUrl.href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      card.append(link);
    }
    for (const relation of doc.relations || []) {
      card.append(text("p", `Связанный документ (${relation.relation_type}): ${relation.title}`));
    }
    for (const file of doc.files || []) {
      const link = text("a", `${file.filename} · ${formatFileSize(file.size_bytes)}`, "file-download");
      link.href = `/api/files/${encodeURIComponent(file.id)}`;
      link.setAttribute("download", "");
      card.append(link);
    }
    container.append(card);
  }
}

function formatFileSize(bytes) {
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} КБ`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
}

function renderFeature(feature, target = results) {
  const props = feature.properties;
  const card = text("article", null, "feature-card");
  card.tabIndex = 0;
  card.setAttribute("role", "button");
  card.dataset.featureId = feature.id;
  card.setAttribute("aria-label", feature.geometry
    ? `${props.name}, показать на глобусе`
    : `${props.name}, открыть карточку; координаты не нанесены на глобус`);
  const selected = feature.id === selectedFeature?.id;
  card.setAttribute("aria-pressed", String(selected));
  if (selected) card.classList.add("selected");
  card.append(text("h3", props.name));
  card.append(text("p", `${kindLabel(props.kind)} · документов: ${props.documents.length}`));
  if (!feature.geometry) {
    card.append(text("p", `Исходные координаты ${props.source_crs || "неизвестной CRS"} сохранены; на глобусе WGS 84 не отображаются.`));
  }
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
  if (!geometry) return [];
  if (geometry.type === "Point") return [geometry.coordinates];
  if (geometry.type === "MultiPoint") return geometry.coordinates;
  return [];
}

function renderSelection(feature) {
  selectedFeature = feature;
  selectionTitle.textContent = feature.properties.name;
  selectionEyebrow.textContent = feature.geometry ? "ОБЪЕКТ НА ГЛОБУСЕ" : "КАРТОЧКА ОБЪЕКТА";
  const geometry = feature.geometry || feature.properties.source_geometry;
  const coordinates = geometry && (featureCoordinates({ geometry })[0] || firstPolygonCoordinate(geometry));
  selectionLocation.textContent = coordinates
    ? `${feature.properties.source_crs || "EPSG:4326"}: ${coordinates[1].toFixed(6)}°, ${coordinates[0].toFixed(6)}°` +
      (feature.geometry ? "" : " · не нанесено на WGS 84 без утверждённой трансформации")
    : "Координаты не указаны.";
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

function unwrapRing(ring) {
  const unwrapped = [];
  for (const coordinate of ring) {
    const longitude = unwrapped.length
      ? unwrapped[unwrapped.length - 1][0] + ((coordinate[0] - unwrapped[unwrapped.length - 1][0] + 540) % 360) - 180
      : coordinate[0];
    unwrapped.push([longitude, coordinate[1]]);
  }
  return unwrapped;
}

function ringCenterLongitude(ring) {
  return ring.reduce((sum, coordinate) => sum + coordinate[0], 0) / ring.length;
}

function drawRasterRing(rasterContext, ring, shift, width, height) {
  ring.forEach(([longitude, latitude], index) => {
    const x = ((longitude + shift + 180) / 360) * width;
    const y = ((90 - latitude) / 180) * height;
    if (index === 0) rasterContext.moveTo(x, y);
    else rasterContext.lineTo(x, y);
  });
  rasterContext.closePath();
}

function buildLandPoints(countryFeatures) {
  const width = 1440;
  const height = 720;
  const spacing = 6;
  const raster = document.createElement("canvas");
  raster.width = width;
  raster.height = height;
  const rasterContext = raster.getContext("2d", { willReadFrequently: true });
  rasterContext.fillStyle = "#fff";

  for (const feature of countryFeatures) {
    const polygons = feature.geometry.type === "Polygon"
      ? [feature.geometry.coordinates]
      : feature.geometry.type === "MultiPolygon" ? feature.geometry.coordinates : [];
    for (const polygon of polygons) {
      const rings = polygon.map(unwrapRing);
      if (!rings.length) continue;
      const outerCenter = ringCenterLongitude(rings[0]);
      for (let shift = -360; shift <= 360; shift += 360) {
        rasterContext.beginPath();
        for (let ringIndex = 0; ringIndex < rings.length; ringIndex += 1) {
          const ring = rings[ringIndex];
          const alignment = ringIndex === 0
            ? 0
            : 360 * Math.round((outerCenter - ringCenterLongitude(ring)) / 360);
          drawRasterRing(rasterContext, ring, shift + alignment, width, height);
        }
        rasterContext.fill("evenodd");
      }
    }
  }

  const pixels = rasterContext.getImageData(0, 0, width, height).data;
  const points = [];
  for (let y = spacing / 2; y < height; y += spacing) {
    for (let x = spacing / 2; x < width; x += spacing) {
      if (pixels[(Math.floor(y) * width + Math.floor(x)) * 4 + 3] < 128) continue;
      const latitude = 90 - y / height * 180;
      const longitude = x / width * 360 - 180;
      const radians = latitude * Math.PI / 180;
      points.push({
        longitude,
        sinLatitude: Math.sin(radians),
        cosLatitude: Math.cos(radians),
      });
    }
  }
  return points;
}

function drawLandPoints(geometry) {
  const centerLatitude = rotation.latitude * Math.PI / 180;
  const sinCenter = Math.sin(centerLatitude);
  const cosCenter = Math.cos(centerLatitude);
  const radiansPerDegree = Math.PI / 180;
  const radius = Math.max(0.8, Math.min(1.5, geometry.radius * 0.0022));
  context.save();
  context.beginPath();
  context.arc(geometry.x, geometry.y, geometry.radius, 0, Math.PI * 2);
  context.clip();
  context.beginPath();
  for (const landPoint of landPoints) {
    const deltaLongitude = (landPoint.longitude - rotation.longitude) * radiansPerDegree;
    const sinLongitude = Math.sin(deltaLongitude);
    const cosLongitude = Math.cos(deltaLongitude);
    const x = landPoint.cosLatitude * sinLongitude;
    const y = landPoint.sinLatitude * cosCenter - landPoint.cosLatitude * cosLongitude * sinCenter;
    const z = landPoint.sinLatitude * sinCenter + landPoint.cosLatitude * cosLongitude * cosCenter;
    if (z <= 0) continue;
    context.moveTo(geometry.x + geometry.radius * x + radius, geometry.y - geometry.radius * y);
    context.arc(geometry.x + geometry.radius * x, geometry.y - geometry.radius * y, radius, 0, Math.PI * 2);
  }
  context.fillStyle = "rgba(139, 151, 226, .82)";
  context.fill();
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
  if (!geo) return;
  if (!["Polygon", "MultiPolygon"].includes(geo.type)) return;
  const area = feature.properties.kind === "area";
  const stroke = area ? "#d79a77" : "#f4c8aa";
  if (geo.type === "Polygon") {
    for (const ring of geo.coordinates) drawRing(ring, geometry, area ? "rgba(215, 154, 119, .12)" : null, stroke);
  } else if (geo.type === "MultiPolygon") {
    for (const polygon of geo.coordinates) {
      for (const ring of polygon) drawRing(ring, geometry, area ? "rgba(215, 154, 119, .12)" : null, stroke);
    }
  }
}

function buildMarkerGroups(geometry) {
  const groupsByCell = new Map();
  const groups = [];
  const cellSize = 22;
  const clusterDistance = 18;
  for (const feature of features) {
    if (!feature.geometry) continue;
    if (["Polygon", "MultiPolygon"].includes(feature.geometry.type)) continue;
    for (const coordinates of featureCoordinates(feature)) {
      const point = project(coordinates, geometry);
      if (point.z <= 0) continue;
      const cellX = Math.floor(point.x / cellSize);
      const cellY = Math.floor(point.y / cellSize);
      let nearest = null;
      let nearestDistance = clusterDistance;
      for (let dx = -1; dx <= 1; dx += 1) {
        for (let dy = -1; dy <= 1; dy += 1) {
          for (const candidate of groupsByCell.get(`${cellX + dx},${cellY + dy}`) || []) {
            const distance = Math.hypot(candidate.x - point.x, candidate.y - point.y);
            if (distance < nearestDistance) {
              nearest = candidate;
              nearestDistance = distance;
            }
          }
        }
      }
      if (nearest) {
        const size = nearest.members.length;
        nearest.x = (nearest.x * size + point.x) / (size + 1);
        nearest.y = (nearest.y * size + point.y) / (size + 1);
        nearest.members.push({ feature, coordinates });
      } else {
        const group = { x: point.x, y: point.y, cellX, cellY, members: [{ feature, coordinates }] };
        const key = `${cellX},${cellY}`;
        if (!groupsByCell.has(key)) groupsByCell.set(key, []);
        groupsByCell.get(key).push(group);
        groups.push(group);
      }
    }
  }
  return groups;
}

function drawMarkerGroup(group) {
  const clustered = group.members.length > 1;
  const hovered = group === hoveredGroup || group.members.some(({ feature }) => feature === hoveredFeature);
  const selected = group.members.some(({ feature }) => feature === selectedFeature);
  const radius = clustered ? Math.min(16, 9 + Math.log2(group.members.length)) : hovered ? 7 : 5.5;
  if (selected) {
    const pulse = radius + 8 + Math.sin(performance.now() / 240) * 2;
    context.beginPath();
    context.arc(group.x, group.y, pulse, 0, Math.PI * 2);
    context.strokeStyle = "rgba(242, 197, 165, .8)";
    context.lineWidth = 1.5;
    context.stroke();
  }
  context.beginPath();
  context.arc(group.x, group.y, radius + 3, 0, Math.PI * 2);
  context.fillStyle = "rgba(7, 11, 28, .92)";
  context.fill();
  context.beginPath();
  context.arc(group.x, group.y, radius, 0, Math.PI * 2);
  context.fillStyle = clustered ? "#9790e6" : "#f2c5a5";
  context.shadowColor = clustered ? "rgba(151, 144, 230, .8)" : "rgba(242, 197, 165, .8)";
  context.shadowBlur = hovered ? 18 : 8;
  context.fill();
  context.shadowBlur = 0;
  context.strokeStyle = clustered ? "#d1ceff" : "#f6d6bd";
  context.lineWidth = 1;
  context.stroke();
  if (clustered) {
    context.fillStyle = "#fff";
    context.font = "600 10px ui-monospace, monospace";
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillText(String(group.members.length), group.x, group.y + 0.5);
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
  drawLandPoints(globeGeometry);
  for (const feature of features) drawFeatureGeometry(feature, globeGeometry);
  markerGroups = buildMarkerGroups(globeGeometry);
  for (const group of markerGroups) drawMarkerGroup(group);
  if (now - lastReadout > 250) {
    document.querySelector("#globe-readout").textContent =
      `ШИР ${rotation.latitude.toFixed(1)}° · ДОЛГ ${rotation.longitude.toFixed(1)}° · МАСШТАБ ${zoom.toFixed(1)}×`;
    lastReadout = now;
  }
  window.requestAnimationFrame(drawGlobe);
}

function focusFeature(feature) {
  const coords = feature.geometry &&
    (featureCoordinates(feature)[0] || firstPolygonCoordinate(feature.geometry));
  if (coords) {
    rotation.longitude = coords[0];
    rotation.latitude = coords[1];
    targetZoom = Math.max(targetZoom, 1.12);
  }
  hoveredFeature = feature;
  renderSelection(feature);
  for (const card of results.children) {
    const selected = Number(card.dataset.featureId) === Number(feature.id);
    card.classList.toggle("selected", selected);
    card.setAttribute("aria-pressed", String(selected));
  }
  const card = [...results.children].find((item) => Number(item.dataset.featureId) === Number(feature.id));
  card?.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function firstPolygonCoordinate(geometry) {
  if (!geometry) return null;
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
  let nearestGroup = null;
  let nearestGroupDistance = Infinity;
  for (const group of markerGroups) {
    const distance = Math.hypot(group.x - x, group.y - y);
    const hitRadius = Math.max(20, Math.min(24, 10 + Math.log2(group.members.length + 1) * 3));
    if (distance < hitRadius && distance < nearestGroupDistance) {
      nearestGroup = group;
      nearestGroupDistance = distance;
    }
  }
  if (nearestGroup) return { group: nearestGroup, feature: nearestGroup.members[0].feature };
  let nearest = null;
  let nearestDistance = Infinity;
  for (const feature of features) {
    if (!feature.geometry || !["Polygon", "MultiPolygon"].includes(feature.geometry.type)) continue;
    for (const coordinates of coordinatesOf(feature)) {
      const point = project(coordinates, globeGeometry);
      const distance = Math.hypot(point.x - x, point.y - y);
      if (point.z > 0 && distance < 20 && distance < nearestDistance) {
        nearest = feature;
        nearestDistance = distance;
      }
    }
  }
  return nearest ? { group: null, feature: nearest } : null;
}

function updateTooltip(target) {
  if (!target) {
    tooltip.hidden = true;
    return;
  }
  const { group, feature } = target;
  tooltip.textContent = group?.members.length > 1
    ? `${group.members.length} объектов рядом · ${group.members.slice(0, 3).map(({ feature: item }) => item.properties.name).join(", ")}`
    : `${feature.properties.name} · ${feature.properties.documents.length} док.`;
  tooltip.hidden = false;
}

function activateMapTarget(target) {
  if (!target) return;
  const { group, feature } = target;
  if (!group || group.members.length < 2) {
    focusFeature(feature);
    return;
  }
  const signature = group.members.map(({ feature: item }) => item.id).sort().join(",");
  if (targetZoom >= 1.64 && zoom >= 1.5) {
    if (signature !== lastActivatedGroup) groupSelectionIndex = 0;
    else groupSelectionIndex += 1;
    lastActivatedGroup = signature;
    focusFeature(group.members[groupSelectionIndex % group.members.length].feature);
    return;
  }
  lastActivatedGroup = signature;
  groupSelectionIndex = 0;
  selectedFeature = null;
  selectionPanel.hidden = true;
  for (const card of results.children) {
    card.classList.remove("selected");
    card.setAttribute("aria-pressed", "false");
  }
  const [longitude, latitude] = group.members[0].coordinates;
  rotation.longitude = longitude;
  rotation.latitude = latitude;
  targetZoom = Math.min(1.65, Math.max(targetZoom, zoom) + 0.25);
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
  const target = featureAt(event.clientX - bounds.left, event.clientY - bounds.top);
  hoveredFeature = target?.feature || null;
  hoveredGroup = target?.group || null;
  canvas.classList.toggle("has-target", Boolean(target));
  updateTooltip(target);
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
    activateMapTarget(featureAt(event.clientX - bounds.left, event.clientY - bounds.top));
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
document.querySelector("#zoom-in").addEventListener("click", () => {
  targetZoom = Math.min(1.65, targetZoom + 0.12);
});
document.querySelector("#zoom-out").addEventListener("click", () => {
  targetZoom = Math.max(0.72, targetZoom - 0.12);
});
document.querySelector("#globe-reset").addEventListener("click", () => {
  selectedFeature = null;
  hoveredFeature = null;
  hoveredGroup = null;
  selectionPanel.hidden = true;
  targetZoom = 1;
  rotation = { longitude: 150.8, latitude: 59.56 };
  for (const card of results.children) {
    card.classList.remove("selected");
    card.setAttribute("aria-pressed", "false");
  }
});
canvas.addEventListener("pointerleave", () => {
  if (!dragging) {
    hoveredFeature = null;
    hoveredGroup = null;
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
  const requestId = ++searchRequestSequence;
  const params = new URLSearchParams(new FormData(document.querySelector("#search-form")));
  for (const [key, value] of [...params.entries()]) if (!value) params.delete(key);
  const exportUrl = new URL("/api/export.csv", location.href);
  exportUrl.search = params.toString();
  document.querySelector("#export-search").href = exportUrl.pathname + exportUrl.search;
  try {
    const response = await fetch(`/api/features?${params}`, { credentials: "same-origin" });
    if (response.status === 401) {
      location.assign("/login");
      return;
    }
    const body = await response.json();
    if (requestId !== searchRequestSequence) return;
    if (!response.ok) throw new Error(body.detail || "Не удалось выполнить поиск");
    features = body.features;
    if (selectedFeature && !features.some((feature) => feature.id === selectedFeature.id)) {
      selectedFeature = null;
      selectionPanel.hidden = true;
    }
    results.replaceChildren();
    for (const feature of features) renderFeature(feature);
    count.textContent = `${features.length} объектов`;
    featureCount.textContent = `${features.length} ОБЪЕКТОВ`;
    if (!features.length) message.textContent = "Ничего не найдено или измените фильтры.";
  } catch (error) {
    if (requestId !== searchRequestSequence) return;
    message.textContent = error.message;
    count.textContent = "Ошибка";
  }
}

async function loadSavedSearches() {
  const response = await fetch("/api/saved-searches", { credentials: "same-origin" });
  if (response.status === 401) {
    location.assign("/login");
    return;
  }
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || "Не удалось загрузить подборки");
  savedSearches.replaceChildren();
  for (const item of body.searches) {
    const row = document.createElement("div");
    row.className = "saved-search";
    const apply = text("button", item.name, "secondary");
    apply.type = "button";
    apply.addEventListener("click", () => {
      const form = document.querySelector("#search-form");
      for (const [key, value] of Object.entries(item.filters)) {
        const input = form.elements.namedItem(key);
        if (input) input.value = value;
      }
      search();
    });
    const remove = text("button", "×", "secondary");
    remove.type = "button";
    remove.setAttribute("aria-label", `Удалить подборку ${item.name}`);
    remove.addEventListener("click", async () => {
      try {
        const deleted = await fetch(`/api/saved-searches/${item.id}`, {
          method: "DELETE", credentials: "same-origin",
        });
        if (!deleted.ok) throw new Error("Не удалось удалить подборку.");
        await loadSavedSearches();
      } catch (error) {
        message.textContent = error.message;
      }
    });
    row.append(apply, remove);
    savedSearches.append(row);
  }
}

document.querySelector("#search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  search();
});
document.querySelector("#save-search").addEventListener("click", async () => {
  const name = window.prompt("Название подборки");
  if (!name?.trim()) return;
  const filters = Object.fromEntries(
    [...new FormData(document.querySelector("#search-form")).entries()]
      .filter(([, value]) => String(value).trim())
      .map(([key, value]) => [key, ["year_from", "year_to"].includes(key) ? Number(value) : value]),
  );
  try {
    const response = await fetch("/api/saved-searches", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim(), filters }),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "Не удалось сохранить подборку.");
    await loadSavedSearches();
  } catch (error) {
    message.textContent = error.message;
  }
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
    landPoints = buildLandPoints(data.features);
    if (!landPoints.length) throw new Error("В офлайн-карте не удалось найти точки суши");
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
      if (session) document.querySelector("#user-label").textContent = `● ${session.username}`;
      if (session?.role === "admin") {
        document.querySelector("#user-admin-link").hidden = false;
        document.querySelector("#catalog-admin-link").hidden = false;
      }
  })
  .catch((error) => {
    message.textContent = error.message;
  });
resizeCanvas();
window.requestAnimationFrame(drawGlobe);
search();
loadSavedSearches().catch((error) => { message.textContent = error.message; });
