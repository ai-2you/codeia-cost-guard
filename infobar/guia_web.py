"""La guía (GUIA.md) como página del panel local: un conversor mínimo de Markdown a HTML, sin dependencias y a prueba de contenido raro.

Todo el texto se escapa antes de dar formato; solo se enlazan direcciones http(s). La página no lleva scripts ni carga nada de fuera."""
import html
import re

CSP = "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"
_ENLACE = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_CODIGO = re.compile(r"`([^`\n]+)`")
_NEGRITA = re.compile(r"\*\*([^*\n]+)\*\*")
_CURSIVA = re.compile(r"(?<![*\w])\*([^*\n]+)\*(?![*\w])")
_ENCABEZADO = re.compile(r"^(#{1,3})\s+(.*)$")
_VINETA = re.compile(r"^[-*]\s+(.*)$")
_NUMERADO = re.compile(r"^\d+\.\s+(.*)$")


def en_linea(texto):
    """Escapa y da formato dentro de una línea: `código`, **negrita**, *cursiva* y [texto](https://…)."""
    t = html.escape(texto, quote=True)
    partes = _CODIGO.split(t)               # lo que va entre comillas invertidas no se vuelve a formatear
    for i in range(0, len(partes), 2):
        p = _ENLACE.sub(lambda m: f'<a href="{m.group(2)}" target="_blank" rel="noopener noreferrer">{m.group(1)}</a>', partes[i])
        p = _NEGRITA.sub(r"<strong>\1</strong>", p)
        partes[i] = _CURSIVA.sub(r"<em>\1</em>", p)
    return "".join(p if i % 2 == 0 else f"<code>{p}</code>" for i, p in enumerate(partes))


def a_html(md):
    """Cuerpo HTML de un Markdown sencillo: encabezados, párrafos, listas con o sin número, citas y bloques de código."""
    salida, parrafo, lista, codigo, en_codigo = [], [], None, [], False

    def cierra_parrafo():
        if parrafo:
            salida.append("<p>" + en_linea(" ".join(parrafo)) + "</p>")
            parrafo.clear()

    def cierra_lista():
        nonlocal lista
        if lista:
            etiqueta, items = lista
            salida.append(f"<{etiqueta}>" + "".join("<li>" + en_linea(" ".join(i)) + "</li>" for i in items) + f"</{etiqueta}>")
            lista = None

    for linea in md.split("\n"):
        if linea.strip().startswith("```"):
            if en_codigo:
                salida.append("<pre><code>" + html.escape("\n".join(codigo)) + "</code></pre>")
                codigo, en_codigo = [], False
            else:
                cierra_parrafo()
                cierra_lista()
                en_codigo = True
            continue
        if en_codigo:
            codigo.append(linea)
            continue
        if not linea.strip():
            cierra_parrafo()
            cierra_lista()
            continue
        m = _ENCABEZADO.match(linea)
        if m:
            cierra_parrafo()
            cierra_lista()
            n = len(m.group(1))
            salida.append(f"<h{n}>{en_linea(m.group(2))}</h{n}>")
            continue
        v, n_ = _VINETA.match(linea), _NUMERADO.match(linea)
        if v or n_:
            cierra_parrafo()
            etiqueta = "ul" if v else "ol"
            if lista and lista[0] != etiqueta:
                cierra_lista()
            if not lista:
                lista = (etiqueta, [])
            lista[1].append([(v or n_).group(1)])
            continue
        if lista and linea.startswith((" ", "\t")):
            lista[1][-1].append(linea.strip())          # continuación de un elemento de lista
            continue
        if linea.startswith(">"):
            cierra_parrafo()
            cierra_lista()
            salida.append("<blockquote>" + en_linea(linea.lstrip("> ")) + "</blockquote>")
            continue
        cierra_lista()
        parrafo.append(linea.strip())
    if en_codigo:
        salida.append("<pre><code>" + html.escape("\n".join(codigo)) + "</code></pre>")
    cierra_parrafo()
    cierra_lista()
    return "\n".join(salida)


ESTILO = """
:root{color-scheme:light dark;--fondo:#f2f2f7;--texto:#1c1c1e;--suave:#6e6e73;--borde:rgba(0,0,0,.12);--bloque:rgba(0,0,0,.05)}
@media (prefers-color-scheme:dark){:root{--fondo:#111214;--texto:#f5f5f7;--suave:#98989d;--borde:rgba(255,255,255,.14);--bloque:rgba(255,255,255,.07)}}
body{margin:0;background:var(--fondo);color:var(--texto);font:16px/1.6 -apple-system,BlinkMacSystemFont,"SF Pro Text","Helvetica Neue",sans-serif}
main{max-width:760px;margin:0 auto;padding:32px 20px 64px}
h1{font-size:28px;margin:0 0 12px}h2{font-size:20px;margin:28px 0 8px;border-bottom:1px solid var(--borde);padding-bottom:4px}h3{font-size:16px;margin:20px 0 6px}
p,li{margin:6px 0}ul,ol{padding-left:24px}
code{font:13.5px ui-monospace,"SF Mono",Menlo,monospace;background:var(--bloque);border-radius:5px;padding:1px 5px}
pre{background:var(--bloque);border-radius:10px;padding:12px 14px;overflow-x:auto}pre code{background:none;padding:0}
blockquote{margin:10px 0;padding:2px 14px;border-left:3px solid var(--borde);color:var(--suave)}
a{color:inherit}
"""


def documento(md):
    """Página completa lista para servir."""
    return ("<!doctype html><html lang=\"es\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>Guía de CostGuard</title><style>{ESTILO}</style></head><body><main>{a_html(md)}</main></body></html>")
