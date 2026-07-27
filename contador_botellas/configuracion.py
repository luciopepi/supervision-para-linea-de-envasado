"""Configuración ajustable del sistema desde la pantalla táctil de la HMI.

Estos son los parámetros que el operario puede tocar sin abrir una consola:
posición de la línea de conteo, confianza de detección, tamaño de imagen para
la red, tiempos de la válvula de descarte, botellas por caja y calidad del
video del tablero. Se persisten en un archivo JSON (por defecto
`configuracion.json`, junto al programa) para que sobrevivan a un reinicio.

Los flags de línea de comandos (`--posicion-linea`, `--confianza`...) siguen
funcionando para el primer arranque o para lo que el archivo no traiga; una
vez que el operario ajusta algo desde la HMI, ese archivo manda (ver
`__main__.py` y el README, sección "Pantalla de configuración").
"""

import json
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any


@dataclass
class ConfiguracionAjustable:
    """Parámetros del sistema ajustables en caliente desde la HMI.

    Los límites, el paso de cada botón +/- y las opciones fijas (para
    `orientacion_linea` y `tamano_inferencia`) están en `LIMITES`, no acá:
    esta clase solo guarda el valor vigente de cada parámetro.
    """

    posicion_linea: float = 0.25
    orientacion_linea: str = "vertical"
    confianza: float = 0.25
    tamano_inferencia: int = 640
    valvula_retardo_ms: int = 500
    valvula_duracion_ms: int = 300
    botellas_por_caja: int = 6
    calidad_video: int = 75
    # Campos que solo se leen al arrancar (ver `REQUIERE_REINICIO`): son los
    # que hacen que la aplicación se pueda usar con doble clic en el ícono,
    # sin escribir nada en una consola.
    fuente: str = "0"
    modelo: str = "yolov8n.pt"
    resolucion_camara: str = "1280x720"
    modo: str = "linea"
    puerto: int = 8000
    valvula_puerto_serie: str = ""


# Rango, paso y opciones fijas de cada campo: la HMI los usa para armar los
# botones +/- (o las opciones fijas) del modal de configuración, y `validar`
# los usa para clampear cualquier valor recibido (del archivo o de la HMI).
# `texto` marca los campos de texto libre (rutas, puertos COM, URL de un
# celular usado como cámara): la HMI los edita con el teclado en pantalla.
LIMITES: dict[str, dict[str, Any]] = {
    "posicion_linea": {"min": 0.05, "max": 0.95, "paso": 0.05},
    "orientacion_linea": {"opciones": ["vertical", "horizontal"]},
    "confianza": {"min": 0.05, "max": 0.90, "paso": 0.05},
    "tamano_inferencia": {"opciones": [416, 480, 640, 960]},
    "valvula_retardo_ms": {"min": 0, "max": 5000, "paso": 50},
    "valvula_duracion_ms": {"min": 50, "max": 3000, "paso": 50},
    "botellas_por_caja": {"min": 1, "max": 24, "paso": 1},
    "calidad_video": {"min": 40, "max": 95, "paso": 5},
    "fuente": {"texto": True},
    "modelo": {"texto": True},
    "resolucion_camara": {"opciones": ["640x480", "800x600", "1280x720", "1920x1080"]},
    "modo": {"opciones": ["linea", "caja"]},
    "puerto": {"min": 1024, "max": 65535, "paso": 1},
    "valvula_puerto_serie": {"texto": True, "vacio_ok": True},
}

# Campos que el pipeline lee una sola vez, al arrancar: la cámara, el modelo
# YOLO, el servidor web y el modo de trabajo se montan antes del primer
# cuadro. Cambiarlos desde la HMI los guarda en el archivo, pero recién
# tienen efecto al volver a abrir la aplicación; la HMI lo avisa en pantalla.
REQUIERE_REINICIO: tuple[str, ...] = (
    "fuente",
    "modelo",
    "resolucion_camara",
    "modo",
    "puerto",
    "valvula_puerto_serie",
)

# Tope de los campos de texto: una ruta de Windows o una URL entran de sobra,
# y evita que un archivo corrupto meta un texto gigante en la pantalla.
MAXIMO_TEXTO = 300

# Tipo de cada campo, para castear antes de clampear en `validar`.
_TIPOS: dict[str, type] = {campo.name: campo.type for campo in fields(ConfiguracionAjustable)}


