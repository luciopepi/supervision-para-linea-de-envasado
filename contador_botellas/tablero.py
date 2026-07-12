"""HMI web de la línea: tablero táctil servido en la red local.

Interfaz pensada para una pantalla táctil junto a la línea, al estilo de los
equipos de inspección industriales (VisionQuality, Tiama): botones grandes
para iniciar/detener la detección, capturar muestras para entrenar el modelo
de defectos y probar la válvula de descarte; video en vivo con las
detecciones; contadores y gráfico de velocidad. Usa solo la librería estándar
(`http.server`), sin dependencias extra.
"""

import json
import threading
import time
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class EstadoTablero:
    """Estado compartido entre el pipeline de video y el servidor web.

    El pipeline publica el último cuadro anotado (JPEG), las estadísticas y
    los eventos; la HMI encola comandos (iniciar, detener, capturar muestra,
    probar válvula) que el pipeline drena en cada cuadro. Todo pasa por un
    lock porque servidor y pipeline corren en hilos distintos.
    """

    def __init__(self) -> None:
        """Inicializa el estado vacío con historial de hasta 90 minutos."""
        self._lock = threading.Lock()
        self._jpeg: bytes | None = None
        self._datos: dict = {}
        # Un punto por segundo; 5400 puntos ≈ 90 minutos de historia.
        self._historial: deque[dict] = deque(maxlen=5400)
        self._eventos: deque[dict] = deque(maxlen=40)
        self._comandos: deque[dict] = deque()
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

    def agregar_evento(self, tipo: str, detalle: str) -> None:
        """Registra un evento (descarte, muestra, cambio de estado) para la HMI."""
        with self._lock:
            self._eventos.appendleft(
                {"hora": datetime.now().strftime("%H:%M:%S"), "tipo": tipo, "detalle": detalle}
            )

    def enviar_comando(self, comando: dict) -> None:
        """Encola un comando enviado desde la HMI hacia el pipeline."""
        with self._lock:
            self._comandos.append(comando)

    def obtener_comandos(self) -> list[dict]:
        """Devuelve y vacía la cola de comandos pendientes."""
        with self._lock:
            pendientes = list(self._comandos)
            self._comandos.clear()
        return pendientes

    def obtener_jpeg(self) -> bytes | None:
        """Devuelve el último cuadro JPEG publicado (o None si aún no hay)."""
        with self._lock:
            return self._jpeg

    def obtener_datos_json(self) -> bytes:
        """Serializa estadísticas + historial + eventos para el endpoint /datos."""
        with self._lock:
            carga = dict(self._datos)
            carga["historial"] = list(self._historial)
            carga["eventos"] = list(self._eventos)
        return json.dumps(carga).encode("utf-8")


