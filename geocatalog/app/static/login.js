const errorElement = document.querySelector("#login-error");
const progressElement = document.querySelector("#login-progress");
const loginForm = document.querySelector("#login-form");
const loginButton = document.querySelector("#login-submit");
const canvas = document.querySelector("#login-globe");
const context = canvas.getContext("2d");
const mapReadout = document.querySelector("#login-map-readout");
const magadan = { longitude: 150.8, latitude: 59.56 };
const landPoints = [];
let rotation = { longitude: 32, latitude: 34 };
let zoom = 1;
let canvasGeometry = null;
let flightStartedAt = null;
let flightComplete = false;
let frameRequested = false;
let lastDrawAt = 0;
let mapReady = false;

function unwrapRing(ring) {
  const unwrapped = [];
  for (const coordinate of ring) {
    const previousLongitude = unwrapped.at(-1)?.[0];
    const longitude = previousLongitude === undefined
      ? coordinate[0]
      : previousLongitude + ((coordinate[0] - previousLongitude + 540) % 360) - 180;
    unwrapped.push([longitude, coordinate[1]]);
  }
  return unwrapped;
}

function ringCenterLongitude(ring) {
  return ring.reduce((sum, coordinate) => sum + coordinate[0], 0) / ring.length;
}

function traceRing(rasterContext, ring, shift, width, height) {
  ring.forEach(([longitude, latitude], index) => {
    const x = (longitude + shift + 180) / 360 * width;
    const y = (90 - latitude) / 180 * height;
    if (index === 0) rasterContext.moveTo(x, y);
    else rasterContext.lineTo(x, y);
  });
  rasterContext.closePath();
}

function buildLandPoints(features) {
  const width = 1440;
  const height = 720;
  const spacing = 6;
  const raster = document.createElement("canvas");
  raster.width = width;
  raster.height = height;
  const rasterContext = raster.getContext("2d", { willReadFrequently: true });
  rasterContext.fillStyle = "#fff";

  for (const feature of features) {
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
          traceRing(rasterContext, ring, shift + alignment, width, height);
        }
        rasterContext.fill("evenodd");
      }
    }
  }

  const pixels = rasterContext.getImageData(0, 0, width, height).data;
  for (let y = spacing / 2; y < height; y += spacing) {
    for (let x = spacing / 2; x < width; x += spacing) {
      if (pixels[(Math.floor(y) * width + Math.floor(x)) * 4 + 3] < 128) continue;
      const latitude = 90 - y / height * 180;
      const radians = latitude * Math.PI / 180;
      landPoints.push({
        longitude: x / width * 360 - 180,
        sinLatitude: Math.sin(radians),
        cosLatitude: Math.cos(radians),
      });
    }
  }
}

function project(latitude, longitude, centerLongitude, centerLatitude) {
  const latitudeRadians = latitude * Math.PI / 180;
  const deltaLongitude = (longitude - centerLongitude) * Math.PI / 180;
  const centerLatitudeRadians = centerLatitude * Math.PI / 180;
  const sinLatitude = Math.sin(latitudeRadians);
  const cosLatitude = Math.cos(latitudeRadians);
  const sinCenter = Math.sin(centerLatitudeRadians);
  const cosCenter = Math.cos(centerLatitudeRadians);
  const sinLongitude = Math.sin(deltaLongitude);
  const cosLongitude = Math.cos(deltaLongitude);
  return {
    x: cosLatitude * sinLongitude,
    y: sinLatitude * cosCenter - cosLatitude * cosLongitude * sinCenter,
    z: sinLatitude * sinCenter + cosLatitude * cosLongitude * cosCenter,
  };
}

