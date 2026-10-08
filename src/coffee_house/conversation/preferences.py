"""Preferencias explícitas del mensaje; ninguna elección se rellena por defecto."""
import re
from dataclasses import dataclass

from coffee_house.domain.pricing import parse_mxn
from coffee_house.retrieval.search import name_similarity, normalize


def family_of(identifier):
    return re.sub(r"^(hot|iced|frappe)_", "", identifier)


def mode_of(identifier):
    for prefix in ("hot", "iced", "frappe"):
        if identifier.startswith(prefix + "_"):
            return prefix
    return "unique"


@dataclass(frozen=True)
class PreferenceDelta:
    family: str | None = None
    proposed_family: str | None = None
    modes: tuple[str, ...] = ()
    sizes: tuple[str, ...] = ()
    extras: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    clear_extras: bool = False
    replace_milk: bool = False
    budget: int | None = None
    extra_count: int | None = None
    unknown: str | None = None


MODE_PATTERNS = {
    "hot": r"\b(caliente|calientito|calientita|hot)\b",
    "iced": r"\b(frio|fria|friazo|helado|helada|iced)\b",
    "frappe": r"\b(frape|frappe|frappes|frapes)\b",
}
EXTRA_PATTERNS = {
    "leche_avena": r"\bavena\b",
    "leche_almendras": r"\balmendras?\b",
    "espresso_extra": r"\b(?:espresso|espressos|expreso|expresos|shot|shots)\s+extra(?:s)?\b|\bextra\s+(?:de\s+)?espresso\b|\bshots?\b",
}


def presentation_requests(question):
    """Asocia tamaños con la modalidad que los precede en una comparación explícita.

    Sin tamaño en una modalidad, el renderizador solo puede mostrar una presentación
    única del catálogo. Nunca traslada mediano de caliente a frío.
    """
    text = normalize(question)
    matches = sorted((m.start(), m.end(), mode) for mode, pattern in MODE_PATTERNS.items()
                     for m in re.finditer(pattern, text))
    if len({mode for _, _, mode in matches}) < 2:
        return ()
    requests = []
    for index, (_, end, mode) in enumerate(matches):
        stop = matches[index + 1][0] if index + 1 < len(matches) else len(text)
        sizes = tuple(size for size, pattern in (
            ("mediano", r"\b(mediano|mediana|12\s*oz|360\s*ml)\b"),
            ("grande", r"\b(grande|16\s*oz|480\s*ml)\b"),
        ) if re.search(pattern, text[end:stop]))
        requests.extend((mode, size) for size in sizes or (None,))
    return tuple(dict.fromkeys(requests))


