import json
from dataclasses import replace

import pytest

from coffee_house.domain.catalog import (
    PROVISIONAL_NOTICE,
    PROVISIONAL_SIZE_NOTICE,
    SINGLE_PRESENTATION,
    load_catalog,
)
from coffee_house.domain.pricing import (
    Availability,
    ClarificationRequired,
    Selection,
    budget_options,
    calculate_quote,
)

from .conftest import CATALOG_PATH

RF39 = [
    ("espresso", 60, 5000), ("espresso_macchiato", 90, 5500),
    ("espresso_cortado", 120, 5500), ("espresso_affogato", 180, 8500),
    ("te_manzanilla", 360, 3000), ("te_limon", 360, 3000),
    ("te_hierbabuena", 360, 3000), ("te_verde", 360, 3000),
]


@pytest.mark.parametrize("identifier,ml,cents", RF39)
def test_rf39_separate_price_size_sources_quote_and_recommendation(catalog, stock, identifier, ml, cents):
    product = catalog.products[identifier]
    assert len(product.variants) == 1
    variant = product.variants[0]
    assert variant.size == SINGLE_PRESENTATION and variant.volume_ml == ml
    assert variant.volume_oz is None and not variant.portion_confirmed
    assert variant.price_status == "documentado" and variant.size_status == "provisional"
    assert variant.price_source.source_id == "menu_otono" and variant.price_source.page == 3
    assert variant.size_source.source_id == "spec_001_r18"
    request = Selection(identifier, SINGLE_PRESENTATION, ())
    quote = calculate_quote(catalog, request)
    assert quote.total_cents == cents and quote.extras_cents == 0
    assert not quote.provisional and quote.size_status == "provisional"
    assert quote.notices == (PROVISIONAL_SIZE_NOTICE,)
    assert quote.size_label == f"Presentación única · {ml} ml"
    assert {source.source_id for source in quote.sources} == {"menu_otono", "spec_001_r18"}
    proposals = budget_options(catalog, [request], cents, stock, requested=request)
    assert len(proposals) == 1 and proposals[0].quote == quote
    assert proposals[0].excess_cents == 0


@pytest.mark.parametrize("identifier,ml,cents", RF39)
def test_rf39_stock_disconnection_and_budget(catalog, stock, identifier, ml, cents):
    request = Selection(identifier, SINGLE_PRESENTATION, ())
    unavailable = Availability({**stock.products, identifier: False}, stock.extras, 1, True)
    assert not budget_options(catalog, [request], cents, unavailable, requested=request)
    with pytest.raises(ClarificationRequired) as error:
        budget_options(catalog, [request], cents, replace(stock, confirmed=False), requested=request)
    assert error.value.code == "unconfirmed_stock"
    proposals = budget_options(catalog, [request], cents - 100, stock, requested=request)
    assert proposals[0].excess_cents == 100 and proposals[0].requires_confirmation
    if cents >= 5000:
        assert budget_options(catalog, [request], cents - 5000, stock, requested=request)
        if cents > 5000:
            assert not budget_options(catalog, [request], cents - 5001, stock, requested=request)


@pytest.mark.parametrize("identifier,ml,cents", RF39)
def test_rf39_no_other_presentation_or_unconfirmed_extras(catalog, identifier, ml, cents):
    for size in (None, "mediano", "grande", "sin_tamano_documentado"):
        with pytest.raises(ClarificationRequired):
            calculate_quote(catalog, Selection(identifier, size, ()))
    for extra in catalog.extras:
        with pytest.raises(ClarificationRequired) as error:
            calculate_quote(catalog, Selection(identifier, SINGLE_PRESENTATION, (extra,)))
        assert error.value.code == "unknown_extra_group"


def test_pumpkin_price_and_size_notices_remain_distinct(catalog):
    for identifier in ("hot_pumpkin_spice_latte", "iced_pumpkin_spice_latte", "frappe_pumpkin_spice_latte"):
        request = Selection(identifier, catalog.products[identifier].variants[0].size, ())
        quote = calculate_quote(catalog, request)
        assert quote.provisional and quote.size_status == "provisional"
        assert quote.notices == (PROVISIONAL_NOTICE, PROVISIONAL_SIZE_NOTICE)
        assert quote.sources[0].source_id == "spec_001_r17"


@pytest.mark.parametrize("mutation", [
    "wrong_ml", "invented_oz", "confirmed_size", "missing_notice", "menu_size_source",
    "spec_price_source", "provisional_price", "invented_group", "renamed_size", "unknown_status",
    "arbitrary_provisional_product", "old_schema",
])
def test_reject_unapproved_or_mislabelled_size_data(tmp_path, mutation):
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    product = next(p for p in data["products"] if p["id"] == "espresso")
    variant = product["variants"][0]
    if mutation == "wrong_ml":
        variant["volume_ml"] = 360
    elif mutation == "invented_oz":
        variant["volume_oz"] = 2
    elif mutation == "confirmed_size":
        variant["portion_confirmed"] = True
    elif mutation == "missing_notice":
        variant["size_notice"] = None
    elif mutation == "menu_size_source":
        variant["size_source"] = {"source_id": "menu_otono", "page": 3, "section": "Tamaños"}
    elif mutation == "spec_price_source":
        variant["price_source"] = variant["size_source"].copy()
    elif mutation == "provisional_price":
        variant["price_status"] = "provisional"
    elif mutation == "invented_group":
        product["extra_group"] = "calientes"
    elif mutation == "renamed_size":
        variant["size"] = "mediano"
        variant["volume_ml"] = 360
        variant["volume_oz"] = 12
    elif mutation == "unknown_status":
        variant["size_status"] = "estimado_libre"
    elif mutation == "arbitrary_provisional_product":
        item = data["products"][0]["variants"][0]
        item.update(size_status="provisional", portion_confirmed=False, size_notice=PROVISIONAL_SIZE_NOTICE,
                    size_source={"source_id": "spec_001_r18", "section": "RF-39"})
    elif mutation == "old_schema":
        data["schema_version"] = 1
    target = tmp_path / "catalog.json"
    target.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        load_catalog(target)