function drawGrid(geometry) {
  context.save();
  context.beginPath();
  context.arc(geometry.x, geometry.y, geometry.radius, 0, Math.PI * 2);
  context.clip();
  const gradient = context.createRadialGradient(
    geometry.x - geometry.radius * .34, geometry.y - geometry.radius * .4, geometry.radius * .05,
    geometry.x, geometry.y, geometry.radius * 1.15,
  );
  gradient.addColorStop(0, "#182445");
  gradient.addColorStop(.72, "#0c142c");
  gradient.addColorStop(1, "#070b1c");
  context.fillStyle = gradient;
  context.fillRect(geometry.x - geometry.radius, geometry.y - geometry.radius, geometry.radius * 2, geometry.radius * 2);

  const centerLatitude = rotation.latitude * Math.PI / 180;
  const sinCenter = Math.sin(centerLatitude);
  const cosCenter = Math.cos(centerLatitude);
  const radiansPerDegree = Math.PI / 180;
  context.beginPath();
  context.strokeStyle = "rgba(157, 160, 211, .18)";
  context.lineWidth = .8;
  for (let latitude = -60; latitude <= 60; latitude += 30) {
    let drawing = false;
    for (let longitude = -180; longitude <= 180; longitude += 3) {
      const point = project(latitude, longitude, rotation.longitude, rotation.latitude);
      if (point.z > 0) {
        const x = geometry.x + geometry.radius * point.x;
        const y = geometry.y - geometry.radius * point.y;
        if (!drawing) context.moveTo(x, y);
        context.lineTo(x, y);
        drawing = true;
      } else {
        drawing = false;
      }
    }
  }
  for (let longitude = -180; longitude < 180; longitude += 30) {
    let drawing = false;
    const deltaLongitude = (longitude - rotation.longitude) * radiansPerDegree;
    const sinLongitude = Math.sin(deltaLongitude);
    const cosLongitude = Math.cos(deltaLongitude);
    for (let latitude = -90; latitude <= 90; latitude += 3) {
      const radians = latitude * radiansPerDegree;
      const sinLatitude = Math.sin(radians);
      const cosLatitude = Math.cos(radians);
      const point = {
        x: cosLatitude * sinLongitude,
        y: sinLatitude * cosCenter - cosLatitude * cosLongitude * sinCenter,
        z: sinLatitude * sinCenter + cosLatitude * cosLongitude * cosCenter,
      };
      if (point.z > 0) {
        const x = geometry.x + geometry.radius * point.x;
        const y = geometry.y - geometry.radius * point.y;
        if (!drawing) context.moveTo(x, y);
        context.lineTo(x, y);
        drawing = true;
      } else {
        drawing = false;
      }
    }
  }
  context.stroke();
  context.restore();

  context.beginPath();
  context.arc(geometry.x, geometry.y, geometry.radius, 0, Math.PI * 2);
  context.strokeStyle = "#8881df";
  context.lineWidth = 2;
  context.shadowColor = "rgba(117, 111, 215, .34)";
  context.shadowBlur = 15;
  context.stroke();
  context.shadowBlur = 0;
}

function drawLand(geometry) {
  const centerLatitude = rotation.latitude * Math.PI / 180;
  const sinCenter = Math.sin(centerLatitude);
  const cosCenter = Math.cos(centerLatitude);
  const radiansPerDegree = Math.PI / 180;
  const dotRadius = Math.max(.85, Math.min(1.45, geometry.radius * .0025));
  context.beginPath();
  for (const landPoint of landPoints) {
    const deltaLongitude = (landPoint.longitude - rotation.longitude) * radiansPerDegree;
    const sinLongitude = Math.sin(deltaLongitude);
    const cosLongitude = Math.cos(deltaLongitude);
    const x = landPoint.cosLatitude * sinLongitude;
    const y = landPoint.sinLatitude * cosCenter - landPoint.cosLatitude * cosLongitude * sinCenter;
    const z = landPoint.sinLatitude * sinCenter + landPoint.cosLatitude * cosLongitude * cosCenter;
    if (z <= 0) continue;
    const pointX = geometry.x + geometry.radius * x;
    const pointY = geometry.y - geometry.radius * y;
    context.moveTo(pointX + dotRadius, pointY);
    context.arc(pointX, pointY, dotRadius, 0, Math.PI * 2);
  }
  context.fillStyle = "rgba(139, 151, 226, .84)";
  context.fill();

  const magadanPoint = project(magadan.latitude, magadan.longitude, rotation.longitude, rotation.latitude);
  if (magadanPoint.z > 0) {
    const x = geometry.x + geometry.radius * magadanPoint.x;
    const y = geometry.y - geometry.radius * magadanPoint.y;
    const pulse = 8 + Math.sin(performance.now() / 300) * 2;
    context.beginPath();
    context.arc(x, y, pulse, 0, Math.PI * 2);
    context.strokeStyle = "rgba(242,197,165,.55)";
    context.lineWidth = 1.5;
    context.stroke();
    context.beginPath();
    context.arc(x, y, 4, 0, Math.PI * 2);
    context.fillStyle = "#f2c5a5";
    context.shadowColor = "#f2c5a5";
    context.shadowBlur = 12;
    context.fill();
    context.shadowBlur = 0;
  }
}

function resizeCanvas() {
  const bounds = canvas.getBoundingClientRect();
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.round(bounds.width * dpr);
  canvas.height = Math.round(bounds.height * dpr);
  context.setTransform(dpr, 0, 0, dpr, 0, 0);
  canvasGeometry = { width: bounds.width, height: bounds.height };
}

