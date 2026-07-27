"""CLI del contador de botellas: `python -m contador_botellas --ayuda`."""

import argparse
import socket
import threading
import webbrowser
from pathlib import Path

from .clasificador import Clasificador
from .configuracion import cargar, guardar, par_resolucion, validar
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
        default=None,
        help='Fuente de video: índice de cámara ("0"), ruta a un .mp4, URL rtsp:// '
        "o la dirección de un celular usado como cámara. Si no se pasa, se usa "
        "la guardada en el archivo de configuración (editable desde la HMI)",
    )
    parser.add_argument(
        "--modelo",
        default=None,
        help="Ruta o nombre del modelo YOLO (por defecto yolov8n.pt, se descarga solo)",
    )
    parser.add_argument(
        "--confianza",
        type=float,
        default=None,
        help="Confianza mínima de detección (0-1, por defecto 0.25)",
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
        default=None,
        help="Orientación de la línea de conteo (vertical si las botellas avanzan de lado)",
    )
    parser.add_argument(
        "--posicion-linea",
        type=float,
        default=None,
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
        "--abrir-navegador",
        action="store_true",
        help="Abrir el tablero en el navegador al arrancar (activa --tablero solo). "
        "Es lo que usa el acceso directo del escritorio en Windows",
    )
    parser.add_argument(
        "--puerto",
        type=int,
        default=None,
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
        default=None,
        help='Resolución pedida a la cámara web, formato "1280x720"',
    )
    parser.add_argument(
        "--tamano-inferencia",
        type=int,
        default=None,
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
        default=None,
        help="Milisegundos entre que la botella cruza la línea y el soplido",
    )
    parser.add_argument(
        "--valvula-duracion",
        type=int,
        default=None,
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
    parser.add_argument(
        "--modo",
        choices=["linea", "caja"],
        default=None,
        help="linea: contar botellas cruzando la línea (avisa sin_tapa/sin_etiqueta "
        "con un modelo propio de partes). caja: contar botellas dentro de cajas "
        "vistas desde arriba y verificar separador; requiere modelo propio con "
        "la clase 'caja' (por defecto linea)",
    )
    parser.add_argument(
        "--botellas-por-caja",
        type=int,
        default=None,
        help="Cuántas botellas debe traer cada caja completa (solo --modo caja, "
        "por defecto 6)",
    )
    parser.add_argument(
        "--config",
        default="configuracion.json",
        help="Archivo con los ajustes editables desde la HMI (cámara, modelo, "
        "resolución, posición de línea, confianza, tiempos de válvula...). "
        "Manda sobre los valores por defecto, y un flag escrito a mano manda "
        "sobre el archivo. Si no existe se crea al arrancar "
        "(por defecto configuracion.json)",
    )
    return parser


# Flag de la línea de comandos → campo de `ConfiguracionAjustable`. Solo se
# aplican los que el usuario escribió de verdad (los demás quedan en None):
# así el archivo manda sobre los valores por defecto, y una prueba puntual
# con `--modelo otro.pt` sigue ganando sobre el archivo sin editarlo.
CLAVES_DESDE_FLAGS: dict[str, str] = {
    "fuente": "fuente",
    "modelo": "modelo",
    "confianza": "confianza",
    "linea": "orientacion_linea",
    "posicion_linea": "posicion_linea",
    "tamano_inferencia": "tamano_inferencia",
    "resolucion": "resolucion_camara",
    "valvula_retardo": "valvula_retardo_ms",
    "valvula_duracion": "valvula_duracion_ms",
    "valvula_puerto": "valvula_puerto_serie",
    "botellas_por_caja": "botellas_por_caja",
    "modo": "modo",
    "puerto": "puerto",
}


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

    # Precedencia: valores por defecto < archivo de configuración (lo que el
    # operario tocó en la pantalla) < flags escritos a mano en esta corrida.
    config = cargar(args.config)
    for flag, clave in CLAVES_DESDE_FLAGS.items():
        valor = getattr(args, flag)
        if valor is None:
            continue
        try:
            setattr(config, clave, validar(clave, valor))
        except ValueError as error:
            raise SystemExit(f"--{flag.replace('_', '-')}: {error}")

    # Primer arranque: dejar el archivo escrito para que la pantalla de
    # configuración tenga de dónde partir y el operario no dependa de flags.
    if not Path(args.config).exists():
        guardar(config, args.config)
        print(f"Configuración inicial guardada en: {args.config}")

    # Abierta con doble clic no hay consola donde leer un traceback: los
    # errores típicos de arranque se explican en castellano y se dice dónde
    # se arregla cada uno desde la pantalla.
    try:
        detector = DetectorBotellas(
            ruta_modelo=config.modelo,
            confianza=config.confianza,
            clases=args.clases,
            dispositivo=args.dispositivo,
            tamano_inferencia=config.tamano_inferencia,
        )
    except Exception as error:
        raise SystemExit(
            f"\n[ERROR] No se pudo cargar el modelo '{config.modelo}': {error}\n\n"
            f"Revisá que el archivo exista (por ejemplo modelos\\detector_partes.pt).\n"
            f"Se cambia desde el engranaje ⚙ de la pantalla, en 'Modelo de detección',\n"
            f"o editando '{args.config}'."
        )
    linea = ConfiguracionLinea(orientacion=config.orientacion_linea, posicion=config.posicion_linea)
    inspector = InspectorBotellas() if args.inspeccion else None
    valvula = ValvulaDescarte(
        puerto=config.valvula_puerto_serie or None,
        retardo_ms=config.valvula_retardo_ms,
        duracion_ms=config.valvula_duracion_ms,
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
        modo=config.modo,
        botellas_por_caja=config.botellas_por_caja,
        config=config,
        ruta_config=args.config,
    )

    resolucion = par_resolucion(config.resolucion_camara)

    estado = None
    # El acceso directo del escritorio arranca con --abrir-navegador: pedir
    # además --tablero sería una trampa para el operario, así que lo implica.
    servir_tablero = args.tablero or args.abrir_navegador
    if servir_tablero:
        estado = EstadoTablero()
        carpeta_registro = None if args.sin_registro else args.registro
        iniciar_tablero(estado, config.puerto, carpeta_registro=carpeta_registro)
        print(f"\nTablero de control disponible en:")
        print(f"  → http://localhost:{config.puerto}   (en esta computadora)")
        print(f"  → http://{ip_local()}:{config.puerto}   (desde otra compu o celular en la misma red)")
        if args.abrir_navegador:
            # En un hilo aparte y con un respiro: si el navegador tarda en
            # levantar, el pipeline no se queda esperándolo.
            threading.Timer(
                1.5, lambda: webbrowser.open(f"http://localhost:{config.puerto}")
            ).start()

    registro = None if args.sin_registro else RegistroProduccion(args.registro)
    if registro is not None:
        print(f"Registro de producción por minuto en: {args.registro}/")
    if registro_detecciones is not None:
        print(f"Fotos de defectos para auditoría en: {args.detecciones}/")
    print("Para salir: tecla q en la ventana de video, o Ctrl+C en esta consola.\n")

    try:
        resumen = contador.procesar(
            fuente=config.fuente,
            ruta_salida=args.salida,
            ruta_csv=args.csv,
            mostrar=args.mostrar,
            max_cuadros=args.max_cuadros,
            estado_tablero=estado,
            registro=registro,
            resolucion=resolucion,
            iniciar_detenido=args.iniciar_detenido,
        )
    except (RuntimeError, FileNotFoundError) as error:
        raise SystemExit(
            f"\n[ERROR] {error}\n\n"
            f"La fuente de video configurada es: '{config.fuente}'\n"
            f"  · Cámara USB: probá 0, 1 o 2 (según cuál esté conectada).\n"
            f"  · Celular como cámara: la dirección http:// que muestra la app.\n"
            f"  · Archivo: la ruta al .mp4 tiene que existir.\n"
            f"Se cambia desde el engranaje ⚙ de la pantalla, en 'Cámara o video',\n"
            f"o editando '{args.config}'."
        )

    print("\n===== RESUMEN =====")
    print(f"Botellas contadas : {int(resumen['total'])}")
    print(f"Defectos descartados: {int(resumen['defectos'])}")
    print(f"Velocidad promedio: {resumen['bpm_promedio']:.1f} botellas/min")
    print(f"Duración procesada: {resumen['duracion_s']:.1f} s")


if __name__ == "__main__":
    main()
