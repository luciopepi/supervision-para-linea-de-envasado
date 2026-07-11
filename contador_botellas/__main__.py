"""CLI del contador de botellas: `python -m contador_botellas --ayuda`."""

import argparse

from .contador import ConfiguracionLinea, ContadorBotellas
from .detector import DetectorBotellas
from .inspeccion import InspectorBotellas


def crear_parser() -> argparse.ArgumentParser:
    """Define los argumentos de línea de comandos en español."""
    parser = argparse.ArgumentParser(
        prog="contador_botellas",
        description=(
            "Cuenta botellas y mide la velocidad de producción (botellas/min) "
            "sobre una cámara web, un archivo de video o un stream RTSP."
        ),
    )
    parser.add_argument(
        "--fuente",
        required=True,
        help='Fuente de video: índice de cámara ("0"), ruta a un .mp4 o URL rtsp://',
    )
    parser.add_argument(
        "--modelo",
        default="yolov8n.pt",
        help="Ruta o nombre del modelo YOLO (por defecto yolov8n.pt, se descarga solo)",
    )
    parser.add_argument(
        "--confianza",
        type=float,
        default=0.3,
        help="Confianza mínima de detección (0-1, por defecto 0.3)",
    )
    parser.add_argument(
        "--clases",
        type=int,
        nargs="*",
        default=None,
        help="IDs de clases a detectar (por defecto 39 = bottle en COCO). "
        "Con un modelo propio, indicar sus clases.",
    )
    parser.add_argument(
        "--linea",
        choices=["vertical", "horizontal"],
        default="vertical",
        help="Orientación de la línea de conteo (vertical si las botellas avanzan de lado)",
    )
    parser.add_argument(
        "--posicion-linea",
        type=float,
        default=0.5,
        help="Posición de la línea como fracción del cuadro (0.5 = centro)",
    )
    parser.add_argument(
        "--ventana-velocidad",
        type=float,
        default=30.0,
        help="Segundos de la ventana deslizante para la velocidad instantánea",
    )
    parser.add_argument("--salida", default=None, help="Ruta del video anotado de salida (.mp4)")
    parser.add_argument("--csv", default=None, help="Ruta del CSV de estadísticas por cuadro")
    parser.add_argument(
        "--mostrar", action="store_true", help="Mostrar ventana en vivo (requiere entorno gráfico)"
    )
    parser.add_argument(
        "--inspeccion",
        action="store_true",
        help="Activar inspección heurística experimental de nivel de llenado",
    )
    parser.add_argument(
        "--dispositivo",
        default=None,
        help='Dispositivo de inferencia: "cpu", "0" (GPU CUDA), "mps"...',
    )
    parser.add_argument(
        "--max-cuadros",
        type=int,
        default=None,
        help="Procesar como máximo N cuadros (útil para pruebas rápidas)",
    )
    return parser


def main() -> None:
    """Punto de entrada del CLI: arma el pipeline y procesa la fuente."""
    args = crear_parser().parse_args()

    detector = DetectorBotellas(
        ruta_modelo=args.modelo,
        confianza=args.confianza,
        clases=args.clases,
        dispositivo=args.dispositivo,
    )
    linea = ConfiguracionLinea(orientacion=args.linea, posicion=args.posicion_linea)
    inspector = InspectorBotellas() if args.inspeccion else None
    contador = ContadorBotellas(
        detector=detector,
        linea=linea,
        ventana_velocidad=args.ventana_velocidad,
        inspector=inspector,
    )

    resumen = contador.procesar(
        fuente=args.fuente,
        ruta_salida=args.salida,
        ruta_csv=args.csv,
        mostrar=args.mostrar,
        max_cuadros=args.max_cuadros,
    )

    print("\n===== RESUMEN =====")
    print(f"Botellas contadas : {int(resumen['total'])}")
    print(f"Velocidad promedio: {resumen['bpm_promedio']:.1f} botellas/min")
    print(f"Duración procesada: {resumen['duracion_s']:.1f} s")


if __name__ == "__main__":
    main()
