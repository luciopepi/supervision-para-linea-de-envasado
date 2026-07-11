"""Inspección de calidad de botellas (experimental).

Este módulo define el punto de extensión para la detección de defectos:
falta de cápsula, nivel de llenado bajo o botella vacía.

**Estado actual**: la forma robusta de resolver esto es entrenar un modelo
propio con imágenes de la línea real, etiquetadas con clases como
`botella_ok`, `sin_capsula`, `nivel_bajo`, `vacia` (ver README, sección
"Entrenar un modelo propio"). Una vez entrenado, se usa directamente con
`--modelo` y `--clases`, y este módulo puede mapear cada clase a una alerta.

Mientras tanto se incluye una heurística simple de nivel de llenado por
análisis de brillo del cuello de la botella, útil solo como demostración con
cámara fija y botellas de vidrio traslúcido: NO usarla para decisiones de
producción sin validarla con imágenes de la línea real.
"""

from dataclasses import dataclass

import numpy as np
import supervision as sv


@dataclass
class ResultadoInspeccion:
    """Resultado de inspección para una botella detectada."""

    tracker_id: int
    alerta: str | None  # None = sin anomalía detectada


class InspectorBotellas:
    """Inspección heurística de botellas a partir del recorte de cada detección.

    `fraccion_cuello` define qué parte superior de la caja se considera cuello.
    `umbral_diferencia` es la diferencia mínima de brillo entre cuello y cuerpo
    para considerar que el líquido no llega al nivel esperado (en botellas
    traslúcidas el vidrio vacío es notablemente más claro/brillante que el
    lleno). Ambos valores deben calibrarse con la cámara real.
    """

    def __init__(self, fraccion_cuello: float = 0.25, umbral_diferencia: float = 60.0) -> None:
        """Guarda los parámetros de calibración de la heurística."""
        self.fraccion_cuello = fraccion_cuello
        self.umbral_diferencia = umbral_diferencia

    def inspeccionar(
        self, cuadro: np.ndarray, detecciones: sv.Detections
    ) -> list[ResultadoInspeccion]:
        """Evalúa cada botella detectada y devuelve alertas heurísticas.

        Compara el brillo medio del tercio inferior (cuerpo, normalmente lleno
        de líquido oscuro) contra la zona del cuello: si el cuerpo es mucho más
        brillante que lo esperado respecto del cuello, se marca `nivel_bajo?`.
        Es una demostración y debe validarse antes de confiar en ella.
        """
        resultados: list[ResultadoInspeccion] = []
        if detecciones.tracker_id is None:
            return resultados
        gris = cuadro.mean(axis=2) if cuadro.ndim == 3 else cuadro
        for caja, tracker_id in zip(detecciones.xyxy, detecciones.tracker_id):
            x1, y1, x2, y2 = (int(v) for v in caja)
            alto = y2 - y1
            if alto < 20:  # recorte demasiado chico para medir algo útil
                resultados.append(ResultadoInspeccion(int(tracker_id), None))
                continue
            cuello = gris[y1 : y1 + int(alto * self.fraccion_cuello), x1:x2]
            cuerpo = gris[y2 - int(alto * 0.33) : y2, x1:x2]
            if cuello.size == 0 or cuerpo.size == 0:
                resultados.append(ResultadoInspeccion(int(tracker_id), None))
                continue
            diferencia = float(cuerpo.mean()) - float(cuello.mean())
            alerta = "nivel_bajo?" if diferencia > self.umbral_diferencia else None
            resultados.append(ResultadoInspeccion(int(tracker_id), alerta))
        return resultados
