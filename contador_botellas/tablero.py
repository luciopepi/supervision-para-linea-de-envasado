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
  html { height: 100%; }
  /* `min-height` (y no `height`) para que la página CREZCA y se pueda
     desplazar cuando no entra: en una pantalla baja o en un celular, con
     altura fija la fila de botones —y con ella CONFIGURACIÓN— quedaba
     cortada abajo, fuera de alcance y sin barra de scroll. */
  body { background: var(--plano); color: var(--tinta); display: flex; flex-direction: column;
         min-height: 100%; font: 15px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif;
         padding: 12px; gap: 12px; user-select: none; }
  header { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
  header h1 { font-size: 20px; font-weight: 700; letter-spacing: .02em; }
  .chip { display: inline-flex; align-items: center; gap: 8px; padding: 8px 16px;
          border: 1px solid var(--borde); border-radius: 999px; font-weight: 700; font-size: 15px; }
  .chip .punto { width: 11px; height: 11px; border-radius: 50%; }
  /* El SKU activo decide a qué producto van las muestras y qué modelo
     inspecciona: el operario tiene que poder leerlo de un vistazo. */
  #chip-sku { font-size: 24px; padding: 12px 24px; border-color: rgba(57,135,229,0.55); }
  #chip-sku .punto { width: 14px; height: 14px; }
  #chip-sku b { color: var(--serie); text-transform: uppercase; letter-spacing: .02em; }
  .hora { color: var(--tinta-2); font-size: 22px; font-weight: 600;
          font-variant-numeric: tabular-nums; }
  /* Reloj y engranaje viajan juntos: al angostarse la pantalla el encabezado
     se parte en dos filas y así el engranaje sigue arriba a la derecha en vez
     de quedar descolgado a la izquierda. */
  .header-derecha { margin-left: auto; display: flex; align-items: center; gap: 12px; }
  /* Engranaje siempre visible arriba a la derecha: el botón CONFIGURACIÓN de
     la botonera puede quedar más abajo que el borde de la pantalla. */
  .boton-chip { cursor: pointer; background: var(--superficie-2); color: var(--tinta);
                font-size: 24px; min-height: 48px; min-width: 58px; justify-content: center;
                font-family: inherit; }
  .boton-chip:active { filter: brightness(1.3); transform: scale(0.96); }
  .rejilla { display: grid; grid-template-columns: minmax(0, 3fr) minmax(230px, 1fr);
             gap: 12px; flex: 1; min-height: 0; }
  @media (max-width: 900px) { .rejilla { grid-template-columns: 1fr; } }
  .tarjeta { background: var(--superficie); border: 1px solid var(--borde);
             border-radius: 12px; padding: 12px; min-height: 0; }
  /* La imagen va posicionada de forma absoluta dentro de la tarjeta: así su
     tamaño natural nunca agranda la fila de la grilla ni pisa los botones. */
  .video { position: relative; min-height: 240px; }
  .video img { position: absolute; inset: 12px; width: calc(100% - 24px);
               height: calc(100% - 24px); object-fit: contain;
               border-radius: 8px; background: #000; }
  .fichas { display: grid; gap: 12px; grid-auto-rows: 1fr; }
  .ficha { display: flex; flex-direction: column; justify-content: center; }
  .ficha .rotulo { color: var(--tinta-2); font-size: 13px; letter-spacing: .07em;
                   text-transform: uppercase; margin-bottom: 2px; }
  .ficha .valor { font-size: clamp(34px, 4.5vw, 52px); font-weight: 800; line-height: 1.05; }
  .ficha .unidad { color: var(--tenue); font-size: 15px; font-weight: 500; }
  .ficha.defecto .valor { color: var(--critico); }
  .botonera { display: grid; grid-template-columns: 1.4fr repeat(5, 1fr); gap: 12px; }
  @media (max-width: 1100px) { .botonera { grid-template-columns: 1fr 1fr; } }
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
  .ev-entrenamiento { background: #2a1a4a; color: #c8b3ff; }
  .ev-caja     { background: #142a1a; color: #8adf9d; }
  .alterna { background: var(--superficie-2); border: 1px solid var(--borde);
             color: var(--tinta-2); border-radius: 8px; padding: 6px 14px; cursor: pointer;
             font-size: 13px; font-weight: 700; text-decoration: none; float: right; }
  #aviso { position: fixed; left: 50%; bottom: 110px; transform: translateX(-50%);
    background: #262624; border: 1px solid var(--borde); border-radius: 10px;
    padding: 12px 22px; font-size: 17px; font-weight: 700; display: none; z-index: 20; }
  /* Modal táctil de selección de SKU: se abre al iniciar la detección o al
     tocar el chip de SKU, para que el operario elija el producto de una
     lista en vez de escribirlo. */
  .overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.72); z-index: 30;
             display: flex; align-items: center; justify-content: center; padding: 20px; }
  .modal-tarjeta { background: var(--superficie); border: 1px solid var(--borde);
                   border-radius: 14px; padding: 20px; width: min(560px, 100%);
                   max-height: 90vh; display: flex; flex-direction: column; gap: 14px; }
  .modal-tarjeta h2 { font-size: 19px; font-weight: 800; }
  .lista-skus { overflow-y: auto; display: flex; flex-direction: column; gap: 10px;
                min-height: 80px; }
  .tarjeta-sku { display: flex; align-items: center; justify-content: space-between;
                 gap: 12px; min-height: 64px; padding: 10px 14px; cursor: pointer;
                 border: 2px solid var(--borde); border-radius: 10px;
                 background: var(--superficie-2); }
  .tarjeta-sku:active { filter: brightness(1.25); }
  .tarjeta-sku.activo { border-color: var(--serie); }
  .tarjeta-sku .nombre-sku { font-size: 20px; font-weight: 800; text-transform: uppercase; }
  .tarjeta-sku .clases-sku { color: var(--tinta-2); font-size: 13px; margin-top: 2px; }
  .etiqueta-modelo { font-size: 12px; font-weight: 700; padding: 4px 10px;
                     border-radius: 999px; white-space: nowrap; }
  .modal-botones { display: grid; grid-template-columns: 2fr 1fr; gap: 12px; }
  .modal-botones button.grande { min-height: 64px; }
  /* Modal de configuración: una fila por parámetro, con botones táctiles
     grandes (mínimo 56px de lado) para no ajustes por error con el dedo. */
  .lista-config { overflow-y: auto; display: flex; flex-direction: column; gap: 2px;
                  max-height: 62vh; }
  /* `flex-wrap` + un ancho mínimo para la etiqueta: en las filas con varias
     opciones (resolución, tamaño de imagen) los botones bajan a un renglón
     propio en vez de aplastar el texto hasta partirlo letra por letra. */
  .fila-config { display: flex; align-items: center; justify-content: space-between;
                 flex-wrap: wrap; gap: 10px 14px; padding: 12px 4px;
                 border-bottom: 1px solid var(--grilla); }
  .fila-config:last-child { border-bottom: none; }
  .etiqueta-config { font-size: 15px; font-weight: 600; color: var(--tinta-2);
                      flex: 1 1 190px; min-width: 170px; line-height: 1.25; }
  .controles-config { display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
                       justify-content: flex-end; flex: 0 0 auto; }
  .btn-paso-config { width: 56px; height: 56px; border-radius: 10px; border: 1px solid var(--borde);
    background: var(--superficie-2); color: var(--tinta); font-size: 28px; font-weight: 800;
    cursor: pointer; display: flex; align-items: center; justify-content: center; font: inherit; }
  .btn-paso-config:active { filter: brightness(1.3); transform: scale(0.95); }
  .valor-config { min-width: 70px; text-align: center; font-size: 22px; font-weight: 800;
                   font-variant-numeric: tabular-nums; }
  .btn-opcion-config { min-height: 56px; min-width: 56px; padding: 8px 16px; border-radius: 10px;
    border: 2px solid var(--borde); background: var(--superficie-2); color: var(--tinta-2);
    font-size: 15px; font-weight: 800; cursor: pointer; font: inherit; }
  .btn-opcion-config.activo { border-color: var(--serie); color: var(--tinta);
                               background: rgba(57,135,229,0.18); }
  /* Los campos de texto (cámara, modelo, COM) se editan con el teclado en
     pantalla: el botón muestra el valor actual y hay que poder leerlo entero. */
  .btn-texto-config { max-width: 320px; overflow: hidden; text-overflow: ellipsis;
                      white-space: nowrap; font-size: 14px; text-align: center; }
  .marca-reinicio { display: inline-block; margin-left: 8px; padding: 2px 8px; font-size: 11px;
                    font-weight: 700; border-radius: 999px; background: #4a3a14; color: #ffd98a;
                    vertical-align: middle; }
  .nota-config { color: var(--tenue); font-size: 13px; line-height: 1.35; }
  .titulo-grupo-config { padding: 16px 4px 6px; font-size: 12px; font-weight: 800;
                         letter-spacing: .08em; text-transform: uppercase; color: var(--tenue);
                         border-bottom: 1px solid var(--grilla); }
  /* Pantallas bajas (panel táctil de 1024x600, notebooks de 1366x768): se
     compacta todo para que la fila de botones entre sin desplazar la página;
     el gráfico y los eventos quedan abajo, a un dedo de distancia. */
  @media (max-height: 780px) {
    header h1 { font-size: 17px; }
    .chip { padding: 6px 12px; font-size: 14px; }
    #chip-sku { font-size: 19px; padding: 8px 16px; }
    .hora { font-size: 18px; }
    .boton-chip { font-size: 21px; min-height: 42px; min-width: 52px; }
    .video { min-height: 170px; }
    .ficha .valor { font-size: clamp(26px, 3.4vw, 40px); }
    button.grande { min-height: 62px; font-size: 17px; }
    .abajo { min-height: 170px; }
  }
</style>
</head>
<body>
<header>
  <h1>LÍNEA DE ENVASADO</h1>
  <span class="chip" id="chip-estado"><span class="punto" style="background:var(--tenue)"></span><span id="chip-texto">EN PAUSA</span></span>
  <span class="chip" id="chip-sku" style="cursor:pointer" title="Tocar para cambiar de producto">
    <span class="punto" style="background:var(--serie)"></span><span>SKU: <b id="sku-nombre">general</b> ✎</span></span>
  <span class="chip" id="chip-modo" style="display:none"><span class="punto" style="background:var(--alerta)"></span><span>MODO: CAJAS</span></span>
  <span class="chip" id="chip-valvula" style="display:none"><span class="punto" style="background:var(--alerta)"></span><span>VÁLVULA SIMULADA</span></span>
  <span class="chip" id="chip-fps" style="display:none" title="Cuadros por segundo que alcanza a analizar esta computadora"><span class="punto"></span><span id="fps-texto">—</span></span>
  <span class="header-derecha">
    <span class="hora" id="hora">—</span>
    <button class="chip boton-chip" id="btn-config-header" title="Configuración del sistema">⚙</button>
  </span>
</header>

<div class="rejilla">
  <div class="tarjeta video"><img src="/video" alt="Video en vivo de la línea con detecciones"></div>
  <div class="fichas">
    <div class="tarjeta ficha">
      <div class="rotulo" id="rotulo-total">Botellas</div>
      <div class="valor" id="total">0</div>
    </div>
    <div class="tarjeta ficha">
      <div class="rotulo">Velocidad</div>
      <div class="valor" id="bpm">0 <span class="unidad">bot/min</span></div>
    </div>
    <div class="tarjeta ficha defecto">
      <div class="rotulo" id="rotulo-defectos">Defectos descartados</div>
      <div class="valor" id="defectos">0</div>
    </div>
    <div class="tarjeta ficha">
      <div class="rotulo">En cámara</div>
      <div class="valor" id="en-cuadro">0</div>
    </div>
  </div>
</div>

<div id="muestras-linea" style="color:var(--tinta-2); font-size:14px; padding:0 4px;">
  Muestras del SKU: —</div>

<div class="botonera">
  <button class="grande iniciar" id="btn-marcha">
    <span class="icono" id="icono-marcha">▶</span><span id="texto-marcha">INICIAR DETECCIÓN</span>
  </button>
  <button class="grande" id="btn-ok"><span class="icono">📷</span>MUESTRA OK</button>
  <button class="grande" id="btn-defecto"><span class="icono">⚠️</span>MUESTRA DEFECTO</button>
  <button class="grande" id="btn-entrenar"><span class="icono">🧠</span><span id="texto-entrenar">ENTRENAR MODELO</span></button>
  <button class="grande" id="btn-valvula"><span class="icono">💨</span>PROBAR VÁLVULA</button>
  <button class="grande" id="btn-config"><span class="icono">⚙</span>CONFIGURACIÓN</button>
</div>

<div class="abajo">
  <div class="tarjeta">
    <h2>Velocidad de producción (botellas/min)
        <button class="alterna" id="btn-csv">⬇ DESCARGAR CSV</button></h2>
    <canvas id="grafico"></canvas>
  </div>
  <div class="tarjeta">
    <h2>Eventos</h2>
    <div class="eventos" id="eventos">—</div>
  </div>
</div>
<div id="aviso"></div>

<div id="modal-sku" class="overlay" style="display:none">
  <div class="modal-tarjeta">
    <h2 id="modal-sku-titulo">¿Qué producto va a correr?</h2>
    <div class="lista-skus" id="lista-skus">—</div>
    <div class="modal-botones">
      <button class="grande" id="btn-sku-nuevo"><span class="icono">➕</span>NUEVO PRODUCTO</button>
      <button class="grande" id="btn-sku-cancelar">CANCELAR</button>
    </div>
  </div>
</div>

<div id="modal-config" class="overlay" style="display:none">
  <div class="modal-tarjeta">
    <h2>Configuración del sistema</h2>
    <div class="nota-config">Cada cambio se guarda solo en el equipo (configuracion.json).
      Los marcados <span class="marca-reinicio">al reiniciar</span> toman efecto la próxima
      vez que abras la aplicación.</div>
    <div class="lista-config" id="lista-config">—</div>
    <div class="modal-botones" style="grid-template-columns: 1fr;">
      <button class="grande" id="btn-config-cerrar">CERRAR</button>
    </div>
  </div>
</div>

<script>
"use strict";
const $ = id => document.getElementById(id);
let historial = [], detectando = false, skus = {}, modoModalSku = "iniciar";

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
  if (detectando) {
    comando({accion: "detener"}, "Detección detenida");
  } else {
    abrirModalSku("iniciar");
  }
});
let ultimaClaseDefecto = "defecto";
$("btn-ok").addEventListener("click", () =>
  comando({accion: "capturar", clase: "ok"}, "Muestra OK guardada"));
