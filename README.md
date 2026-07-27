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
  desde cualquier dispositivo de la red local. Al tocar INICIAR DETECCIÓN se
  abre una lista táctil para elegir el producto (SKU) que va a correr.
- Comanda una **electroválvula de descarte** por relé USB (`--valvula-puerto`),
  con retardo y duración de soplido configurables; sin hardware funciona en
  modo simulado.
- Guarda un **registro de producción por minuto** en CSV diarios (`registros/`).
- Genera un video anotado y un CSV con las estadísticas por cuadro.

- **Detecta defectos aprendiendo de tus propias botellas** (falta de cápsula,
  sin etiqueta, botella distinta...): se capturan muestras y se entrena el
  modelo desde la propia HMI, sin servicios externos (ver
  [Detección de defectos](#detección-de-defectos-entrenar-desde-la-hmi)).

---

## Instalación en Windows (la PC de la planta)

Pensado para que el operario no toque nunca una consola.

1. Copiar toda esta carpeta a la computadora (por ejemplo a `C:\contador`).
   Sirve un pendrive: no hace falta que la máquina tenga el proyecto de antes.
2. Tener instalado [Python 3.10 o superior](https://www.python.org/downloads/).
   **Al instalarlo, tildar "Add Python to PATH".**
3. Doble clic en **`INSTALAR.bat`**. Prepara todo y deja el ícono
   **"Contador de Botellas"** en el Escritorio (tarda unos minutos la primera
   vez: descarga las librerías de visión).
4. Doble clic en el ícono del Escritorio. Se abre la aplicación y **el tablero
   aparece solo en el navegador**.

De ahí en más, todo se configura desde el **engranaje ⚙** arriba a la derecha
de la pantalla: cámara, modelo, resolución, tiempos de la válvula. No hace
falta volver a escribir comandos.

> La ventana negra que queda abierta es el motor de la aplicación: hay que
> dejarla ahí mientras se use. Cerrarla cierra el sistema.

Para llevar el sistema a otra computadora (por ejemplo la pantalla táctil de
la línea), se copia la carpeta entera y se repiten los pasos 2 a 4. Conviene
copiar también `modelos/` (los modelos entrenados) y `configuracion.json`.

## Instalación manual (Linux/Mac, o para desarrollar)

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

Todos los flags son opcionales: sin ninguno, el sistema arranca con lo que
diga `configuracion.json` (lo que el operario dejó puesto desde la pantalla).

| Opción | Por defecto | Descripción |
|---|---|---|
| `--fuente` | `0` (o la del archivo) | Cámara (`0`), archivo `.mp4`, URL `rtsp://` o `http://` de un celular |
| `--modelo` | `yolov8n.pt` | Modelo YOLO; usar el propio cuando esté entrenado |
| `--confianza` | `0.25` | Confianza mínima de detección |
| `--linea` | `vertical` | Orientación de la línea de conteo |
| `--posicion-linea` | `0.25` | Posición de la línea (fracción del cuadro, 0 a 1) |
| `--abrir-navegador` | — | Abre el tablero en el navegador al arrancar (implica `--tablero`); es lo que usa el ícono del Escritorio |
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
| `--detecciones` | `detecciones` | Carpeta de las fotos y el CSV de auditoría de cada defecto detectado |
| `--sin-detecciones` | — | No guardar fotos de defectos para auditoría |
| `--modo` | `linea` | `linea` (contar botellas cruzando la línea) o `caja` (contar botellas por caja; ver [Detección de partes y modo caja](#detección-de-partes-y-modo-caja-en-preparación)) |
| `--botellas-por-caja` | `6` | Botellas que debe traer cada caja completa (solo `--modo caja`) |
| `--config` | `configuracion.json` | Archivo con los ajustes editables desde la HMI (ver [Pantalla de configuración](#pantalla-de-configuración)). Precedencia: valores por defecto < archivo < flag escrito a mano. Si no existe, se crea al arrancar |
| `--clases-defecto` | — | Clases del modelo propio que disparan el descarte |
| `--valvula-puerto` | — | Puerto serie del relé (ej. `COM3`); sin él, modo simulado |
| `--valvula-retardo` | `500` | ms entre el cruce de línea y el soplido |
| `--valvula-duracion` | `300` | ms que dura el soplido |
| `--valvula-protocolo` | `arduino` | `arduino` (bytes '1'/'0') o `lcus` (relé LCUS-1/2) |
| `--inspeccion` | — | Heurística experimental de nivel de llenado |
| `--dispositivo` | auto | `cpu`, `0` (GPU CUDA), `mps` (Mac) |

### La HMI (pantalla táctil)

Con `--tablero`, la interfaz web tiene botones grandes pensados para tocar:

- **▶ INICIAR / ⏹ DETENER DETECCIÓN**: al tocar INICIAR se abre una lista
  táctil con los productos (SKU) conocidos — nombre, muestras por clase y si
  ya tienen modelo entrenado — para elegir con cuál arrancar; también se
  puede cargar uno nuevo con **➕ NUEVO PRODUCTO**. DETENER pausa el conteo
  directo (el video sigue en vivo). Con `--iniciar-detenido` el sistema
  arranca en pausa.
- **📷 MUESTRA OK / ⚠️ MUESTRA DEFECTO**: guarda el cuadro actual y el recorte
  de cada botella en `dataset/<clase>/`. MUESTRA DEFECTO pregunta el nombre
  del defecto (`sin_capsula`, `sin_etiqueta`, `botella_distinta`, ...).
- **🧠 ENTRENAR MODELO**: entrena el clasificador de defectos con las muestras
  capturadas, en el propio equipo (ver
  [Detección de defectos](#detección-de-defectos-entrenar-desde-la-hmi)).
- **💨 PROBAR VÁLVULA**: dispara un pulso de la válvula para verificar el
  conexionado.
- **⚙ CONFIGURACIÓN**: abre la pantalla táctil de ajustes (ver
  [Pantalla de configuración](#pantalla-de-configuración)).
- **Eventos**: cada descarte, muestra o cambio de estado queda listado con su hora.

Para pantalla completa en la PC táctil: abrir el navegador con `F11`, o crear
un acceso directo de Chrome/Edge con `--kiosk http://localhost:8000`.

### Pantalla de configuración

Se abre con el **engranaje ⚙ de arriba a la derecha** (siempre visible) o con
el botón **⚙ CONFIGURACIÓN** de la botonera. Todo se ajusta con botones
táctiles grandes (`−`/`+`, opciones fijas, o teclado en pantalla para las
rutas), sin tocar nunca el `cmd`. Vienen en dos grupos:

**Ajustes de producción** — se aplican *en caliente*, sin reiniciar:

- **Posición de la línea de conteo** — se ve en vivo en el video: cada toque
  mueve la línea dibujada al instante.
- **Orientación de la línea de conteo** (vertical/horizontal).
- **Confianza de detección**.
- **Tamaño de imagen para la red** (416/480/640/960 — más chico = más
  fluido en CPU).
- **Retardo del soplido** y **Duración del soplido** (ms) de la válvula de
  descarte.
- **Botellas por caja** (solo tiene efecto en modo caja).
- **Calidad del video en pantalla** (calidad JPEG del stream de la HMI: más
  baja = más fluido con una red Wi-Fi floja).

**Equipo** — se guardan al instante pero toman efecto **al reiniciar** la
aplicación (la pantalla los marca con un cartelito `al reiniciar`), porque la
cámara, el modelo y el servidor web se montan una sola vez al arrancar:

- **Cámara o video**: `0` para la webcam de la PC (o `1`, `2` si hay varias),
  la dirección `http://…` de un celular usado como cámara, o la ruta de un
  `.mp4`.
- **Modelo de detección**: la ruta del `.pt` (por ejemplo
  `modelos\detector_partes.pt`).
- **Resolución de la cámara**, **Modo de trabajo** (línea o cajas), **Puerto
  del tablero web** y **Puerto COM de la válvula** (vacío = simulada).

Todo queda guardado en el archivo `--config` (por defecto
`configuracion.json`, junto al programa), así que sobrevive al reinicio.
**Regla de precedencia** al arrancar: valores por defecto < lo que diga el
archivo < un flag escrito a mano en esa corrida. En la práctica: el operario
ajusta desde la pantalla y eso queda; y si alguna vez hace falta una prueba
puntual sin tocar nada, `--modelo otro.pt` sigue ganando por esa única vez.

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

### Auditoría de detecciones

Cada vez que una botella defectuosa cruza la línea y se descarta, el sistema
guarda evidencia fotográfica en `detecciones/<AAAA-MM-DD>/` (salvo que se
pase `--sin-detecciones`): el recorte de esa botella y el cuadro completo,
como `bot<id>_<defecto>_<hora>.jpg` y `bot<id>_<defecto>_<hora>_cuadro.jpg`.
Junto a las fotos queda un `detecciones.csv` del día con una fila por
descarte: `hora, sku, botella, defecto, total_sesion, bpm, foto_recorte,
foto_cuadro`. Sirve para revisar más tarde, mirando las fotos, si el modelo
decidió bien o si conviene capturar más muestras de ese defecto. La carpeta
se configura con `--detecciones` y un error de escritura en disco solo se
avisa por consola, sin frenar la línea.

### Dónde queda todo guardado y cuánto ocupa

Todo vive en la carpeta del programa (por ejemplo `C:\contador`):

| Carpeta | Contenido | Tamaño aproximado |
|---|---|---|
| `registros/` | Un CSV por día, una fila por minuto | ~5 KB por día (nada) |
| `dataset/<sku>/<clase>/` | Fotos JPG de las muestras | recorte ~30 KB, cuadro ~200 KB; 50 muestras ≈ 10 MB |
| `modelos/<sku>/` | El modelo entrenado del SKU | ~3–10 MB por SKU |
| `detecciones/<AAAA-MM-DD>/` | Fotos y CSV de auditoría de cada defecto descartado | ~230 KB por defecto detectado |

No se guardan videos (solo si pedís `--salida`). Los CSV se descargan desde
la HMI con **⬇ DESCARGAR CSV**, que lista los días disponibles y baja el que
elijas — también desde otra computadora de la red. Y siempre podés copiar las
carpetas directamente con el explorador de Windows a un pendrive o disco.

### Si detecta las botellas pero no las tapas ni las etiquetas

Casi siempre es que quedó cargado el **modelo de fábrica** (`yolov8n.pt`),
que solo conoce la clase "botella". El modelo propio de partes hay que
elegirlo: **⚙ → "Modelo de detección"** → `modelos\detector_partes.pt`, y
volver a abrir la aplicación (ese ajuste se lee al arrancar).

El sistema ayuda a darse cuenta: al arrancar imprime en la consola qué
modelo cargó y cuántas clases tiene, y si encuentra un detector entrenado en
`modelos/` mientras corre con el de fábrica, deja un aviso en la consola y en
los eventos de la pantalla. En el primer arranque, si ya hay un
`modelos/detector_partes.pt`, lo toma solo.

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
├── configuracion.py → ajustes editables desde la HMI (persisten en configuracion.json)
├── tablero.py      → HMI táctil web (video en vivo, botones, eventos, gráfico)
├── captura.py      → hilo de captura de cámara (resolución, sin retraso)
├── salidas.py      → válvula de descarte por relé USB (o modo simulado)
├── clasificador.py → entrenamiento y clasificación de defectos en el equipo
├── detecciones.py  → fotos y CSV de auditoría de cada defecto descartado
├── registro.py     → CSV diarios de producción por minuto
├── inspeccion.py   → inspección de defectos (experimental / punto de extensión)
├── partes.py       → clases de partes de botella/caja para el futuro detector propio
└── fotogramas.py   → extrae fotogramas de videos/ para armar el dataset de detección
INSTALAR.bat        → instalación en Windows + ícono en el Escritorio
CONTADOR.bat        → arranca la aplicación y abre el tablero en el navegador
contador.ico        → ícono del acceso directo (se regenera con herramientas/)
herramientas/       → utilidades del proyecto (por ahora, el generador del ícono)
videos/             → videos de prueba de líneas de envasado
```

## Detección de defectos: entrenar desde la HMI

El sistema aprende a distinguir **tus** botellas directamente en el equipo,
sin servicios externos. El detector encuentra cada botella; un clasificador
entrenado con tus muestras decide si es `ok` o qué defecto tiene (falta de
cápsula, sin etiqueta, botella distinta, nivel bajo...).

**Un modelo por producto (SKU):** el chip **SKU** del encabezado muestra el
producto activo; tocándolo abre la misma lista táctil de productos que
INICIAR DETECCIÓN (sin arrancar el conteo) para cambiar a otro o crear uno
nuevo (por ejemplo `vinotinto750`, `aceite1l`). Cada SKU tiene sus propias
muestras en
`dataset/<sku>/<clase>/` y su propio modelo en `modelos/<sku>/clasificador.pt`,
que se carga automáticamente al cambiar de producto — el cambio de trabajo es
tocar el chip y elegir el SKU. También se puede arrancar directo con
`--sku nombre`.

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

## Detección de partes y modo caja (en preparación)

El detector de hoy solo distingue "botella" (modelo COCO). Está preparada la
lógica para un futuro **modelo propio de partes**, entrenado en el equipo con
estas clases exactas: `botella`, `tapa`, `etiqueta_frente`, `etiqueta_dorso`,
`caja`, `separador`. Todavía no existe ese modelo — esto documenta cómo se va
a comportar el sistema en cuanto se cargue uno con `--modelo` (con el modelo
COCO actual, nada de esto se activa y el sistema funciona exactamente igual
que hoy).

**Modo línea (por defecto) con el modelo de partes**: además de contar
botellas, si el modelo detecta `tapa` y/o `etiqueta_frente`/`etiqueta_dorso`,
cada botella que pasa se audita a lo largo de varios cuadros; si nunca se le
vio una tapa se agrega la alerta `sin_tapa`, y si nunca se le vio ninguna de
las dos etiquetas (frente o dorso — una botella normal solo muestra una,
según cómo rota), `sin_etiqueta`. Estas alertas se integran al mismo flujo
de descarte, foto de auditoría y evento que ya usan el clasificador de
defectos y `--clases-defecto`.

**Modo caja** (`--modo caja`): pensado para una segunda cámara cenital que
mira las cajas ya armadas, antes de cerrarlas. Cuenta los **cierres**
(tapa, corcho o cápsula) que ve dentro de cada caja —no las botellas—
porque vista desde arriba y con poca luz una botella de vidrio oscuro casi
no se distingue, pero el cierre queda mirando a la cámara. Así, con una sola
cuenta, detecta dos defectos: si a la caja le falta una botella o si una
botella va sin tapar, en ambos casos hay un cierre de menos. Usa la mediana
de varias lecturas mientras la caja pasa y también verifica el separador de
cartón; al cruzar la línea genera un evento
`Caja #12: 6/6 botellas tapadas ✓` o
`Caja #12: 5/6 botellas tapadas — INCOMPLETA (falta una botella o una tapa)`,
y si está incompleta o le falta el separador, guarda foto de auditoría y
descarta con la válvula, igual que una botella defectuosa. Requiere un
modelo con la clase `caja`; sin ella el sistema avisa el error al arrancar
en vez de intentar contar algo que no existe. La cantidad esperada por caja
se configura con `--botellas-por-caja` (por defecto 6):

```bash
python -m contador_botellas --fuente 0 --modelo modelo_partes.pt \
  --modo caja --botellas-por-caja 6 --tablero
```

**Herramienta de fotogramas**: primer paso para entrenar ese modelo propio es
juntar imágenes etiquetadas. `contador_botellas/fotogramas.py` extrae cuadros
de los videos de la línea, descartando los repetidos (útil porque la cinta
parada genera cientos de cuadros casi idénticos):

```bash
python -m contador_botellas.fotogramas --videos videos --salida dataset_deteccion/imagenes --por-segundo 2
```

| Opción | Por defecto | Descripción |
|---|---|---|
| `--modo` | `linea` | `linea` o `caja` (ver arriba) |
| `--botellas-por-caja` | `6` | Botellas esperadas por caja completa (solo `--modo caja`) |

## Notas

- Los videos `videos/VID-20260711-WA0023.mp4` y `videos/VID-20260711-WA0024.mp4`
  son material de referencia (tutoriales), no videos de la línea.
- `supervision` está fijado a `<0.30` porque `ByteTrack` se muda al paquete
  [`trackers`](https://github.com/roboflow/trackers) a partir de esa versión.
- En CPU el sistema procesa en tiempo casi real con `yolov8n`; con GPU se puede
  subir a `yolov8s`/`yolov8m` para más precisión.
