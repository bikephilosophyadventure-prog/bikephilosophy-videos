"""
Publica en el blog de WordPress una página indexable por cada vídeo largo del canal,
dentro de una página madre «Vídeos» (bikephilosophy.es/blog/videos/<vídeo>/).

Cada página lleva: el vídeo, los capítulos con su minuto, los productos mencionados,
la transcripción completa por capítulos y datos estructurados VideoObject con Clips
(para que Google pueda mostrar los «momentos clave» en los resultados).

Reglas:
  - Los Shorts no se publican.
  - Un vídeo solo se publica cuando ya tiene transcripción y esta tiene un mínimo de
    palabras (para no crear páginas pobres).
  - El título y la URL se ponen al crear la página y después no se tocan: se pueden
    mejorar a mano en WordPress. El contenido sí lo gestiona este script.

Variables de entorno (secrets en GitHub):
  WP_URL           p. ej. https://www.bikephilosophy.es/blog
  WP_USER          usuario de WordPress
  WP_APP_PASSWORD  contraseña de aplicación de ese usuario
  BUSCADOR_URL     (opcional) dirección pública del buscador
Si faltan las tres primeras, el script no hace nada.
"""
import hashlib
import html
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

import requests

RAIZ = Path(__file__).resolve().parent.parent
F_VIDEOS = RAIZ / "docs" / "videos.json"
F_TRANS = RAIZ / "docs" / "transcripciones.json"
F_WP = RAIZ / "data" / "wordpress.json"

MIN_PALABRAS = 300        # transcripción mínima para publicar la página
MAX_POR_EJECUCION = 40    # páginas nuevas o actualizadas por pasada
SLUG_MADRE = "videos"
VERSION_PLANTILLA = "1"   # súbelo si cambias el diseño, para regenerar todas las páginas

WHATSAPP = "https://wa.me/34608985261"
TIENDA = "https://www.bikephilosophy.es/es/"
CANAL = "https://www.youtube.com/@bikephilosophy"


# ---------------------------------------------------------------- utilidades

def e(t):
    return html.escape(str(t), quote=True)


def fmt(s):
    h, m, x = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{x:02d}" if h else f"{m:02d}:{x:02d}"


def iso_dur(s):
    h, m, x = s // 3600, s % 3600 // 60, s % 60
    return "PT" + (f"{h}H" if h else "") + (f"{m}M" if m else "") + f"{x}S"


def slug(t):
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t[:80].rstrip("-") or "video"


def yt(vid, s=0):
    return f"https://www.youtube.com/watch?v={vid}" + (f"&t={s}s" if s else "")


def leer_json(f, defecto):
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return defecto


def escribir_json(f, datos):
    f.parent.mkdir(parents=True, exist_ok=True)
    nuevo = json.dumps(datos, ensure_ascii=False, separators=(",", ":"))
    if not f.exists() or f.read_text(encoding="utf-8") != nuevo:
        f.write_text(nuevo, encoding="utf-8")


def resumen(desc, n=300):
    """Primeras frases de la descripción, sin la línea de WhatsApp ni emojis sueltos."""
    t = re.sub(r"Si aún tienes dudas[^.?!]*[.?!:]?", "", desc, flags=re.I)
    t = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) <= n:
        return t
    corte = t.rfind(". ", 0, n)
    return t[: corte + 1] if corte > 80 else t[:n].rsplit(" ", 1)[0] + "…"


# ---------------------------------------------------------- contenido de página

