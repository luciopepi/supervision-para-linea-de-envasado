"""Detección de partes de la botella y de la caja (modelo propio, en preparación).

Hoy el detector solo distingue "botella" (con el modelo COCO o uno propio
básico). Este módulo prepara la lógica para un futuro modelo entrenado en el
equipo con clases más finas:

- `botella`, `tapa`, `etiqueta_frente`, `etiqueta_dorso`: para el modo
  "línea", que además de contar botellas avisa si a alguna le faltó la tapa
  o ninguna etiqueta (`AuditorPartes`).
- `caja`, `separador`: para el modo "caja", que cuenta cuántas botellas trae
  cada caja vista desde arriba y si tiene el separador de cartón
  (`AuditorCajas`).

Toda la lógica de acá se activa solo si el modelo cargado tiene esas clases
(ver `detector.py` y `contador.py`); con el modelo COCO de hoy no se usa nada
de este módulo.
"""

import numpy as np
import supervision as sv

# Nombres de clase exactos que va a usar el futuro modelo propio de partes
# (en minúsculas, tal como los vea `DetectorBotellas.nombres_clases`).
CLASE_BOTELLA = "botella"
CLASE_TAPA = "tapa"
CLASE_ETIQUETA_FRENTE = "etiqueta_frente"
CLASE_ETIQUETA_DORSO = "etiqueta_dorso"
CLASE_CAJA = "caja"
CLASE_SEPARADOR = "separador"
CLASE_CORCHO = "corcho"
CLASE_CAPSULA = "capsula"
CLASE_NIVEL_LLENADO = "nivel_llenado"

# Partes que se esperan ver en una botella individual (modo línea).
PARTES_BOTELLA = (
    CLASE_TAPA,
    CLASE_ETIQUETA_FRENTE,
    CLASE_ETIQUETA_DORSO,
    CLASE_CORCHO,
    CLASE_CAPSULA,
    CLASE_NIVEL_LLENADO,
)

# Cualquiera de estos cierres visto sobre la botella cuenta como "tiene
# cierre": tapa a rosca, corcho o cápsula según el producto. La alerta
# `sin_tapa` se dispara solo si no se vio ninguno de los tres.
CIERRES_BOTELLA = (CLASE_TAPA, CLASE_CORCHO, CLASE_CAPSULA)

# Fracción mínima del área de una parte que tiene que caer dentro de la
# botella/caja para considerarla asociada a ella. No es 1.0 porque una tapa
# o una botella puede sobresalir levemente del cuadro que la contiene.
UMBRAL_CONTENCION = 0.5

# Cuadros mínimos en los que hay que haber visto una botella antes de poder
# opinar si le faltó una parte: con pocos cuadros (recién entra a escena,
# ocluida) no hay datos suficientes para no dar falsos positivos.
MINIMO_CUADROS = 5


def fraccion_contenida(contenedores: np.ndarray, contenidos: np.ndarray) -> np.ndarray:
    """Calcula qué fracción del área de cada caja "contenida" cae dentro de cada "contenedor".

    `contenedores` es (N, 4) y `contenidos` es (M, 4), ambos cajas en formato
    xyxy (x1, y1, x2, y2). Devuelve una matriz (N, M) donde la celda [i, j]
    es la fracción del área de `contenidos[j]` que queda dentro de
    `contenedores[i]` (1.0 = totalmente contenida, 0.0 = sin superposición).
    Una caja "contenida" con área cero (detección degenerada) da fracción 0
    en vez de dividir por cero. Totalmente vectorizado con NumPy: sin bucles
    por detección, para no volverse un cuello de botella con muchas partes
    por cuadro.
    """
    cantidad_contenedores, cantidad_contenidos = len(contenedores), len(contenidos)
    if cantidad_contenedores == 0 or cantidad_contenidos == 0:
        return np.zeros((cantidad_contenedores, cantidad_contenidos), dtype=np.float64)

    # Broadcasting (N, 1) contra (M,) da la matriz (N, M) de una sola vez.
    x1 = np.maximum(contenedores[:, 0:1], contenidos[:, 0])
    y1 = np.maximum(contenedores[:, 1:2], contenidos[:, 1])
    x2 = np.minimum(contenedores[:, 2:3], contenidos[:, 2])
    y2 = np.minimum(contenedores[:, 3:4], contenidos[:, 3])
    ancho_interseccion = np.clip(x2 - x1, a_min=0, a_max=None)
    alto_interseccion = np.clip(y2 - y1, a_min=0, a_max=None)
    area_interseccion = ancho_interseccion * alto_interseccion

    area_contenidos = (contenidos[:, 2] - contenidos[:, 0]) * (contenidos[:, 3] - contenidos[:, 1])
    return np.where(area_contenidos > 0, area_interseccion / np.where(area_contenidos > 0, area_contenidos, 1), 0.0)