function drawGlobe(now) {
  frameRequested = false;
  if (!canvasGeometry || !mapReady) return;
  const width = canvasGeometry.width;
  const height = canvasGeometry.height;
  context.clearRect(0, 0, width, height);
  const scale = Math.min(width, height) * .41 * zoom;
  const geometry = { x: width * .51, y: height * .52, radius: scale };
  context.save();
  context.beginPath();
  context.arc(geometry.x, geometry.y, geometry.radius, 0, Math.PI * 2);
  context.clip();
  drawGrid(geometry);
  drawLand(geometry);
  context.restore();
  mapReadout.textContent = flightStartedAt === null
    ? "МИРОВОЙ ОБЗОР · ТОЧЕЧНАЯ КАРТА"
    : "МАГАДАН · ЦЕНТР КАРТЫ";
  lastDrawAt = now;
}

function requestDraw(now = performance.now()) {
  if (!frameRequested && now - lastDrawAt >= 30) {
    frameRequested = true;
    window.requestAnimationFrame(drawGlobe);
  }
}

function easeInOut(value) {
  return value < .5 ? 4 * value ** 3 : 1 - ((-2 * value + 2) ** 3) / 2;
}

function flyToMagadan() {
  return new Promise((resolve) => {
    const start = { ...rotation };
    let longitudeDelta = ((magadan.longitude - start.longitude + 540) % 360) - 180;
    const startZoom = zoom;
    const duration = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 100 : 1450;
    flightStartedAt = performance.now();

    function step(now) {
      const progress = Math.min(1, (now - flightStartedAt) / duration);
      const eased = easeInOut(progress);
      rotation = {
        longitude: start.longitude + longitudeDelta * eased,
        latitude: start.latitude + (magadan.latitude - start.latitude) * eased,
      };
      zoom = startZoom + (2.8 - startZoom) * eased;
      drawGlobe(now);
      if (progress < 1) {
        window.requestAnimationFrame(step);
      } else {
        flightComplete = true;
        resolve();
      }
    }
    window.requestAnimationFrame(step);
  });
}

async function initializeMap() {
  resizeCanvas();
  const response = await fetch("/static/ne_110m_admin_0_countries.geojson", { credentials: "same-origin" });
  if (!response.ok) throw new Error(`Карта недоступна (${response.status})`);
  const data = await response.json();
  if (!Array.isArray(data.features)) throw new Error("Файл карты имеет неверный формат");
  buildLandPoints(data.features);
  if (!landPoints.length) throw new Error("На карте не найдены точки суши");
  mapReady = true;
  drawGlobe(performance.now());
  let previous = performance.now();

  function animate(now) {
    if (flightComplete) return;
    if (flightStartedAt === null) {
      const elapsed = Math.min((now - previous) / 1000, .05);
      rotation.longitude = (rotation.longitude + elapsed * .7) % 360;
    }
    previous = now;
    requestDraw(now);
    window.requestAnimationFrame(animate);
  }
  window.requestAnimationFrame(animate);
}

const initialError = new URLSearchParams(window.location.search).get("error");
if (initialError) {
  errorElement.textContent = initialError === "invalid"
    ? "Неверное имя пользователя или пароль."
    : "Не удалось выполнить вход. Обратитесь к администратору.";
  errorElement.hidden = false;
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (loginButton.disabled) return;
  errorElement.hidden = true;
  progressElement.hidden = false;
  progressElement.textContent = "Проверяем учётные данные…";
  loginButton.disabled = true;

  try {
    const response = await fetch(loginForm.action, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams(new FormData(loginForm)),
    });
    const destination = new URL(response.url, window.location.href);
    if (destination.pathname === "/login") {
      errorElement.textContent = destination.searchParams.get("error") === "invalid"
        ? "Неверное имя пользователя или пароль."
        : "Войти не удалось. Проверьте данные и попробуйте ещё раз.";
      errorElement.hidden = false;
      progressElement.hidden = true;
      loginButton.disabled = false;
      return;
    }
    if (!response.ok || destination.pathname !== "/") {
      let detail = "";
      try {
        detail = (await response.json()).detail || "";
      } catch {
        detail = "";
      }
      throw new Error(detail || "Сервис входа временно недоступен.");
    }

    document.body.classList.add("is-flying");
    progressElement.textContent = "Вход выполнен. Приближаемся к Магадану…";
    await flyToMagadan();
    window.location.assign("/");
  } catch (error) {
    errorElement.textContent = error.message || "Не удалось подключиться к серверу.";
    errorElement.hidden = false;
    progressElement.hidden = true;
    loginButton.disabled = false;
  }
});

window.addEventListener("resize", () => {
  resizeCanvas();
  requestDraw(performance.now() + 31);
});
initializeMap().catch((error) => {
  mapReadout.textContent = "КАРТА НЕ ЗАГРУЗИЛАСЬ";
  console.error(error);
});
