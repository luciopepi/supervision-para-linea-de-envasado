"""Detección de botellas con YOLO (ultralytics) devolviendo `sv.Detections`."""

import numpy as np
import supervision as sv

# Índice de la clase "bottle" en el dataset COCO con el que vienen
# preentrenados los modelos YOLO oficiales.
CLASE_BOTELLA_COCO = 39


class DetectorBotellas:
    """Envuelve un modelo YOLO y filtra únicamente las botellas.

    Con el modelo preentrenado (COCO) se filtra la clase `bottle`. Cuando en el
    futuro se entrene un modelo propio (por ejemplo con clases `botella_ok`,
    `sin_capsula`, `nivel_bajo`), pasar la ruta del modelo y la lista de clases
    a considerar en `clases`.
    """

    def __init__(
        self,
        ruta_modelo: str = "yolov8n.pt",
        confianza: float = 0.3,
        clases: list[int] | None = None,
        dispositivo: str | None = None,
    ) -> None:
        """Carga el modelo YOLO y guarda los parámetros de inferencia."""
        from ultralytics import YOLO  # import perezoso: tarda en cargar

        self.modelo = YOLO(ruta_modelo)
        self.confianza = confianza
        self.clases = clases if clases is not None else [CLASE_BOTELLA_COCO]
        self.dispositivo = dispositivo

    @property
    def nombres_clases(self) -> dict[int, str]:
        """Mapa id de clase → nombre según el modelo cargado."""
        return self.modelo.names

    def detectar(self, cuadro: np.ndarray) -> sv.Detections:
        """Detecta botellas en un cuadro BGR y devuelve `sv.Detections` filtradas."""
        resultado = self.modelo(
            cuadro,
            conf=self.confianza,
            classes=self.clases,
            device=self.dispositivo,
            verbose=False,
        )[0]
        return sv.Detections.from_ultralytics(resultado)