def extract_preferences(question, catalog):
    text = normalize(question)
    modes = tuple(key for key, pattern in MODE_PATTERNS.items() if re.search(pattern, text))
    sizes = tuple(key for key, pattern in (
        ("mediano", r"\b(mediano|mediana|12\s*oz|360\s*ml)\b"),
        ("grande", r"\b(grande|16\s*oz|480\s*ml)\b"),
    ) if re.search(pattern, text))
    extras, removed = [], []
    product_text = text
    for key, pattern in EXTRA_PATTERNS.items():
        for match in re.finditer(pattern, text):
            before = text[max(0, match.start()-32):match.start()]
            negative = bool(re.search(r"(?:sin|quita|quitar|retira|retirar|no quiero|no le pongas)(?:\s+(?:la|el|leche|de|un|una))*\s*$", before))
            (removed if negative else extras).append(key)
        product_text = re.sub(pattern, " ", product_text)
    # Las familias provienen del catálogo, no de una lista de respuestas por producto.
    families = tuple(dict.fromkeys(family_of(p.id) for p in catalog.products.values()))
    name_text = re.sub(r"\b(de|del)\b", " ", product_text)
    ignored = {"ola", "hola", "baso", "vaso", "un", "una", "el", "la", "lo", "los", "las", "tiene", "tienen", "hay", "quiero", "kiero", "cuanto", "cuesta", "sale", "con", "cn", "leche", "d", "sin", "nada", "extras", "extra", "ponle", "otro", "otra", "solo", "uno", "dos", "tres", "si", "por", "favor", "y", "grande", "mediano", "mediana", "caliente", "calientito", "frio", "fria", "iced", "hot", "frappe", "frape", "queda", "quedan", "quita", "retira", "cambia", "mejor", "ahora", "tengo", "traigo", "pesos", "maximo", "total"}
    name_text = " ".join(word for word in re.findall(r"\w+", name_text) if word not in ignored and not word.isdigit())
    exact = [f for f in families if re.search(r"\b" + re.escape(f.replace("_", " ")) + r"\b", name_text)]
    family, proposed = None, None
    if exact:
        longest = max(len(f.split("_")) for f in exact)
        winners = [f for f in exact if len(f.split("_")) == longest]
        if len(winners) == 1:
            family = winners[0]
    scores = sorted(((name_similarity(name_text, f.replace("_", " ")), len(f.split("_")), f)
                     for f in families), reverse=True)
    # Un nombre aproximado se confirma; nunca se adopta por similitud solamente.
    if name_text and scores and scores[0][0] >= 75:
        best = max((entry for entry in scores if entry[0] >= 75), key=lambda entry: (entry[1], entry[0]))
        if family is None or best[1] > len(family.split("_")):
            proposed, family = best[2], None
    budget_matches = re.findall(r"(?:\$\s*|(?:tengo|traigo|maximo|presupuesto(?:\s+de)?|hasta|cuento con)\s+)([0-9]+(?:[.,][0-9]{1,2})?)", text)
    budget = parse_mxn(budget_matches[-1].replace(",", ".")) if len(set(budget_matches)) == 1 else None
    unknown = "presupuesto" if len(set(budget_matches)) > 1 else None
    if re.search(r"\b(vegano|vegana|sin azucar|sin lactosa|sin leche|sin cafe|descafeinado|deslactosada|soya|coco)\b", text):
        unknown = "restriccion"
    if re.search(r"\b(?:latte|cappuccino|americano|chocolate)(?:\s+(?:caliente|frio|grande|mediano))*\s+(?:de|con sabor a)\s+", text):
        tail = re.split(r"\b(?:latte|cappuccino|americano|chocolate)(?:\s+(?:caliente|frio|grande|mediano))*\s+(?:de|con sabor a)\s+", text, maxsplit=1)[1]
        known = set(" ".join(families).replace("_", " ").split())
        flavor = re.findall(r"\w+", tail)[0] if re.findall(r"\w+", tail) else ""
        if flavor not in known and flavor not in ("avena", "almendras"):
            unknown = "producto"
    substitution = re.search(r"\b(?:cambia|cambiar)\b.*\bpor\b(.*)", text)
    if substitution:
        extras = [e for e in extras if not e.startswith("leche_") or re.search(EXTRA_PATTERNS[e], substitution[1])]
    if re.search(r"\bextra(?:s)?\b", text) and not (extras or removed or re.search(r"\b(sin extras|sin nada extra|sin extra|quita todos los extras)\b", text)):
        unknown = "extra"
    count = None
    quantities = {"cero":0, "un":1, "uno":1, "dos":2, "tres":3, "cuatro":4, "cinco":5,
                  "seis":6, "siete":7, "ocho":8, "nueve":9, "diez":10}
    quantity_pattern = r"(?<!\w)([-+]?\d+(?:[.,]\d+)?|cero|un|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\s+"
    for extra in tuple(extras):
        noun = r"(?:espresso|espressos|expreso|expresos|shot|shots)" if extra=="espresso_extra" else (
            r"(?:extras?\s+(?:de\s+)?)?(?:leches?(?:\s+de)?\s+)?" + ("avena" if extra=="leche_avena" else "almendras?"))
        match = re.search(quantity_pattern+noun+r"\b", text)
        value = 1
        if match:
            raw = match[1]
            if re.search(r"[-+.,]",raw):
                unknown = "cantidad"
            else:
                value = quantities[raw] if raw in quantities else int(raw)
        if value == 0:
            extras.remove(extra)
            removed.append(extra)
        else:
            count = max(count or 1, value)
    if re.fullmatch(r"(?:si[, ]+)?(?:solo )?(?:uno|un|1)[.!? ]*", text):
        count = 1
    if family and re.search(r"\bno quiero\s+(?:un\s+|una\s+)?" + re.escape(family.replace("_", " ")) + r"\b", text):
        unknown = "rechazo"
    return PreferenceDelta(family, proposed, modes, sizes, tuple(dict.fromkeys(extras)),
        tuple(dict.fromkeys(removed)), bool(re.search(r"\b(sin extras|sin nada extra|sin nada|quita todos los extras)\b", text)),
        bool(re.search(r"\b(cambia|cambiar|en vez|en lugar|mejor con|mejor de)\b", text)), budget, count, unknown)
