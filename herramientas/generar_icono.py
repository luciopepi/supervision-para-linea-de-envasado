"""Genera `contador.ico`, el ícono del acceso directo del escritorio en Windows.

Dibuja una botella estilizada sobre fondo oscuro, en los tamaños que pide
Windows (16 a 256 px). El .ico resultante está commiteado en el repositorio:
este script solo hace falta para volver a generarlo si se quiere cambiar el
diseño (`python herramientas/generar_icono.py`).
"""

from pathlib import Path

from PIL import Image, ImageDraw

# Se dibuja en grande y se reduce: el suavizado de bordes queda mucho mejor
# que dibujando directo en 32 px.
LADO = 512
TAMANOS = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

FONDO = (13, 13, 13, 255)
VIDRIO = (57, 135, 229, 255)
LIQUIDO = (208, 59, 59, 255)
ETIQUETA = (243, 242, 237, 255)


def dibujar_botella(lienzo: ImageDraw.ImageDraw) -> None:
    """Dibuja la botella (cuello, cuerpo, vino y etiqueta) sobre el lienzo."""
    centro = LADO // 2
    ancho_cuello, ancho_cuerpo = 54, 150

    tapa = (centro - ancho_cuello // 2 - 8, 62, centro + ancho_cuello // 2 + 8, 104)
    lienzo.rounded_rectangle(tapa, radius=10, fill=LIQUIDO)

    cuello = (centro - ancho_cuello // 2, 96, centro + ancho_cuello // 2, 196)
    lienzo.rectangle(cuello, fill=VIDRIO)

    # Hombro: une el cuello con el cuerpo sin escalón, como una botella real.
    hombro = [
        (centro - ancho_cuello // 2, 196),
        (centro - ancho_cuerpo // 2, 258),
        (centro + ancho_cuerpo // 2, 258),
        (centro + ancho_cuello // 2, 196),
    ]
    lienzo.polygon(hombro, fill=VIDRIO)

    cuerpo = (centro - ancho_cuerpo // 2, 250, centro + ancho_cuerpo // 2, 452)
    lienzo.rounded_rectangle(cuerpo, radius=22, fill=VIDRIO)

    vino = (centro - ancho_cuerpo // 2 + 14, 300, centro + ancho_cuerpo // 2 - 14, 438)
    lienzo.rounded_rectangle(vino, radius=14, fill=LIQUIDO)

    etiqueta = (centro - ancho_cuerpo // 2 - 4, 330, centro + ancho_cuerpo // 2 + 4, 396)
    lienzo.rounded_rectangle(etiqueta, radius=6, fill=ETIQUETA)


def main() -> None:
    """Genera el archivo `contador.ico` en la raíz del repositorio."""
    imagen = Image.new("RGBA", (LADO, LADO), FONDO)
    dibujar_botella(ImageDraw.Draw(imagen))
    destino = Path(__file__).resolve().parent.parent / "contador.ico"
    imagen.save(destino, format="ICO", sizes=TAMANOS)
    print(f"Ícono generado en {destino}")


if __name__ == "__main__":
    main()