class AuditorPartes:
    """Acumula, por botella (tracker_id), qué partes se le vieron a lo largo de su paso.

    Pensado para el modo "línea" con un modelo propio que además de
    `botella` reconoce `tapa`, `etiqueta_frente` y `etiqueta_dorso`: esas
    partes pueden aparecer sueltas en el cuadro (la caja que dibuja el
    detector alrededor de la tapa no coincide exactamente con la de la
    botella), así que hay que asociar cada una a la botella que más la
    contiene antes de poder decidir si a una botella le faltó algo.
    """

    _MAX_IDS_EN_MEMORIA = 500
    _IDS_A_CONSERVAR = 100

    def __init__(self) -> None:
        """Inicializa los acumuladores vacíos (uno por tracker_id de botella)."""
        self._partes_vistas: dict[int, set[str]] = {}
        self._cuadros_vistos: dict[int, int] = {}

    def actualizar(
        self, botellas: sv.Detections, partes_xyxy: np.ndarray, partes_nombres: list[str]
    ) -> None:
        """Asocia cada parte detectada en este cuadro a la botella que más la contiene.

        Para cada parte busca, entre las `botellas` (ya con `tracker_id` del
        seguidor), la de mayor fracción de contención; si supera
        `UMBRAL_CONTENCION` registra el nombre de esa parte para ese
        tracker_id. También suma 1 al contador de cuadros vistos de cada
        botella presente en este cuadro, tenga o no partes asociadas esta vez.
        """
        if botellas.tracker_id is None or len(botellas) == 0:
            return
        for tracker_id in botellas.tracker_id:
            tid = int(tracker_id)
            self._cuadros_vistos[tid] = self._cuadros_vistos.get(tid, 0) + 1
            self._partes_vistas.setdefault(tid, set())

        if len(partes_nombres) > 0:
            fracciones = fraccion_contenida(botellas.xyxy, partes_xyxy)
            mejor_botella = np.argmax(fracciones, axis=0)
            mejor_fraccion = np.max(fracciones, axis=0)
            for indice_parte, nombre_parte in enumerate(partes_nombres):
                if mejor_fraccion[indice_parte] >= UMBRAL_CONTENCION:
                    tid = int(botellas.tracker_id[mejor_botella[indice_parte]])
                    self._partes_vistas[tid].add(nombre_parte)

        self._limpiar_memoria()

    def faltantes(self, tracker_id: int) -> list[str]:
        """Devuelve las partes que le faltaron a una botella (o [] si no hay datos aún).

        Con menos de `MINIMO_CUADROS` cuadros vistos no hay datos suficientes
        para opinar y devuelve lista vacía. "sin_tapa" solo si nunca se le
        vio NINGÚN cierre (`tapa` a rosca, `corcho` o `capsula`: cuál
        corresponde depende del producto, pero una botella cerrada muestra
        al menos uno). "sin_etiqueta" solo si nunca se le vio NI
        `etiqueta_frente` NI `etiqueta_dorso` (una botella normal solo
        muestra una de las dos según cómo rota al pasar frente a la cámara).
        """
        if self._cuadros_vistos.get(tracker_id, 0) < MINIMO_CUADROS:
            return []
        vistas = self._partes_vistas.get(tracker_id, set())
        faltantes: list[str] = []
        if not vistas.intersection(CIERRES_BOTELLA):
            faltantes.append("sin_tapa")
        if CLASE_ETIQUETA_FRENTE not in vistas and CLASE_ETIQUETA_DORSO not in vistas:
            faltantes.append("sin_etiqueta")
        return faltantes

    def _limpiar_memoria(self) -> None:
        """Poda los acumuladores más viejos si superan ~500 tracker_ids en memoria."""
        if len(self._cuadros_vistos) <= self._MAX_IDS_EN_MEMORIA:
            return
        ids_recientes = list(self._cuadros_vistos.items())[-self._IDS_A_CONSERVAR :]
        self._cuadros_vistos = dict(ids_recientes)
        ids_a_conservar = {tid for tid, _ in ids_recientes}
        self._partes_vistas = {
            tid: partes for tid, partes in self._partes_vistas.items() if tid in ids_a_conservar
        }


