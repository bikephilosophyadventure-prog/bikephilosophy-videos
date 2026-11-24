"""
Actualiza el buscador de vídeos de Bikephilosophy a partir del canal de YouTube.

Qué hace en cada ejecución:
  1. Lista todos los vídeos públicos del canal (título, descripción, duración).
  2. Lee los capítulos y los enlaces (tienda y vídeos mencionados) de cada descripción.
  3. Descarga los subtítulos de los vídeos que aún no los tienen o que han cambiado,
     sin pasarse del presupuesto de cuota de la API.
  4. Escribe docs/videos.json y docs/transcripciones.json, que es lo que lee la landing.

Variables de entorno (en GitHub van como "secrets"):
  YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN  -> autorización de la cuenta del canal
  YT_PRESUPUESTO (opcional) -> unidades de cuota por ejecución (por defecto 1500)
"""
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

import requests

RAIZ = Path(__file__).resolve().parent.parent
F_VIDEOS = RAIZ / "docs" / "videos.json"
F_TRANS = RAIZ / "docs" / "transcripciones.json"
F_ESTADO = RAIZ / "data" / "estado.json"
F_CORRECCIONES = RAIZ / "scripts" / "correcciones.json"

API = "https://www.googleapis.com/youtube/v3"
IDIOMA = "es"
SEG_FRAGMENTO = 25          # segundos por fragmento de transcripción
DUR_SHORT = 180             # la API no marca los Shorts: <= 3 min sin capítulos se trata como Short
REINTENTO_HORAS = 3         # cada cuánto se vuelve a mirar un vídeo que aún no tiene subtítulos
REVISION_RECIENTES_H = 24   # vídeos de los últimos 30 días: se mira a diario si se corrigieron subtítulos
REVISION_ANTIGUOS_D = 30    # vídeos antiguos: se comprueban una vez al mes
MAX_INTENTOS = 8            # después, solo se reintenta en la revisión mensual

COSTE = {"lista": 1, "videos": 1, "captions_list": 50, "captions_download": 200}


class SinCuota(Exception):
    pass


# ---------------------------------------------------------------- utilidades

