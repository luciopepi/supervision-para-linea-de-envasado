"""Pipeline principal: detección + seguimiento + conteo + velocidad + descarte."""

import csv
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import supervision as sv

from .captura import CapturaEnVivo, configurar_camara
from .detector import DetectorBotellas
from .inspeccion import InspectorBotellas
from .registro import RegistroProduccion
from .salidas import ValvulaDescarte
from .tablero import EstadoTablero
from .velocidad import EstimadorVelocidad


@dataclass
class ConfiguracionLinea:
    """Ubicación de la línea de conteo dentro del cuadro.

    `orientacion` es "vertical" (botellas que avanzan en horizontal) u
    "horizontal" (botellas que avanzan en vertical). `posicion` es la fracción
    del ancho/alto donde se ubica la línea (0.5 = centro del cuadro).
    """

    orientacion: str = "vertical"
    posicion: float = 0.5

    def crear_zona(self, ancho: int, alto: int) -> sv.LineZone:
        """Construye la `sv.LineZone` para un cuadro de `ancho` x `alto` píxeles."""
        if self.orientacion == "vertical":
            x = int(ancho * self.posicion)
            inicio, fin = sv.Point(x, 0), sv.Point(x, alto)
        else:
            y = int(alto * self.posicion)
            inicio, fin = sv.Point(0, y), sv.Point(ancho, y)
        # El ancla central hace el conteo más estable que las 4 esquinas
        # cuando las botellas van muy juntas o parcialmente ocluidas.
        return sv.LineZone(
            start=inicio, end=fin, triggering_anchors=[sv.Position.CENTER]
        )


