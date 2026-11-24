# Buscador de vídeos de Bikephilosophy

Landing para buscar en los vídeos del canal de YouTube por lo que se menciona en ellos y saltar al minuto exacto. Se actualiza sola: cada 4 horas lee el canal, añade los vídeos nuevos y descarga sus subtítulos.

## Qué hay en esta carpeta

| Archivo | Para qué sirve |
| --- | --- |
| `docs/index.html` | La landing. Es lo que ve el público. |
| `docs/videos.json` | Índice de vídeos, capítulos y enlaces. Lo genera el script. |
| `docs/transcripciones.json` | Lo que se dice en cada vídeo, por fragmentos. Lo genera el script. |
| `scripts/actualizar.py` | Lee el canal y regenera los dos archivos anteriores. |
| `scripts/autorizar.py` | Se usa una sola vez, para conectar la cuenta de YouTube. |
| `scripts/correcciones.json` | Errores típicos de los subtítulos automáticos y su forma correcta. |
| `.github/workflows/actualizar.yml` | La tarea programada que ejecuta el script cada 4 horas. |

Los `videos.json` y `transcripciones.json` que vienen ahora son de muestra (3 vídeos, sin transcripciones). La primera ejecución los sustituye por los datos reales del canal.

## Puesta en marcha (una sola vez, unos 30 minutos)

### 1. Repositorio en GitHub

1. Crea una cuenta u organización de GitHub de la empresa si no la tenéis.
2. Crea un repositorio nuevo, por ejemplo `bikephilosophy-videos`, **público**. GitHub Pages es gratis en repositorios públicos; las claves de acceso no quedan expuestas porque van guardadas aparte como secrets.
3. Sube todo el contenido de esta carpeta (incluida la carpeta oculta `.github`).

### 2. Acceso a la API de YouTube (Google Cloud)

Hazlo con la cuenta de Google que administra el canal.

1. Entra en console.cloud.google.com y crea un proyecto (por ejemplo «Buscador vídeos»).
2. En «APIs y servicios › Biblioteca», busca **YouTube Data API v3** y pulsa Habilitar.
3. En «Pantalla de consentimiento de OAuth»: tipo **Externo**, nombre de la app «Buscador Bikephilosophy» y tu email.
4. **Importante:** cuando termines, pulsa **Publicar aplicación** para pasarla a «En producción». Si se queda en modo de prueba, la autorización caduca a los 7 días y la actualización deja de funcionar. Google avisará de que la app no está verificada; para uso propio no hace falta verificarla.
5. En «Credenciales › Crear credenciales › ID de cliente de OAuth», elige tipo **Aplicación de escritorio** y descarga el JSON. Renómbralo a `client_secret.json`. No lo subas nunca al repositorio.

### 3. Autorizar el canal

En un ordenador con Python instalado, desde esta carpeta:

```
pip install google-auth-oauthlib
python scripts/autorizar.py client_secret.json
```

Se abre el navegador. Entra con la cuenta del canal, y si Google te pregunta qué cuenta o canal usar, elige **Bikephilosophy** (el canal, no tu perfil personal). Acepta el aviso de «app no verificada» (Configuración avanzada › Ir a…). Al terminar, la terminal muestra tres valores.

### 4. Guardar los tres valores en GitHub

En el repositorio: **Settings › Secrets and variables › Actions › New repository secret**. Crea tres, copiando el nombre exacto:

- `YT_CLIENT_ID`
- `YT_CLIENT_SECRET`
- `YT_REFRESH_TOKEN`

### 5. Publicar la landing

En **Settings › Pages**: Source «Deploy from a branch», rama `main`, carpeta `/docs`. Guarda.

### 6. Primera ejecución

En la pestaña **Actions › Actualizar vídeos › Run workflow**. Al terminar, el registro muestra una línea como esta:

```
84 vídeos | 6 transcripciones nuevas o actualizadas | 78 sin transcripción todavía | cuota usada: 1500
```

Desde ese momento se ejecuta sola cada 4 horas.

### 7. Dominio propio

Para que la landing viva en `videos.bikephilosophy.es`:

1. En el DNS del dominio, crea un registro **CNAME** `videos` apuntando a `<usuario-o-organización>.github.io`.
2. En **Settings › Pages › Custom domain**, escribe `videos.bikephilosophy.es`, guarda y marca **Enforce HTTPS** cuando aparezca disponible (puede tardar unas horas).

### 8. Enlazarla desde la tienda

La opción recomendada es un enlace en el menú de PrestaShop y en el blog a `https://videos.bikephilosophy.es`. La landing ya tiene la cabecera de marca y el botón «Volver a la tienda».

Si se prefiere que quede dentro de una página CMS de PrestaShop, este bloque la incrusta:

```html
<iframe src="https://videos.bikephilosophy.es/" title="Buscador de vídeos de Bikephilosophy"
        style="width:100%; height:100vh; border:0" loading="lazy"></iframe>
```

Con el iframe la página tiene su propio scroll dentro de la tienda, por eso es preferible el enlace directo.

## Cómo funciona día a día

- **Vídeo nuevo:** aparece en la landing en un máximo de 4 horas, con sus capítulos y los enlaces de la descripción. Lo que se dice en el vídeo se añade cuando YouTube termina de generar los subtítulos automáticos (de minutos a unas horas); el script lo reintenta hasta conseguirlo.
- **Subtítulos corregidos en YouTube Studio:** durante el primer mes de cada vídeo se comprueba a diario si han cambiado; después, una vez al mes.
- **Vídeo borrado u oculto:** desaparece de la landing en la siguiente actualización.
- **Carga inicial del catálogo:** la cuota gratuita de la API permite descargar unos 35 subtítulos al día. Todos los vídeos aparecen desde el primer momento con sus capítulos; las transcripciones del catálogo antiguo se van completando en los días siguientes, de los más nuevos a los más antiguos.

## Mantenimiento

**Errores de los subtítulos automáticos.** Si ves una marca o modelo mal transcrito (por ejemplo «orbea ois»), añádelo a `scripts/correcciones.json` con su forma correcta. Se aplica la próxima vez que se actualice cada vídeo.

**Capítulos.** El buscador funciona mejor cuando los capítulos nombran el modelo o la pieza («Orbea Oiz: nueva generación» mejor que «Orbea»). Los capítulos tienen que empezar en `00:00` y ser al menos tres, como exige YouTube.

**Enlaces a la tienda.** Cualquier enlace a bikephilosophy.es en la descripción aparece como botón «En la tienda», con el texto que va antes del enlace en esa línea. Los enlaces a otros vídeos del canal aparecen como «Vídeos mencionados».

**Sugerencias de búsqueda y ajustes.** Al principio del script de `docs/index.html` están las búsquedas sugeridas, el número de vídeos del carrusel y otros ajustes.

## Si algo falla

- **La tarea falla con «No se pudo autorizar con YouTube».** La autorización se ha revocado (cambio de contraseña de la cuenta, permisos retirados o app en modo de prueba). Repite los pasos 3 y 4.
- **Las tareas programadas se han desactivado.** GitHub las pausa si un repositorio pasa 60 días sin actividad. El script actualiza la fecha de comprobación cada día, así que no debería ocurrir; si pasa, se reactivan en la pestaña Actions.
- **Un vídeo no tiene transcripción tras varios días.** Puede que YouTube no haya generado subtítulos (vídeo sin voz o solo música). En `data/estado.json` se ve el número de intentos y el último error.