CSS = """
.bp-video{--bp-verde:#67AF23;--bp-negro:#070201;--bp-gris:#5E5F63}
.bp-video .bp-reproductor{position:relative;aspect-ratio:16/9;background:#000;margin:0 0 28px}
.bp-video .bp-reproductor iframe{position:absolute;inset:0;width:100%;height:100%;border:0}
.bp-video .bp-etiqueta{font-size:13px;letter-spacing:.2em;font-weight:800;text-transform:uppercase;margin:40px 0 14px}
.bp-video .bp-capitulos{list-style:none;margin:0;padding:0;border-top:1px solid #DCDDE1}
.bp-video .bp-capitulos li{display:flex;gap:18px;align-items:baseline;padding:12px 0;border-bottom:1px solid #DCDDE1}
.bp-video .bp-t{font-weight:900;font-style:italic;font-variant-numeric:tabular-nums;min-width:64px}
.bp-video .bp-botones{display:flex;flex-wrap:wrap;gap:10px;margin:0;padding:0;list-style:none}
.bp-video .bp-boton{display:inline-flex;align-items:center;min-height:44px;padding:0 18px;border-radius:10px;font-weight:800;text-decoration:none;border:2px solid var(--bp-negro);color:var(--bp-negro)}
.bp-video .bp-boton.bp-verde{background:var(--bp-verde);border-color:var(--bp-verde)}
.bp-video .bp-transcripcion h3{margin:32px 0 10px}
.bp-video .bp-transcripcion p{line-height:1.7}
.bp-video .bp-transcripcion a.bp-ts{font-weight:800;text-decoration:none;color:var(--bp-gris);margin-right:6px}
.bp-video .bp-cta{margin:48px 0 0;padding:28px;background:var(--bp-negro);color:#fff}
.bp-video .bp-cta p{color:#fff;margin:0 0 16px}
""".strip()


