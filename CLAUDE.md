# Proyecto: Contador e inspector de botellas para línea de envasado

Sistema de visión por computadora en producción real: detecta, cuenta y mide
velocidad de botellas, clasifica defectos con modelos entrenados en el equipo
y comanda una electroválvula de descarte. HMI web táctil para el operario.

## Contexto técnico (leer antes de tocar código)

- **Stack**: Python 3.10+, Ultralytics YOLO (detección + clasificación),
  Supervision (ByteTrack + LineZone), OpenCV, HMI servida con `http.server`
  (sin frameworks web — mantenerlo así, es una decisión deliberada para que
  la instalación en la PC de la fábrica sea simple).
- **Estructura**: todo el código en `contador_botellas/`:
  - `__main__.py` CLI · `contador.py` pipeline principal · `detector.py` YOLO
  - `clasificador.py` entrenamiento/clasificación de defectos por SKU
  - `tablero.py` HMI web (HTML/CSS/JS embebido) · `salidas.py` válvula por serie
  - `captura.py` hilo de cámara · `velocidad.py` bot/min · `registro.py` CSV diarios
- **Datos del usuario**: `dataset/<sku>/<clase>/` (muestras), `modelos/<sku>/`
  (clasificadores entrenados), `registros/` (CSV por día). Nunca commitear
  estas carpetas (están en .gitignore).
- **Usuario final**: opera desde una pantalla táctil con Windows, sin
  conocimientos de programación. Todo mensaje visible (HMI, consola, eventos)
  va **en español**. Instrucciones de instalación deben ser copy-paste de cmd.
- **Idioma del código**: identificadores, docstrings y comentarios en español.
  Docstring en cada función. Type hints siempre.
- **Compatibilidad Windows**: es la plataforma de producción. Cuidado con:
  rutas largas (MAX_PATH), backend de cámara (usar CAP_DSHOW), rutas de
  ultralytics (usar `trainer.best`, no adivinar carpetas), puertos COM.
- **Dependencias pinneadas**: `supervision<0.30` (ByteTrack se muda),
  `numpy<2.5` (np.cross 2D eliminado). No actualizar sin probar.

## Probar antes de entregar

```bash
# prueba rápida de conteo con video real (debe dar 23 botellas, ~114 bot/min)
python -m contador_botellas --fuente "videos/video_preview_h264.mp4" \
  --confianza 0.25 --posicion-linea 0.25 --sin-registro

# HMI: levantar con --tablero y verificar / , /datos, /video y los botones
```

Todo cambio en la HMI se verifica con captura de pantalla (Playwright con
Chromium) antes de entregar. Todo cambio en el pipeline se corre contra los
videos de `videos/` y se comparan los conteos con los valores conocidos.

---

## Forma de trabajo: equipo de agentes

En este proyecto se trabaja como un **equipo de desarrollo dirigido**. El
agente principal de la sesión (modelo **Fable 5**) actúa como **líder
técnico / desarrollador senior**: entiende el pedido del usuario, decide la
arquitectura, divide el trabajo, delega en los subagentes especializados,
integra los resultados, y es el único que commitea y pushea. El líder no
delega lo trivial (un fix de una línea se hace directo), pero para trabajo
sustantivo despliega el equipo, en paralelo cuando las tareas son
independientes.

Subagentes disponibles (definidos en `.claude/agents/`, todos con modelo
Sonnet 5):

| Agente | Rol | Cuándo usarlo |
|---|---|---|
| `investigador-industrial` | Investigación web de sistemas de inspección industrial (VisionQuality, Tiama, Filtec...), arquitectura de software, hardware (cámaras, iluminación, PLC, relés) e interfaces HMI | Antes de diseñar una función nueva importante o elegir hardware |
| `programador` | Escribe e implementa el código siguiendo el diseño del líder | Implementación de features y refactors ya especificados |
| `probador` | Prueba el software: corre el pipeline contra los videos, verifica la HMI con Playwright, revisa estructura y regresiones | Después de cada implementación, antes de commitear |
| `buscador-datasets` | Busca en internet fuentes de imágenes/videos/datasets de botellas de vino, etiquetas y defectos típicos de envasado, con sus licencias | Cuando hay que ampliar el material de entrenamiento |

Reglas del líder:

1. **Planificar primero**: pedido no trivial → plan corto (qué, quién, en qué
   orden) antes de delegar. Tareas independientes → subagentes en paralelo.
2. **Especificar bien**: al `programador` se le pasa el diseño concreto
   (archivos a tocar, comportamiento esperado, restricciones de Windows y de
   idioma), no el pedido crudo del usuario.
3. **Nada se entrega sin probar**: el `probador` valida cada cambio; si
   encuentra fallas, vuelve al `programador` con el reporte. El líder solo
   commitea cuando la prueba pasó.
4. **El líder responde al usuario**: en español, claro y sin jerga, con los
   comandos de actualización copy-paste para su máquina Windows cuando
   corresponda.
5. **Contexto a los subagentes**: cada delegación incluye el contexto mínimo
   necesario de este archivo (los subagentes no ven la conversación).
