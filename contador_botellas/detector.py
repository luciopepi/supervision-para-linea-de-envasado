"""Detección de botellas con YOLO (ultralytics) devolviendo `sv.Detections`."""

import numpy as np
import supervision as sv

from .partes import CLASE_BOTELLA

# Índice de la clase "bottle" en el dataset COCO con el que vienen
# preentrenados los modelos YOLO oficiales.
CLASE_BOTELLA_COCO = 39

# Umbral bajo con el que se corre la red cuando el resultado alimenta al
# seguidor: ByteTrack usa las detecciones dudosas (confianza entre 0.1 y el
# umbral del usuario) en su segunda asociación para sostener el rastro de
# botellas borrosas u ocluidas. Filtrarlas antes rompe ese mecanismo y hace
# perder conteos (botellas desenfocadas cerca de la línea).
UMBRAL_SEGUIMIENTO = 0.1


class DetectorBotellas:
    """Envuelve un modelo YOLO y filtra únicamente las botellas.

    Con el modelo preentrenado (COCO) se filtra la clase `bottle`. Cuando en el
    futuro se entrene un modelo propio (por ejemplo con clases `botella_ok`,
    `sin_capsula`, `nivel_bajo`), pasar la ruta del modelo y la lista de clases
    a considerar en `clases`.

    Si no se pasa `clases` explícitamente y el modelo cargado tiene la clase
    `botella` (nombre exacto del futuro modelo propio de partes, ver
    `partes.py`), se usan TODAS las clases del modelo sin filtro: ese modelo
    necesita ver también `tapa`, `etiqueta_frente`, `etiqueta_dorso`, `caja` y
    `separador` para que `ContadorBotellas` arme sus auditores. Con el modelo
    COCO de hoy (sin la clase `botella`) se sigue filtrando solo `bottle`,
    exactamente como hasta ahora.
    """

    def __init__(
        self,
        ruta_modelo: str = "yolov8n.pt",
        confianza: float = 0.3,
        clases: list[int] | None = None,
        dispositivo: str | None = None,
        tamano_inferencia: int = 640,
    ) -> None:
        """Carga el modelo YOLO y guarda los parámetros de inferencia.

        `tamano_inferencia` es el lado mayor al que se reescala la imagen para
        la red: 640 es el estándar; 480 o 416 aceleran mucho en CPU con poca
        pérdida de precisión cuando las botellas se ven grandes en el cuadro.
        """
        from ultralytics import YOLO  # import perezoso: tarda en cargar

        self.modelo = YOLO(ruta_modelo)
        self.dispositivo = dispositivo
        self.tamano_inferencia = tamano_inferencia
        self.confianza = confianza
        if clases is not None:
            self.clases = clases
        elif CLASE_BOTELLA in self.modelo.names.values():
            # Modelo propio de partes: sin filtro, se necesitan todas sus
            # clases (botella + tapa/etiqueta/caja/separador).
            self.clases = None
        else:
            self.clases = [CLASE_BOTELLA_COCO]

    @property
    def nombres_clases(self) -> dict[int, str]:
        """Mapa id de clase → nombre según el modelo cargado."""
        return self.modelo.names

    @property
    def ids_por_nombre(self) -> dict[str, int]:
        """Mapa inverso de `nombres_clases`: nombre de clase → id de clase."""
        return {nombre: id_clase for id_clase, nombre in self.nombres_clases.items()}

    def detectar(self, cuadro: np.ndarray, umbral: float | None = None) -> sv.Detections:
        """Detecta botellas en un cuadro BGR y devuelve `sv.Detections` filtradas.

        Sin `umbral` se usa la confianza configurada por el usuario (para
        capturas de muestras y detecciones puntuales).
        """
        resultado = self.modelo(
            cuadro,
            conf=self.confianza if umbral is None else umbral,
            classes=self.clases,
            device=self.dispositivo,
            imgsz=self.tamano_inferencia,
            verbose=False,
        )[0]
        return sv.Detections.from_ultralytics(resultado)

    def detectar_para_seguimiento(self, cuadro: np.ndarray) -> sv.Detections:
        """Detecta con umbral bajo para alimentar a ByteTrack.

        Devuelve también las detecciones dudosas (>= `UMBRAL_SEGUIMIENTO`);
        el seguidor decide cuáles sostienen un rastro real. La confianza del
        usuario se aplica en el seguidor (`track_activation_threshold`), no acá.
        """
        return self.detectar(cuadro, umbral=UMBRAL_SEGUIMIENTO)
