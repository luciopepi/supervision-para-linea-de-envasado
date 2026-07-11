"""Pipeline principal: detección + seguimiento + conteo + velocidad."""

import csv
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import supervision as sv

from .detector import DetectorBotellas
from .inspeccion import InspectorBotellas
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
    estadísticas en CSV y un resumen final.
    """

    def __init__(
        self,
        detector: DetectorBotellas,
        linea: ConfiguracionLinea,
        ventana_velocidad: float = 30.0,
        inspector: InspectorBotellas | None = None,
    ) -> None:
        """Guarda los componentes del pipeline; el estado se crea en `procesar`."""
        self.detector = detector
        self.linea = linea
        self.ventana_velocidad = ventana_velocidad
        self.inspector = inspector

    def procesar(
        self,
        fuente: str,
        ruta_salida: str | None = None,
        ruta_csv: str | None = None,
        mostrar: bool = False,
        max_cuadros: int | None = None,
    ) -> dict[str, float]:
        """Procesa la fuente cuadro a cuadro y devuelve el resumen final.

        El resumen contiene `total`, `bpm_promedio` y `duracion_s`.
        """
        captura = self._abrir_fuente(fuente)
        fps = captura.get(cv2.CAP_PROP_FPS) or 30.0
        ancho = int(captura.get(cv2.CAP_PROP_FRAME_WIDTH))
        alto = int(captura.get(cv2.CAP_PROP_FRAME_HEIGHT))

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

        numero_cuadro = 0
        try:
            while True:
                ok, cuadro = captura.read()
                if not ok:
                    break
                numero_cuadro += 1
                if max_cuadros and numero_cuadro > max_cuadros:
                    break
                instante = numero_cuadro / fps

                detecciones = self.detector.detectar(cuadro)
                detecciones = rastreador.update_with_detections(detecciones)
                entrantes, salientes = zona.trigger(detecciones)
                cruces = int(np.sum(entrantes)) + int(np.sum(salientes))
                velocidad.registrar_cruces(cruces, instante)

                alertas: dict[int, str] = {}
                if self.inspector is not None:
                    for resultado in self.inspector.inspeccionar(cuadro, detecciones):
                        if resultado.alerta:
                            alertas[resultado.tracker_id] = resultado.alerta

                cuadro = self._anotar(
                    cuadro,
                    detecciones,
                    zona,
                    velocidad,
                    alertas,
                    anotador_cajas,
                    anotador_etiquetas,
                    anotador_trazas,
                    anotador_linea,
                )

                if escritor is not None:
                    escritor.write(cuadro)
                if escritor_csv is not None:
                    escritor_csv.writerow(
                        [
                            f"{instante:.2f}",
                            velocidad.total,
                            len(detecciones),
                            f"{velocidad.botellas_por_minuto():.1f}",
                            f"{velocidad.promedio_botellas_por_minuto():.1f}",
                        ]
                    )
                if mostrar:
                    cv2.imshow("Contador de botellas", cuadro)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
        finally:
            captura.release()
            if escritor is not None:
                escritor.release()
            if archivo_csv is not None:
                archivo_csv.close()
            if mostrar:
                cv2.destroyAllWindows()

        return {
            "total": float(velocidad.total),
            "bpm_promedio": velocidad.promedio_botellas_por_minuto(),
            "duracion_s": numero_cuadro / fps,
        }

    @staticmethod
    def _abrir_fuente(fuente: str) -> cv2.VideoCapture:
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
        else:
            if not fuente.startswith(("rtsp://", "http://", "https://")):
                if not Path(fuente).exists():
                    raise FileNotFoundError(f"No existe el archivo de video: {fuente}")
            captura = cv2.VideoCapture(fuente)
        if not captura.isOpened():
            raise RuntimeError(f"No se pudo abrir la fuente de video: {fuente}")
        return captura

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
                    texto += f" ⚠{alerta}"
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