$("btn-defecto").addEventListener("click", () => {
  // Permite tipos de defecto propios: sin_capsula, sin_etiqueta, botella_distinta…
  const clase = prompt("Nombre del defecto (una palabra):", ultimaClaseDefecto);
  if (clase === null) return;
  ultimaClaseDefecto = clase.trim() || "defecto";
  comando({accion: "capturar", clase: ultimaClaseDefecto},
          "Muestra de " + ultimaClaseDefecto.toUpperCase() + " guardada");
});
$("btn-entrenar").addEventListener("click", () => {
  if (confirm("¿Entrenar el modelo con las muestras capturadas?\\n" +
              "La detección queda en pausa mientras entrena (varios minutos)."))
    comando({accion: "entrenar"}, "Entrenamiento iniciado");
});
$("btn-valvula").addEventListener("click", () =>
  comando({accion: "probar_valvula"}, "Probando válvula…"));
$("chip-sku").addEventListener("click", () => abrirModalSku("cambiar"));

function abrirModalSku(modo) {
  // "iniciar": elegir producto y arrancar la detección con él.
  // "cambiar": solo activar el producto, sin tocar la detección.
  modoModalSku = modo;
  $("modal-sku-titulo").textContent = modo === "iniciar" ?
    "¿Qué producto va a correr?" : "Cambiar de producto";
  pintarSkusModal();
  $("modal-sku").style.display = "flex";
}

