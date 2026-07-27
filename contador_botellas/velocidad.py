"""Estimación de velocidad de producción (botellas por minuto) y del equipo."""

import time
from collections import deque


class EstimadorVelocidad:
    """Calcula botellas/minuto a partir de los cruces de la línea de conteo.

    Mantiene una ventana deslizante con los instantes (en segundos de video)
    en que cada botella cruzó la línea. La velocidad instantánea se calcula
    sobre esa ventana y la velocidad promedio sobre todo el video.
    """

    def __init__(self, ventana_segundos: float = 30.0) -> None:
        """Inicializa el estimador con el tamaño de ventana deslizante en segundos."""
        self.ventana_segundos = ventana_segundos
        self._cruces: deque[float] = deque()
        self._total: int = 0
        self._primer_cruce: float | None = None
        self._ultimo_instante: float = 0.0

    def registrar_cruces(self, cantidad: int, instante: float) -> None:
        """Registra `cantidad` de botellas que cruzaron la línea en `instante` (segundos)."""
        self._ultimo_instante = instante
        for _ in range(cantidad):
            self._cruces.append(instante)
            self._total += 1
            if self._primer_cruce is None:
                self._primer_cruce = instante
        # Descarta cruces que quedaron fuera de la ventana deslizante.
        limite = instante - self.ventana_segundos
        while self._cruces and self._cruces[0] < limite:
            self._cruces.popleft()

    @property
    def total(self) -> int:
        """Cantidad total de botellas contadas desde el inicio."""
        return self._total

    def botellas_por_minuto(self) -> float:
        """Velocidad instantánea (botellas/min) sobre la ventana deslizante.

        Antes de completarse la primera ventana se usa el tiempo transcurrido
        real, con un piso de 10 segundos: sin ese piso, dos cruces casi
        simultáneos al arrancar producen picos irreales de miles de bot/min.
        """
        if not self._cruces:
            return 0.0
        transcurrido = min(
            self.ventana_segundos, max(10.0, self._ultimo_instante - self._cruces[0])
        )
        return len(self._cruces) * 60.0 / transcurrido

    def promedio_botellas_por_minuto(self) -> float:
        """Velocidad promedio (botellas/min) desde el primer cruce hasta el último instante."""
        if self._primer_cruce is None:
            return 0.0
        transcurrido = self._ultimo_instante - self._primer_cruce
        if transcurrido <= 0:
            return 0.0
        return self._total * 60.0 / transcurrido


class MedidorCuadros:
    """Mide cuántos cuadros por segundo alcanza a procesar la computadora.

    Es el indicador de si el equipo da abasto: no importa qué tan rápido
    corra el video de la cámara, sino cuántos cuadros llega a analizar el
    detector por segundo. Con pocos cuadros por segundo una botella rápida
    aparece en muy pocas imágenes y el seguidor puede perderle el rastro
    (conteos de menos), además de verse entrecortado en la pantalla.

    Usa el reloj de pared (no el tiempo del video) sobre una ventana de los
    últimos cuadros, para que el número refleje el momento actual y no un
    promedio de toda la corrida.
    """

    def __init__(self, ventana_cuadros: int = 30) -> None:
        """Prepara la ventana con los instantes reales de los últimos cuadros."""
        self._instantes: deque[float] = deque(maxlen=ventana_cuadros)

    def registrar_cuadro(self) -> None:
        """Anota que se terminó de procesar un cuadro, en este instante."""
        self._instantes.append(time.monotonic())

    def reiniciar(self) -> None:
        """Olvida lo medido: se usa al reanudar tras una pausa.

        Sin esto, el hueco de tiempo de la pausa se mezclaría con los cuadros
        nuevos y mostraría una velocidad falsamente baja al retomar.
        """
        self._instantes.clear()

    def cuadros_por_segundo(self) -> float:
        """Cuadros por segundo de la ventana (0 si todavía no hay dos cuadros)."""
        if len(self._instantes) < 2:
            return 0.0
        transcurrido = self._instantes[-1] - self._instantes[0]
        if transcurrido <= 0:
            return 0.0
        return (len(self._instantes) - 1) / transcurrido
