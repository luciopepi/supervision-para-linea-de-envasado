"""CLI del contador de botellas: `python -m contador_botellas --ayuda`."""

import argparse
import socket
from pathlib import Path

from .clasificador import Clasificador
from .contador import ConfiguracionLinea, ContadorBotellas
from .detecciones import RegistroDetecciones
from .detector import DetectorBotellas
from .inspeccion import InspectorBotellas
from .registro import RegistroProduccion
from .salidas import ValvulaDescarte
from .tablero import EstadoTablero, iniciar_tablero


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
    parser.add_argument(
        "--tablero",
        action="store_true",
        help="Servir el tablero de control web (video en vivo + estadísticas)",
    )
    parser.add_argument(
        "--puerto",
        type=int,
        default=8000,
        help="Puerto del tablero web (por defecto 8000)",
    )
    parser.add_argument(
        "--registro",
        default="registros",
        help="Carpeta donde guardar los CSV diarios de producción por minuto",
    )
    parser.add_argument(
        "--sin-registro",
        action="store_true",
        help="No guardar el registro de producción por minuto",
    )
    parser.add_argument(
        "--resolucion",
        default="1280x720",
        help='Resolución pedida a la cámara web, formato "1280x720"',
    )
    parser.add_argument(
        "--tamano-inferencia",
        type=int,
        default=640,
        help="Tamaño de imagen para la red (480 o 416 = más fluido en CPU)",
    )
    parser.add_argument(
        "--clases-defecto",
        type=int,
        nargs="*",
        default=None,
        help="IDs de clases del modelo propio que son defectos (activan el descarte)",
    )
    parser.add_argument(
        "--valvula-puerto",
        default=None,
        help='Puerto serie del relé de la válvula de descarte (ej. "COM3"). '
        "Sin este parámetro la válvula queda en modo simulado.",
    )
    parser.add_argument(
        "--valvula-retardo",
        type=int,
        default=500,
        help="Milisegundos entre que la botella cruza la línea y el soplido",
    )
    parser.add_argument(
        "--valvula-duracion",
        type=int,
        default=300,
        help="Milisegundos que dura el soplido de descarte",
    )
    parser.add_argument(
        "--valvula-protocolo",
        choices=["arduino", "lcus"],
        default="arduino",
        help="Protocolo del relé USB: arduino (bytes '1'/'0') o lcus (LCUS-1/2)",
    )
    parser.add_argument(
        "--dataset",
        default="dataset",
        help="Carpeta donde guardar las muestras capturadas desde la HMI",
    )
    parser.add_argument(
        "--modelos",
        default="modelos",
        help="Carpeta del clasificador de defectos entrenado en el equipo",
    )
    parser.add_argument(
        "--sku",
        default="general",
        help="Producto activo al arrancar: sus muestras van a dataset/<sku>/ y "
        "su modelo a modelos/<sku>/ (también se cambia desde la HMI)",
    )
    parser.add_argument(
        "--iniciar-detenido",
        action="store_true",
        help="Arrancar con la detección en pausa (se inicia desde la HMI)",
    )
    parser.add_argument(
        "--detecciones",
        default="detecciones",
        help="Carpeta donde guardar las fotos y el CSV de cada defecto detectado "
        "(auditoría del modelo)",
    )
    parser.add_argument(
        "--sin-detecciones",
        action="store_true",
        help="No guardar fotos de defectos para auditoría",
    )
    return parser


def ip_local() -> str:
    """Mejor IP local para mostrar la URL del tablero en la red."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "localhost"


def main() -> None:
    """Punto de entrada del CLI: arma el pipeline y procesa la fuente."""
    args = crear_parser().parse_args()

    detector = DetectorBotellas(
        ruta_modelo=args.modelo,
        confianza=args.confianza,
        clases=args.clases,
        dispositivo=args.dispositivo,
        tamano_inferencia=args.tamano_inferencia,
    )
    linea = ConfiguracionLinea(orientacion=args.linea, posicion=args.posicion_linea)
    inspector = InspectorBotellas() if args.inspeccion else None
    valvula = ValvulaDescarte(
        puerto=args.valvula_puerto,
        retardo_ms=args.valvula_retardo,
        duracion_ms=args.valvula_duracion,
        protocolo=args.valvula_protocolo,
    )
    # Si el SKU ya tiene un clasificador entrenado en el equipo, se carga solo.
    clasificador = None
    ruta_clasificador = Path(args.modelos) / args.sku / "clasificador.pt"
    if ruta_clasificador.exists():
        clasificador = Clasificador(ruta_clasificador)
        print(f"Clasificador de defectos cargado: {ruta_clasificador}")

    registro_detecciones = (
        None if args.sin_detecciones else RegistroDetecciones(args.detecciones)
    )

    contador = ContadorBotellas(
        detector=detector,
        linea=linea,
        ventana_velocidad=args.ventana_velocidad,
        inspector=inspector,
        valvula=valvula,
        clases_defecto=set(args.clases_defecto or []),
        carpeta_muestras=args.dataset,
        carpeta_modelos=args.modelos,
        clasificador=clasificador,
        sku=args.sku,
        registro_detecciones=registro_detecciones,
    )

    try:
        ancho_res, alto_res = (int(v) for v in args.resolucion.lower().split("x"))
        resolucion = (ancho_res, alto_res)
    except ValueError:
        raise SystemExit(f'--resolucion inválida: "{args.resolucion}" (usar p. ej. 1280x720)')

    estado = None
    if args.tablero:
        estado = EstadoTablero()
        carpeta_registro = None if args.sin_registro else args.registro
        iniciar_tablero(estado, args.puerto, carpeta_registro=carpeta_registro)
        print(f"\nTablero de control disponible en:")
        print(f"  → http://localhost:{args.puerto}   (en esta computadora)")
        print(f"  → http://{ip_local()}:{args.puerto}   (desde otra compu o celular en la misma red)")

    registro = None if args.sin_registro else RegistroProduccion(args.registro)
    if registro is not None:
        print(f"Registro de producción por minuto en: {args.registro}/")
    if registro_detecciones is not None:
        print(f"Fotos de defectos para auditoría en: {args.detecciones}/")
    print("Para salir: tecla q en la ventana de video, o Ctrl+C en esta consola.\n")

    resumen = contador.procesar(
        fuente=args.fuente,
        ruta_salida=args.salida,
        ruta_csv=args.csv,
        mostrar=args.mostrar,
        max_cuadros=args.max_cuadros,
        estado_tablero=estado,
        registro=registro,
        resolucion=resolucion,
        iniciar_detenido=args.iniciar_detenido,
    )

    print("\n===== RESUMEN =====")
    print(f"Botellas contadas : {int(resumen['total'])}")
    print(f"Defectos descartados: {int(resumen['defectos'])}")
    print(f"Velocidad promedio: {resumen['bpm_promedio']:.1f} botellas/min")
    print(f"Duración procesada: {resumen['duracion_s']:.1f} s")


if __name__ == "__main__":
    main()