PAGINA_HTML = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Línea de envasado — Control de producción</title>
<style>
  :root {
    --plano: #0d0d0d; --superficie: #1a1a19; --superficie-2: #222220;
    --borde: rgba(255,255,255,0.10);
    --tinta: #ffffff; --tinta-2: #c3c2b7; --tenue: #898781;
    --grilla: #2c2c2a; --eje: #383835;
    --serie: #3987e5; --ok: #0ca30c; --alerta: #fab219; --critico: #d03b3b;
  }
  * { box-sizing: border-box; margin: 0; -webkit-tap-highlight-color: transparent; }
  html, body { height: 100%; }
  body { background: var(--plano); color: var(--tinta); display: flex; flex-direction: column;
         font: 15px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif; padding: 12px; gap: 12px;
         user-select: none; }
  header { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
  header h1 { font-size: 20px; font-weight: 700; letter-spacing: .02em; }
  .chip { display: inline-flex; align-items: center; gap: 8px; padding: 8px 16px;
          border: 1px solid var(--borde); border-radius: 999px; font-weight: 700; font-size: 15px; }
  .chip .punto { width: 11px; height: 11px; border-radius: 50%; }
  .hora { margin-left: auto; color: var(--tinta-2); font-size: 22px; font-weight: 600;
          font-variant-numeric: tabular-nums; }
  .rejilla { display: grid; grid-template-columns: minmax(0, 3fr) minmax(230px, 1fr);
             gap: 12px; flex: 1; min-height: 0; }
  @media (max-width: 900px) { .rejilla { grid-template-columns: 1fr; } }
  .tarjeta { background: var(--superficie); border: 1px solid var(--borde);
             border-radius: 12px; padding: 12px; min-height: 0; }
  .video { display: flex; align-items: center; justify-content: center; }
  .video img { max-width: 100%; max-height: 100%; border-radius: 8px; background: #000; }
  .fichas { display: grid; gap: 12px; grid-auto-rows: 1fr; }
  .ficha { display: flex; flex-direction: column; justify-content: center; }
  .ficha .rotulo { color: var(--tinta-2); font-size: 13px; letter-spacing: .07em;
                   text-transform: uppercase; margin-bottom: 2px; }
  .ficha .valor { font-size: clamp(34px, 4.5vw, 52px); font-weight: 800; line-height: 1.05; }
  .ficha .unidad { color: var(--tenue); font-size: 15px; font-weight: 500; }
  .ficha.defecto .valor { color: var(--critico); }
  .botonera { display: grid; grid-template-columns: 1.4fr repeat(3, 1fr); gap: 12px; }
  @media (max-width: 900px) { .botonera { grid-template-columns: 1fr 1fr; } }
  button.grande { border: 1px solid var(--borde); border-radius: 12px; cursor: pointer;
    min-height: 76px; font: inherit; font-size: 19px; font-weight: 800; letter-spacing: .03em;
    color: var(--tinta); background: var(--superficie-2); padding: 10px 14px;
    display: flex; align-items: center; justify-content: center; gap: 12px; }
  button.grande:active { transform: scale(0.97); filter: brightness(1.25); }
  button.grande .icono { font-size: 26px; line-height: 1; }
  #btn-marcha.iniciar { background: #0d3a0d; border-color: #1d6b1d; }
  #btn-marcha.detener { background: #4a1414; border-color: #8a2626; }
  .abajo { display: grid; grid-template-columns: 3fr 2fr; gap: 12px; min-height: 210px; }
  @media (max-width: 900px) { .abajo { grid-template-columns: 1fr; } }
  .abajo h2 { font-size: 13px; font-weight: 700; color: var(--tinta-2); margin-bottom: 6px;
              text-transform: uppercase; letter-spacing: .07em; }
  #grafico { width: 100%; height: 160px; display: block; }
  .eventos { overflow-y: auto; max-height: 175px; }
  .eventos table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
  .eventos td { padding: 5px 8px; border-bottom: 1px solid var(--grilla); font-size: 14px; }
  .eventos td.hora-ev { color: var(--tenue); white-space: nowrap; width: 1%; }
  .etiqueta-ev { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 12px;
                 font-weight: 700; margin-right: 6px; }
  .ev-descarte { background: #4a1414; color: #ff9d9d; }
  .ev-muestra  { background: #14304a; color: #9dc8ff; }
  .ev-valvula  { background: #4a3a14; color: #ffd98a; }
  .ev-estado   { background: #2c2c2a; color: var(--tinta-2); }
  #aviso { position: fixed; left: 50%; bottom: 110px; transform: translateX(-50%);
    background: #262624; border: 1px solid var(--borde); border-radius: 10px;
    padding: 12px 22px; font-size: 17px; font-weight: 700; display: none; z-index: 20; }
</style>
</head>
<body>
<header>
  <h1>LÍNEA DE ENVASADO</h1>
  <span class="chip" id="chip-estado"><span class="punto" style="background:var(--tenue)"></span><span id="chip-texto">EN PAUSA</span></span>
  <span class="chip" id="chip-valvula" style="display:none"><span class="punto" style="background:var(--alerta)"></span><span>VÁLVULA SIMULADA</span></span>
  <span class="hora" id="hora">—</span>
</header>

<div class="rejilla">
  <div class="tarjeta video"><img src="/video" alt="Video en vivo de la línea con detecciones"></div>
  <div class="fichas">
    <div class="tarjeta ficha">
      <div class="rotulo">Botellas</div>
      <div class="valor" id="total">0</div>
    </div>
    <div class="tarjeta ficha">
      <div class="rotulo">Velocidad</div>
      <div class="valor" id="bpm">0 <span class="unidad">bot/min</span></div>
    </div>
    <div class="tarjeta ficha defecto">
      <div class="rotulo">Defectos descartados</div>
      <div class="valor" id="defectos">0</div>
    </div>
    <div class="tarjeta ficha">
      <div class="rotulo">En cámara</div>
      <div class="valor" id="en-cuadro">0</div>
    </div>
  </div>
</div>

<div class="botonera">
  <button class="grande iniciar" id="btn-marcha">
    <span class="icono" id="icono-marcha">▶</span><span id="texto-marcha">INICIAR DETECCIÓN</span>
  </button>
  <button class="grande" id="btn-ok"><span class="icono">📷</span>MUESTRA OK</button>
  <button class="grande" id="btn-defecto"><span class="icono">⚠️</span>MUESTRA DEFECTO</button>
  <button class="grande" id="btn-valvula"><span class="icono">💨</span>PROBAR VÁLVULA</button>
</div>

<div class="abajo">
  <div class="tarjeta">
    <h2>Velocidad de producción (botellas/min)</h2>
    <canvas id="grafico"></canvas>
  </div>
  <div class="tarjeta">
    <h2>Eventos</h2>
    <div class="eventos" id="eventos">—</div>
  </div>
</div>
<div id="aviso"></div>

<script>
"use strict";
const $ = id => document.getElementById(id);
let historial = [], detectando = false;

function formatoHora(t) {
  return new Date(t * 1000).toLocaleTimeString("es-AR", {hour12: false});
}

function avisar(texto) {
  const a = $("aviso");
  a.textContent = texto; a.style.display = "block";
  clearTimeout(a._t); a._t = setTimeout(() => a.style.display = "none", 2500);
}

async function comando(cuerpo, mensaje) {
  try {
    await fetch("/comando", {method: "POST", headers: {"Content-Type": "application/json"},
                             body: JSON.stringify(cuerpo)});
    if (mensaje) avisar(mensaje);
  } catch (e) { avisar("Sin conexión con el equipo"); }
}

$("btn-marcha").addEventListener("click", () => {
  comando({accion: detectando ? "detener" : "iniciar"},
          detectando ? "Detección detenida" : "Detección iniciada");
});
$("btn-ok").addEventListener("click", () =>
  comando({accion: "capturar", clase: "ok"}, "Muestra OK guardada"));
$("btn-defecto").addEventListener("click", () =>
  comando({accion: "capturar", clase: "defecto"}, "Muestra de DEFECTO guardada"));
$("btn-valvula").addEventListener("click", () =>
  comando({accion: "probar_valvula"}, "Probando válvula…"));

function pintarMarcha() {
  const b = $("btn-marcha");
  b.classList.toggle("detener", detectando);
  b.classList.toggle("iniciar", !detectando);
  $("icono-marcha").textContent = detectando ? "⏹" : "▶";
  $("texto-marcha").textContent = detectando ? "DETENER DETECCIÓN" : "INICIAR DETECCIÓN";
}

async function actualizar() {
  try {
    const r = await fetch("/datos", {cache: "no-store"});
    const d = await r.json();
    detectando = !!d.detectando; pintarMarcha();
    $("total").textContent = d.total ?? 0;
    $("bpm").innerHTML = (d.bpm ?? 0).toFixed(0) + ' <span class="unidad">bot/min</span>';
    $("defectos").textContent = d.defectos ?? 0;
    $("en-cuadro").textContent = d.en_cuadro ?? 0;
    $("hora").textContent = formatoHora(d.hora ?? Date.now() / 1000);
    const punto = $("chip-estado").querySelector(".punto"), texto = $("chip-texto");
    if (!detectando) { texto.textContent = "EN PAUSA"; punto.style.background = "var(--tenue)"; }
    else if ((d.segundos_sin_cruce ?? 0) < 30) {
      texto.textContent = "PRODUCIENDO"; punto.style.background = "var(--ok)";
    } else { texto.textContent = "SIN PRODUCCIÓN"; punto.style.background = "var(--critico)"; }
    $("chip-valvula").style.display = (d.valvula && d.valvula.simulada) ? "" : "none";
    historial = (d.historial || []).slice(-900);
    dibujar();
    pintarEventos(d.eventos || []);
  } catch (e) { /* servidor ocupado: reintenta en el próximo tick */ }
}

function pintarEventos(eventos) {
  if (!eventos.length) { $("eventos").textContent = "Sin eventos todavía."; return; }
  let html = "<table>";
  for (const ev of eventos) {
    html += `<tr><td class="hora-ev">${ev.hora}</td>` +
            `<td><span class="etiqueta-ev ev-${ev.tipo}">${ev.tipo.toUpperCase()}</span>` +
            `${ev.detalle}</td></tr>`;
  }
  $("eventos").innerHTML = html + "</table>";
}

const lienzo = $("grafico"), ctx = lienzo.getContext("2d");
function dibujar() {
  const dpr = window.devicePixelRatio || 1;
  const w = lienzo.clientWidth, h = lienzo.clientHeight || 160;
  lienzo.width = w * dpr; lienzo.height = h * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const m = {izq: 44, der: 10, arr: 8, aba: 20};
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
  ctx.strokeStyle = "#2c2c2a"; ctx.fillStyle = "#898781";
  ctx.font = "11px system-ui"; ctx.textAlign = "right"; ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const v = maxY / 4 * i, y = Y(v);
    ctx.beginPath(); ctx.moveTo(m.izq, y); ctx.lineTo(w - m.der, y); ctx.stroke();
    ctx.fillText(v.toFixed(0), m.izq - 6, y + 4);
  }
  ctx.textAlign = "center";
  for (let i = 0; i <= 2; i++) {
    const t = t0 + (t1 - t0) * i / 2;
    ctx.fillText(formatoHora(t), X(t), h - 5);
  }
  ctx.strokeStyle = "#383835";
  ctx.beginPath(); ctx.moveTo(m.izq, Y(0)); ctx.lineTo(w - m.der, Y(0)); ctx.stroke();
  ctx.beginPath();
  historial.forEach((p, i) => i ? ctx.lineTo(X(p.t), Y(p.bpm)) : ctx.moveTo(X(p.t), Y(p.bpm)));
  ctx.strokeStyle = "#3987e5"; ctx.lineWidth = 2; ctx.lineJoin = "round"; ctx.stroke();
  ctx.lineTo(X(t1), Y(0)); ctx.lineTo(X(t0), Y(0)); ctx.closePath();
  ctx.fillStyle = "rgba(57,135,229,0.14)"; ctx.fill();
}

window.addEventListener("resize", dibujar);
actualizar();
setInterval(actualizar, 1000);
</script>
</body>
</html>
"""


class _ManejadorTablero(BaseHTTPRequestHandler):
    """Sirve la página, el stream MJPEG, los datos JSON y recibe comandos."""

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
                # Stream MJPEG: reenvía el último cuadro ~20 veces por segundo.
                while True:
                    jpeg = self.estado.obtener_jpeg()
                    if jpeg is not None:
                        self.wfile.write(b"--cuadro\r\n")
                        self.send_header("Content-Type", "image/jpeg")
                        self.send_header("Content-Length", str(len(jpeg)))
                        self.end_headers()
                        self.wfile.write(jpeg)
                        self.wfile.write(b"\r\n")
                    time.sleep(1 / 20)
            except (BrokenPipeError, ConnectionResetError):
                pass  # el navegador cerró la pestaña
        else:
            self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802 — nombre requerido por BaseHTTPRequestHandler
        """Recibe comandos de la HMI en /comando y los encola para el pipeline."""
        if self.path != "/comando":
            self.send_error(404)
            return
        try:
            largo = int(self.headers.get("Content-Length", 0))
            comando = json.loads(self.rfile.read(largo) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self.send_error(400)
            return
        self.estado.enviar_comando(comando)
        cuerpo = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, *args) -> None:
        """Silencia el log por consola del servidor HTTP."""


class _ServidorTablero(ThreadingHTTPServer):
    """ThreadingHTTPServer que no imprime tracebacks por desconexiones normales."""

    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        """Ignora los cortes de conexión del navegador; el resto va a consola."""
        import sys

        tipo = sys.exc_info()[0]
        if tipo in (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return
        super().handle_error(request, client_address)


def iniciar_tablero(estado: EstadoTablero, puerto: int = 8000) -> _ServidorTablero:
    """Levanta el servidor del tablero en un hilo demonio y lo devuelve."""
    _ManejadorTablero.estado = estado
    servidor = _ServidorTablero(("0.0.0.0", puerto), _ManejadorTablero)
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    return servidor
