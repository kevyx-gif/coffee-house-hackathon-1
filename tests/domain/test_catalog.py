import json

import pytest

from coffee_house.domain.catalog import SINGLE_PRESENTATION, UNKNOWN_SIZE, load_catalog

from .conftest import CATALOG_PATH

# Tabla de cotejo independiente: página 2 y página 3, precios en pesos.
# «u» conserva precio único PDF; la presentación RF-39 se autoriza por separado.
REFERENCE = """
iced_cappuccino g85
iced_caramel_macchiato g95
iced_mocca g95
iced_mocca_blanco g95
iced_oreo_latte g95
iced_americano g70
iced_latte g80
coffee_tonic g95
orange_coffee g95
orange_coffee_tonic g95
iced_dirty_taro g105
iced_dirty_chai g105
iced_chocolate g85
iced_chocolate_blanco g85
iced_taro_latte g95
iced_chai_latte g95
iced_strawberry_latte g95
iced_te_negro_limon g70
iced_te_negro_durazno g70
iced_te_negro_frutos_rojos g70
iced_te_verde_limon g70
iced_te_verde_fresa g70
iced_te_verde_mango g70
frappe_cappuccino g95
frappe_mocca g95
frappe_caramel_macchiato g95
frappe_oreo g95
frappe_chocolate g95
frappe_chocolate_blanco g95
frappe_choco_avellana g95
frappe_taro g95
frappe_chai g95
malteada_chocolate g90
malteada_fresa g90
malteada_vainilla g90
smoothie_fresa g90
smoothie_brisa_tropical g90
smoothie_berries g90
espresso u50
espresso_macchiato u55
espresso_cortado u55
espresso_affogato u85
hot_americano m60 g70
long_black m60
hot_latte m70 g80
hot_cappuccino m70 g80
flat_white m80
hot_matcha_latte m85 g95
hot_dirty_matcha m95 g105
iced_matcha_latte g95
iced_strawberry_matcha g105
iced_mango_matcha g105
iced_berries_matcha g105
iced_dirty_matcha g105
frappe_matcha g95
hot_mocca m85 g95
hot_mocca_blanco m85 g95
hot_caramel_macchiato m85 g95
hot_oreo_latte m85 g95
hot_dirty_taro m95 g105
hot_dirty_chai m95 g105
hot_chocolate m75 g85
hot_chocolate_blanco m75 g85
hot_taro_latte m85 g95
hot_chai_latte m85 g95
te_manzanilla u30
te_limon u30
te_hierbabuena u30
te_verde u30
hot_pumpkin_spice_latte m85 g95
iced_pumpkin_spice_latte g95
frappe_pumpkin_spice_latte g95
"""


def test_all_88_prices_against_reviewed_reference(catalog):
    sizes = {"m": "mediano", "g": "grande", "u": SINGLE_PRESENTATION}
    expected = {}
    for row in REFERENCE.strip().splitlines():
        identifier, *prices = row.split()
        expected[identifier] = {sizes[price[0]]: int(price[1:]) * 100 for price in prices}
    actual = {key: {variant.size: variant.price_cents for variant in product.variants}
              for key, product in catalog.products.items()}
    assert actual == expected
    assert len(actual) == 72
    assert sum(len(variants) for variants in actual.values()) == 88


def test_sizes_sources_and_no_extrapolation(catalog):
    for product in catalog.products.values():
        for variant in product.variants:
            if variant.size == UNKNOWN_SIZE:
                assert variant.volume_ml is None and variant.volume_oz is None
                assert not variant.portion_confirmed and variant.size_source is None
            elif variant.size == SINGLE_PRESENTATION:
                assert not variant.portion_confirmed
                assert variant.size_source.source_id == "spec_001_r18"
                assert variant.price_source.source_id == "menu_otono"
                assert variant.volume_oz is None
            elif variant.price_status == "provisional":
                assert not variant.portion_confirmed
                assert variant.size_source.source_id == "spec_001_r17"
            else:
                assert variant.portion_confirmed
                assert variant.size_source.page == (2 if product.extra_group == "frias" else 3)
    assert [variant.size for variant in catalog.products["long_black"].variants] == ["mediano"]
    assert [variant.size for variant in catalog.products["flat_white"].variants] == ["mediano"]


def test_missing_information_is_explicit(catalog):
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    assert data["sections_without_products"][0]["message"] == "Consulta opciones y precios con el personal"
    assert all(not product.ingredients_exhaustive for product in catalog.products.values())
    assert catalog.products["smoothie_brisa_tropical"].documented_ingredients == ("mango", "piña", "crema de coco")
    assert catalog.products["hot_latte"].documented_ingredients is None
    limits = (CATALOG_PATH.parent / "knowledge/limites.md").read_text(encoding="utf-8")
    assert "Horarios y dirección: sin documentar" in limits
    assert "Garantías de alérgenos" in limits


def test_extras_and_seed_authorized(catalog):
    assert set(catalog.extras) == {"leche_avena", "leche_almendras", "espresso_extra"}
    assert all(extra.price_cents == 1500 and extra.max_quantity == 1 for extra in catalog.extras.values())
    assert catalog.seed_unavailable_products == {"iced_oreo_latte"}
    assert catalog.seed_unavailable_extras == {"leche_almendras"}


@pytest.mark.parametrize("mutation", [
    "negative_price", "float_price", "boolean_price", "duplicate_product", "duplicate_variant",
    "wrong_volume", "invented_source", "unlabelled_estimate", "missing_portion", "unknown_seed",
])
def test_invalid_catalog_fails_closed(tmp_path, mutation):
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    variant = data["products"][0]["variants"][0]
    if mutation == "negative_price":
        variant["price_cents"] = -1
    elif mutation == "float_price":
        variant["price_cents"] = 8500.0
    elif mutation == "boolean_price":
        variant["price_cents"] = True
    elif mutation == "duplicate_product":
        data["products"].append(data["products"][0])
    elif mutation == "duplicate_variant":
        data["products"][0]["variants"].append(variant.copy())
    elif mutation == "wrong_volume":
        variant["volume_ml"] = 999
    elif mutation == "invented_source":
        variant["price_source"]["source_id"] = "imaginado"
    elif mutation == "unlabelled_estimate":
        data["products"][-1]["note"] = None
    elif mutation == "missing_portion":
        variant["size"] = UNKNOWN_SIZE
        variant["volume_ml"] = None
        variant["volume_oz"] = None
    elif mutation == "unknown_seed":
        data["simulation_seed"]["unavailable_products"].append("pan_inventado")
    target = tmp_path / "catalog.json"
    target.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        load_catalog(target)
