"""Registro histórico de producción en archivos CSV diarios."""

import csv
from datetime import datetime
from pathlib import Path


class RegistroProduccion:
    """Anota la producción minuto a minuto en un CSV por día.

    Genera archivos `produccion_AAAA-MM-DD.csv` dentro de `carpeta`, con una
    fila por minuto: hora, botellas de ese minuto, total acumulado de la
    sesión y velocidad promedio. Si el archivo del día ya existe (por ejemplo
    tras reiniciar el programa), sigue agregando filas al final.
    """

    def __init__(self, carpeta: str = "registros") -> None:
        """Crea la carpeta de registros y arranca el minuto en curso."""
        self.carpeta = Path(carpeta)
        self.carpeta.mkdir(parents=True, exist_ok=True)
        self._minuto_actual: str = self._minuto_de(datetime.now())
        self._botellas_minuto: int = 0

    @staticmethod
    def _minuto_de(momento: datetime) -> str:
        """Clave 'AAAA-MM-DD HH:MM' del minuto al que pertenece `momento`."""
        return momento.strftime("%Y-%m-%d %H:%M")

    def registrar(self, cruces: int, total: int, bpm_promedio: float) -> None:
        """Acumula cruces y, al cambiar el minuto, escribe la fila del anterior."""
        ahora = datetime.now()
        minuto = self._minuto_de(ahora)
        if minuto != self._minuto_actual:
            self._escribir_fila(self._minuto_actual, total - cruces, bpm_promedio)
            self._minuto_actual = minuto
            self._botellas_minuto = 0
        self._botellas_minuto += cruces

    def cerrar(self, total: int, bpm_promedio: float) -> None:
        """Escribe la fila del minuto en curso al terminar la sesión."""
        if self._botellas_minuto > 0:
            self._escribir_fila(self._minuto_actual, total, bpm_promedio)

    def _escribir_fila(self, minuto: str, total: int, bpm_promedio: float) -> None:
        """Agrega una fila al CSV del día, creando el encabezado si es nuevo."""
        ruta = self.carpeta / f"produccion_{minuto[:10]}.csv"
        nuevo = not ruta.exists()
        with open(ruta, "a", newline="") as archivo:
            escritor = csv.writer(archivo)
            if nuevo:
                escritor.writerow(
                    ["minuto", "botellas", "total_sesion", "bpm_promedio_sesion"]
                )
            escritor.writerow(
                [minuto, self._botellas_minuto, total, f"{bpm_promedio:.1f}"]
            )
