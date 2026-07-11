"""Tablero de control web servido en la red local (sin dependencias extra).

Expone en el navegador el video en vivo con las detecciones, contadores de
producción, estado de la línea y un gráfico de velocidad, al estilo de los
paneles de inspección industriales. Usa solo la librería estándar
(`http.server`), así que no agrega dependencias al proyecto.
"""

import json
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class EstadoTablero:
    """Estado compartido entre el pipeline de video y el servidor web.

    El pipeline publica el último cuadro anotado (JPEG) y las estadísticas;
    el servidor los lee desde otros hilos, por eso todo pasa por un lock.
    """

    def __init__(self) -> None:
        """Inicializa el estado vacío con historial de hasta 90 minutos."""
        self._lock = threading.Lock()
        self._jpeg: bytes | None = None
        self._datos: dict = {}
        # Un punto por segundo; 5400 puntos ≈ 90 minutos de historia.
        self._historial: deque[dict] = deque(maxlen=5400)
        self._ultimo_punto: float = 0.0

    def publicar(self, jpeg: bytes, datos: dict) -> None:
        """Publica el cuadro anotado y las estadísticas del momento."""
        ahora = time.time()
        with self._lock:
            self._jpeg = jpeg
            self._datos = datos
            # Agrega al historial como máximo un punto por segundo.
            if ahora - self._ultimo_punto >= 1.0:
                self._historial.append(
                    {"t": ahora, "bpm": datos.get("bpm", 0.0), "total": datos.get("total", 0)}
                )
                self._ultimo_punto = ahora

    def obtener_jpeg(self) -> bytes | None:
        """Devuelve el último cuadro JPEG publicado (o None si aún no hay)."""
        with self._lock:
            return self._jpeg

    def obtener_datos_json(self) -> bytes:
        """Serializa estadísticas + historial para el endpoint /datos."""
        with self._lock:
            carga = dict(self._datos)
            carga["historial"] = list(self._historial)
        return json.dumps(carga).encode("utf-8")


PAGINA_HTML = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Línea de envasado — Contador de botellas</title>
<style>
  :root {
    --plano: #0d0d0d; --superficie: #1a1a19; --borde: rgba(255,255,255,0.10);
    --tinta: #ffffff; --tinta-2: #c3c2b7; --tenue: #898781;
    --grilla: #2c2c2a; --eje: #383835;
    --serie: #3987e5; --ok: #0ca30c; --critico: #d03b3b;
  }
  * { box-sizing: border-box; margin: 0; }
  body { background: var(--plano); color: var(--tinta);
         font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; padding: 16px; }
  header { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; flex-wrap: wrap; }
  header h1 { font-size: 18px; font-weight: 600; }
  .chip { display: inline-flex; align-items: center; gap: 7px; padding: 4px 12px;
          border: 1px solid var(--borde); border-radius: 999px; font-weight: 600; font-size: 13px; }
  .chip .punto { width: 9px; height: 9px; border-radius: 50%; }
  .hora { margin-left: auto; color: var(--tenue); font-variant-numeric: tabular-nums; }
  .rejilla { display: grid; grid-template-columns: minmax(0, 2fr) minmax(220px, 1fr); gap: 14px; }
  @media (max-width: 800px) { .rejilla { grid-template-columns: 1fr; } }
  .tarjeta { background: var(--superficie); border: 1px solid var(--borde);
             border-radius: 10px; padding: 14px; }
  .video img { width: 100%; display: block; border-radius: 6px; background: #000; min-height: 200px; }
  .fichas { display: grid; gap: 14px; }
  .ficha .rotulo { color: var(--tinta-2); font-size: 12px; letter-spacing: .06em;
                   text-transform: uppercase; margin-bottom: 4px; }
  .ficha .valor { font-size: 40px; font-weight: 700; line-height: 1.1; }
  .ficha .unidad { color: var(--tenue); font-size: 14px; font-weight: 400; }
  .grafico-caja { margin-top: 14px; }
  .grafico-caja h2 { font-size: 13px; font-weight: 600; color: var(--tinta-2); margin-bottom: 8px; }
  #grafico { width: 100%; height: 220px; display: block; cursor: crosshair; }
  .pie { color: var(--tenue); font-size: 12px; margin-top: 10px; }
  .alterna { background: none; border: 1px solid var(--borde); color: var(--tinta-2);
             border-radius: 6px; padding: 3px 10px; cursor: pointer; font-size: 12px; }
  table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
  th, td { text-align: right; padding: 4px 8px; border-bottom: 1px solid var(--grilla); }
  th:first-child, td:first-child { text-align: left; }
  th { color: var(--tenue); font-weight: 500; }
  #tabla { display: none; max-height: 220px; overflow-y: auto; }
  #aviso-tooltip { position: fixed; pointer-events: none; background: #262624; color: var(--tinta);
    border: 1px solid var(--borde); border-radius: 6px; padding: 5px 9px; font-size: 12px;
    display: none; z-index: 10; font-variant-numeric: tabular-nums; }
