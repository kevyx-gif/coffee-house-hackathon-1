import html
from collections import defaultdict

from coffee_house.domain.pricing import format_mxn


def render_menu(catalog, stock):
    esc = html.escape
    groups = defaultdict(list)
    for product in catalog.products.values():
        available = stock.products.get(product.id) if stock.confirmed else None
        label = "En stock" if available is True else "Agotado" if available is False else "Sin actualizar"
        style = "available" if available is True else "out" if available is False else "unknown"
        variants = []
        for variant in product.variants:
            notice = "Precio estimado para la demo; pendiente de confirmación del negocio" if variant.price_status == "provisional" else ""
            if variant.size_notice:
                notice += (" · " if notice else "") + variant.size_notice
            variants.append(f'<div class="variant"><span>{esc(variant.size_label)}</span><strong>{format_mxn(variant.price_cents)}</strong></div>' +
                            (f'<small>{esc(notice)}</small>' if notice else ""))
        search = esc(product.name.casefold(), quote=True)
        groups[product.category].append(f'<article class="menu-card" data-name="{search}" data-category="{esc(product.category, quote=True)}">'
            f'<div class="card-top"><h3>{esc(product.name)}</h3><span class="badge {style}">{label}</span></div>' +
            "".join(variants) + f'<small class="source">{esc(product.source.brief)}</small></article>')
    options = '<option value="">Todas las categorías</option>' + "".join(f'<option>{esc(name)}</option>' for name in groups)
    body = '<div class="menu-filters"><input id="menu-filter" aria-label="Buscar en el menú" placeholder="Busca tu bebida…"><select id="menu-category" aria-label="Categoría">' + options + '</select><button id="menu-reset" class="menu-reset">Ver todo</button></div><p id="menu-count" class="menu-count" aria-live="polite">' + str(len(catalog.products)) + ' bebidas en el menú</p><p id="menu-empty" class="menu-empty" hidden>No encontramos bebidas con esos filtros. Pulsa Ver todo para explorar el menú completo.</p>'
    for name, cards in groups.items():
        body += f'<section class="menu-category" data-category="{esc(name, quote=True)}"><h2>{esc(name)}</h2><div class="menu-grid">' + "".join(cards) + '</div></section>'
    body += '<section class="extras-section"><h2>Personaliza tu bebida</h2><p>Un extra de cada tipo; elige solo una leche. Consulta el grupo correspondiente a tu bebida.</p>'
    for extra in catalog.extras.values():
        value = stock.extras.get(extra.id) if stock.confirmed else None
        status = "En stock" if value is True else "Agotado" if value is False else "Sin actualizar"
        body += f'<div class="extra-item"><strong>{esc(extra.name)}</strong><span>{format_mxn(extra.price_cents)} · {status}</span></div>'
    return body + '</section><section class="bread"><h2>Panadería</h2><p>Consulta opciones y precios con el personal.</p></section>'
