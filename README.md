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
| `--inspeccion` | — | Heurística experimental de nivel de llenado |
| `--dispositivo` | auto | `cpu`, `0` (GPU CUDA), `mps` (Mac) |

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
└── inspeccion.py   → inspección de defectos (experimental / punto de extensión)
videos/             → videos de prueba de líneas de envasado
```

## Entrenar un modelo propio

El modelo preentrenado (COCO) detecta botellas genéricas y funciona muy bien
para contar. Para detectar **defectos** (falta de cápsula, nivel bajo, botella
vacía) hace falta un modelo entrenado con imágenes de **tu línea real**:

1. **Capturar imágenes** de la línea con la cámara definitiva, en las
   condiciones reales de luz. Incluir ejemplos de cada defecto (aunque haya
   que provocarlos a propósito). Unas 200–500 imágenes es un buen comienzo.
2. **Etiquetar** con [Roboflow](https://roboflow.com) (gratis para proyectos
   chicos) con clases como: `botella_ok`, `sin_capsula`, `nivel_bajo`, `vacia`.
3. **Entrenar** un YOLO con esas etiquetas (en Roboflow, Google Colab o local):
   ```bash
   yolo train model=yolov8n.pt data=dataset/data.yaml epochs=100 imgsz=640
   ```
4. **Usar el modelo propio** en este sistema:
   ```bash
   python -m contador_botellas --fuente 0 --modelo runs/detect/train/weights/best.pt --clases 0 1 2 3
   ```

Con eso, cada botella detectada trae su clase (`sin_capsula`, etc.) y el
módulo `inspeccion.py` puede convertirlas en alertas de producción.

## Notas

- Los videos `videos/VID-20260711-WA0023.mp4` y `videos/VID-20260711-WA0024.mp4`
  son material de referencia (tutoriales), no videos de la línea.
- `supervision` está fijado a `<0.30` porque `ByteTrack` se muda al paquete
  [`trackers`](https://github.com/roboflow/trackers) a partir de esa versión.
- En CPU el sistema procesa en tiempo casi real con `yolov8n`; con GPU se puede
  subir a `yolov8s`/`yolov8m` para más precisión.
