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


# Rango, paso y opciones fijas de cada campo: la HMI los usa para armar los
# botones +/- (o las opciones fijas) del modal de configuración, y `validar`
# los usa para clampear cualquier valor recibido (del archivo o de la HMI).
LIMITES: dict[str, dict[str, Any]] = {
    "posicion_linea": {"min": 0.05, "max": 0.95, "paso": 0.05},
    "orientacion_linea": {"opciones": ["vertical", "horizontal"]},
    "confianza": {"min": 0.05, "max": 0.90, "paso": 0.05},
    "tamano_inferencia": {"opciones": [416, 480, 640, 960]},
    "valvula_retardo_ms": {"min": 0, "max": 5000, "paso": 50},
    "valvula_duracion_ms": {"min": 50, "max": 3000, "paso": 50},
    "botellas_por_caja": {"min": 1, "max": 24, "paso": 1},
    "calidad_video": {"min": 40, "max": 95, "paso": 5},
}

# Tipo de cada campo, para castear antes de clampear en `validar`.
_TIPOS: dict[str, type] = {campo.name: campo.type for campo in fields(ConfiguracionAjustable)}


def validar(clave: str, valor: Any) -> Any:
    """Castea y clampea `valor` para el campo `clave` de `ConfiguracionAjustable`.

    Clave desconocida → `ValueError`. Para los campos numéricos con rango
    (`min`/`max`) se castea al tipo del campo y se recorta al límite más
    cercano. Para los campos de opciones fijas: `orientacion_linea` solo
    acepta "vertical" u "horizontal" (si no, `ValueError`, porque no hay un
    "valor más cercano" sensato para un texto); `tamano_inferencia` se ajusta
    a la opción numérica fija más cercana.
    """
    if clave not in LIMITES:
        raise ValueError(f"Configuración desconocida: '{clave}'")
    limites = LIMITES[clave]

    if clave == "orientacion_linea":
        valor_normalizado = str(valor).strip().lower()
        if valor_normalizado not in limites["opciones"]:
            raise ValueError(
                f"orientacion_linea debe ser 'vertical' u 'horizontal' "
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


def como_dict(config: ConfiguracionAjustable) -> dict[str, Any]:
    """Arma el dict que consume la HMI para pintar el modal de configuración.

    Devuelve `{"valores": {...}, "limites": {...}}`: los valores vigentes de
    cada campo y, para cada uno, su mínimo/máximo/paso u opciones fijas —
    así el JavaScript arma los controles sin tener los límites hardcodeados.
    """
    return {"valores": asdict(config), "limites": LIMITES}
