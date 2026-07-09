# extraer_entry.py — script temporal de un solo uso.
# Descarga el feed y guarda la PRIMERA entrada completa en un fichero de texto.

import requests
import config

url = config.PCSP_FEEDS[config.ACTIVE_FEED]
contenido = requests.get(url, timeout=30).text

inicio = contenido.find("<entry>")
fin = contenido.find("</entry>", inicio) + len("</entry>")

entrada = contenido[inicio:fin]

with open("entrada_ejemplo.xml", "w", encoding="utf-8") as f:
    f.write(entrada)

print(f"Entrada guardada en entrada_ejemplo.xml ({len(entrada)} caracteres)")