def validar(clave: str, valor: Any) -> Any:
    """Castea y clampea `valor` para el campo `clave` de `ConfiguracionAjustable`.

    Clave desconocida → `ValueError`. Los campos de texto (`texto` en
    `LIMITES`) se limpian de espacios y se rechazan vacíos salvo que tengan
    `vacio_ok` (el puerto COM vacío significa "válvula simulada"). Los campos
    de opciones fijas de texto (`orientacion_linea`, `modo`,
    `resolucion_camara`) solo aceptan una de sus opciones: no hay un "valor
    más cercano" sensato para un texto. Los de opciones numéricas
    (`tamano_inferencia`) se ajustan a la opción más cercana, y los numéricos
    con rango se recortan al límite más cercano.
    """
    if clave not in LIMITES:
        raise ValueError(f"Configuración desconocida: '{clave}'")
    limites = LIMITES[clave]

    if limites.get("texto"):
        texto = str(valor).strip()
        if not texto and not limites.get("vacio_ok"):
            raise ValueError(f"'{clave}' no puede quedar vacío")
        if len(texto) > MAXIMO_TEXTO:
            raise ValueError(f"'{clave}' es demasiado largo (máximo {MAXIMO_TEXTO} caracteres)")
        return texto

    opciones = limites.get("opciones")
    if opciones and isinstance(opciones[0], str):
        valor_normalizado = str(valor).strip().lower()
        if valor_normalizado not in opciones:
            raise ValueError(
                f"'{clave}' debe ser una de estas opciones: {', '.join(opciones)} "
                f"(se recibió: '{valor}')"
            )
        return valor_normalizado

    tipo = _TIPOS[clave]
    try:
        valor_num = tipo(valor)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Valor inválido para '{clave}': {valor!r} ({error})") from error

    if "opciones" in limites:
        opciones = limites["opciones"]
        return min(opciones, key=lambda opcion: abs(opcion - valor_num))

    minimo, maximo = limites["min"], limites["max"]
    return tipo(min(maximo, max(minimo, valor_num)))


def cargar(
    ruta: str | Path, defecto: "ConfiguracionAjustable | None" = None
) -> "ConfiguracionAjustable":
    """Carga la configuración ajustable desde un archivo JSON.

    `defecto` es la base sobre la que se aplican los valores del archivo
    (si no se pasa, se usan los valores por defecto de la dataclass); así
    `__main__.py` puede partir de los flags de la línea de comandos y dejar
    que el archivo los pise campo por campo (ver la regla de precedencia en
    el README). Un campo ausente en el archivo conserva el valor de
    `defecto`; uno fuera de rango se ajusta al límite más cercano con un
    aviso por consola; el archivo ausente, ilegible o con formato inesperado
    no interrumpe el arranque: simplemente se usa `defecto` completo.
    """
    config = replace(defecto if defecto is not None else ConfiguracionAjustable())
    ruta = Path(ruta)
    if not ruta.exists():
        return config
    try:
        contenido = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        print(f"Aviso: no se pudo leer '{ruta}' ({error}); se usa la configuración por defecto.")
        return config
    if not isinstance(contenido, dict):
        print(f"Aviso: '{ruta}' no tiene el formato esperado; se usa la configuración por defecto.")
        return config

    for campo in fields(config):
        if campo.name not in contenido:
            continue
        valor_crudo = contenido[campo.name]
        try:
            valor_normalizado = validar(campo.name, valor_crudo)
        except ValueError as error:
            print(
                f"Aviso: '{campo.name}' inválido en '{ruta}' ({error}); "
                f"se mantiene {getattr(config, campo.name)!r}."
            )
            continue
        if valor_normalizado != valor_crudo:
            print(
                f"Aviso: '{campo.name}' = {valor_crudo!r} fuera de rango en '{ruta}'; "
                f"ajustado a {valor_normalizado!r}."
            )
        setattr(config, campo.name, valor_normalizado)
    return config


def guardar(config: ConfiguracionAjustable, ruta: str | Path) -> None:
    """Escribe `config` como JSON legible en `ruta`.

    Un error de escritura (disco lleno, carpeta sin permisos...) solo se
    avisa por consola: nunca debe tumbar la línea en producción.
    """
    ruta = Path(ruta)
    try:
        ruta.write_text(
            json.dumps(asdict(config), indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as error:
        print(f"Aviso: no se pudo guardar la configuración en '{ruta}' ({error}).")


def par_resolucion(texto: str) -> tuple[int, int]:
    """Convierte una resolución "1280x720" en el par de enteros (1280, 720).

    Un texto con otro formato no interrumpe el arranque: se avisa por consola
    y se cae al valor por defecto de la dataclass, porque quedarse sin cámara
    por un typo en el archivo sería peor que usar una resolución distinta.
    """
    try:
        ancho, alto = (int(parte) for parte in str(texto).lower().split("x"))
    except ValueError:
        defecto = ConfiguracionAjustable.resolucion_camara
        print(f"Aviso: resolución '{texto}' inválida; se usa {defecto}.")
        ancho, alto = (int(parte) for parte in defecto.split("x"))
    return ancho, alto


def como_dict(config: ConfiguracionAjustable) -> dict[str, Any]:
    """Arma el dict que consume la HMI para pintar el modal de configuración.

    Devuelve `{"valores": {...}, "limites": {...}, "reinicio": [...]}`: los
    valores vigentes de cada campo, para cada uno su mínimo/máximo/paso u
    opciones fijas, y la lista de campos que recién se aplican al reiniciar
    — así el JavaScript arma los controles y sus avisos sin tener nada de
    esto hardcodeado.
    """
    return {"valores": asdict(config), "limites": LIMITES, "reinicio": list(REQUIERE_REINICIO)}
