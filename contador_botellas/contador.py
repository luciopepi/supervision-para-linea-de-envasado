"""Pipeline principal: detección + seguimiento + conteo + velocidad + descarte."""

import csv
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import supervision as sv

from .captura import CapturaEnVivo, configurar_camara
from .clasificador import (
    MINIMO_MUESTRAS_POR_CLASE,
    Clasificador,
    contar_muestras,
    entrenar_en_hilo,
)
from .configuracion import ConfiguracionAjustable, como_dict, guardar, validar
from .detecciones import RegistroDetecciones
from .detector import DetectorBotellas
from .inspeccion import InspectorBotellas
from .partes import (
    CLASE_BOTELLA,
    CLASE_CAJA,
    CLASE_SEPARADOR,
    PARTES_BOTELLA,
    AuditorCajas,
    AuditorPartes,
)
from .registro import RegistroProduccion
from .salidas import ValvulaDescarte
from .tablero import EstadoTablero
from .velocidad import EstimadorVelocidad


@dataclass
class ConfiguracionLinea:
    """Ubicación de la línea de conteo dentro del cuadro.

    `orientacion` es "vertical" (botellas que avanzan en horizontal) u
    "horizontal" (botellas que avanzan en vertical). `posicion` es la fracción
    del ancho/alto donde se ubica la línea (0.5 = centro del cuadro).
    """

    orientacion: str = "vertical"
    posicion: float = 0.5

    def crear_zona(self, ancho: int, alto: int) -> sv.LineZone:
        """Construye la `sv.LineZone` para un cuadro de `ancho` x `alto` píxeles."""
        if self.orientacion == "vertical":
            x = int(ancho * self.posicion)
            inicio, fin = sv.Point(x, 0), sv.Point(x, alto)
        else:
            y = int(alto * self.posicion)
            inicio, fin = sv.Point(0, y), sv.Point(ancho, y)
        # El ancla central hace el conteo más estable que las 4 esquinas
        # cuando las botellas van muy juntas o parcialmente ocluidas.
        return sv.LineZone(
            start=inicio, end=fin, triggering_anchors=[sv.Position.CENTER]
        )


