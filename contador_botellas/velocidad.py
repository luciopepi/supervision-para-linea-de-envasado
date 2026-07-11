"""Estimación de velocidad de producción (botellas por minuto)."""

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
        real para no inflar la estimación.
        """
        if not self._cruces:
            return 0.0
        transcurrido = min(self.ventana_segundos, self._ultimo_instante - self._cruces[0])
        # Con un solo cruce (o cruces simultáneos) no hay intervalo medible.
        if transcurrido <= 0:
            return 0.0
        return len(self._cruces) * 60.0 / transcurrido

    def promedio_botellas_por_minuto(self) -> float:
        """Velocidad promedio (botellas/min) desde el primer cruce hasta el último instante."""
        if self._primer_cruce is None:
            return 0.0
        transcurrido = self._ultimo_instante - self._primer_cruce
        if transcurrido <= 0:
            return 0.0
        return self._total * 60.0 / transcurrido
