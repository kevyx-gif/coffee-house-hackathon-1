"""Exploración del catálogo sin adivinar una selección ni consultar al modelo."""
import re

from coffee_house.responses import CustomerReply
from coffee_house.retrieval.search import normalize


def browse_reply(question, queries):
    text = normalize(question)
    listing = re.search(r"\b(tipos|variedades|opciones|sabores|cuales|muestrame|lista)\b", text)
    if not listing or re.search(r"\b(alergia|alergico|gluten|presupuesto|extra|extras|sin|precio|cuesta)\b", text):
        return None
    stop = {"que", "cuales", "tipos", "tipo", "variedades", "variedad", "opciones", "opcion", "sabores", "sabor", "de", "del", "la", "el", "los", "las", "un", "una", "unos", "unas", "tienen", "tiene", "hay", "me", "puedes", "mostrar", "muestrame", "lista", "por", "favor", "disponibles", "disponible", "en", "stock", "existencia", "hola", "ola", "y", "o", "calientes", "caliente", "frios", "frio", "fria", "frias", "hot", "iced", "frappe", "frappes", "quiero", "ver", "saber", "conocer", "venden", "manejan"}
    words = set(re.findall(r"[a-z]+", text)) - stop
    # Singularizar solo contra palabras realmente presentes en el catálogo.
    def product_words(product):
        words = set(normalize(product.name+" "+product.category).split())
        return words | {w[:-1] for w in words if w.endswith("s")}
    vocabulary = {w for p in queries.catalog.products.values() for w in product_words(p)}
    terms = set()
    for word in words:
        if word.endswith("s") and word[:-1] in vocabulary:
            terms.add(word[:-1])
        elif word in vocabulary:
            terms.add(word)
        else:
            return None
    if not terms:
        return None
    products = [p for p in queries.catalog.products.values() if terms <= product_words(p)]
    if not products:
        return None
    modes = set()
    if re.search(r"\b(calientes?|hot)\b", text): modes.add("hot")
    if re.search(r"\b(frios?|frias?|iced)\b", text): modes.add("iced")
    if re.search(r"\b(frappes?|frapes?)\b", text): modes.add("frappe")
    if modes:
        products = [p for p in products if p.id.split("_",1)[0] in modes]
    if not products:
        return None
    snapshot = queries.stock.snapshot()
    groups = {"Calientes":[], "Fríos":[], "Frappés":[], "Otras opciones":[]}
    unavailable = []
    for product in products:
        if snapshot.products.get(product.id) is not True:
            unavailable.append(product.name)
            continue
        group = {"hot":"Calientes", "iced":"Fríos", "frappe":"Frappés"}.get(product.id.split("_",1)[0],"Otras opciones")
        name = re.sub(r"^(?:Iced|Hot) ","",product.name)
        name = re.sub(r" (?:caliente|frío|frappé)$","",name)
        groups[group].append(name)
    lines = ["Por ahora tenemos estas opciones:"] if any(groups.values()) else ["Por ahora se nos terminaron estas opciones."]
    lines += [title+": "+", ".join(names)+"." for title,names in groups.items() if names]
    if unavailable: lines.append("Agotados por ahora: "+", ".join(unavailable)+".")
    if any(groups.values()): lines.append("¿Cuál se te antoja?")
    return CustomerReply("\n\n".join(lines),"catalog_options",tuple(dict.fromkeys(p.source for p in products)),stock_revision=snapshot.revision)