function cerrarModalSku() {
  $("modal-sku").style.display = "none";
}

let ultimoHtmlSkus = null;
function pintarSkusModal() {
  const nombres = Object.keys(skus).sort();
  const contenedor = $("lista-skus");
  if (!nombres.length) {
    contenedor.innerHTML = "<div style='color:var(--tenue)'>Todavía no hay productos cargados.</div>";
    ultimoHtmlSkus = null;
    return;
  }
  const skuActivo = $("sku-nombre").textContent;
  const html = nombres.map(nombre => {
    const info = skus[nombre] || {clases: {}, entrenado: false};
    const partes = Object.entries(info.clases || {}).map(([c, n]) => `${c}: ${n}`);
    const detalle = partes.length ? partes.join(" · ") : "sin muestras todavía";
    const etiqueta = info.entrenado
      ? '<span class="etiqueta-modelo" style="background:rgba(12,163,12,0.18); color:var(--ok)">MODELO ✓</span>'
      : '<span class="etiqueta-modelo" style="background:rgba(137,135,129,0.18); color:var(--tenue)">SIN MODELO</span>';
    const clase = "tarjeta-sku" + (nombre === skuActivo ? " activo" : "");
    return `<div class="${clase}" data-sku="${nombre}">` +
           `<div><div class="nombre-sku">${nombre.toUpperCase()}</div>` +
           `<div class="clases-sku">${detalle}</div></div>${etiqueta}</div>`;
  }).join("");
  // Redibujar solo si algo cambió: reconstruir el DOM en cada tick podría
  // perder un toque que caiga justo en el instante del repintado.
  if (html === ultimoHtmlSkus) return;
  ultimoHtmlSkus = html;
  contenedor.innerHTML = html;
  contenedor.querySelectorAll(".tarjeta-sku").forEach(tarjeta => {
    tarjeta.addEventListener("click", () => elegirSku(tarjeta.dataset.sku));
  });
}

