"""Captura de video en un hilo separado para cámaras y streams en vivo.

Leer la cámara en su propio hilo evita dos problemas típicos:
- el video "entrecortado": el hilo principal procesa (YOLO) más lento de lo
  que la cámara produce cuadros, y si se lee de forma directa los cuadros se
  acumulan en el buffer y la imagen queda cada vez más atrasada;
- la baja resolución: OpenCV abre las webcams a 640x480 salvo que se pida
  otra cosa; acá se configura resolución y formato MJPG (necesario para que
  muchas webcams entreguen 30 fps en HD).
"""

import threading

import cv2
import numpy as np


def configurar_camara(captura: cv2.VideoCapture, ancho: int, alto: int) -> None:
    """Pide a la webcam resolución `ancho`x`alto`, formato MJPG y 30 fps.

    El orden importa: primero el FOURCC y después la resolución; si la cámara
    no soporta el modo pedido se queda con el más cercano que tenga.
    """
    captura.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    captura.set(cv2.CAP_PROP_FRAME_WIDTH, ancho)
    captura.set(cv2.CAP_PROP_FRAME_HEIGHT, alto)
    captura.set(cv2.CAP_PROP_FPS, 30)


class CapturaEnVivo:
    """Lee la fuente continuamente y expone siempre el último cuadro.

    Así el pipeline procesa al ritmo que puede y la imagen nunca queda
    atrasada: los cuadros que no llegan a procesarse se descartan.
    """

    def __init__(self, captura: cv2.VideoCapture) -> None:
        """Arranca el hilo lector sobre una captura ya abierta."""
        self._captura = captura
        self._lock = threading.Lock()
        self._cuadro: np.ndarray | None = None
        self._ok = True
        self._hilo = threading.Thread(target=self._leer_continuo, daemon=True)
        self._hilo.start()

    def _leer_continuo(self) -> None:
        """Bucle del hilo: lee sin parar y guarda solo el último cuadro."""
        while self._ok:
            ok, cuadro = self._captura.read()
            if not ok:
                self._ok = False
                break
            with self._lock:
                self._cuadro = cuadro

    def leer(self) -> tuple[bool, np.ndarray | None]:
        """Devuelve una copia del último cuadro disponible (o False si terminó)."""
        with self._lock:
            if self._cuadro is None:
                return self._ok, None
            return True, self._cuadro.copy()

    def liberar(self) -> None:
        """Detiene el hilo lector y libera la cámara."""
        self._ok = False
        self._hilo.join(timeout=2)
        self._captura.release()