</style>
</head>
<body>
<header>
  <h1>Línea de envasado</h1>
  <span class="chip" id="chip-estado"><span class="punto" style="background:var(--tenue)"></span>ESPERANDO</span>
  <span class="hora" id="hora">—</span>
</header>

<div class="rejilla">
  <div class="tarjeta video"><img src="/video" alt="Video en vivo de la línea con detecciones"></div>
  <div class="fichas">
    <div class="tarjeta ficha">
      <div class="rotulo">Botellas contadas</div>
      <div class="valor" id="total">0</div>
    </div>
    <div class="tarjeta ficha">
      <div class="rotulo">Velocidad actual</div>
      <div class="valor" id="bpm">0 <span class="unidad">bot/min</span></div>
    </div>
    <div class="tarjeta ficha">
      <div class="rotulo">Promedio de la sesión</div>
      <div class="valor" id="bpm-prom">0 <span class="unidad">bot/min</span></div>
    </div>
    <div class="tarjeta ficha">
      <div class="rotulo">Botellas en cámara</div>
      <div class="valor" id="en-cuadro">0</div>
    </div>
  </div>
</div>

<div class="tarjeta grafico-caja">
  <h2>Velocidad de producción (botellas/min)
      <button class="alterna" id="btn-tabla" style="float:right">Ver tabla</button></h2>
  <canvas id="grafico"></canvas>
  <div id="tabla"></div>
  <div class="pie">Últimos 15 minutos · un punto por segundo · actualiza cada segundo</div>
</div>
<div id="aviso-tooltip"></div>

<script>
"use strict";
const $ = id => document.getElementById(id);
let historial = [];

function formatoHora(t) {
  return new Date(t * 1000).toLocaleTimeString("es-AR", {hour12: false});
}

async function actualizar() {
  try {
    const r = await fetch("/datos", {cache: "no-store"});
    const d = await r.json();
    $("total").textContent = d.total ?? 0;
    $("bpm").innerHTML = (d.bpm ?? 0).toFixed(1) + ' <span class="unidad">bot/min</span>';
    $("bpm-prom").innerHTML = (d.bpm_promedio ?? 0).toFixed(1) + ' <span class="unidad">bot/min</span>';
    $("en-cuadro").textContent = d.en_cuadro ?? 0;
    $("hora").textContent = formatoHora(d.hora ?? Date.now() / 1000);
    const chip = $("chip-estado"), punto = chip.querySelector(".punto");
    if ((d.total ?? 0) === 0 && (d.segundos_sin_cruce ?? 1e9) > 1e8) {
      chip.lastChild.textContent = "ESPERANDO"; punto.style.background = "var(--tenue)";
    } else if ((d.segundos_sin_cruce ?? 0) < 30) {
      chip.lastChild.textContent = "PRODUCIENDO"; punto.style.background = "var(--ok)";
    } else {
      chip.lastChild.textContent = "SIN PRODUCCIÓN"; punto.style.background = "var(--critico)";
    }
    historial = (d.historial || []).slice(-900);   // 15 min
    dibujar();
    if ($("tabla").style.display !== "none") llenarTabla();
  } catch (e) { /* servidor ocupado: reintenta en el próximo tick */ }
}

