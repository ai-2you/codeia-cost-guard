"""Medidas del desplegable, compartidas por el CSS y por las zonas de clic de barra.py.

Todo lo que se puede pulsar esta a una distancia FIJA del borde de arriba (cabecera, pestanas,
selector de servidor) o del de abajo (botones y linea final). Asi las zonas valen aunque el
contenido de en medio cambie de altura, sin medir la pagina. Unidades: puntos (px CSS a 1x).
"""
ANCHO = 380
MARGEN = 14                 # relleno lateral de la tarjeta

# --- desde ARRIBA ---
CABECERA_ALTO = 58          # escudo + titulo + subtitulo + boton refrescar
PESTANAS_Y, PESTANAS_ALTO = 58, 34
SELECTOR_Y, SELECTOR_ALTO = 100, 26     # "SERVIDOR · NOMBRE ⌄" (solo en Servidores; 8 pt bajo las pestanas)
REFRESCAR_X, REFRESCAR_ANCHO = ANCHO - MARGEN - 46, 46

# --- desde ABAJO ---
LINEA_ALTO = 30             # "Medido a las 16:44" + boton ⋯
BOTONES_ALTO = 50           # fila de botones (sus 38 pt + aire)
MENU_ANCHO = 44             # zona del ⋯ a la derecha de la linea final


def zonas(n_botones, con_selector):
    """Zonas de clic en puntos, origen ARRIBA-izquierda: [(nombre, x0, y0, x1, y1, ancla)].

    ancla "arriba": y medida desde el borde superior; "abajo": desde el inferior. Asi el mismo
    rectangulo vale para cualquier alto de desplegable."""
    z = [("refrescar", REFRESCAR_X, 8, REFRESCAR_X + REFRESCAR_ANCHO, CABECERA_ALTO - 8, "arriba")]
    mitad = MARGEN + (ANCHO - 2 * MARGEN) / 2
    z.append(("pestana:agentes", MARGEN, PESTANAS_Y, mitad, PESTANAS_Y + PESTANAS_ALTO, "arriba"))
    z.append(("pestana:servidores", mitad, PESTANAS_Y, ANCHO - MARGEN, PESTANAS_Y + PESTANAS_ALTO, "arriba"))
    if con_selector:
        z.append(("selector", MARGEN, SELECTOR_Y, ANCHO - MARGEN, SELECTOR_Y + SELECTOR_ALTO, "arriba"))
    if n_botones:
        ancho_b = (ANCHO - 2 * MARGEN) / n_botones
        y0 = LINEA_ALTO
        for i in range(n_botones):
            z.append((f"boton:{i}", MARGEN + i * ancho_b, y0 + BOTONES_ALTO, MARGEN + (i + 1) * ancho_b, y0, "abajo"))
    z.append(("menu", ANCHO - MENU_ANCHO, LINEA_ALTO, ANCHO, 0, "abajo"))
    return z


def zona_en(x, y_desde_abajo, alto_total, n_botones, con_selector):
    """Nombre de la zona bajo un clic de AppKit (vista sin voltear: y=0 es el borde INFERIOR)."""
    y_desde_arriba = alto_total - y_desde_abajo
    for nombre, x0, a, x1, b, ancla in zonas(n_botones, con_selector):
        if not x0 <= x < x1:
            continue
        if ancla == "arriba" and a <= y_desde_arriba < b:
            return nombre
        if ancla == "abajo" and b <= y_desde_abajo < a:
            return nombre
    return None
