"""Clasificador de defectos entrenable en el propio equipo.

El detector YOLO encuentra *dónde* hay botellas; este módulo aprende *cómo es
cada tipo de botella* a partir de las muestras capturadas con los botones
MUESTRA OK / MUESTRA DEFECTO de la HMI (recortes guardados en `dataset/`).

Flujo:
1. Capturar muestras de cada clase (`ok`, `defecto`, `sin_capsula`, ...) con
   la botella pasando por la cinta. Mínimo ~20 recortes por clase; cuantas
   más, mejor.
2. Tocar ENTRENAR MODELO en la HMI (o correr `entrenar()` desde Python). Se
   entrena un YOLO de clasificación con esos recortes, en la propia máquina.
3. Al terminar, el pipeline clasifica cada botella que pasa; toda clase
   distinta de `ok` cuenta como defecto y dispara el descarte.
"""

import random
import shutil
import threading
from collections import Counter
from pathlib import Path
from typing import Callable

import numpy as np

# Recortes por clase por debajo de este número el entrenamiento se rechaza:
# con menos ejemplos el modelo memoriza en vez de aprender.
MINIMO_MUESTRAS_POR_CLASE = 10


def contar_muestras(carpeta_dataset: str | Path) -> dict[str, int]:
    """Cuenta los recortes de botella (archivos *_bot*.jpg) por clase."""
    conteo: dict[str, int] = {}
    carpeta = Path(carpeta_dataset)
    if not carpeta.exists():
        return conteo
    for subcarpeta in sorted(carpeta.iterdir()):
        if subcarpeta.is_dir():
            conteo[subcarpeta.name] = len(list(subcarpeta.glob("*_bot*.jpg")))
    return conteo


def _preparar_dataset(origen: Path, destino: Path) -> list[str]:
    """Arma la estructura train/val que espera YOLO-cls a partir de dataset/.

    Usa solo los recortes de botella (no los cuadros completos), con una
    partición 85/15 reproducible. Devuelve la lista de clases.
    """
    rng = random.Random(37)
    if destino.exists():
        shutil.rmtree(destino)
    clases = []
    for subcarpeta in sorted(origen.iterdir()):
        if not subcarpeta.is_dir():
            continue
        recortes = sorted(subcarpeta.glob("*_bot*.jpg"))
        if len(recortes) < MINIMO_MUESTRAS_POR_CLASE:
            continue
        clases.append(subcarpeta.name)
        rng.shuffle(recortes)
        corte = max(1, int(len(recortes) * 0.15))
        for particion, archivos in (("val", recortes[:corte]), ("train", recortes[corte:])):
            carpeta = destino / particion / subcarpeta.name
            carpeta.mkdir(parents=True, exist_ok=True)
            for archivo in archivos:
                shutil.copy2(archivo, carpeta / archivo.name)
    return clases


def entrenar(
    carpeta_dataset: str | Path = "dataset",
    carpeta_modelos: str | Path = "modelos",
    epocas: int = 40,
    tamano: int = 128,
    informar: Callable[[str], None] = print,
) -> Path | None:
    """Entrena el clasificador con las muestras capturadas y devuelve la ruta.

    Corre en la propia máquina (CPU o GPU). Con ~50 recortes por clase y
    tamaño 128 tarda pocos minutos en CPU. Devuelve None si no hay muestras
    suficientes.
    """
    from ultralytics import YOLO  # import perezoso: tarda en cargar

    origen = Path(carpeta_dataset)
    conteo = contar_muestras(origen)
    validas = {c: n for c, n in conteo.items() if n >= MINIMO_MUESTRAS_POR_CLASE}
    if len(validas) < 2:
        informar(
            f"Faltan muestras: se necesitan al menos 2 clases con "
            f"{MINIMO_MUESTRAS_POR_CLASE}+ recortes cada una. Hay: {conteo or 'ninguna'}"
        )
        return None

    destino = Path(carpeta_modelos) / "dataset_entrenamiento"
    clases = _preparar_dataset(origen, destino)
    informar(f"Entrenando con clases {clases} ({sum(validas.values())} recortes)...")

    # Parte del clasificador preentrenado si se puede descargar; si no hay
    # internet, entrena desde cero (necesita más muestras para rendir igual).
    try:
        modelo = YOLO("yolov8n-cls.pt")
    except Exception:
        informar("Sin acceso a los pesos preentrenados; entrenando desde cero.")
        modelo = YOLO("yolov8n-cls.yaml")

    modelo.train(
        data=str(destino),
        epochs=epocas,
        imgsz=tamano,
        project=str(Path(carpeta_modelos) / "corridas"),
        name="clasificador",
        exist_ok=True,
        verbose=False,
        plots=False,
    )
    mejor = Path(carpeta_modelos) / "corridas" / "clasificador" / "weights" / "best.pt"
    salida = Path(carpeta_modelos) / "clasificador.pt"
    shutil.copy2(mejor, salida)
    informar(f"Modelo entrenado y guardado en {salida}")
    return salida


class Clasificador:
    """Clasifica el recorte de cada botella con el modelo entrenado.

    Para no recargar la CPU, cada botella (tracker_id) se clasifica unas pocas
    veces y se decide por mayoría; el resultado queda cacheado para esa
    botella.
    """

    VOTOS_POR_BOTELLA = 3

    def __init__(self, ruta_modelo: str | Path, tamano: int = 128) -> None:
        """Carga el modelo de clasificación entrenado."""
        from ultralytics import YOLO  # import perezoso

        self.modelo = YOLO(str(ruta_modelo))
        self.tamano = tamano
        self._votos: dict[int, list[str]] = {}
        self._decision: dict[int, str] = {}

    def clasificar_botella(self, recorte: np.ndarray, tracker_id: int) -> str | None:
        """Devuelve la clase decidida para la botella, votando hasta 3 veces.

        Devuelve None mientras no haya decisión firme (pocos votos aún).
        """
        if tracker_id in self._decision:
            return self._decision[tracker_id]
        if recorte.size == 0 or min(recorte.shape[:2]) < 24:
            return None
        resultado = self.modelo(recorte, imgsz=self.tamano, verbose=False)[0]
        clase = resultado.names[int(resultado.probs.top1)]
        votos = self._votos.setdefault(tracker_id, [])
        votos.append(clase)
        if len(votos) >= self.VOTOS_POR_BOTELLA:
            decision = Counter(votos).most_common(1)[0][0]
            self._decision[tracker_id] = decision
            # Limpieza simple para que los cachés no crezcan sin límite.
            if len(self._decision) > 500:
                self._votos.clear()
                self._decision = dict(list(self._decision.items())[-100:])
            return decision
        return None


def entrenar_en_hilo(
    carpeta_dataset: str | Path,
    carpeta_modelos: str | Path,
    informar: Callable[[str], None],
    al_terminar: Callable[[Path | None], None],
) -> threading.Thread:
    """Lanza `entrenar` en un hilo demonio y avisa al terminar."""

    def _trabajo() -> None:
        """Cuerpo del hilo: entrena y entrega el resultado (o el error)."""
        try:
            ruta = entrenar(carpeta_dataset, carpeta_modelos, informar=informar)
        except Exception as error:
            informar(f"Error al entrenar: {error}")
            ruta = None
        al_terminar(ruta)

    hilo = threading.Thread(target=_trabajo, daemon=True)
    hilo.start()
    return hilo