class ContadorBotellas:
    """Orquesta el pipeline completo sobre una fuente de video.

    Fuentes soportadas: índice de cámara web ("0", "1"...), ruta a un archivo
    de video o URL de stream (RTSP/HTTP). Produce un video anotado opcional,
    estadísticas en CSV, tablero web, registro por minuto y señal de descarte.
    """

    def __init__(
        self,
        detector: DetectorBotellas,
        linea: ConfiguracionLinea,
        config: ConfiguracionAjustable,
        ruta_config: str | Path,
        ventana_velocidad: float = 30.0,
        inspector: InspectorBotellas | None = None,
        valvula: ValvulaDescarte | None = None,
        clases_defecto: set[int] | None = None,
        carpeta_muestras: str = "dataset",
        carpeta_modelos: str = "modelos",
        clasificador: Clasificador | None = None,
        sku: str = "general",
        registro_detecciones: RegistroDetecciones | None = None,
        modo: str = "linea",
        botellas_por_caja: int = 6,
    ) -> None:
        """Guarda los componentes del pipeline; el estado se crea en `procesar`.

        `sku` es el producto activo: las muestras van a `dataset/<sku>/<clase>/`
        y el modelo de defectos de ese producto a `modelos/<sku>/clasificador.pt`,
        así cada producto de la línea tiene su propio entrenamiento.
        `registro_detecciones`, si se pasa, guarda foto y CSV de auditoría de
        cada botella descartada (ver `detecciones.py`).

        `modo` es "linea" (contar botellas cruzando la línea, con aviso de
        `sin_tapa`/`sin_etiqueta` si el modelo detecta esas partes) o "caja"
        (contar botellas dentro de cada caja vista desde arriba y verificar
        el separador; requiere un modelo propio con la clase `caja`, ver
        `partes.py`). `botellas_por_caja` es cuántas botellas debe traer cada
        caja completa (solo se usa en modo "caja").

        `config` trae los parámetros ajustables desde la HMI (posición y
        orientación de la línea, confianza, tamaño de inferencia, tiempos de
        válvula, botellas por caja, calidad del video); `ruta_config` es
        dónde persistirlos cuando el operario los cambia desde la pantalla de
        configuración (comando `"configurar"`, ver `_configurar`). `linea` y
        `botellas_por_caja` se siguen aceptando como parámetros sueltos por
        compatibilidad, pero los valores que realmente se usan salen de
        `config` (se aplican al arrancar `procesar` y quedan sincronizados
        con cada cambio en caliente).
        """
        if modo not in ("linea", "caja"):
            raise ValueError(f"Modo inválido: '{modo}' (usar 'linea' o 'caja')")
        self.detector = detector
        self.linea = linea
        self.config = config
        self.ruta_config = Path(ruta_config)
        self.ventana_velocidad = ventana_velocidad
        self.inspector = inspector
        self.valvula = valvula
        self.clases_defecto = clases_defecto or set()
        self.carpeta_muestras = Path(carpeta_muestras)
        self.carpeta_modelos = Path(carpeta_modelos)
        self.clasificador = clasificador
        self.sku = self._nombre_clase_valido(sku)
        self.registro_detecciones = registro_detecciones
        self.modo = modo
        self.botellas_por_caja = botellas_por_caja
        self.calidad_video = config.calidad_video
        self._entrenando = False
        self._muestras_cache: dict[str, int] = {}
        self._muestras_cache_hora: float = 0.0
        self._skus_cache: dict[str, dict] = {}
        self._skus_cache_hora: float = 0.0
        # Zona/rastreador vigentes durante `procesar`; se recrean en caliente
        # desde `_configurar` (línea o confianza) sin perder los totales, que
        # viven en `EstimadorVelocidad`, no acá.
        self._zona: sv.LineZone | None = None
        self._rastreador: sv.ByteTrack | None = None
        self._ancho: int | None = None
        self._alto: int | None = None
        self._fps: float | None = None

    def _carpeta_sku(self) -> Path:
        """Carpeta de muestras del producto activo: dataset/<sku>/."""
        return self.carpeta_muestras / self.sku

    def _modelos_sku(self) -> Path:
        """Carpeta del modelo del producto activo: modelos/<sku>/."""
        return self.carpeta_modelos / self.sku

    def _cambiar_sku(self, nombre: str, estado: EstadoTablero) -> None:
        """Activa otro producto: cambia carpeta de muestras y carga su modelo."""
        self.sku = self._nombre_clase_valido(nombre)
        self._muestras_cache_hora = 0.0
        self._skus_cache_hora = 0.0
        ruta_modelo = self._modelos_sku() / "clasificador.pt"
        if ruta_modelo.exists():
            # Un archivo de modelo dañado no debe tumbar el pipeline: se avisa
            # y el SKU queda activo sin clasificador (solo cuenta botellas).
            try:
                self.clasificador = Clasificador(ruta_modelo)
                estado.agregar_evento(
                    "estado", f"SKU activo: {self.sku} (modelo de defectos cargado)"
                )
            except Exception as error:
                self.clasificador = None
                estado.agregar_evento(
                    "estado",
                    f"SKU activo: {self.sku} — no se pudo cargar su modelo "
                    f"({error}). Re-entrenalo desde la HMI.",
                )
        else:
            self.clasificador = None
            estado.agregar_evento(
                "estado", f"SKU activo: {self.sku} (sin modelo entrenado todavía)"
            )

    def _muestras_sku(self) -> dict[str, int]:
        """Conteo de recortes por clase del SKU activo, cacheado unos segundos."""
        ahora = time.monotonic()
        if ahora - self._muestras_cache_hora > 5.0:
            self._muestras_cache = contar_muestras(self._carpeta_sku())
            self._muestras_cache_hora = ahora
        return self._muestras_cache

    def _listar_skus(self) -> dict[str, dict]:
        """Lista los SKU conocidos con sus muestras por clase y si están entrenados.

        Un SKU "conocido" es cualquier subcarpeta de `dataset/` o de
        `modelos/` (salvo las carpetas internas de entrenamiento
        `dataset_entrenamiento` y `corridas`), más el SKU activo aunque
        todavía no tenga carpetas propias. Se usa para que la HMI ofrezca
        una lista táctil de productos al iniciar la detección o al cambiar
        de SKU. Cacheado 10 s como máximo (igual que `_muestras_sku`);
        `_cambiar_sku` y la captura de muestras invalidan la caché.
        """
        ahora = time.monotonic()
        if ahora - self._skus_cache_hora <= 10.0:
            return self._skus_cache
        carpetas_internas = {"dataset_entrenamiento", "corridas"}
        nombres: set[str] = {self.sku}
        if self.carpeta_muestras.exists():
            nombres.update(
                carpeta.name
                for carpeta in self.carpeta_muestras.iterdir()
                if carpeta.is_dir() and carpeta.name not in carpetas_internas
            )
        if self.carpeta_modelos.exists():
            nombres.update(
                carpeta.name
                for carpeta in self.carpeta_modelos.iterdir()
                if carpeta.is_dir() and carpeta.name not in carpetas_internas
            )
        self._skus_cache = {
            nombre: {
                "clases": contar_muestras(self.carpeta_muestras / nombre),
                "entrenado": (self.carpeta_modelos / nombre / "clasificador.pt").exists(),
            }
            for nombre in sorted(nombres)
        }
        self._skus_cache_hora = ahora
        return self._skus_cache

    def procesar(
        self,
        fuente: str,
        ruta_salida: str | None = None,
        ruta_csv: str | None = None,
        mostrar: bool = False,
        max_cuadros: int | None = None,
        estado_tablero: EstadoTablero | None = None,
        registro: RegistroProduccion | None = None,
        resolucion: tuple[int, int] | None = None,
        iniciar_detenido: bool = False,
    ) -> dict[str, float]:
        """Procesa la fuente cuadro a cuadro y devuelve el resumen final.

        El resumen contiene `total`, `bpm_promedio` y `duracion_s`. Si se pasa
        `estado_tablero`, publica cada cuadro anotado, las estadísticas y
        atiende los comandos de la HMI (iniciar/detener, muestras, válvula).
        """
        # La configuración ajustable manda sobre los parámetros sueltos que
        # se pasaron al constructor: se aplica acá para que un arranque con
        # `configuracion.json` ya existente (o retomado tras un cambio en
        # caliente en una corrida anterior) siempre parta de esos valores.
        self.detector.confianza = self.config.confianza
        self.detector.tamano_inferencia = self.config.tamano_inferencia
        self.linea = ConfiguracionLinea(
            orientacion=self.config.orientacion_linea, posicion=self.config.posicion_linea
        )
        self.botellas_por_caja = self.config.botellas_por_caja
        self.calidad_video = self.config.calidad_video
        if self.valvula is not None:
            self.valvula.retardo_ms = self.config.valvula_retardo_ms
            self.valvula.duracion_ms = self.config.valvula_duracion_ms

        captura_cruda = self._abrir_fuente(fuente, resolucion)
        # Con cámara o stream el reloj de pared es la referencia de tiempo;
        # con un archivo se usa el tiempo del video para que la velocidad no
        # dependa de lo rápido que procese la computadora.
        en_vivo = fuente.isdigit() or fuente.startswith(("rtsp://", "http://", "https://"))
        fps = captura_cruda.get(cv2.CAP_PROP_FPS) or 30.0
        ancho = int(captura_cruda.get(cv2.CAP_PROP_FRAME_WIDTH))
        alto = int(captura_cruda.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if en_vivo:
            print(f"Cámara abierta a {ancho}x{alto}.")
        # En vivo, un hilo dedicado lee la cámara para que la imagen nunca
        # quede atrasada aunque la detección procese más lento.
        captura = CapturaEnVivo(captura_cruda) if en_vivo else captura_cruda
        inicio = time.monotonic()

        # Guardados como atributos (y no variables locales) porque
        # `_configurar` los recrea en caliente cuando el operario toca la
        # pantalla de configuración; el bucle relee `self._zona`/
        # `self._rastreador` en cada vuelta.
        self._ancho, self._alto, self._fps = ancho, alto, fps
        self._zona = self.linea.crear_zona(ancho, alto)
        # La confianza del usuario se aplica acá (activación de rastros) y no
        # en el detector: así ByteTrack recibe también las detecciones dudosas
        # y sostiene el rastro de botellas borrosas u ocluidas junto a la línea.
        self._rastreador = sv.ByteTrack(
            frame_rate=int(fps),
            track_activation_threshold=self.config.confianza,
        )
        velocidad = EstimadorVelocidad(ventana_segundos=self.ventana_velocidad)

        # Con un modelo propio de partes, el detector entrega todas sus
        # clases juntas: acá se decide cuál es la "clase principal" que va al
        # rastreador y a la línea de conteo, y se preparan los auditores que
        # acumulan las demás clases (partes de la botella o de la caja). Con
        # el modelo COCO de hoy no hay clase `botella` de partes ni `caja`:
        # id_principal queda en None y todo sigue exactamente como antes (ni
        # se separan detecciones ni se instancia ningún auditor).
        ids_por_nombre = self.detector.ids_por_nombre
        nombres_clases = self.detector.nombres_clases
        id_principal: int | None = None
        auditor_partes: AuditorPartes | None = None
        auditor_cajas: AuditorCajas | None = None
        tiene_separador = CLASE_SEPARADOR in ids_por_nombre
        if self.modo == "caja":
            if CLASE_CAJA not in ids_por_nombre:
                raise ValueError(
                    "El modo caja necesita un modelo con la clase 'caja' "
                    "(entrenar el detector de partes)."
                )
            id_principal = ids_por_nombre[CLASE_CAJA]
            auditor_cajas = AuditorCajas()
        else:
            if CLASE_BOTELLA in ids_por_nombre:
                id_principal = ids_por_nombre[CLASE_BOTELLA]
            if any(parte in ids_por_nombre for parte in PARTES_BOTELLA):
                auditor_partes = AuditorPartes()

        anotador_cajas = sv.BoxAnnotator(thickness=2)
        anotador_etiquetas = sv.LabelAnnotator(text_scale=0.4, text_padding=4)
        anotador_trazas = sv.TraceAnnotator(thickness=2, trace_length=int(fps))
        anotador_linea = sv.LineZoneAnnotator(
            thickness=2, text_scale=0.6, display_in_count=False, display_out_count=False
        )
        # Anotadores de las partes (tapa/etiqueta/cápsula/separador/corcho...):
        # se dibujan aparte de la clase principal para que el operario VEA qué
        # partes reconoce el modelo propio y pueda validar el entrenamiento.
        # El color sale del id de clase (por defecto en BoxAnnotator), así cada
        # parte lleva su propio color. Con el modelo COCO no hay partes y estos
        # anotadores no se usan nunca.
        anotador_partes = sv.BoxAnnotator(thickness=1)
        anotador_partes_etiquetas = sv.LabelAnnotator(text_scale=0.35, text_padding=2)

        escritor = None
        if ruta_salida:
            codec = cv2.VideoWriter_fourcc(*"mp4v")
            escritor = cv2.VideoWriter(ruta_salida, codec, fps, (ancho, alto))

        archivo_csv = None
        escritor_csv = None
        if ruta_csv:
            archivo_csv = open(ruta_csv, "w", newline="")
            escritor_csv = csv.writer(archivo_csv)
            escritor_csv.writerow(
                ["tiempo_s", "total", "botellas_en_cuadro", "bpm_instantaneo", "bpm_promedio"]
            )

        detectando = not iniciar_detenido
        defectos_total = 0
        descartadas: set[int] = set()  # tracker_ids ya enviados a descarte
        cajas_completas = 0
        cajas_incompletas = 0
        cajas_procesadas: set[int] = set()  # tracker_ids de caja ya evaluados al cruzar
        ultimas_detecciones: sv.Detections | None = None
        ultimo_cuadro_crudo: np.ndarray | None = None
        numero_cuadro = 0
        ultimo_cruce: float | None = None
        try:
            while True:
                ok, cuadro = captura.leer() if en_vivo else captura.read()
                if not ok:
                    break
                if cuadro is None:  # la cámara todavía no entregó el primer cuadro
                    time.sleep(0.01)
                    continue
                numero_cuadro += 1
                if max_cuadros and numero_cuadro > max_cuadros:
                    break
                instante = time.monotonic() - inicio if en_vivo else numero_cuadro / fps
                ultimo_cuadro_crudo = cuadro

                if estado_tablero is not None:
                    detectando = self._atender_comandos(
                        estado_tablero,
                        detectando,
                        ultimo_cuadro_crudo,
                        ultimas_detecciones,
                    )

                if detectando:
                    detecciones = self.detector.detectar_para_seguimiento(cuadro)
                    # El detector entrega todas las clases juntas; se separa
                    # la clase principal (botella en modo línea, caja en modo
                    # caja) del resto de las partes antes de rastrear: solo
                    # la principal va a ByteTrack y a la línea de conteo.
                    if id_principal is not None:
                        es_principal = detecciones.class_id == id_principal
                        detecciones_partes = detecciones[~es_principal]
                        detecciones = detecciones[es_principal]
                    else:
                        detecciones_partes = detecciones[np.zeros(len(detecciones), dtype=bool)]
                    # Se relee `self._rastreador`/`self._zona` en cada vuelta
                    # (y no una copia local tomada antes del bucle) porque
                    # `_configurar` puede reemplazarlos en caliente al tocar
                    # confianza, posición u orientación de la línea.
                    detecciones = self._rastreador.update_with_detections(detecciones)
                    ultimas_detecciones = detecciones
                    entrantes, salientes = self._zona.trigger(detecciones)
                    cruces = int(np.sum(entrantes)) + int(np.sum(salientes))
                    velocidad.registrar_cruces(cruces, instante)
                    if cruces:
                        ultimo_cruce = instante
                    if registro is not None:
                        registro.registrar(
                            cruces, velocidad.total, velocidad.promedio_botellas_por_minuto()
                        )

                    if auditor_partes is not None or auditor_cajas is not None:
                        nombres_partes = [
                            str(nombres_clases.get(int(id_clase), id_clase))
                            for id_clase in detecciones_partes.class_id
                        ]
                        if auditor_partes is not None:
                            auditor_partes.actualizar(
                                detecciones, detecciones_partes.xyxy, nombres_partes
                            )
                        if auditor_cajas is not None:
                            auditor_cajas.actualizar(
                                detecciones, detecciones_partes.xyxy, nombres_partes
                            )

                    alertas = self._detectar_defectos(cuadro, detecciones, auditor_partes)
                    defectos_total += self._descartar_defectuosas(
                        detecciones,
                        entrantes | salientes,
                        alertas,
                        descartadas,
                        estado_tablero,
                        cuadro,
                        velocidad,
                    )
                    if auditor_cajas is not None:
                        nuevas_completas, nuevas_incompletas = self._procesar_cajas_cruzadas(
                            detecciones,
                            entrantes | salientes,
                            auditor_cajas,
                            tiene_separador,
                            cajas_procesadas,
                            estado_tablero,
                            cuadro,
                            velocidad,
                        )
                        cajas_completas += nuevas_completas
                        cajas_incompletas += nuevas_incompletas

                    cuadro = self._anotar(
                        cuadro, detecciones, self._zona, velocidad, alertas,
                        anotador_cajas, anotador_etiquetas, anotador_trazas, anotador_linea,
                        detecciones_partes, nombres_clases,
                        anotador_partes, anotador_partes_etiquetas,
                    )
                else:
                    ultimas_detecciones = None
                    cuadro = self._anotar_pausa(cuadro)
                    if not en_vivo:
                        # Sin este freno, un archivo en pausa se consumiría a
                        # máxima velocidad y el video se "adelantaría" solo.
                        time.sleep(1.0 / fps)

                if escritor is not None:
                    escritor.write(cuadro)
                if escritor_csv is not None and detectando:
                    escritor_csv.writerow(
                        [
                            f"{instante:.2f}",
                            velocidad.total,
                            len(ultimas_detecciones) if ultimas_detecciones is not None else 0,
                            f"{velocidad.botellas_por_minuto():.1f}",
                            f"{velocidad.promedio_botellas_por_minuto():.1f}",
                        ]
                    )
                if estado_tablero is not None:
                    self._publicar(
                        estado_tablero, cuadro, velocidad, ultimas_detecciones,
                        detectando, defectos_total, instante, ultimo_cruce,
                        cajas_completas, cajas_incompletas,
                    )
                if mostrar:
                    cv2.imshow("Contador de botellas", cuadro)
                    tecla = cv2.waitKey(1) & 0xFF
                    if tecla in (ord("q"), ord("Q"), 27):  # q o Esc
                        break
        except KeyboardInterrupt:
            print("\nDetenido con Ctrl+C.")
        finally:
            if en_vivo:
                captura.liberar()
            else:
                captura.release()
            if escritor is not None:
                escritor.release()
            if archivo_csv is not None:
                archivo_csv.close()
            if registro is not None:
                registro.cerrar(velocidad.total, velocidad.promedio_botellas_por_minuto())
            if self.valvula is not None:
                self.valvula.cerrar()
            if mostrar:
                cv2.destroyAllWindows()

        return {
            "total": float(velocidad.total),
            "bpm_promedio": velocidad.promedio_botellas_por_minuto(),
            "duracion_s": numero_cuadro / fps,
            "defectos": float(defectos_total),
        }

    @staticmethod
    def _abrir_fuente(
        fuente: str, resolucion: tuple[int, int] | None = None
    ) -> cv2.VideoCapture:
        """Abre cámara web (índice numérico), archivo de video o URL de stream."""
        if fuente.isdigit():
            print(f"Abriendo cámara {fuente}...")
            # En Windows el backend por defecto (Media Foundation) puede
            # colgarse minutos al abrir la webcam; DirectShow abre al instante.
            if sys.platform == "win32":
                captura = cv2.VideoCapture(int(fuente), cv2.CAP_DSHOW)
                if not captura.isOpened():
                    captura = cv2.VideoCapture(int(fuente))
            else:
                captura = cv2.VideoCapture(int(fuente))
            if captura.isOpened() and resolucion is not None:
                configurar_camara(captura, *resolucion)
        else:
            if not fuente.startswith(("rtsp://", "http://", "https://")):
                if not Path(fuente).exists():
                    raise FileNotFoundError(f"No existe el archivo de video: {fuente}")
            captura = cv2.VideoCapture(fuente)
        if not captura.isOpened():
            raise RuntimeError(f"No se pudo abrir la fuente de video: {fuente}")
        return captura

    def _atender_comandos(
        self,
        estado: EstadoTablero,
        detectando: bool,
        cuadro: np.ndarray | None,
        detecciones: sv.Detections | None,
    ) -> bool:
        """Ejecuta los comandos pendientes de la HMI y devuelve el nuevo estado."""
        for comando in estado.obtener_comandos():
            accion = comando.get("accion")
            if accion == "iniciar":
                sku_pedido = comando.get("sku")
                if sku_pedido:
                    nombre_valido = self._nombre_clase_valido(str(sku_pedido))
                    if nombre_valido != self.sku:
                        self._cambiar_sku(nombre_valido, estado)
                detectando = True
                estado.agregar_evento("estado", "Detección iniciada")
            elif accion == "detener":
                detectando = False
                estado.agregar_evento("estado", "Detección detenida")
            elif accion == "capturar" and cuadro is not None:
                clase = self._nombre_clase_valido(str(comando.get("clase", "ok")))
                # Con la detección en pausa no hay detecciones del pipeline:
                # se corre una detección puntual para poder recortar botellas.
                if detecciones is None or len(detecciones) == 0:
                    detecciones = self.detector.detectar(cuadro)
                recortes = self._guardar_muestras(cuadro, detecciones, clase)
                self._muestras_cache_hora = 0.0
                self._skus_cache_hora = 0.0
                totales = ", ".join(f"{c}: {n}" for c, n in self._muestras_sku().items())
                if recortes == 0:
                    estado.agregar_evento(
                        "muestra",
                        f"⚠ No se detectó ninguna botella en el cuadro: no se "
                        f"guardaron recortes. Acercá la botella.",
                    )
                else:
                    estado.agregar_evento(
                        "muestra",
                        f"{recortes} recorte(s) en dataset/{self.sku}/{clase}/ "
                        f"— total: {totales}",
                    )
            elif accion == "cambiar_sku":
                self._cambiar_sku(str(comando.get("sku", "general")), estado)
            elif accion == "probar_valvula":
                if self.valvula is not None:
                    self.valvula.probar()
                    modo = "SIMULADA" if self.valvula.simulada else "real"
                    estado.agregar_evento("valvula", f"Prueba de válvula ({modo})")
                else:
                    estado.agregar_evento("valvula", "Válvula no configurada (--valvula-puerto)")
            elif accion == "entrenar":
                detectando = self._iniciar_entrenamiento(estado, detectando)
            elif accion == "configurar":
                self._configurar(estado, str(comando.get("clave", "")), comando.get("valor"))
        return detectando

    def _configurar(self, estado: EstadoTablero, clave: str, valor: object) -> None:
        """Aplica en caliente un ajuste pedido desde la pantalla de configuración.

        Valida y clampea `valor` con `configuracion.validar`; si la clave no
        existe o el valor no tiene sentido (por ejemplo una orientación que
        no sea "vertical"/"horizontal"), no toca nada y deja un evento con el
        error. Si es válido, actualiza `self.config`, aplica el cambio al
        componente correspondiente y persiste la configuración completa en
        `self.ruta_config`.
        """
        try:
            valor_normalizado = validar(clave, valor)
        except ValueError as error:
            estado.agregar_evento("configuracion", f"⚠ No se pudo ajustar: {error}")
            return

        setattr(self.config, clave, valor_normalizado)

        if clave in ("posicion_linea", "orientacion_linea"):
            # Los totales de conteo viven en EstimadorVelocidad, no en la
            # LineZone: recrearla no pierde lo ya contado.
            self.linea = ConfiguracionLinea(
                orientacion=self.config.orientacion_linea,
                posicion=self.config.posicion_linea,
            )
            if self._ancho is not None and self._alto is not None:
                self._zona = self.linea.crear_zona(self._ancho, self._alto)
        elif clave == "confianza":
            self.detector.confianza = valor_normalizado
            if self._fps is not None:
                # Se pierde el rastro un instante al recrear ByteTrack; es
                # aceptable frente a mantener la confianza vieja hasta que el
                # operario reinicie el proceso.
                self._rastreador = sv.ByteTrack(
                    frame_rate=int(self._fps), track_activation_threshold=valor_normalizado
                )
        elif clave == "tamano_inferencia":
            self.detector.tamano_inferencia = valor_normalizado
        elif clave == "valvula_retardo_ms":
            if self.valvula is not None:
                self.valvula.retardo_ms = valor_normalizado
        elif clave == "valvula_duracion_ms":
            if self.valvula is not None:
                self.valvula.duracion_ms = valor_normalizado
        elif clave == "botellas_por_caja":
            self.botellas_por_caja = valor_normalizado
        elif clave == "calidad_video":
            self.calidad_video = valor_normalizado

        guardar(self.config, self.ruta_config)
        estado.agregar_evento("configuracion", f"Configuración: {clave} → {valor_normalizado}")

    def _iniciar_entrenamiento(self, estado: EstadoTablero, detectando: bool) -> bool:
        """Lanza el entrenamiento del clasificador en segundo plano.

        Pausa la detección mientras entrena para dejarle la CPU al
        entrenamiento; al terminar carga el modelo nuevo en caliente.
        """
        if self._entrenando:
            estado.agregar_evento("entrenamiento", "Ya hay un entrenamiento en curso")
            return detectando
        # Validación previa: si faltan muestras se avisa sin pausar nada.
        conteo = contar_muestras(self._carpeta_sku())
        validas = {c: n for c, n in conteo.items() if n >= MINIMO_MUESTRAS_POR_CLASE}
        if len(validas) < 2:
            estado.agregar_evento(
                "entrenamiento",
                f"Faltan muestras para entrenar el SKU '{self.sku}': se necesitan "
                f"2 clases con {MINIMO_MUESTRAS_POR_CLASE}+ recortes. "
                f"Hay: {conteo or 'ninguna'}. "
                f"Usá MUESTRA OK / MUESTRA DEFECTO con una botella a la vista.",
            )
            return detectando
        self._entrenando = True
        estado.agregar_evento(
            "entrenamiento",
            f"Entrenamiento del SKU '{self.sku}' iniciado (la detección queda en pausa)",
        )

        def al_terminar(ruta) -> None:
            """Carga el clasificador recién entrenado y avisa en la HMI."""
            if ruta is not None:
                try:
                    self.clasificador = Clasificador(ruta)
                    estado.agregar_evento(
                        "entrenamiento",
                        "Modelo listo: tocá INICIAR DETECCIÓN para inspeccionar",
                    )
                except Exception as error:
                    estado.agregar_evento("entrenamiento", f"Error al cargar modelo: {error}")
            self._entrenando = False

        entrenar_en_hilo(
            self._carpeta_sku(),
            self._modelos_sku(),
            informar=lambda mensaje: estado.agregar_evento("entrenamiento", mensaje),
            al_terminar=al_terminar,
        )
        return False

    @staticmethod
    def _nombre_clase_valido(clase: str) -> str:
        """Convierte el nombre de clase de la HMI en un nombre de carpeta seguro."""
        limpio = "".join(c for c in clase.lower().strip() if c.isalnum() or c in "_-")
        return limpio or "defecto"

    def _guardar_muestras(
        self, cuadro: np.ndarray, detecciones: sv.Detections | None, clase: str
    ) -> int:
        """Guarda el cuadro completo y el recorte de cada botella en dataset/<clase>/.

        El entrenamiento usa solo los recortes (`*_bot*.jpg`); el cuadro
        completo queda como referencia. Devuelve cuántos recortes se guardaron.
        """
        carpeta = self._carpeta_sku() / clase
        carpeta.mkdir(parents=True, exist_ok=True)
        marca = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        cv2.imwrite(str(carpeta / f"{marca}_cuadro.jpg"), cuadro)
        recortes = 0
        if detecciones is not None and len(detecciones) > 0:
            alto_cuadro, ancho_cuadro = cuadro.shape[:2]
            for i, caja in enumerate(detecciones.xyxy):
                x1, y1, x2, y2 = (int(v) for v in caja)
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(ancho_cuadro, x2), min(alto_cuadro, y2)
                if x2 - x1 > 10 and y2 - y1 > 10:
                    cv2.imwrite(str(carpeta / f"{marca}_bot{i}.jpg"), cuadro[y1:y2, x1:x2])
                    recortes += 1
        return recortes

    def _detectar_defectos(
        self,
        cuadro: np.ndarray,
        detecciones: sv.Detections,
        auditor_partes: AuditorPartes | None = None,
    ) -> dict[int, str]:
        """Junta alertas de defecto por tracker_id: clasificador entrenado,
        clases del modelo detector, heurística de inspección y partes
        faltantes (`sin_tapa`/`sin_etiqueta`) del auditor de partes."""
        alertas: dict[int, str] = {}
        if detecciones.tracker_id is None:
            return alertas
        if self.clasificador is not None:
            alto_cuadro, ancho_cuadro = cuadro.shape[:2]
            for caja, tracker_id in zip(detecciones.xyxy, detecciones.tracker_id):
                x1, y1, x2, y2 = (int(v) for v in caja)
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(ancho_cuadro, x2), min(alto_cuadro, y2)
                if x2 <= x1 or y2 <= y1:
                    continue
                clase = self.clasificador.clasificar_botella(
                    cuadro[y1:y2, x1:x2], int(tracker_id)
                )
                if clase is not None and clase != "ok":
                    alertas[int(tracker_id)] = clase
        if self.clases_defecto:
            nombres = self.detector.nombres_clases
            for clase_id, tracker_id in zip(detecciones.class_id, detecciones.tracker_id):
                if int(clase_id) in self.clases_defecto:
                    alertas.setdefault(int(tracker_id), str(nombres.get(int(clase_id), clase_id)))
        if self.inspector is not None:
            for resultado in self.inspector.inspeccionar(cuadro, detecciones):
                if resultado.alerta:
                    alertas.setdefault(resultado.tracker_id, resultado.alerta)
        if auditor_partes is not None:
            for tracker_id in detecciones.tracker_id:
                tid = int(tracker_id)
                faltantes = auditor_partes.faltantes(tid)
                if faltantes:
                    alertas.setdefault(tid, ", ".join(faltantes))
        return alertas

    def _descartar_defectuosas(
        self,
        detecciones: sv.Detections,
        cruzaron: np.ndarray,
        alertas: dict[int, str],
        descartadas: set[int],
        estado: EstadoTablero | None,
        cuadro: np.ndarray,
        velocidad: EstimadorVelocidad,
    ) -> int:
        """Activa la válvula para cada botella defectuosa que cruza la línea.

        Devuelve cuántos descartes nuevos hubo. Cada tracker_id se descarta una
        sola vez, aunque siga apareciendo en cuadros siguientes. Si hay un
        `RegistroDetecciones` configurado, guarda foto y fila de auditoría de
        cada descarte nuevo (`cuadro` es el cuadro crudo, sin anotar).
        """
        if not alertas or detecciones.tracker_id is None:
            return 0
        nuevos = 0
        for caja, tracker_id, cruzo in zip(
            detecciones.xyxy, detecciones.tracker_id, cruzaron
        ):
            tid = int(tracker_id)
            if cruzo and tid in alertas and tid not in descartadas:
                descartadas.add(tid)
                nuevos += 1
                if self.valvula is not None:
                    self.valvula.descartar()
                if self.registro_detecciones is not None:
                    self.registro_detecciones.registrar(
                        cuadro,
                        caja,
                        self.sku,
                        tid,
                        alertas[tid],
                        velocidad.total,
                        velocidad.botellas_por_minuto(),
                    )
                    detalle = f"Botella #{tid} descartada: {alertas[tid]} (foto en detecciones/)"
                else:
                    detalle = f"Botella #{tid} descartada: {alertas[tid]}"
                if estado is not None:
                    estado.agregar_evento("descarte", detalle)
        return nuevos

    def _procesar_cajas_cruzadas(
        self,
        detecciones: sv.Detections,
        cruzaron: np.ndarray,
        auditor_cajas: AuditorCajas,
        tiene_separador: bool,
        procesadas: set[int],
        estado: EstadoTablero | None,
        cuadro: np.ndarray,
        velocidad: EstimadorVelocidad,
    ) -> tuple[int, int]:
        """Evalúa cada caja que cruza la línea: cierres presentes y separador.

        Cuenta los cierres (tapa/corcho/cápsula) contenidos en la caja, no
        las botellas: vistas de arriba y con poca luz el cierre es lo más
        visible, y un cierre de menos delata tanto una botella faltante
        como una botella sin tapar. Cada tracker_id de caja se evalúa una
        sola vez, al cruzar. Siempre agrega un evento con el resultado
        ("Caja #3: 6/6 botellas tapadas ✓" o "...INCOMPLETA"); si está
        incompleta o (teniendo el modelo la clase `separador`) no se vio el
        separador, además registra foto de auditoría y activa la válvula de
        descarte. Devuelve (cajas_completas_nuevas, cajas_incompletas_nuevas).
        """
        completas = 0
        incompletas = 0
        if detecciones.tracker_id is None:
            return completas, incompletas
        for caja_xyxy, tracker_id, cruzo in zip(
            detecciones.xyxy, detecciones.tracker_id, cruzaron
        ):
            tid = int(tracker_id)
            if not cruzo or tid in procesadas:
                continue
            procesadas.add(tid)
            resultado = auditor_cajas.resultado(tid, self.botellas_por_caja)
            cierres, esperadas = resultado["cierres"], resultado["esperadas"]
            completa = resultado["completa"]
            falta_separador = tiene_separador and not resultado["separador"]
            if completa:
                completas += 1
                texto = f"Caja #{tid}: {cierres}/{esperadas} botellas tapadas ✓"
            else:
                incompletas += 1
                texto = (
                    f"Caja #{tid}: {cierres}/{esperadas} botellas tapadas — "
                    f"INCOMPLETA (falta una botella o una tapa)"
                )
            if falta_separador:
                texto += ", sin separador"
            if estado is not None:
                estado.agregar_evento("caja", texto)
            if not completa or falta_separador:
                defecto = f"caja_{cierres}de{esperadas}" if not completa else "sin_separador"
                if self.valvula is not None:
                    self.valvula.descartar()
                if self.registro_detecciones is not None:
                    self.registro_detecciones.registrar(
                        cuadro, caja_xyxy, self.sku, tid, defecto,
                        velocidad.total, velocidad.botellas_por_minuto(),
                    )
        return completas, incompletas

    def _publicar(
        self,
        estado: EstadoTablero,
        cuadro: np.ndarray,
        velocidad: EstimadorVelocidad,
        detecciones: sv.Detections | None,
        detectando: bool,
        defectos_total: int,
        instante: float,
        ultimo_cruce: float | None,
        cajas_completas: int = 0,
        cajas_incompletas: int = 0,
    ) -> None:
        """Publica el cuadro anotado y las estadísticas para el tablero web."""
        ok_jpeg, jpeg = cv2.imencode(
            ".jpg", cuadro, [cv2.IMWRITE_JPEG_QUALITY, self.calidad_video]
        )
        if not ok_jpeg:
            return
        sin_cruce = 1e9 if ultimo_cruce is None else instante - ultimo_cruce
        valvula_info = None
        if self.valvula is not None:
            valvula_info = {
                "simulada": self.valvula.simulada,
                "activaciones": self.valvula.activaciones,
            }
        datos = {
            "total": velocidad.total,
            "bpm": round(velocidad.botellas_por_minuto(), 1),
            "bpm_promedio": round(velocidad.promedio_botellas_por_minuto(), 1),
            "en_cuadro": len(detecciones) if detecciones is not None else 0,
            "defectos": defectos_total,
            "detectando": detectando,
            "entrenando": self._entrenando,
            "clasificador_activo": self.clasificador is not None,
            "sku": self.sku,
            "muestras": self._muestras_sku(),
            "skus": self._listar_skus(),
            "valvula": valvula_info,
            "segundos_sin_cruce": round(sin_cruce, 1),
            "hora": time.time(),
            "modo": self.modo,
            "config": como_dict(self.config),
        }
        if self.modo == "caja":
            datos["cajas_completas"] = cajas_completas
            datos["cajas_incompletas"] = cajas_incompletas
        estado.publicar(jpeg.tobytes(), datos)

    @staticmethod
    def _anotar_pausa(cuadro: np.ndarray) -> np.ndarray:
        """Marca visualmente que la detección está en pausa (video sigue en vivo)."""
        capa = cuadro.copy()
        cv2.rectangle(capa, (8, 8), (330, 44), (0, 0, 0), -1)
        cuadro = cv2.addWeighted(capa, 0.55, cuadro, 0.45, 0)
        cv2.putText(
            cuadro, "DETECCION EN PAUSA", (16, 34),
            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (80, 80, 235), 2, cv2.LINE_AA,
        )
        return cuadro

    def _anotar(
        self,
        cuadro: np.ndarray,
        detecciones: sv.Detections,
        zona: sv.LineZone,
        velocidad: EstimadorVelocidad,
        alertas: dict[int, str],
        anotador_cajas: sv.BoxAnnotator,
        anotador_etiquetas: sv.LabelAnnotator,
        anotador_trazas: sv.TraceAnnotator,
        anotador_linea: sv.LineZoneAnnotator,
        detecciones_partes: sv.Detections | None = None,
        nombres_clases: dict[int, str] | None = None,
        anotador_partes: sv.BoxAnnotator | None = None,
        anotador_partes_etiquetas: sv.LabelAnnotator | None = None,
    ) -> np.ndarray:
        """Dibuja detecciones, partes, línea de conteo y panel de estadísticas sobre el cuadro.

        Además de la clase principal (botella o caja) con su rastro y su
        tracker_id, dibuja las partes detectadas (`detecciones_partes`: tapa,
        etiqueta, cápsula, separador...) con su nombre y confianza, para que el
        operario vea qué reconoce el modelo propio. Con el modelo COCO
        `detecciones_partes` viene vacío y no se dibuja ninguna parte.
        """
        etiquetas = []
        if detecciones.tracker_id is not None:
            for tracker_id, conf in zip(detecciones.tracker_id, detecciones.confidence):
                texto = f"#{tracker_id} {conf:.2f}"
                alerta = alertas.get(int(tracker_id))
                if alerta:
                    texto += f" DEFECTO:{alerta}"
                etiquetas.append(texto)

        cuadro = anotador_trazas.annotate(cuadro, detecciones)
        cuadro = anotador_cajas.annotate(cuadro, detecciones)
        if etiquetas:
            cuadro = anotador_etiquetas.annotate(cuadro, detecciones, labels=etiquetas)
        # Las partes van encima de la botella porque suelen caer dentro de su
        # recuadro; su etiqueta muestra el nombre de la clase y la confianza.
        if (
            detecciones_partes is not None
            and len(detecciones_partes) > 0
            and anotador_partes is not None
            and anotador_partes_etiquetas is not None
        ):
            nombres = nombres_clases or {}
            etiquetas_partes = [
                f"{nombres.get(int(id_clase), id_clase)} {confianza:.2f}"
                for id_clase, confianza in zip(
                    detecciones_partes.class_id, detecciones_partes.confidence
                )
            ]
            cuadro = anotador_partes.annotate(cuadro, detecciones_partes)
            cuadro = anotador_partes_etiquetas.annotate(
                cuadro, detecciones_partes, labels=etiquetas_partes
            )
        cuadro = anotador_linea.annotate(cuadro, line_counter=zona)

        panel = [
            f"BOTELLAS: {velocidad.total}",
            f"VELOCIDAD: {velocidad.botellas_por_minuto():.1f} bot/min",
            f"PROMEDIO: {velocidad.promedio_botellas_por_minuto():.1f} bot/min",
        ]
        # Panel semitransparente arriba a la izquierda para legibilidad.
        alto_panel = 22 * len(panel) + 16
        capa = cuadro.copy()
        cv2.rectangle(capa, (8, 8), (280, alto_panel), (0, 0, 0), -1)
        cuadro = cv2.addWeighted(capa, 0.55, cuadro, 0.45, 0)
        for i, linea_texto in enumerate(panel):
            cv2.putText(
                cuadro,
                linea_texto,
                (16, 30 + 22 * i),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )
        return cuadro