function elegirSku(nombre) {
  cerrarModalSku();
  if (modoModalSku === "iniciar") {
    comando({accion: "iniciar", sku: nombre}, "Iniciando con SKU " + nombre.toUpperCase());
  } else {
    comando({accion: "cambiar_sku", sku: nombre}, "Cambiando a SKU " + nombre.toUpperCase());
  }
}

$("btn-sku-nuevo").addEventListener("click", () => {
  const nombre = prompt("Nombre del producto/SKU nuevo:", "");
  if (nombre === null || !nombre.trim()) return;
  elegirSku(nombre.trim());
});
$("btn-sku-cancelar").addEventListener("click", cerrarModalSku);
$("modal-sku").addEventListener("click", (evento) => {
  if (evento.target.id === "modal-sku") cerrarModalSku();
});

// --- Pantalla de configuración: un parámetro por fila, con botones táctiles
// grandes. Los valores llegan en d.config (ver /datos → "config") y se
// guardan en configuracion.json en el equipo; el operario los toca en
// vivo y ve el efecto en el video (la línea de conteo se dibuja según la
// posición vigente).
const NOMBRE_CONFIG = {
  posicion_linea: "Posición de la línea de conteo",
  orientacion_linea: "Orientación de la línea de conteo",
  confianza: "Confianza de detección",
  tamano_inferencia: "Tamaño de imagen para la red (más chico = más fluido)",
  valvula_retardo_ms: "Retardo del soplido (ms)",
  valvula_duracion_ms: "Duración del soplido (ms)",
  botellas_por_caja: "Botellas por caja",
  calidad_video: "Calidad del video en pantalla",
  // De acá para abajo, los que se leen al arrancar (config.reinicio).
  fuente: "Cámara o video (0 = webcam, o una dirección http:// del celular)",
  modelo: "Modelo de detección (.pt)",
  resolucion_camara: "Resolución de la cámara",
  modo: "Modo de trabajo (línea o cajas)",
  puerto: "Puerto del tablero web",
  valvula_puerto_serie: "Puerto COM de la válvula (vacío = simulada)",
};
const ORDEN_CONFIG = Object.keys(NOMBRE_CONFIG);
// Texto de ayuda del teclado en pantalla, por campo de texto libre.
const AYUDA_TEXTO_CONFIG = {
  fuente: "Cámara o video:\\n  0 = webcam de esta PC (probá 1 o 2 si hay varias)\\n" +
          "  http://192.168.1.50:8080/video = celular con IP Webcam\\n" +
          "  videos\\\\prueba.mp4 = un archivo de video",
  modelo: "Ruta del modelo YOLO (.pt):\\n  modelos\\\\detector_partes.pt = tu modelo entrenado\\n" +
          "  yolov8n.pt = el modelo de fábrica (solo botellas)",
  valvula_puerto_serie: "Puerto COM del relé de la válvula (ej: COM3).\\n" +
                        "Dejalo vacío para trabajar en modo simulado.",
};
let config = {valores: {}, limites: {}, reinicio: []};
let ultimoHtmlConfig = null;