class ContadorBotellas:
    """Orquesta el pipeline completo sobre una fuente de video.

    Fuentes soportadas: índice de cámara web ("0", "1"...), ruta a un archivo
    de video o URL de stream (RTSP/HTTP). Produce un video anotado opcional,
    estadísticas en CSV, tablero web, registro por minuto y señal de descarte.
    """

    def __init__(
        self,
        detector: DetectorBotellas,
        linea: ConfiguracionLinea,
        ventana_velocidad: float = 30.0,
        inspector: InspectorBotellas | None = None,
        valvula: ValvulaDescarte | None = None,
        clases_defecto: set[int] | None = None,
        carpeta_muestras: str = "dataset",
    ) -> None:
        """Guarda los componentes del pipeline; el estado se crea en `procesar`."""
        self.detector = detector
        self.linea = linea
        self.ventana_velocidad = ventana_velocidad
        self.inspector = inspector
        self.valvula = valvula
        self.clases_defecto = clases_defecto or set()
        self.carpeta_muestras = Path(carpeta_muestras)

    def procesar(
        self,
        fuente: str,
        ruta_salida: str | None = None,
        ruta_csv: str | None = None,
        mostrar: bool = False,
        max_cuadros: int | None = None,
        estado_tablero: EstadoTablero | None = None,
        registro: RegistroProduccion | None = None,
        resolucion: tuple[int, int] | None = None,
        iniciar_detenido: bool = False,
    ) -> dict[str, float]:
        """Procesa la fuente cuadro a cuadro y devuelve el resumen final.

        El resumen contiene `total`, `bpm_promedio` y `duracion_s`. Si se pasa
        `estado_tablero`, publica cada cuadro anotado, las estadísticas y
        atiende los comandos de la HMI (iniciar/detener, muestras, válvula).
        """
        captura_cruda = self._abrir_fuente(fuente, resolucion)
        # Con cámara o stream el reloj de pared es la referencia de tiempo;
        # con un archivo se usa el tiempo del video para que la velocidad no
        # dependa de lo rápido que procese la computadora.
        en_vivo = fuente.isdigit() or fuente.startswith(("rtsp://", "http://", "https://"))
        fps = captura_cruda.get(cv2.CAP_PROP_FPS) or 30.0
        ancho = int(captura_cruda.get(cv2.CAP_PROP_FRAME_WIDTH))
        alto = int(captura_cruda.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if en_vivo:
            print(f"Cámara abierta a {ancho}x{alto}.")
        # En vivo, un hilo dedicado lee la cámara para que la imagen nunca
        # quede atrasada aunque la detección procese más lento.
        captura = CapturaEnVivo(captura_cruda) if en_vivo else captura_cruda
        inicio = time.monotonic()

        zona = self.linea.crear_zona(ancho, alto)
        rastreador = sv.ByteTrack(frame_rate=int(fps))
        velocidad = EstimadorVelocidad(ventana_segundos=self.ventana_velocidad)

        anotador_cajas = sv.BoxAnnotator(thickness=2)
        anotador_etiquetas = sv.LabelAnnotator(text_scale=0.4, text_padding=4)
        anotador_trazas = sv.TraceAnnotator(thickness=2, trace_length=int(fps))
        anotador_linea = sv.LineZoneAnnotator(
            thickness=2, text_scale=0.6, display_in_count=False, display_out_count=False
        )

        escritor = None
        if ruta_salida:
            codec = cv2.VideoWriter_fourcc(*"mp4v")
            escritor = cv2.VideoWriter(ruta_salida, codec, fps, (ancho, alto))

        archivo_csv = None
        escritor_csv = None
        if ruta_csv:
            archivo_csv = open(ruta_csv, "w", newline="")
            escritor_csv = csv.writer(archivo_csv)
            escritor_csv.writerow(
                ["tiempo_s", "total", "botellas_en_cuadro", "bpm_instantaneo", "bpm_promedio"]
            )

        detectando = not iniciar_detenido
        defectos_total = 0
        descartadas: set[int] = set()  # tracker_ids ya enviados a descarte
        ultimas_detecciones: sv.Detections | None = None
        ultimo_cuadro_crudo: np.ndarray | None = None
        numero_cuadro = 0
        ultimo_cruce: float | None = None
        try:
            while True:
                ok, cuadro = captura.leer() if en_vivo else captura.read()
                if not ok:
                    break
                if cuadro is None:  # la cámara todavía no entregó el primer cuadro
                    time.sleep(0.01)
                    continue
                numero_cuadro += 1
                if max_cuadros and numero_cuadro > max_cuadros:
                    break
                instante = time.monotonic() - inicio if en_vivo else numero_cuadro / fps
                ultimo_cuadro_crudo = cuadro

                if estado_tablero is not None:
                    detectando = self._atender_comandos(
                        estado_tablero,
                        detectando,
                        ultimo_cuadro_crudo,
                        ultimas_detecciones,
                    )

                if detectando:
                    detecciones = self.detector.detectar(cuadro)
                    detecciones = rastreador.update_with_detections(detecciones)
                    ultimas_detecciones = detecciones
                    entrantes, salientes = zona.trigger(detecciones)
                    cruces = int(np.sum(entrantes)) + int(np.sum(salientes))
                    velocidad.registrar_cruces(cruces, instante)
                    if cruces:
                        ultimo_cruce = instante
                    if registro is not None:
                        registro.registrar(
                            cruces, velocidad.total, velocidad.promedio_botellas_por_minuto()
                        )

                    alertas = self._detectar_defectos(cuadro, detecciones)
                    defectos_total += self._descartar_defectuosas(
                        detecciones, entrantes | salientes, alertas, descartadas, estado_tablero
                    )

                    cuadro = self._anotar(
                        cuadro, detecciones, zona, velocidad, alertas,
                        anotador_cajas, anotador_etiquetas, anotador_trazas, anotador_linea,
                    )
                else:
                    ultimas_detecciones = None
                    cuadro = self._anotar_pausa(cuadro)
                    if not en_vivo:
                        # Sin este freno, un archivo en pausa se consumiría a
                        # máxima velocidad y el video se "adelantaría" solo.
                        time.sleep(1.0 / fps)

                if escritor is not None:
                    escritor.write(cuadro)
                if escritor_csv is not None and detectando:
                    escritor_csv.writerow(
                        [
                            f"{instante:.2f}",
                            velocidad.total,
                            len(ultimas_detecciones) if ultimas_detecciones is not None else 0,
                            f"{velocidad.botellas_por_minuto():.1f}",
                            f"{velocidad.promedio_botellas_por_minuto():.1f}",
                        ]
                    )
                if estado_tablero is not None:
                    self._publicar(
                        estado_tablero, cuadro, velocidad, ultimas_detecciones,
                        detectando, defectos_total, instante, ultimo_cruce,
                    )
                if mostrar:
                    cv2.imshow("Contador de botellas", cuadro)
                    tecla = cv2.waitKey(1) & 0xFF
                    if tecla in (ord("q"), ord("Q"), 27):  # q o Esc
                        break
        except KeyboardInterrupt:
            print("\nDetenido con Ctrl+C.")
        finally:
            if en_vivo:
                captura.liberar()
            else:
                captura.release()
            if escritor is not None:
                escritor.release()
            if archivo_csv is not None:
                archivo_csv.close()
            if registro is not None:
                registro.cerrar(velocidad.total, velocidad.promedio_botellas_por_minuto())
            if self.valvula is not None:
                self.valvula.cerrar()
            if mostrar:
                cv2.destroyAllWindows()

        return {
            "total": float(velocidad.total),
            "bpm_promedio": velocidad.promedio_botellas_por_minuto(),
            "duracion_s": numero_cuadro / fps,
            "defectos": float(defectos_total),
        }

    @staticmethod
    def _abrir_fuente(
        fuente: str, resolucion: tuple[int, int] | None = None
    ) -> cv2.VideoCapture:
        """Abre cámara web (índice numérico), archivo de video o URL de stream."""
        if fuente.isdigit():
            print(f"Abriendo cámara {fuente}...")
            # En Windows el backend por defecto (Media Foundation) puede
            # colgarse minutos al abrir la webcam; DirectShow abre al instante.
            if sys.platform == "win32":
                captura = cv2.VideoCapture(int(fuente), cv2.CAP_DSHOW)
                if not captura.isOpened():
                    captura = cv2.VideoCapture(int(fuente))
            else:
                captura = cv2.VideoCapture(int(fuente))
            if captura.isOpened() and resolucion is not None:
                configurar_camara(captura, *resolucion)
        else:
            if not fuente.startswith(("rtsp://", "http://", "https://")):
                if not Path(fuente).exists():
                    raise FileNotFoundError(f"No existe el archivo de video: {fuente}")
            captura = cv2.VideoCapture(fuente)
        if not captura.isOpened():
            raise RuntimeError(f"No se pudo abrir la fuente de video: {fuente}")
        return captura

    def _atender_comandos(
        self,
        estado: EstadoTablero,
        detectando: bool,
        cuadro: np.ndarray | None,
        detecciones: sv.Detections | None,
    ) -> bool:
        """Ejecuta los comandos pendientes de la HMI y devuelve el nuevo estado."""
        for comando in estado.obtener_comandos():
            accion = comando.get("accion")
            if accion == "iniciar":
                detectando = True
                estado.agregar_evento("estado", "Detección iniciada")
            elif accion == "detener":
                detectando = False
                estado.agregar_evento("estado", "Detección detenida")
            elif accion == "capturar" and cuadro is not None:
                clase = str(comando.get("clase", "ok"))
                cantidad = self._guardar_muestras(cuadro, detecciones, clase)
                estado.agregar_evento(
                    "muestra", f"{cantidad} imagen(es) guardada(s) en dataset/{clase}/"
                )
            elif accion == "probar_valvula":
                if self.valvula is not None:
                    self.valvula.probar()
                    modo = "SIMULADA" if self.valvula.simulada else "real"
                    estado.agregar_evento("valvula", f"Prueba de válvula ({modo})")
                else:
                    estado.agregar_evento("valvula", "Válvula no configurada (--valvula-puerto)")
        return detectando

    def _guardar_muestras(
        self, cuadro: np.ndarray, detecciones: sv.Detections | None, clase: str
    ) -> int:
        """Guarda el cuadro completo y el recorte de cada botella en dataset/<clase>/.

        Estas imágenes son la materia prima para entrenar el modelo propio de
        defectos (ver README): se etiquetan y se entrena un YOLO con ellas.
        """
        carpeta = self.carpeta_muestras / clase
        carpeta.mkdir(parents=True, exist_ok=True)
        marca = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        cv2.imwrite(str(carpeta / f"{marca}_cuadro.jpg"), cuadro)
        guardadas = 1
        if detecciones is not None and len(detecciones) > 0:
            alto_cuadro, ancho_cuadro = cuadro.shape[:2]
            for i, caja in enumerate(detecciones.xyxy):
                x1, y1, x2, y2 = (int(v) for v in caja)
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(ancho_cuadro, x2), min(alto_cuadro, y2)
                if x2 - x1 > 10 and y2 - y1 > 10:
                    cv2.imwrite(str(carpeta / f"{marca}_bot{i}.jpg"), cuadro[y1:y2, x1:x2])
                    guardadas += 1
        return guardadas

    def _detectar_defectos(
        self, cuadro: np.ndarray, detecciones: sv.Detections
    ) -> dict[int, str]:
        """Junta alertas de defecto por tracker_id: clases del modelo + heurística."""
        alertas: dict[int, str] = {}
        if self.clases_defecto and detecciones.tracker_id is not None:
            nombres = self.detector.nombres_clases
            for clase_id, tracker_id in zip(detecciones.class_id, detecciones.tracker_id):
                if int(clase_id) in self.clases_defecto:
                    alertas[int(tracker_id)] = str(nombres.get(int(clase_id), clase_id))
        if self.inspector is not None:
            for resultado in self.inspector.inspeccionar(cuadro, detecciones):
                if resultado.alerta:
                    alertas.setdefault(resultado.tracker_id, resultado.alerta)
        return alertas

    def _descartar_defectuosas(
        self,
        detecciones: sv.Detections,
        cruzaron: np.ndarray,
        alertas: dict[int, str],
        descartadas: set[int],
        estado: EstadoTablero | None,
    ) -> int:
        """Activa la válvula para cada botella defectuosa que cruza la línea.

        Devuelve cuántos descartes nuevos hubo. Cada tracker_id se descarta una
        sola vez, aunque siga apareciendo en cuadros siguientes.
        """
        if not alertas or detecciones.tracker_id is None:
            return 0
        nuevos = 0
        for tracker_id, cruzo in zip(detecciones.tracker_id, cruzaron):
            tid = int(tracker_id)
            if cruzo and tid in alertas and tid not in descartadas:
                descartadas.add(tid)
                nuevos += 1
                if self.valvula is not None:
                    self.valvula.descartar()
                if estado is not None:
                    estado.agregar_evento(
                        "descarte", f"Botella #{tid} descartada: {alertas[tid]}"
                    )
        return nuevos

    def _publicar(
        self,
        estado: EstadoTablero,
        cuadro: np.ndarray,
        velocidad: EstimadorVelocidad,
        detecciones: sv.Detections | None,
        detectando: bool,
        defectos_total: int,
        instante: float,
        ultimo_cruce: float | None,
    ) -> None:
        """Publica el cuadro anotado y las estadísticas para el tablero web."""
        ok_jpeg, jpeg = cv2.imencode(".jpg", cuadro, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if not ok_jpeg:
            return
        sin_cruce = 1e9 if ultimo_cruce is None else instante - ultimo_cruce
        valvula_info = None
        if self.valvula is not None:
            valvula_info = {
                "simulada": self.valvula.simulada,
                "activaciones": self.valvula.activaciones,
            }
        estado.publicar(
            jpeg.tobytes(),
            {
                "total": velocidad.total,
                "bpm": round(velocidad.botellas_por_minuto(), 1),
                "bpm_promedio": round(velocidad.promedio_botellas_por_minuto(), 1),
                "en_cuadro": len(detecciones) if detecciones is not None else 0,
                "defectos": defectos_total,
                "detectando": detectando,
                "valvula": valvula_info,
                "segundos_sin_cruce": round(sin_cruce, 1),
                "hora": time.time(),
            },
        )

    @staticmethod
    def _anotar_pausa(cuadro: np.ndarray) -> np.ndarray:
        """Marca visualmente que la detección está en pausa (video sigue en vivo)."""
        capa = cuadro.copy()
        cv2.rectangle(capa, (8, 8), (330, 44), (0, 0, 0), -1)
        cuadro = cv2.addWeighted(capa, 0.55, cuadro, 0.45, 0)
        cv2.putText(
            cuadro, "DETECCION EN PAUSA", (16, 34),
            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (80, 80, 235), 2, cv2.LINE_AA,
        )
        return cuadro

    def _anotar(
        self,
        cuadro: np.ndarray,
        detecciones: sv.Detections,
        zona: sv.LineZone,
        velocidad: EstimadorVelocidad,
        alertas: dict[int, str],
        anotador_cajas: sv.BoxAnnotator,
        anotador_etiquetas: sv.LabelAnnotator,
        anotador_trazas: sv.TraceAnnotator,
        anotador_linea: sv.LineZoneAnnotator,
    ) -> np.ndarray:
        """Dibuja detecciones, línea de conteo y panel de estadísticas sobre el cuadro."""
        etiquetas = []
        if detecciones.tracker_id is not None:
            for tracker_id, conf in zip(detecciones.tracker_id, detecciones.confidence):
                texto = f"#{tracker_id} {conf:.2f}"
                alerta = alertas.get(int(tracker_id))
                if alerta:
                    texto += f" DEFECTO:{alerta}"
                etiquetas.append(texto)

        cuadro = anotador_trazas.annotate(cuadro, detecciones)
        cuadro = anotador_cajas.annotate(cuadro, detecciones)
        if etiquetas:
            cuadro = anotador_etiquetas.annotate(cuadro, detecciones, labels=etiquetas)
        cuadro = anotador_linea.annotate(cuadro, line_counter=zona)

        panel = [
            f"BOTELLAS: {velocidad.total}",
            f"VELOCIDAD: {velocidad.botellas_por_minuto():.1f} bot/min",
            f"PROMEDIO: {velocidad.promedio_botellas_por_minuto():.1f} bot/min",
        ]
        # Panel semitransparente arriba a la izquierda para legibilidad.
        alto_panel = 22 * len(panel) + 16
        capa = cuadro.copy()
        cv2.rectangle(capa, (8, 8), (280, alto_panel), (0, 0, 0), -1)
        cuadro = cv2.addWeighted(capa, 0.55, cuadro, 0.45, 0)
        for i, linea_texto in enumerate(panel):
            cv2.putText(
                cuadro,
                linea_texto,
                (16, 30 + 22 * i),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )
        return cuadro
