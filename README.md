# Contador de botellas para línea de envasado

Sistema de visión por computadora que **detecta, cuenta y mide la velocidad de
producción de botellas** en una línea de envasado, a partir de una cámara web,
un archivo de video o una cámara IP (RTSP).

Construido sobre [Ultralytics YOLO](https://docs.ultralytics.com) (detección) y
[Supervision](https://supervision.roboflow.com) (seguimiento ByteTrack + línea
de conteo).

**Qué hace hoy:**

- Detecta cada botella en el cuadro (modelo YOLO preentrenado, clase `bottle`).
- Le asigna un ID de seguimiento para no contarla dos veces.
- Cuenta cada botella que cruza una línea configurable.
- Calcula la velocidad: botellas/minuto instantánea (ventana deslizante) y promedio.
- Sirve una **HMI táctil web** (`--tablero`): video en vivo con las
  detecciones, contadores grandes, botones INICIAR/DETENER, captura de
  muestras para entrenamiento, prueba de válvula, eventos y gráfico de
  velocidad — pensada para una pantalla táctil junto a la línea, visible
  desde cualquier dispositivo de la red local.
- Comanda una **electroválvula de descarte** por relé USB (`--valvula-puerto`),
  con retardo y duración de soplido configurables; sin hardware funciona en
  modo simulado.
- Guarda un **registro de producción por minuto** en CSV diarios (`registros/`).
- Genera un video anotado y un CSV con las estadísticas por cuadro.

**Hoja de ruta:** detección de defectos (falta de cápsula, nivel de llenado
bajo, botella vacía) entrenando un modelo propio con imágenes de la línea real
(ver [Entrenar un modelo propio](#entrenar-un-modelo-propio)).

---

## Instalación

Requiere Python 3.10 o superior.

```bash
python -m venv venv
source venv/bin/activate        # en Windows: venv\Scripts\activate
pip install -r requirements.txt
```

La primera vez, el modelo `yolov8n.pt` (~6 MB) se descarga automáticamente.

## Uso

**Con un video de este repositorio:**

```bash
python -m contador_botellas \
  --fuente "videos/video_preview_h264.mp4" \
  --confianza 0.25 \
  --posicion-linea 0.25 \
  --salida resultado.mp4 \
  --csv estadisticas.csv
```

**Con la cámara web** (índice 0) y ventana en vivo:

```bash
python -m contador_botellas --fuente 0 --mostrar
```

**Con tablero de control web** (recomendado para producción):

```bash
python -m contador_botellas --fuente 0 --tablero
```

Al arrancar imprime las direcciones del tablero, por ejemplo:

```
Tablero de control disponible en:
  → http://localhost:8000   (en esta computadora)
  → http://192.168.1.34:8000   (desde otra compu o celular en la misma red)
```

El tablero muestra el video en vivo con las detecciones, botellas contadas,
velocidad actual y promedio, el estado de la línea (PRODUCIENDO / SIN
PRODUCCIÓN) y un gráfico de la velocidad de los últimos 15 minutos, con vista
de tabla. Además, salvo que se pase `--sin-registro`, se guarda un CSV por día
en `registros/` con la producción minuto a minuto — sirve como histórico de
turnos.

Para salir: tecla `q` (o `Esc`) sobre la ventana de video, o `Ctrl+C` en la
consola.

**Con una cámara IP:**

```bash
python -m contador_botellas --fuente "rtsp://usuario:clave@192.168.1.50:554/stream" --mostrar
```

Al terminar imprime un resumen:

```
===== RESUMEN =====
Botellas contadas : 23
Velocidad promedio: 114.2 botellas/min
Duración procesada: 12.6 s
```

### Opciones principales

| Opción | Por defecto | Descripción |
|---|---|---|
| `--fuente` | (obligatoria) | Cámara (`0`), archivo `.mp4` o URL `rtsp://` |
| `--modelo` | `yolov8n.pt` | Modelo YOLO; usar el propio cuando esté entrenado |
| `--confianza` | `0.3` | Confianza mínima de detección |
| `--linea` | `vertical` | Orientación de la línea de conteo |
| `--posicion-linea` | `0.5` | Posición de la línea (fracción del cuadro, 0 a 1) |
| `--ventana-velocidad` | `30` | Segundos para la velocidad instantánea |
| `--salida` | — | Video anotado de salida |
| `--csv` | — | CSV con estadísticas por cuadro |
| `--mostrar` | — | Ventana en vivo (tecla `q` para salir) |
| `--tablero` | — | HMI táctil web en la red local |
| `--puerto` | `8000` | Puerto del tablero web |
| `--registro` | `registros` | Carpeta de los CSV diarios por minuto |
| `--sin-registro` | — | No guardar el registro por minuto |
| `--resolucion` | `1280x720` | Resolución pedida a la cámara web |
| `--tamano-inferencia` | `640` | Tamaño de imagen para la red (480/416 = más fluido en CPU) |
| `--iniciar-detenido` | — | Arrancar en pausa; se inicia desde la HMI |
| `--dataset` | `dataset` | Carpeta de las muestras capturadas desde la HMI |
| `--clases-defecto` | — | Clases del modelo propio que disparan el descarte |
| `--valvula-puerto` | — | Puerto serie del relé (ej. `COM3`); sin él, modo simulado |
| `--valvula-retardo` | `500` | ms entre el cruce de línea y el soplido |
| `--valvula-duracion` | `300` | ms que dura el soplido |
| `--valvula-protocolo` | `arduino` | `arduino` (bytes '1'/'0') o `lcus` (relé LCUS-1/2) |
| `--inspeccion` | — | Heurística experimental de nivel de llenado |
| `--dispositivo` | auto | `cpu`, `0` (GPU CUDA), `mps` (Mac) |

### La HMI (pantalla táctil)

Con `--tablero`, la interfaz web tiene botones grandes pensados para tocar:

- **▶ INICIAR / ⏹ DETENER DETECCIÓN**: arranca o pausa el conteo (el video
  sigue en vivo). Con `--iniciar-detenido` el sistema arranca en pausa.
- **📷 MUESTRA OK / ⚠️ MUESTRA DEFECTO**: guarda el cuadro actual y el recorte
  de cada botella en `dataset/ok/` o `dataset/defecto/`. Así se junta el
  material para entrenar el modelo de defectos directamente desde la línea:
  cuando pase una botella sin cápsula o mal llenada, tocá MUESTRA DEFECTO.
- **💨 PROBAR VÁLVULA**: dispara un pulso de la válvula para verificar el
  conexionado.
- **Eventos**: cada descarte, muestra o cambio de estado queda listado con su hora.

Para pantalla completa en la PC táctil: abrir el navegador con `F11`, o crear
un acceso directo de Chrome/Edge con `--kiosk http://localhost:8000`.

### La válvula de descarte

La forma más simple de comandar la electroválvula desde Windows es un **relé
USB**: un Arduino (u otro micro) con módulo relé, o un relé USB tipo LCUS-1.
Se configura con `--valvula-puerto COM3` (ver el número de puerto en el
Administrador de dispositivos de Windows). El flujo es:

1. El modelo detecta una botella con clase de defecto (`--clases-defecto`) o
   la heurística de inspección la marca.
2. Cuando esa botella **cruza la línea de conteo**, se programa el soplido:
   espera `--valvula-retardo` ms (el tiempo de viaje hasta la válvula, a
   calibrar en la línea) y activa la salida `--valvula-duracion` ms.
3. Cada descarte queda registrado como evento en la HMI.

Sketch de Arduino de ejemplo (protocolo `arduino`, relé en el pin 7):

```cpp
void setup() { Serial.begin(9600); pinMode(7, OUTPUT); }
void loop() {
  if (Serial.available()) {
    char c = Serial.read();
    digitalWrite(7, c == '1' ? HIGH : LOW);
  }
}
```

Sin `--valvula-puerto`, la válvula queda en **modo simulado**: los descartes
se registran como eventos (ideal para probar la lógica antes de armar el
hardware).

### Si la cámara se ve entrecortada o en baja resolución

- La resolución se pide con `--resolucion 1280x720` (por defecto). Si la
  cámara no la soporta, queda en el modo más cercano.
- La fluidez depende de la velocidad de detección: en una PC sin GPU, probá
  `--tamano-inferencia 480` (o `416`) — acelera mucho con muy poca pérdida de
  precisión cuando las botellas se ven grandes.
- La lectura de la cámara corre en un hilo propio: la imagen nunca queda
  atrasada; si la PC no llega a procesar todos los cuadros, descarta los
  viejos y muestra siempre el actual.

### Consejo: dónde poner la línea de conteo

Ubicá la línea en una zona donde las botellas se vean **completas y sin
obstáculos** (evitá sectores donde la maquinaria las tape). En los videos de
ejemplo, `--posicion-linea 0.25` funciona bien porque el centro del cuadro
está ocupado por la llenadora.

## Estructura del proyecto

```
contador_botellas/
├── __main__.py     → CLI (python -m contador_botellas)
├── contador.py     → pipeline: tracking + línea de conteo + anotación
├── detector.py     → detección YOLO → sv.Detections
├── velocidad.py    → botellas/min (ventana deslizante y promedio)
├── tablero.py      → HMI táctil web (video en vivo, botones, eventos, gráfico)
├── captura.py      → hilo de captura de cámara (resolución, sin retraso)
├── salidas.py      → válvula de descarte por relé USB (o modo simulado)
├── clasificador.py → entrenamiento y clasificación de defectos en el equipo
├── registro.py     → CSV diarios de producción por minuto
└── inspeccion.py   → inspección de defectos (experimental / punto de extensión)
videos/             → videos de prueba de líneas de envasado
```

## Detección de defectos: entrenar desde la HMI

El sistema aprende a distinguir **tus** botellas directamente en el equipo,
sin servicios externos. El detector encuentra cada botella; un clasificador
entrenado con tus muestras decide si es `ok` o qué defecto tiene (falta de
cápsula, sin etiqueta, botella distinta, nivel bajo...).

**Flujo completo desde la pantalla:**

1. **Capturar muestras**: con una botella buena pasando frente a la cámara,
   tocá **MUESTRA OK** varias veces (guarda el recorte de cada botella en
   `dataset/ok/`). Después pasá la botella con el defecto y tocá
   **MUESTRA DEFECTO** — te pregunta el nombre (`sin_capsula`, `sin_etiqueta`,
   `botella_distinta`, o el que quieras) y guarda en esa carpeta. Podés crear
   tantos tipos de defecto como necesites.
   - ⚠ El botón guarda el recorte de **todas** las botellas visibles en ese
     momento: capturá con un solo tipo de botella frente a la cámara.
   - Mínimo **10 recortes por clase** (el sistema lo exige); con 30–50 por
     clase funciona mucho mejor. Variá posición, ángulo y luz.
2. **Entrenar**: tocá **🧠 ENTRENAR MODELO**. La detección se pausa, el
   entrenamiento corre en el equipo (unos minutos en CPU) y el avance se ve
   en Eventos. Al terminar, el modelo queda guardado en
   `modelos/clasificador.pt` y **se activa solo**.
3. **Inspeccionar**: tocá INICIAR DETECCIÓN. Cada botella que pasa se
   clasifica; toda clase distinta de `ok` se marca en la imagen, suma al
   contador de **defectos descartados** y dispara la válvula al cruzar la
   línea.

El modelo queda en `modelos/` y se carga automáticamente en los próximos
arranques. Para re-entrenar con más muestras, tocá el botón de nuevo (dice
RE-ENTRENAR). Si el equipo tiene internet, el entrenamiento parte de un
modelo preentrenado (mejor con pocas muestras); sin internet entrena desde
cero (juntá más muestras en ese caso).

**Defectos muy finos** (etiqueta apenas torcida, milímetros de nivel de
llenado): son alcanzables con el mismo flujo pero exigen más muestras (100+
por clase), cámara fija bien posicionada e iluminación constante — como en
los equipos industriales, la luz estable es el 80% del éxito.

## Notas

- Los videos `videos/VID-20260711-WA0023.mp4` y `videos/VID-20260711-WA0024.mp4`
  son material de referencia (tutoriales), no videos de la línea.
- `supervision` está fijado a `<0.30` porque `ByteTrack` se muda al paquete
  [`trackers`](https://github.com/roboflow/trackers) a partir de esa versión.
- En CPU el sistema procesa en tiempo casi real con `yolov8n`; con GPU se puede
  subir a `yolov8s`/`yolov8m` para más precisión.