class AuditorCajas:
    """Acumula, por caja (tracker_id), cuántos cierres se le vieron y si tuvo separador.

    Pensado para el modo "caja": una cámara cenital ve la caja abierta con
    las botellas adentro (y, si el packaging lo usa, un separador de
    cartón). Se cuentan los CIERRES (tapa, corcho o cápsula), no las
    botellas: vistas desde arriba y con poca luz, el cuerpo de una botella
    de vidrio oscuro (vino tinto) casi no se distingue, pero el cierre
    queda arriba mirando a la cámara y es lo más visible. Contar cierres
    detecta de una dos defectos: si a la caja le falta una botella o si
    una botella va sin tapar, en ambos casos hay un cierre de menos. Se
    acumula por cuadro para no confiar en una sola lectura donde un cierre
    puede quedar tapado por un reflejo o la mano del operario.
    """

    _MAX_IDS_EN_MEMORIA = 500
    _IDS_A_CONSERVAR = 100

    def __init__(self) -> None:
        """Inicializa los acumuladores vacíos (uno por tracker_id de caja)."""
        self._historial: dict[int, list[int]] = {}
        self._separador_visto: dict[int, bool] = {}

    def actualizar(
        self, cajas: sv.Detections, partes_xyxy: np.ndarray, partes_nombres: list[str]
    ) -> None:
        """Cuenta, para cada caja de este cuadro, los cierres y el separador contenidos.

        Agrega la cantidad de cierres contenidos (tapa, corcho o cápsula:
        cada botella tapada aporta uno) al historial de cada tracker_id de
        caja (la mediana de ese historial es más robusta que la lectura de
        un único cuadro) y marca si en algún cuadro se vio un `separador`
        contenido en esa caja.
        """
        if cajas.tracker_id is None or len(cajas) == 0:
            return
        cantidad_partes = len(partes_nombres)
        if cantidad_partes > 0:
            nombres = np.asarray(partes_nombres, dtype=object)
            es_cierre = np.isin(nombres, CIERRES_BOTELLA)
            es_separador = nombres == CLASE_SEPARADOR
            contenida = fraccion_contenida(cajas.xyxy, partes_xyxy) >= UMBRAL_CONTENCION
        else:
            es_cierre = es_separador = np.zeros(0, dtype=bool)
            contenida = np.zeros((len(cajas), 0), dtype=bool)

        for indice_caja, tracker_id in enumerate(cajas.tracker_id):
            tid = int(tracker_id)
            fila = contenida[indice_caja]
            cantidad_cierres = int(np.sum(fila & es_cierre))
            vio_separador = bool(np.any(fila & es_separador))
            self._historial.setdefault(tid, []).append(cantidad_cierres)
            self._separador_visto[tid] = self._separador_visto.get(tid, False) or vio_separador

        self._limpiar_memoria()

    def resultado(self, tracker_id: int, esperadas: int) -> dict[str, int | bool]:
        """Resume el estado final de una caja al cruzar la línea.

        `cierres` es la mediana entera del historial de cierres por cuadro
        (0 si nunca se acumuló historial para esa caja); `completa` es si esa
        mediana alcanza `esperadas`; `separador` es si alguna vez se vio la
        clase `separador` contenida en esa caja. Un cierre de menos indica
        que falta una botella o que una botella va sin tapar.
        """
        historial = self._historial.get(tracker_id, [])
        cierres = int(round(np.median(historial))) if historial else 0
        return {
            "cierres": cierres,
            "esperadas": esperadas,
            "completa": cierres >= esperadas,
            "separador": self._separador_visto.get(tracker_id, False),
        }

    def _limpiar_memoria(self) -> None:
        """Poda los acumuladores más viejos si superan ~500 tracker_ids en memoria."""
        if len(self._historial) <= self._MAX_IDS_EN_MEMORIA:
            return
        ids_recientes = list(self._historial.items())[-self._IDS_A_CONSERVAR :]
        self._historial = dict(ids_recientes)
        ids_a_conservar = {tid for tid, _ in ids_recientes}
        self._separador_visto = {
            tid: visto for tid, visto in self._separador_visto.items() if tid in ids_a_conservar
        }
