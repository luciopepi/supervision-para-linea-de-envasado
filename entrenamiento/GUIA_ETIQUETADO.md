# Guía de etiquetado del detector de partes

Objetivo: enseñarle al modelo a ver, en cada imagen, dónde está cada
**botella**, cada **tapa** (cápsula), cada **etiqueta_frente**, cada
**etiqueta_dorso**, cada **caja** y cada **separador** de cartón.

El flujo tiene tres pasos. El paso 2 es el único manual: corregir un
pre-etiquetado que la computadora hace sola. Presupuestá 2-4 horas la
primera vez.

## Paso 1 — Extraer los fotogramas de los videos (en tu PC)

```
cd C:\contador
venv\Scripts\activate
python -m contador_botellas.fotogramas --videos videos --salida dataset_deteccion\imagenes --por-segundo 2
```

Genera unos cientos de imágenes en `dataset_deteccion\imagenes`,
descartando cuadros repetidos. Comprimí esa carpeta como ZIP (clic
derecho → Enviar a → Carpeta comprimida): lo vas a subir a Colab.

## Paso 2 — Pre-etiquetar en Colab y corregir en makesense.ai

1. Abrí el cuaderno `entrenamiento/entrenar_detector_partes.ipynb` en
   [Google Colab](https://colab.research.google.com) (Archivo → Subir
   cuaderno) y seguí sus celdas **hasta el final de la Parte A**: sube tu
   ZIP, un modelo grande (Grounding DINO) marca solo las botellas, tapas,
   etiquetas y cajas que reconoce, y descargás un ZIP con ese borrador.
2. Entrá a [makesense.ai](https://www.makesense.ai) → **Get Started** →
   arrastrá las imágenes → *Object Detection*. Creá las 9 etiquetas con
   estos nombres EXACTOS (minúsculas, sin acentos, con guion bajo) y **en
   este orden** (el orden conecta los números del archivo con cada nombre):
   `botella`, `tapa`, `etiqueta_frente`, `etiqueta_dorso`, `caja`,
   `separador`, `corcho`, `capsula`, `nivel_llenado`.
3. Importá el borrador: **Actions → Import annotations → YOLO format** y
   elegí los `.txt` del ZIP del paso 1.
4. Corregí imagen por imagen: ajustá rectángulos corridos, agregá lo que
   falte y borrá lo que sobre. Guía de decisiones:
   - **etiqueta_frente vs etiqueta_dorso**: el borrador marca todas las
     etiquetas como `etiqueta_frente`; cambiá la clase a `etiqueta_dorso`
     donde se vea la etiqueta trasera (la chica, normalmente con texto).
   - **tapa**: el borrador falla seguido acá (objeto chico) — revisá con
     atención; toda cápsula visible debe tener su rectángulo.
   - **separador**: marcalo a mano (el borrador no lo conoce): es el
     cartón entre botellas visto desde arriba, aunque se vea de canto.
   - **tapa / corcho / capsula**: `tapa` es la tapa a rosca; `corcho` el
     corcho visible en la boca; `capsula` la cápsula que envuelve el
     cuello. Se marca lo que se VE: si la cápsula tapa el corcho, solo
     hay cápsula. El programa considera cerrada a la botella si tiene
     cualquiera de los tres.
   - **nivel_llenado**: rectángulo finito y ancho centrado en la línea
     donde el líquido toca el aire (del ancho del cuello, poca altura).
     Solo cuando la interfase se ve; en botellas opacas no se marca.
   - Regla de oro: **todo lo visible se marca**. Una botella a medias en
     el borde también. Lo que no está marcado, el modelo aprende que "no
     existe", y eso genera falsos negativos.
   - Nada de makesense sale de tu navegador: los videos de la planta no
     se suben a ningún servidor.
5. Al terminar: **Actions → Export annotations → YOLO format (a ZIP)**.
   Ese ZIP (etiquetas corregidas) + tus imágenes son el dataset final.

## Paso 3 — Entrenar en Colab (Parte B del cuaderno)

Volvé al cuaderno, **Parte B**: subí las etiquetas corregidas, y corré
las celdas de entrenamiento (20-60 minutos con la GPU gratuita). La
última celda descarga `detector_partes.pt`. Copialo a
`C:\contador\modelos\` y probalo:

```
python -m contador_botellas --fuente "videos/VID-20260713-WA0049.mp4" --modelo modelos\detector_partes.pt --sin-registro --tablero

:: modo caja, con el video de cajas:
python -m contador_botellas --fuente "videos/VID-20260713-WA0052.mp4" --modelo modelos\detector_partes.pt --modo caja --botellas-por-caja 6 --sin-registro --tablero
```

Consejos finales:

- Meta inicial: 250-500 apariciones etiquetadas por clase. Si al modelo
  le cuesta una clase (típicamente `tapa` o `separador`), filmá más
  ejemplos de esa parte y repetí el ciclo — el dataset se acumula.
- Guardá el dataset corregido (imágenes + etiquetas) en un pendrive o
  Drive: es el activo más valioso del proyecto, más que el modelo.