def datos_estructurados(v, url_pagina):
    caps = v["capitulos"]
    dato = {
        "@context": "https://schema.org",
        "@type": "VideoObject",
        "name": v["titulo"],
        "description": resumen(v.get("desc", ""), 500) or v["titulo"],
        "thumbnailUrl": [f"https://i.ytimg.com/vi/{v['id']}/maxresdefault.jpg",
                         f"https://i.ytimg.com/vi/{v['id']}/hqdefault.jpg"],
        "uploadDate": v["publicado"],
        "embedUrl": f"https://www.youtube.com/embed/{v['id']}",
        "url": url_pagina,
        "publisher": {"@type": "Organization", "name": "Bikephilosophy", "url": TIENDA},
    }
    if v.get("dur"):
        dato["duration"] = iso_dur(v["dur"])
    if len(caps) > 1:
        partes = []
        for i, c in enumerate(caps):
            fin = caps[i + 1]["s"] if i + 1 < len(caps) else (v.get("dur") or c["s"] + 60)
            partes.append({"@type": "Clip", "name": c["titulo"], "startOffset": c["s"],
                           "endOffset": fin, "url": yt(v["id"], c["s"])})
        dato["hasPart"] = partes
    return json.dumps(dato, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def transcripcion_html(v, frags):
    caps = v["capitulos"]
    grupos = {i: [] for i in range(len(caps))}
    for s, texto in frags:
        i = max(k for k, c in enumerate(caps) if c["s"] <= s) if s >= caps[0]["s"] else 0
        grupos[i].append((s, texto))
    out = ['<div class="bp-transcripcion">']
    for i, c in enumerate(caps):
        if not grupos[i]:
            continue
        if len(caps) > 1:
            out.append(f'<h3 id="cap-{i + 1}">{e(c["titulo"])}</h3>')
        bloque = grupos[i]
        for j in range(0, len(bloque), 3):          # párrafos de ~75 s
            trozo = bloque[j:j + 3]
            s0 = trozo[0][0]
            texto = " ".join(t for _, t in trozo)
            out.append(f'<p><a class="bp-ts" href="{e(yt(v["id"], s0))}" target="_blank" rel="noopener">{fmt(s0)}</a>{e(texto)}</p>')
    out.append("</div>")
    return "\n".join(out)


def pagina_video(v, frags, url_pagina, buscador):
    caps = v["capitulos"]
    h = [f'<style>{CSS}</style>', '<div class="bp-video">']
    intro = resumen(v.get("desc", ""))
    if intro:
        h.append(f"<p>{e(intro)}</p>")
    h.append(f'<div class="bp-reproductor"><iframe src="https://www.youtube-nocookie.com/embed/{v["id"]}?rel=0" '
             f'title="{e(v["titulo"])}" loading="lazy" allow="encrypted-media; picture-in-picture; fullscreen" allowfullscreen></iframe></div>')
    if len(caps) > 1:
        h.append('<h2 class="bp-etiqueta">En este vídeo</h2><ul class="bp-capitulos">')
        for i, c in enumerate(caps):
            h.append(f'<li><a class="bp-t" href="{e(yt(v["id"], c["s"]))}" target="_blank" rel="noopener">{fmt(c["s"])}</a>'
                     f'<a href="#cap-{i + 1}">{e(c["titulo"])}</a></li>')
        h.append("</ul>")
    if v.get("productos"):
        h.append('<h2 class="bp-etiqueta">Productos del vídeo</h2><ul class="bp-botones">')
        h += [f'<li><a class="bp-boton bp-verde" href="{e(p["url"])}">{e(p["nombre"])}</a></li>' for p in v["productos"]]
        h.append("</ul>")
    h.append('<h2 class="bp-etiqueta">Transcripción</h2>')
    h.append(transcripcion_html(v, frags))
    if v.get("relacionados"):
        h.append('<h2 class="bp-etiqueta">Vídeos mencionados</h2><ul class="bp-botones">')
        h += [f'<li><a class="bp-boton" href="{e(p["url"])}" target="_blank" rel="noopener">{e(p["nombre"])}</a></li>' for p in v["relacionados"]]
        h.append("</ul>")
    h.append('<div class="bp-cta"><p><strong>¿Dudas con tu próxima bici?</strong> Te asesoramos en nuestras tiendas '
             'de Málaga, Vélez-Málaga y Marbella, o por WhatsApp.</p><ul class="bp-botones">'
             f'<li><a class="bp-boton bp-verde" href="{WHATSAPP}" target="_blank" rel="noopener">Escribir por WhatsApp</a></li>'
             f'<li><a class="bp-boton" style="color:#fff;border-color:#fff" href="{e(buscador)}">Buscar en todos los vídeos</a></li></ul></div>')
    h.append("</div>")
    h.append(f'<script type="application/ld+json">{datos_estructurados(v, url_pagina)}</script>')
    return "<!-- wp:html -->\n" + "\n".join(h) + "\n<!-- /wp:html -->"


def pagina_madre(publicadas, videos, buscador):
    h = [f'<style>{CSS}</style>', '<div class="bp-video">',
         "<p>Guías de compra, análisis y novedades de las marcas con las que trabajamos, explicadas en vídeo "
         "por el equipo de Bikephilosophy. Cada vídeo tiene su transcripción completa y los capítulos para ir "
         "directamente a lo que te interesa.</p>",
         f'<p><a class="bp-boton bp-verde" href="{e(buscador)}">Buscar un tema en todos los vídeos</a></p>',
         '<h2 class="bp-etiqueta">Todos los vídeos</h2><ul class="bp-capitulos">']
    for v in videos:
        p = publicadas.get(v["id"])
        if p:
            fecha = "/".join(reversed(v["publicado"].split("-")))
            h.append(f'<li><span class="bp-t">{fecha}</span><a href="{e(p["url"])}">{e(v["titulo"])}</a></li>')
    h += ["</ul>", "</div>"]
    return "<!-- wp:html -->\n" + "\n".join(h) + "\n<!-- /wp:html -->"


# --------------------------------------------------------------- WordPress

class WordPress:
    def __init__(self, base, usuario, clave):
        self.api = base.rstrip("/") + "/wp-json/wp/v2"
        self.s = requests.Session()
        self.s.auth = (usuario, clave.replace(" ", ""))
        self.s.headers["User-Agent"] = "bikephilosophy-videos/1.0"
        token = os.environ.get("WP_CF_TOKEN", "").strip()
        if token:                                    # deja pasar el script por la regla de Cloudflare
            self.s.headers["X-Bikephilosophy-Token"] = token

    def _ok(self, r):
        if r.status_code >= 400:
            if "Just a moment" in r.text or "cf-chl" in r.text:
                sys.exit(f"Cloudflare ha bloqueado la petición ({r.status_code}). Falta la regla que deja pasar al script; ver README.")
            sys.exit(f"WordPress respondió {r.status_code}: {r.text[:300]}")
        return r.json()

    def buscar(self, slug_, padre=None):
        p = {"slug": slug_, "status": "publish,draft,private", "context": "edit"}
        if padre is not None:
            p["parent"] = padre
        res = self._ok(self.s.get(f"{self.api}/pages", params=p, timeout=60))
        return res[0] if res else None

    def crear(self, **datos):
        return self._ok(self.s.post(f"{self.api}/pages", json=datos, timeout=60))

    def actualizar(self, pid, **datos):
        return self._ok(self.s.post(f"{self.api}/pages/{pid}", json=datos, timeout=60))


def main():
    base, usuario, clave = (os.environ.get(k, "").strip() for k in ("WP_URL", "WP_USER", "WP_APP_PASSWORD"))
    if not (base and usuario and clave):
        print("WordPress sin configurar: no se publican páginas.")
        return
    buscador = os.environ.get("BUSCADOR_URL", "").strip() or "https://bikephilosophyadventure-prog.github.io/bikephilosophy-videos/"
    wp = WordPress(base, usuario, clave)

    datos = leer_json(F_VIDEOS, {"videos": []})
    videos = datos["videos"]
    trans = leer_json(F_TRANS, {})
    reg = leer_json(F_WP, {"madre": None, "paginas": {}})

    if not reg.get("madre"):
        madre = wp.buscar(SLUG_MADRE, padre=0) or wp.crear(title="Vídeos", slug=SLUG_MADRE, status="publish",
                                                            content="<!-- wp:paragraph --><p>Vídeos de Bikephilosophy.</p><!-- /wp:paragraph -->")
        reg["madre"] = {"id": madre["id"], "url": madre["link"]}
    madre_id = reg["madre"]["id"]

    hechos = 0
    for v in videos:
        if v.get("short") or hechos >= MAX_POR_EJECUCION:
            continue
        frags = trans.get(v["id"]) or []
        if sum(len(t.split()) for _, t in frags) < MIN_PALABRAS:
            continue
        previo = reg["paginas"].get(v["id"])
        url = previo["url"] if previo else f'{reg["madre"]["url"].rstrip("/")}/{slug(v["titulo"])}/'
        contenido = pagina_video(v, frags, url, buscador)
        huella = hashlib.sha1((VERSION_PLANTILLA + contenido).encode()).hexdigest()
        if previo and previo.get("huella") == huella:
            continue
        if previo:
            wp.actualizar(previo["id"], content=contenido)
            previo["huella"] = huella
        else:
            existente = wp.buscar(slug(v["titulo"]), padre=madre_id)
            if existente:
                p = wp.actualizar(existente["id"], content=contenido)
            else:
                p = wp.crear(title=v["titulo"], slug=slug(v["titulo"]), parent=madre_id, status="publish",
                             content=contenido, comment_status="closed")
            reg["paginas"][v["id"]] = {"id": p["id"], "url": p["link"], "huella": huella}
            if p["link"] != url:                          # rehacer los datos estructurados con la URL real
                contenido = pagina_video(v, frags, p["link"], buscador)
                wp.actualizar(p["id"], content=contenido)
        hechos += 1

    vivos = {v["id"] for v in videos}
    for k, val in list(reg["paginas"].items()):           # vídeo borrado u oculto: su página pasa a borrador
        if k not in vivos:
            wp.actualizar(val["id"], status="draft")
            del reg["paginas"][k]
    madre_html = pagina_madre(reg["paginas"], videos, buscador)
    huella_madre = hashlib.sha1(madre_html.encode()).hexdigest()
    if reg["madre"].get("huella") != huella_madre:
        wp.actualizar(madre_id, content=madre_html)
        reg["madre"]["huella"] = huella_madre

    for v in videos:                                      # el buscador enlaza a cada ficha
        p = reg["paginas"].get(v["id"])
        if p:
            v["pagina"] = p["url"]
        else:
            v.pop("pagina", None)
    escribir_json(F_VIDEOS, datos)
    escribir_json(F_WP, reg)
    print(f"WordPress: {hechos} páginas creadas o actualizadas | {len(reg['paginas'])} publicadas en total")


if __name__ == "__main__":
    main()
