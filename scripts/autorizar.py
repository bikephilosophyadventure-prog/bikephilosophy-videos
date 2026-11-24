"""
Paso único: autoriza el acceso a la cuenta de YouTube del canal y muestra los tres
valores que hay que guardar como "secrets" en GitHub.

Uso (en tu ordenador, con la cuenta de Google propietaria del canal a mano):
    pip install google-auth-oauthlib
    python scripts/autorizar.py ruta/al/client_secret.json
"""
import json
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

ALCANCE = ["https://www.googleapis.com/auth/youtube.force-ssl"]

if len(sys.argv) != 2:
    sys.exit("Uso: python scripts/autorizar.py client_secret.json")

flujo = InstalledAppFlow.from_client_secrets_file(sys.argv[1], scopes=ALCANCE)
cred = flujo.run_local_server(port=0, access_type="offline", prompt="consent")
with open(sys.argv[1], encoding="utf-8") as f:
    cliente = json.load(f)["installed"]

print("\nGuarda estos tres valores en GitHub > Settings > Secrets and variables > Actions:\n")
print("YT_CLIENT_ID      =", cliente["client_id"])
print("YT_CLIENT_SECRET  =", cliente["client_secret"])
print("YT_REFRESH_TOKEN  =", cred.refresh_token)
print("\nNo compartas estos valores ni los subas al repositorio.")
