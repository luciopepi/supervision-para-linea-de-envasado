"""Extrae fotogramas de los videos de la línea para armar el dataset de detección.

Primer paso para entrenar el futuro detector propio de partes (`partes.py`):
recorrer los videos de prueba y guardar cuadros de a `--por-segundo` por
segundo, listos para etiquetar (con Roboflow, CVAT, LabelImg...). Se corre
como script propio:

```
python -m contador_botellas.fotogramas --videos videos --salida dataset_deteccion/imagenes
```
"""

import argparse
from pathlib import Path

import cv2
import numpy as np


def _diferencia_media(cuadro_a: np.ndarray, cuadro_b: np.ndarray) -> float:
    """Calcula la diferencia media de píxeles entre dos cuadros BGR del mismo tamaño."""
    return float(np.mean(cv2.absdiff(cuadro_a, cuadro_b)))


def _reescalar_si_hace_falta(cuadro: np.ndarray, max_lado: int) -> np.ndarray:
    """Reescala `cuadro` si su lado mayor supera `max_lado`, preservando el aspecto."""
    alto, ancho = cuadro.shape[:2]
    lado_mayor = max(alto, ancho)
    if lado_mayor <= max_lado:
        return cuadro
    factor = max_lado / lado_mayor
    return cv2.resize(cuadro, (int(ancho * factor), int(alto * factor)), interpolation=cv2.INTER_AREA)


def extraer_fotogramas(
    ruta_video: Path,
    carpeta_salida: Path,
    por_segundo: float = 2.0,
    diferencia_minima: float = 4.0,
    max_lado: int = 1280,
) -> tuple[int, int]:
    """Extrae cuadros de `ruta_video` y los guarda como JPG en `carpeta_salida`.

    Toma `por_segundo` cuadros por segundo de video, descartando el cuadro si
    su diferencia media de píxeles contra el último cuadro guardado de ese
    mismo video es menor a `diferencia_minima` (filtro anti-duplicados: la
    cinta parada o entre lotes genera cientos de cuadros casi idénticos que
    no aportan nada al dataset). Devuelve (guardados, salteados).
    """
    captura = cv2.VideoCapture(str(ruta_video))
    if not captura.isOpened():
        raise RuntimeError(f"No se pudo abrir el video: {ruta_video}")

    fps = captura.get(cv2.CAP_PROP_FPS) or 30.0
    paso_cuadros = max(1, round(fps / por_segundo))
    carpeta_salida.mkdir(parents=True, exist_ok=True)

    guardados = 0
    salteados = 0
    ultimo_guardado: np.ndarray | None = None
    numero_cuadro = -1
    try:
        while True:
            ok, cuadro = captura.read()
            if not ok:
                break
            numero_cuadro += 1
            if numero_cuadro % paso_cuadros != 0:
                continue
            cuadro = _reescalar_si_hace_falta(cuadro, max_lado)
            if ultimo_guardado is not None and _diferencia_media(cuadro, ultimo_guardado) < diferencia_minima:
                salteados += 1
                continue
            nombre = f"{ruta_video.stem}_{numero_cuadro:06d}.jpg"
            cv2.imwrite(str(carpeta_salida / nombre), cuadro)
            ultimo_guardado = cuadro
            guardados += 1
    finally:
        captura.release()
    return guardados, salteados


def _videos_a_procesar(ruta_videos: Path) -> list[Path]:
    """Lista los .mp4 a procesar: el archivo puntual, o todos los de la carpeta."""
    if ruta_videos.is_file():
        return [ruta_videos]
    if not ruta_videos.exists():
        raise FileNotFoundError(f"No existe la carpeta/archivo de videos: {ruta_videos}")
    return sorted(ruta_videos.glob("*.mp4"))


def crear_parser() -> argparse.ArgumentParser:
    """Define los argumentos de línea de comandos en español."""
    parser = argparse.ArgumentParser(
        prog="contador_botellas.fotogramas",
        description=(
            "Extrae fotogramas de videos de la línea para armar el dataset de "
            "entrenamiento del detector de partes (botella, tapa, etiqueta, caja...)."
        ),
    )
    parser.add_argument(
        "--videos",
        required=True,
        help="Carpeta con videos .mp4, o la ruta a un único video",
    )
    parser.add_argument(
        "--salida",
        required=True,
        help="Carpeta donde guardar los fotogramas extraídos (JPG)",
    )
    parser.add_argument(
        "--por-segundo",
        type=float,
        default=2.0,
        help="Cuadros a extraer por segundo de video (por defecto 2)",
    )
    parser.add_argument(
        "--diferencia-minima",
        type=float,
        default=4.0,
        help="Diferencia media mínima de píxeles contra el último cuadro guardado "
        "del mismo video para no descartarlo como duplicado (por defecto 4.0)",
    )
    parser.add_argument(
        "--max-lado",
        type=int,
        default=1280,
        help="Lado mayor máximo en píxeles; se reescala si lo supera (por defecto 1280)",
    )
    return parser


def main() -> None:
    """Punto de entrada: recorre los videos y extrae los fotogramas de cada uno."""
    args = crear_parser().parse_args()
    ruta_videos = Path(args.videos)
    carpeta_salida = Path(args.salida)
    videos = _videos_a_procesar(ruta_videos)
    if not videos:
        print(f"No se encontraron videos .mp4 en: {ruta_videos}")
        return

    total_guardados = 0
    total_salteados = 0
    for video in videos:
        guardados, salteados = extraer_fotogramas(
            video,
            carpeta_salida,
            por_segundo=args.por_segundo,
            diferencia_minima=args.diferencia_minima,
            max_lado=args.max_lado,
        )
        total_guardados += guardados
        total_salteados += salteados
        print(f"{video.name}: {guardados} cuadro(s) guardado(s), {salteados} salteado(s) por duplicado")

    print(f"\nTotal: {total_guardados} cuadro(s) guardado(s) en {carpeta_salida}/ "
          f"({total_salteados} salteado(s) por duplicado)")


if __name__ == "__main__":
    main()