const lienzo = $("grafico"), ctx = lienzo.getContext("2d");
function dibujar() {
  const dpr = window.devicePixelRatio || 1;
  const w = lienzo.clientWidth, h = lienzo.clientHeight || 220;
  lienzo.width = w * dpr; lienzo.height = h * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const m = {izq: 44, der: 10, arr: 10, aba: 22};
  const aw = w - m.izq - m.der, ah = h - m.arr - m.aba;
  if (historial.length < 2) {
    ctx.fillStyle = "#898781"; ctx.font = "13px system-ui";
    ctx.fillText("Esperando datos…", m.izq, h / 2);
    return;
  }
  const t0 = historial[0].t, t1 = historial[historial.length - 1].t;
  const maxY = Math.max(10, Math.ceil(Math.max(...historial.map(p => p.bpm)) * 1.15 / 10) * 10);
  const X = t => m.izq + (t - t0) / Math.max(1, t1 - t0) * aw;
  const Y = v => m.arr + ah - v / maxY * ah;
  // grilla horizontal + rótulos del eje Y
  ctx.strokeStyle = "#2c2c2a"; ctx.fillStyle = "#898781";
  ctx.font = "11px system-ui"; ctx.textAlign = "right"; ctx.lineWidth = 1;
  const pasos = 4;
  for (let i = 0; i <= pasos; i++) {
    const v = maxY / pasos * i, y = Y(v);
    ctx.beginPath(); ctx.moveTo(m.izq, y); ctx.lineTo(w - m.der, y); ctx.stroke();
    ctx.fillText(v.toFixed(0), m.izq - 6, y + 4);
  }
  // rótulos del eje X (3 marcas de hora)
  ctx.textAlign = "center";
  for (let i = 0; i <= 2; i++) {
    const t = t0 + (t1 - t0) * i / 2;
    ctx.fillText(formatoHora(t), X(t), h - 6);
  }
  // línea base
  ctx.strokeStyle = "#383835";
  ctx.beginPath(); ctx.moveTo(m.izq, Y(0)); ctx.lineTo(w - m.der, Y(0)); ctx.stroke();
  // serie: relleno suave + línea de 2px
  ctx.beginPath();
  historial.forEach((p, i) => i ? ctx.lineTo(X(p.t), Y(p.bpm)) : ctx.moveTo(X(p.t), Y(p.bpm)));
  ctx.strokeStyle = "#3987e5"; ctx.lineWidth = 2; ctx.lineJoin = "round"; ctx.stroke();
  ctx.lineTo(X(t1), Y(0)); ctx.lineTo(X(t0), Y(0)); ctx.closePath();
  ctx.fillStyle = "rgba(57,135,229,0.14)"; ctx.fill();
}

// Tooltip con línea guía sobre el punto más cercano al cursor.
lienzo.addEventListener("mousemove", ev => {
  if (historial.length < 2) return;
  const caja = lienzo.getBoundingClientRect();
  const t0 = historial[0].t, t1 = historial[historial.length - 1].t;
  const m = {izq: 44, der: 10};
  const frac = (ev.clientX - caja.left - m.izq) / (caja.width - m.izq - m.der);
  const tBuscado = t0 + Math.min(1, Math.max(0, frac)) * (t1 - t0);
  let cercano = historial[0];
  for (const p of historial) if (Math.abs(p.t - tBuscado) < Math.abs(cercano.t - tBuscado)) cercano = p;
  const tip = $("aviso-tooltip");
  tip.style.display = "block";
  tip.style.left = (ev.clientX + 14) + "px"; tip.style.top = (ev.clientY - 10) + "px";
  tip.textContent = formatoHora(cercano.t) + " · " + cercano.bpm.toFixed(1) + " bot/min · total " + cercano.total;
  dibujar();
  const dpr = window.devicePixelRatio || 1;
  const w = lienzo.clientWidth, h = lienzo.clientHeight;
  const aw = w - 44 - 10, ah = h - 10 - 22;
  const maxY = Math.max(10, Math.ceil(Math.max(...historial.map(p => p.bpm)) * 1.15 / 10) * 10);
  const x = 44 + (cercano.t - t0) / Math.max(1, t1 - t0) * aw;
  const y = 10 + ah - cercano.bpm / maxY * ah;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.strokeStyle = "#898781"; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(x, 10); ctx.lineTo(x, 10 + ah); ctx.stroke();
  ctx.beginPath(); ctx.arc(x, y, 4, 0, Math.PI * 2);
  ctx.fillStyle = "#3987e5"; ctx.fill();
  ctx.strokeStyle = "#1a1a19"; ctx.lineWidth = 2; ctx.stroke();
});
lienzo.addEventListener("mouseleave", () => { $("aviso-tooltip").style.display = "none"; dibujar(); });

