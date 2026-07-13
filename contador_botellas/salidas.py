"""Salida física para descartar botellas defectuosas (electroválvula).

La forma más simple y barata de comandar una electroválvula desde una PC con
Windows es un **módulo relé USB** (por ejemplo un Arduino con relé, o un relé
USB tipo LCUS-1). La PC manda un carácter por el puerto serie (COM3, COM4...)
y el relé abre/cierra el circuito de la válvula.

Protocolos soportados:
- `arduino`: envía el byte '1' para activar y '0' para desactivar (ver el
  sketch de ejemplo en el README).
- `lcus`: relé USB LCUS-1/LCUS-2 (chip CH340), tramas A0 01 01 A2 / A0 01 00 A1.

Sin puerto configurado (o si no se puede abrir) funciona en **modo simulado**:
registra cada activación como evento, ideal para probar la lógica sin hardware.
"""

import threading
import time


class ValvulaDescarte:
    """Comanda la válvula de descarte con retardo y duración configurables.

    `retardo_ms` es el tiempo que tarda la botella en viajar desde la cámara
    hasta la posición de la válvula (calibrar en la línea real);
    `duracion_ms` es cuánto queda activada la salida (el soplido).
    """

    def __init__(
        self,
        puerto: str | None = None,
        baudios: int = 9600,
        retardo_ms: int = 500,
        duracion_ms: int = 300,
        protocolo: str = "arduino",
    ) -> None:
        """Abre el puerto serie si se indicó; si falla queda en modo simulado."""
        self.retardo_ms = retardo_ms
        self.duracion_ms = duracion_ms
        self.protocolo = protocolo
        self.activaciones = 0
        self._lock = threading.Lock()
        self._serie = None
        if puerto:
            try:
                import serial  # import perezoso: pyserial es opcional

                self._serie = serial.Serial(puerto, baudios, timeout=1)
                print(f"Válvula de descarte conectada en {puerto} ({protocolo}).")
            except Exception as error:  # puerto inexistente, ocupado, sin pyserial
                print(f"Aviso: no se pudo abrir el puerto {puerto} ({error}).")
                print("La válvula queda en MODO SIMULADO (solo registra eventos).")

    @property
    def simulada(self) -> bool:
        """True si no hay hardware conectado y las activaciones solo se registran."""
        return self._serie is None

    def descartar(self) -> None:
        """Programa un pulso de descarte tras `retardo_ms` (no bloquea el video)."""
        threading.Timer(self.retardo_ms / 1000.0, self._pulso).start()

    def probar(self) -> None:
        """Activa la válvula ya mismo, para verificar el conexionado desde la HMI."""
        threading.Thread(target=self._pulso, daemon=True).start()

    def _bytes_activar(self) -> bytes:
        """Trama de activación según el protocolo configurado."""
        return b"\xa0\x01\x01\xa2" if self.protocolo == "lcus" else b"1"

    def _bytes_desactivar(self) -> bytes:
        """Trama de desactivación según el protocolo configurado."""
        return b"\xa0\x01\x00\xa1" if self.protocolo == "lcus" else b"0"

    def _pulso(self) -> None:
        """Mantiene la salida activa `duracion_ms` y la apaga (o lo simula)."""
        with self._lock:  # evita pulsos superpuestos si llegan dos descartes juntos
            self.activaciones += 1
            if self._serie is None:
                print(f"[VÁLVULA SIMULADA] pulso de {self.duracion_ms} ms")
                time.sleep(self.duracion_ms / 1000.0)
                return
            self._serie.write(self._bytes_activar())
            time.sleep(self.duracion_ms / 1000.0)
            self._serie.write(self._bytes_desactivar())

    def cerrar(self) -> None:
        """Desactiva la salida y libera el puerto serie."""
        if self._serie is not None:
            try:
                self._serie.write(self._bytes_desactivar())
                self._serie.close()
            except Exception:
                pass
