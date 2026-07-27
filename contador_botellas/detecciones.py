"""Registro en disco de cada botella descartada, para auditar el modelo.

Cuando la línea descarta una botella por defecto, además de accionar la
válvula conviene guardar evidencia: la foto de esa botella y un renglón en
un CSV. Así, más adelante, se puede revisar si el modelo decidió bien (o si
hay que capturar más muestras de ese defecto).
"""

import csv
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np


class RegistroDetecciones:
    """Guarda foto y fila de auditoría de cada botella descartada.

    Organiza todo por día (`detecciones/<AAAA-MM-DD>/`) para que las
    carpetas no crezcan sin límite y sea fácil llevarse solo el día que
    interesa. Nunca debe frenar el pipeline: cualquier error de escritura
    se avisa por consola y se descarta.
    """

    def __init__(self, carpeta: str = "detecciones") -> None:
        """Guarda la carpeta base; no crea nada en disco hasta el primer registro."""
        self.carpeta = Path(carpeta)

    def registrar(
        self,
        cuadro: np.ndarray,
        caja: np.ndarray,
        sku: str,
        botella_id: int,
        defecto: str,
        total: int,
        bpm: float,
    ) -> None:
        """Guarda las fotos de un descarte y agrega su fila al CSV del día.

        `caja` es la caja `[x1, y1, x2, y2]` de la botella en `cuadro`
        (cuadro crudo, sin anotar). Se guardan hasta dos JPG: el recorte de
        la caja (si al clipearla a los bordes del cuadro queda de al menos
        10x10 px) y el cuadro completo. Atrapa cualquier excepción para que
        un problema de disco no tumbe el conteo de la línea.
        """
        try:
            self._registrar(cuadro, caja, sku, botella_id, defecto, total, bpm)
        except Exception as error:
            print(f"Aviso: no se pudo guardar la foto de auditoría en disco: {error}")

    def _registrar(
        self,
        cuadro: np.ndarray,
        caja: np.ndarray,
        sku: str,
        botella_id: int,
        defecto: str,
        total: int,
        bpm: float,
    ) -> None:
        """Cuerpo real de `registrar` (separado para envolverlo en try/except)."""
        ahora = datetime.now()
        carpeta_dia = self.carpeta / ahora.strftime("%Y-%m-%d")
        carpeta_dia.mkdir(parents=True, exist_ok=True)

        marca = ahora.strftime("%H%M%S_%f")
        defecto_seguro = self._nombre_archivo_valido(defecto)

        nombre_recorte = ""
        alto_cuadro, ancho_cuadro = cuadro.shape[:2]
        x1, y1, x2, y2 = (int(v) for v in caja)
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(ancho_cuadro, x2), min(alto_cuadro, y2)
        if x2 - x1 > 10 and y2 - y1 > 10:
            nombre_recorte = f"bot{botella_id}_{defecto_seguro}_{marca}.jpg"
            cv2.imwrite(str(carpeta_dia / nombre_recorte), cuadro[y1:y2, x1:x2])

        nombre_cuadro = f"bot{botella_id}_{defecto_seguro}_{marca}_cuadro.jpg"
        cv2.imwrite(str(carpeta_dia / nombre_cuadro), cuadro)

        ruta_csv = carpeta_dia / "detecciones.csv"
        es_nuevo = not ruta_csv.exists()
        with open(ruta_csv, "a", newline="", encoding="utf-8") as archivo:
            escritor = csv.writer(archivo)
            if es_nuevo:
                escritor.writerow(
                    [
                        "hora", "sku", "botella", "defecto", "total_sesion", "bpm",
                        "foto_recorte", "foto_cuadro",
                    ]
                )
            escritor.writerow(
                [
                    ahora.strftime("%H:%M:%S"), sku, botella_id, defecto, total,
                    f"{bpm:.1f}", nombre_recorte, nombre_cuadro,
                ]
            )

    @staticmethod
    def _nombre_archivo_valido(nombre: str) -> str:
        """Sanea un nombre para usarlo en un archivo: solo alfanuméricos, `-` y `_`."""
        limpio = "".join(c for c in nombre if c.isalnum() or c in "_-")
        return limpio or "defecto"