// Vista de tabla (accesibilidad y lectura exacta): un renglón por minuto.
$("btn-tabla").addEventListener("click", () => {
  const t = $("tabla"), g = $("grafico");
  const verTabla = t.style.display === "none" || !t.style.display;
  t.style.display = verTabla ? "block" : "none";
  g.style.display = verTabla ? "none" : "block";
  $("btn-tabla").textContent = verTabla ? "Ver gráfico" : "Ver tabla";
  if (verTabla) llenarTabla();
});
function llenarTabla() {
  const porMinuto = new Map();
  for (const p of historial) {
    const clave = formatoHora(p.t).slice(0, 5);
    porMinuto.set(clave, p);   // se queda con el último punto de cada minuto
  }
  let html = "<table><tr><th>Hora</th><th>bot/min</th><th>Total</th></tr>";
  [...porMinuto.entries()].reverse().forEach(([hora, p]) => {
    html += `<tr><td>${hora}</td><td>${p.bpm.toFixed(1)}</td><td>${p.total}</td></tr>`;
  });
  $("tabla").innerHTML = html + "</table>";
}

window.addEventListener("resize", dibujar);
actualizar();
setInterval(actualizar, 1000);
</script>
</body>
</html>
"""


class _ManejadorTablero(BaseHTTPRequestHandler):
    """Sirve la página, el stream MJPEG y los datos JSON del tablero."""

    estado: EstadoTablero  # asignado por iniciar_tablero

    def do_GET(self) -> None:  # noqa: N802 — nombre requerido por BaseHTTPRequestHandler
        """Atiende /, /datos y /video."""
        if self.path in ("/", "/index.html"):
            cuerpo = PAGINA_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)
        elif self.path == "/datos":
            cuerpo = self.estado.obtener_datos_json()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)
        elif self.path == "/video":
            self.send_response(200)
            self.send_header(
                "Content-Type", "multipart/x-mixed-replace; boundary=cuadro"
            )
            self.end_headers()
            try:
                # Stream MJPEG: reenvía el último cuadro ~15 veces por segundo.
                while True:
                    jpeg = self.estado.obtener_jpeg()
                    if jpeg is not None:
                        self.wfile.write(b"--cuadro\r\n")
                        self.send_header("Content-Type", "image/jpeg")
                        self.send_header("Content-Length", str(len(jpeg)))
                        self.end_headers()
                        self.wfile.write(jpeg)
                        self.wfile.write(b"\r\n")
                    time.sleep(1 / 15)
            except (BrokenPipeError, ConnectionResetError):
                pass  # el navegador cerró la pestaña
        else:
            self.send_error(404)

    def log_message(self, *args) -> None:
        """Silencia el log por consola del servidor HTTP."""


def iniciar_tablero(estado: EstadoTablero, puerto: int = 8000) -> ThreadingHTTPServer:
    """Levanta el servidor del tablero en un hilo demonio y lo devuelve."""
    _ManejadorTablero.estado = estado
    servidor = ThreadingHTTPServer(("0.0.0.0", puerto), _ManejadorTablero)
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    return servidor
