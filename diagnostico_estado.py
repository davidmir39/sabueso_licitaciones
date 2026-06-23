# diagnostico_estado.py — script temporal de un solo uso.
# Colócalo en la raíz del proyecto (junto a main.py) y ejecútalo.
# No modifica nada: solo lee el feed e imprime los estados.

import config
from src.scraper_atom import AtomScraper, _esta_cerrada

# La URL activa se obtiene del diccionario PCSP_FEEDS usando ACTIVE_FEED,
# igual que hace el resto del proyecto.
url_feed = config.PCSP_FEEDS[config.ACTIVE_FEED]
scraper = AtomScraper(url_feed)

print(f"{'ESTADO_CONTRATO':<30} | {'¿CERRADA?':<10} | TÍTULO")
print("-" * 90)

for schema in scraper.iterar_licitaciones(limite=20):
    estado = schema.estado_contrato
    cerrada = _esta_cerrada(estado)
    # repr() nos muestra si es None, string vacío, o tiene espacios raros
    print(f"{repr(estado):<30} | {str(cerrada):<10} | {schema.titulo[:40]}")