function escaparHtml(texto) {
  // Los valores de texto (rutas, URL) van al HTML: se escapan para que un
  // caracter como < no rompa la pantalla de configuración.
  return String(texto).replace(/[&<>"']/g, c =>
    ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[c]);
}

function formatoValorConfig(clave, valor) {
  // posicion_linea y confianza son fracciones (0-1): dos decimales se leen
  // mejor en la pantalla que "0.30000000000000004".
  if (clave === "posicion_linea" || clave === "confianza") return Number(valor).toFixed(2);
  return String(valor);
}

function pintarConfigModal() {
  const contenedor = $("lista-config");
  const valores = config.valores || {};
  const limites = config.limites || {};
  const reinicio = config.reinicio || [];
  // La lista se parte en dos grupos (lo que se toca en producción y lo que
  // se define una vez en el equipo): con 14 filas, un título cada tanto es
  // lo que hace entender de un vistazo que la lista sigue más abajo.
  let grupoActual = null;
  const html = ORDEN_CONFIG.filter(clave => clave in valores).map(clave => {
    const lim = limites[clave] || {};
    const valor = valores[clave];
    let controles;
    if (lim.texto) {
      const mostrado = String(valor ?? "").trim() || "(vacío)";
      controles = `<button class="btn-opcion-config btn-texto-config" data-clave="${clave}" ` +
                  `data-texto="1">${escaparHtml(mostrado)}</button>`;
    } else if (lim.opciones) {
      // Sirve igual para las opciones de texto (orientación, modo,
      // resolución) y las numéricas (tamaño de imagen).
      controles = lim.opciones.map(opcion => {
        // Mayúsculas solo en las opciones de una palabra (VERTICAL, CAJA):
        // una resolución se lee mejor como "1280x720" que como "1280X720".
        const texto = typeof opcion === "string" && !/[0-9]/.test(opcion)
          ? opcion.toUpperCase() : opcion;
        return `<button class="btn-opcion-config${valor === opcion ? " activo" : ""}" ` +
               `data-clave="${clave}" data-valor="${escaparHtml(opcion)}">` +
               `${escaparHtml(texto)}</button>`;
      }).join("");
    } else {
      controles =
        `<button class="btn-paso-config" data-clave="${clave}" data-signo="-1">−</button>` +
        `<span class="valor-config">${formatoValorConfig(clave, valor)}</span>` +
        `<button class="btn-paso-config" data-clave="${clave}" data-signo="1">+</button>`;
    }
    const esDeEquipo = reinicio.includes(clave);
    const marca = esDeEquipo ? '<span class="marca-reinicio">al reiniciar</span>' : "";
    let titulo = "";
    if (esDeEquipo !== grupoActual) {
      grupoActual = esDeEquipo;
      titulo = `<div class="titulo-grupo-config">` +
               (esDeEquipo ? "Equipo — se aplican al reiniciar" : "Ajustes de producción") +
               `</div>`;
    }
    return titulo + `<div class="fila-config">` +
           `<div class="etiqueta-config">${NOMBRE_CONFIG[clave]}${marca}</div>` +
           `<div class="controles-config">${controles}</div></div>`;
  }).join("");
  // Mismo cuidado que en el modal de SKU: no reconstruir el DOM si no
  // cambió nada, para no perder un toque que caiga justo en el repintado.
  if (html === ultimoHtmlConfig) return;
  ultimoHtmlConfig = html;
  contenedor.innerHTML = html || "<div style='color:var(--tenue)'>Sin datos de configuración todavía.</div>";
  contenedor.querySelectorAll(".btn-paso-config").forEach(boton => {
    boton.addEventListener("click", () => {
      const clave = boton.dataset.clave;
      const lim = (config.limites || {})[clave] || {};
      const actual = Number((config.valores || {})[clave] ?? 0);
      const paso = Number(lim.paso ?? 1);
      let siguiente = actual + Number(boton.dataset.signo) * paso;
      if (lim.min !== undefined) siguiente = Math.max(lim.min, siguiente);
      if (lim.max !== undefined) siguiente = Math.min(lim.max, siguiente);
      siguiente = Math.round(siguiente * 1000) / 1000; // corrige arrastre de coma flotante
      enviarConfig(clave, siguiente);
    });
  });
  contenedor.querySelectorAll(".btn-opcion-config").forEach(boton => {
    boton.addEventListener("click", () => {
      const clave = boton.dataset.clave;
      if (boton.dataset.texto) { editarTextoConfig(clave); return; }
      const opciones = ((config.limites || {})[clave] || {}).opciones || [];
      const esNumerica = typeof opciones[0] === "number";
      enviarConfig(clave, esNumerica ? Number(boton.dataset.valor) : boton.dataset.valor);
    });
  });
}

function editarTextoConfig(clave) {
  // Teclado en pantalla: en la pantalla táctil de Windows, tocar el campo de
  // un prompt levanta el teclado virtual, así que no hace falta un teclado
  // propio dentro de la HMI.
  const actual = String((config.valores || {})[clave] ?? "");
  const ayuda = AYUDA_TEXTO_CONFIG[clave] || NOMBRE_CONFIG[clave];
  const nuevo = prompt(ayuda + "\\n\\nValor actual:", actual);
  if (nuevo === null) return;
  enviarConfig(clave, nuevo.trim());
}

function enviarConfig(clave, valor) {
  comando({accion: "configurar", clave: clave, valor: valor});
}

function abrirModalConfig() {
  pintarConfigModal();
  $("modal-config").style.display = "flex";
}
function cerrarModalConfig() {
  $("modal-config").style.display = "none";
}
$("btn-config").addEventListener("click", abrirModalConfig);
$("btn-config-header").addEventListener("click", abrirModalConfig);
$("btn-config-cerrar").addEventListener("click", cerrarModalConfig);
$("modal-config").addEventListener("click", (evento) => {
  if (evento.target.id === "modal-config") cerrarModalConfig();
});

$("btn-csv").addEventListener("click", async () => {
  // Lista los días con registro y deja elegir cuál descargar.
  try {
    const dias = await (await fetch("/registros")).json();
    if (!dias.length) { avisar("Todavía no hay registros guardados"); return; }
    const eleccion = prompt(
      "Días con registro:\\n" + dias.join("\\n") +
      "\\n\\nEscribí la fecha a descargar (AAAA-MM-DD):", dias[dias.length - 1]);
    if (eleccion === null || !eleccion.trim()) return;
    window.location = "/registro.csv?fecha=" + encodeURIComponent(eleccion.trim());
  } catch (e) { avisar("No se pudo listar los registros"); }
});

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
    skus = d.skus || {};
    if ($("modal-sku").style.display !== "none") pintarSkusModal();
    config = d.config || {valores: {}, limites: {}, reinicio: []};
    if ($("modal-config").style.display !== "none") pintarConfigModal();
    const entrenando = !!d.entrenando;
    $("btn-entrenar").disabled = entrenando;
    $("btn-entrenar").style.opacity = entrenando ? 0.5 : 1;
    $("texto-entrenar").textContent = entrenando ? "ENTRENANDO…" :
      (d.clasificador_activo ? "RE-ENTRENAR" : "ENTRENAR MODELO");
    $("total").textContent = d.total ?? 0;
    $("sku-nombre").textContent = d.sku ?? "general";
    const m = d.muestras || {};
    const partes = Object.entries(m).map(([c, n]) => `${c}: <b>${n}</b>`);
    $("muestras-linea").innerHTML = "Muestras del SKU <b>" + (d.sku ?? "general") +
      "</b>: " + (partes.length ? partes.join(" · ") : "ninguna todavía") +
      " &nbsp;<span style='color:var(--tenue)'>(mínimo 10 por clase para entrenar)</span>";
    // Modo "caja": mismas fichas, otro significado (cajas en vez de botellas
    // sueltas). Se cambia solo el texto de rótulos y unidades, no el HTML.
    const modoCaja = d.modo === "caja";
    $("chip-modo").style.display = modoCaja ? "" : "none";
    $("rotulo-total").textContent = modoCaja ? "Cajas" : "Botellas";
    $("rotulo-defectos").textContent = modoCaja ? "Cajas incompletas" : "Defectos descartados";
    $("bpm").innerHTML = (d.bpm ?? 0).toFixed(0) +
      ' <span class="unidad">' + (modoCaja ? "cajas/min" : "bot/min") + '</span>';
    $("defectos").textContent = (modoCaja ? d.cajas_incompletas : d.defectos) ?? 0;
    $("en-cuadro").textContent = d.en_cuadro ?? 0;
    $("hora").textContent = formatoHora(d.hora ?? Date.now() / 1000);
    const punto = $("chip-estado").querySelector(".punto"), texto = $("chip-texto");
    if (!detectando) { texto.textContent = "EN PAUSA"; punto.style.background = "var(--tenue)"; }
    else if ((d.segundos_sin_cruce ?? 0) < 30) {
      texto.textContent = "PRODUCIENDO"; punto.style.background = "var(--ok)";
    } else { texto.textContent = "SIN PRODUCCIÓN"; punto.style.background = "var(--critico)"; }
    $("chip-valvula").style.display = (d.valvula && d.valvula.simulada) ? "" : "none";
    // Cuadros por segundo: dice si la computadora da abasto. Por debajo de 5
    // el seguidor empieza a perder botellas rápidas, así que se pinta en rojo
    // para que se vea sin tener que interpretar el número.
    const fps = d.cuadros_por_segundo ?? 0;
    $("chip-fps").style.display = (detectando && fps > 0) ? "" : "none";
    if (detectando && fps > 0) {
      $("fps-texto").textContent = fps.toFixed(1) + " cuadros/s";
      const colorFps = fps >= 10 ? "var(--ok)" : (fps >= 5 ? "var(--alerta)" : "var(--critico)");
      $("chip-fps").querySelector(".punto").style.background = colorFps;
    }
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
    carpeta_registro: str | None = None  # carpeta de los CSV diarios

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
        elif self.path.startswith("/registro.csv"):
            self._servir_registro()
        elif self.path == "/registros":
            self._listar_registros()
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

    def _listar_registros(self) -> None:
        """Lista las fechas con registro de producción (endpoint /registros)."""
        from pathlib import Path

        fechas: list[str] = []
        if self.carpeta_registro is not None and Path(self.carpeta_registro).exists():
            fechas = sorted(
                ruta.stem.removeprefix("produccion_")
                for ruta in Path(self.carpeta_registro).glob("produccion_*.csv")
            )
        cuerpo = json.dumps(fechas).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def _servir_registro(self) -> None:
        """Descarga el CSV de producción de una fecha (/registro.csv?fecha=AAAA-MM-DD)."""
        from datetime import date
        from pathlib import Path
        from urllib.parse import parse_qs, urlparse

        if self.carpeta_registro is None:
            self.send_error(404, "El registro esta desactivado (--sin-registro)")
            return
        parametros = parse_qs(urlparse(self.path).query)
        fecha = parametros.get("fecha", [date.today().isoformat()])[0]
        # Solo se aceptan fechas AAAA-MM-DD: evita pedir rutas arbitrarias.
        try:
            fecha = date.fromisoformat(fecha).isoformat()
        except ValueError:
            self.send_error(400, "Fecha invalida: usar AAAA-MM-DD")
            return
        nombre = f"produccion_{fecha}.csv"
        ruta = Path(self.carpeta_registro) / nombre
        if not ruta.exists():
            self.send_error(404, "No hay registro para esa fecha")
            return
        cuerpo = ruta.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{nombre}"')
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

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


def iniciar_tablero(
    estado: EstadoTablero, puerto: int = 8000, carpeta_registro: str | None = None
) -> _ServidorTablero:
    """Levanta el servidor del tablero en un hilo demonio y lo devuelve."""
    _ManejadorTablero.estado = estado
    _ManejadorTablero.carpeta_registro = carpeta_registro
    servidor = _ServidorTablero(("0.0.0.0", puerto), _ManejadorTablero)
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    return servidor