def ahora():
    return dt.datetime.now(dt.timezone.utc)


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def de_iso(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


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


def a_segundos(t):
    p = [int(x) for x in t.split(":")]
    while len(p) < 3:
        p.insert(0, 0)
    return p[0] * 3600 + p[1] * 60 + p[2]


def duracion_iso(s):
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return 0
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


# ------------------------------------------------------- lectura de descripciones

RE_CAP = re.compile(r"^\s*[\(\[]?((?:\d{1,2}:)?\d{1,2}:\d{2})[\)\]]?\s*[-–—|:·]?\s*(.+?)\s*$")
RE_URL = re.compile(r"https?://\S+")


def capitulos(desc):
    """Capítulos según la regla de YouTube: empiezan en 0:00, al menos 3 y en orden."""
    caps = []
    for linea in desc.splitlines():
        m = RE_CAP.match(linea)
        if m:
            titulo = re.sub(r"[\s,;]+\)", ")", m.group(2)).strip(" -–—")
            caps.append({"s": a_segundos(m.group(1)), "titulo": titulo[:1].upper() + titulo[1:]})
    validos = len(caps) >= 3 and caps[0]["s"] == 0 and all(a["s"] < b["s"] for a, b in zip(caps, caps[1:]))
    return caps if validos else []


def limpiar_nombre(texto):
    t = RE_URL.sub("", texto)
    t = re.sub(r"^[^\w¿¡«\"]+", "", t)          # emojis, viñetas y flechas al principio
    t = t.split("(")[0]                          # quita "(specs del modelo)"
    t = re.sub(r"[\s:—–-]+$", "", t)             # separadores finales
    return re.sub(r"\s{2,}", " ", t).strip()


def enlaces(desc, video_id):
    productos, relacionados, vistos = [], [], set()
    for linea in desc.splitlines():
        for url in RE_URL.findall(linea):
            url = url.rstrip(".,;)")
            if url in vistos:
                continue
            vistos.add(url)
            nombre = limpiar_nombre(linea)
            if "bikephilosophy.es" in url:
                ruta = re.sub(r"^https?://[^/]+", "", url).strip("/")
                if ruta in ("", "es"):
                    continue                     # la portada no es un producto
                productos.append({"nombre": nombre or "Ver en la tienda", "url": url})
            elif re.search(r"(youtube\.com/(watch|shorts)|youtu\.be/)", url) and video_id not in url:
                relacionados.append({"nombre": nombre or "Ver vídeo", "url": url})
    return productos, relacionados


def texto_descripcion(desc):
    """Descripción sin enlaces ni capítulos, para buscar también en ella."""
    lineas = [l for l in desc.splitlines() if not RE_CAP.match(l)]
    t = RE_URL.sub("", "\n".join(lineas))
    return re.sub(r"\s+", " ", t).strip()[:2000]


# ------------------------------------------------------------ subtítulos

def cargar_correcciones():
    datos = leer_json(F_CORRECCIONES, {})
    return [(re.compile(r"\b" + re.escape(k) + r"\b", re.I), v)
            for k, v in datos.items() if not k.startswith("_")]


RE_TIEMPO = re.compile(r"(?:(\d{1,2}):)?(\d{2}):(\d{2})[.,]\d{3}\s+-->")


def fragmentos_vtt(vtt, correcciones):
    """Convierte un .vtt en fragmentos [segundo_inicio, texto] de unos 25 segundos."""
    cues, actual, previo = [], None, ""
    for linea in vtt.splitlines():
        m = RE_TIEMPO.match(linea.strip())
        if m:
            h = int(m.group(1) or 0)
            actual = [h * 3600 + int(m.group(2)) * 60 + int(m.group(3)), []]
            cues.append(actual)
        elif actual is not None and linea.strip():
            texto = re.sub(r"<[^>]+>", "", linea).strip()
            if texto and texto != previo:        # los automáticos repiten líneas
                actual[1].append(texto)
                previo = texto
    frags, ini, buf = [], None, []
    for s, lineas in cues:
        if not lineas:
            continue
        if ini is None:
            ini = s
        if s - ini >= SEG_FRAGMENTO and buf:
            frags.append([ini, " ".join(buf)])
            ini, buf = s, []
        buf.extend(lineas)
    if buf:
        frags.append([ini, " ".join(buf)])
    for f in frags:
        for patron, bueno in correcciones:
            f[1] = patron.sub(bueno, f[1])
    return frags


# --------------------------------------------------------------- API YouTube

class YouTube:
    def __init__(self, presupuesto):
        self.restante = presupuesto
        r = requests.post("https://oauth2.googleapis.com/token", data={
            "client_id": os.environ["YT_CLIENT_ID"],
            "client_secret": os.environ["YT_CLIENT_SECRET"],
            "refresh_token": os.environ["YT_REFRESH_TOKEN"],
            "grant_type": "refresh_token",
        }, timeout=30)
        if r.status_code != 200:
            sys.exit("No se pudo autorizar con YouTube. ¿Sigue siendo válido el refresh token? " + r.text)
        self.cab = {"Authorization": "Bearer " + r.json()["access_token"]}

    def pedir(self, ruta, coste, **params):
        if coste > self.restante:
            raise SinCuota()
        r = requests.get(f"{API}/{ruta}", params=params, headers=self.cab, timeout=60)
        self.restante -= coste
        if r.status_code == 403 and "quota" in r.text.lower():
            raise SinCuota()
        r.raise_for_status()
        return r

    def subidas(self):
        canal = self.pedir("channels", COSTE["lista"], part="contentDetails", mine="true").json()
        lista = canal["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
        ids, token = [], None
        while True:
            p = {"part": "contentDetails", "playlistId": lista, "maxResults": 50}
            if token:
                p["pageToken"] = token
            r = self.pedir("playlistItems", COSTE["lista"], **p).json()
            ids += [i["contentDetails"]["videoId"] for i in r["items"]]
            token = r.get("nextPageToken")
            if not token:
                return ids

    def detalles(self, ids):
        for i in range(0, len(ids), 50):
            r = self.pedir("videos", COSTE["videos"], part="snippet,contentDetails,status",
                           id=",".join(ids[i:i + 50])).json()
            yield from r["items"]

    def pista(self, video_id):
        """Elige la mejor pista: español, corregida a mano antes que automática."""
        items = self.pedir("captions", COSTE["captions_list"], part="snippet", videoId=video_id).json()["items"]
        items = [c for c in items if not c["snippet"].get("isDraft")]
        if not items:
            return None
        def prioridad(c):
            s = c["snippet"]
            return (s["language"].split("-")[0] == IDIOMA, s.get("trackKind", "").lower() != "asr")
        return max(items, key=prioridad)

    def descargar(self, pista_id):
        return self.pedir(f"captions/{pista_id}", COSTE["captions_download"], tfmt="vtt").text


# ------------------------------------------------------------------- proceso

def leer_video(v):
    sn = v["snippet"]
    desc = sn.get("description", "")
    dur = duracion_iso(v["contentDetails"].get("duration"))
    caps = capitulos(desc)
    productos, relacionados = enlaces(desc, v["id"])
    return {
        "id": v["id"],
        "titulo": sn["title"],
        "publicado": sn["publishedAt"][:10],
        "dur": dur,
        "short": dur <= DUR_SHORT and not caps,
        "capitulos": caps or [{"s": 0, "titulo": "Vídeo completo"}],
        "desc": texto_descripcion(desc),
        "productos": productos,
        "relacionados": relacionados,
    }


def main():
    presupuesto = int(os.environ.get("YT_PRESUPUESTO", "1500"))
    yt = YouTube(presupuesto)
    estado = leer_json(F_ESTADO, {})
    trans = leer_json(F_TRANS, {})
    correcciones = cargar_correcciones()
    t0 = ahora()

    videos = [leer_video(v) for v in yt.detalles(yt.subidas())
              if v["status"].get("privacyStatus") == "public"
              and v["snippet"].get("liveBroadcastContent", "none") == "none"]
    videos.sort(key=lambda x: x["publicado"], reverse=True)
    vivos = {v["id"] for v in videos}
    trans = {k: val for k, val in trans.items() if k in vivos}   # vídeos borrados u ocultos fuera

    # Cola de subtítulos: primero lo nuevo, luego reintentos y por último revisiones.
    def prioridad(v):
        e = estado.get(v["id"], {})
        ultimo = de_iso(e["comprobado"]) if e.get("comprobado") else None
        horas = (t0 - ultimo).total_seconds() / 3600 if ultimo else 1e9
        reciente = (t0.date() - dt.date.fromisoformat(v["publicado"])).days <= 30
        if v["id"] not in trans:
            if e.get("intentos", 0) >= MAX_INTENTOS:
                return (4, -horas) if horas >= REVISION_ANTIGUOS_D * 24 else None
            if horas >= REINTENTO_HORAS:
                return (0 if ultimo is None else 1, -horas)
            return None
        if reciente and horas >= REVISION_RECIENTES_H:
            return (2, -horas)
        if horas >= REVISION_ANTIGUOS_D * 24:
            return (3, -horas)
        return None

    cola = sorted((p, v["id"]) for v in videos if (p := prioridad(v)) is not None)
    hechos = 0
    try:
        for _, vid in cola:
            e = estado.setdefault(vid, {})
            pista = yt.pista(vid)
            e["comprobado"] = iso(t0)
            if not pista:
                e["intentos"] = e.get("intentos", 0) + 1
                continue
            if vid in trans and pista["snippet"].get("lastUpdated") == e.get("version"):
                continue                                         # sin cambios
            try:
                vtt = yt.descargar(pista["id"])
            except requests.HTTPError as err:
                e["intentos"] = e.get("intentos", 0) + 1
                e["error"] = str(err)[:200]
                continue
            trans[vid] = fragmentos_vtt(vtt, correcciones)
            e.update({"version": pista["snippet"].get("lastUpdated"), "intentos": 0})
            e.pop("error", None)
            hechos += 1
    except SinCuota:
        print("Presupuesto de cuota agotado en esta ejecución; se sigue en la próxima.")

    estado = {k: val for k, val in estado.items() if k in vivos}
    escribir_json(F_VIDEOS, {"comprobado": t0.date().isoformat(), "videos": videos})
    escribir_json(F_TRANS, trans)
    escribir_json(F_ESTADO, estado)
    pendientes = sum(1 for v in videos if v["id"] not in trans)
    print(f"{len(videos)} vídeos | {hechos} transcripciones nuevas o actualizadas | "
          f"{pendientes} sin transcripción todavía | cuota usada: {presupuesto - yt.restante}")


if __name__ == "__main__":
    main()
