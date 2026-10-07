from dataclasses import replace

import pytest

from coffee_house.domain.catalog import (
    PROVISIONAL_NOTICE,
    SINGLE_PRESENTATION,
    UNKNOWN_SIZE,
)
from coffee_house.domain.pricing import (
    Availability,
    ClarificationRequired,
    Selection,
    budget_options,
    calculate_quote,
    format_mxn,
    in_stock,
    parse_mxn,
)


@pytest.mark.parametrize("product,size,extras,total", [
    ("hot_latte", "mediano", (), 7000),
    ("hot_latte", "grande", (), 8000),
    ("iced_latte", "grande", ("leche_avena", "espresso_extra"), 11000),
    ("hot_latte", "grande", ("espresso_extra",), 9500),
    ("hot_latte", "grande", ("leche_avena",), 9500),
    ("hot_oreo_latte", "mediano", (), 8500),
    ("iced_caramel_macchiato", "grande", ("leche_avena", "espresso_extra"), 12500),
])
def test_e07_e08_e09_e11_e16_e18_totals(catalog, product, size, extras, total):
    quote = calculate_quote(catalog, Selection(product, size, extras))
    assert quote.total_cents == total
    assert quote.total_cents == quote.base_cents + quote.extras_cents
    assert not quote.provisional
    assert quote.sources[0].source_id == "menu_otono"


@pytest.mark.parametrize("product,size,total", [
    ("hot_pumpkin_spice_latte", "mediano", 8500),
    ("hot_pumpkin_spice_latte", "grande", 9500),
    ("iced_pumpkin_spice_latte", "grande", 9500),
    ("frappe_pumpkin_spice_latte", "grande", 9500),
])
def test_e10_provisional_prices_and_totals(catalog, product, size, total):
    quote = calculate_quote(catalog, Selection(product, size, ("leche_avena",)))
    assert quote.base_cents == total
    assert quote.total_cents == total + 1500
    assert quote.provisional and PROVISIONAL_NOTICE in quote.notices
    assert quote.sources[0].source_id == "spec_001_r17"


@pytest.mark.parametrize("selection,code", [
    (Selection("hot_latte", "grande", ("espresso_extra", "espresso_extra")), "repeated_extra"),
    (Selection("hot_latte", "grande", ("leche_avena", "leche_almendras")), "exclusive_milks"),
    (Selection("iced_oreo_latte", "mediano", ()), "invalid_size"),
    (Selection("hot_latte", None, ()), "missing_size"),
    (Selection("panaderia", "grande", ()), "unknown_product"),
    (Selection("hot_latte", "mediano", ("jarabe_inventado",)), "unknown_extra"),
    (Selection("espresso", SINGLE_PRESENTATION, ("leche_avena",)), "unknown_extra_group"),
])
def test_clarification_without_silent_changes(catalog, selection, code):
    original = selection
    with pytest.raises(ClarificationRequired) as error:
        calculate_quote(catalog, selection)
    assert error.value.code == code
    assert selection == original


def test_e15_only_budget_fit_when_one_exists(catalog, stock):
    large = Selection("hot_latte", "grande", ())
    medium = replace(large, size="mediano")
    options = budget_options(catalog, [large, medium], 7500, stock, requested=large)
    assert [option.quote.total_cents for option in options] == [7000]
    assert options[0].requires_confirmation and options[0].changes
    assert options[0].excess_cents == 0
    assert large.size == "grande"


def test_e15_explicit_question_allows_comparing_sizes_without_assuming_one(catalog, stock):
    requested = Selection("hot_latte", None, ())
    candidates = [replace(requested, size=size) for size in ("mediano", "grande")]
    proposals = budget_options(catalog, candidates, 7500, stock, requested=requested, choose_size=True)
    assert len(proposals) == 1 and proposals[0].quote.selection.size == "mediano"
    assert proposals[0].requires_confirmation
    assert requested.size is None
    with pytest.raises(ClarificationRequired):
        budget_options(catalog, candidates, 7500, stock, requested=requested)


def test_e16_e17_excess_and_removing_extras_require_confirmation(catalog, stock):
    requested = Selection("iced_caramel_macchiato", "grande", ("leche_avena", "espresso_extra"))
    options = budget_options(catalog, [requested], 10000, stock, requested=requested)
    assert options[0].excess_cents == 2500 and options[0].requires_confirmation
    assert not budget_options(catalog, [requested], 6000, stock, requested=requested)
    alternative = replace(requested, extras=())
    options = budget_options(catalog, [requested, alternative], 6000, stock, requested=requested)
    assert len(options) == 1 and options[0].quote.total_cents == 9500
    assert options[0].excess_cents == 3500 and options[0].changes and options[0].requires_confirmation
    assert requested.extras == ("leche_avena", "espresso_extra")


@pytest.mark.parametrize("budget,expected", [(7500, 1), (7400, 0), (7499, 0), (12500, 1)])
def test_exact_50_51_and_cent_boundary(catalog, stock, budget, expected):
    request = Selection("iced_caramel_macchiato", "grande", ("leche_avena", "espresso_extra"))
    proposals = budget_options(catalog, [request], budget, stock, requested=request)
    assert len(proposals) == expected
    if proposals:
        assert proposals[0].excess_cents == max(0, 12500 - budget)


def test_unavailable_product_and_extra_independent(catalog, stock):
    iced = calculate_quote(catalog, Selection("iced_oreo_latte", "grande", ()))
    hot = calculate_quote(catalog, Selection("hot_oreo_latte", "grande", ()))
    almond = calculate_quote(catalog, Selection("hot_latte", "grande", ("leche_almendras",)))
    plain = calculate_quote(catalog, Selection("hot_latte", "grande", ()))
    assert not in_stock(iced, stock) and in_stock(hot, stock)
    assert not in_stock(almond, stock) and in_stock(plain, stock)
    assert not budget_options(catalog, [almond.selection], 12000, stock, requested=almond.selection)


def test_fresh_snapshot_changes_results_and_does_not_mutate_seed(catalog, stock):
    request = Selection("hot_latte", "grande", ())
    current = Availability({**stock.products, "hot_latte": False}, stock.extras, 1, True)
    assert budget_options(catalog, [request], 8000, stock, requested=request)
    assert not budget_options(catalog, [request], 8000, current, requested=request)
    assert stock.products["hot_latte"]
    with pytest.raises(TypeError):
        stock.products["hot_latte"] = False


def test_no_stock_claim_when_disconnected_or_incomplete(catalog, stock):
    request = Selection("hot_latte", "grande", ())
    for snapshot in (replace(stock, confirmed=False), Availability({}, {}, 0, True)):
        with pytest.raises(ClarificationRequired):
            budget_options(catalog, [request], 8000, snapshot, requested=request)
    # Precio informativo sigue consultable sin atribuir existencia.
    assert calculate_quote(catalog, request).total_cents == 8000


def test_no_alternatives_bypass_missing_size_or_two_milks(catalog, stock):
    alternative = Selection("hot_latte", "grande", ("leche_avena",))
    for request in (replace(alternative, size=None), replace(alternative, extras=("leche_avena", "leche_almendras"))):
        with pytest.raises(ClarificationRequired):
            budget_options(catalog, [alternative], 12000, stock, requested=request)


def test_unknown_portion_price_only_not_recommendation(catalog, stock):
    # Datos controlados sin excepción: RF-39 no autoriza completar cualquier porción.
    product = catalog.products["hot_latte"]
    variant = replace(product.variants[0], size=UNKNOWN_SIZE, volume_ml=None, volume_oz=None,
                      portion_confirmed=False, size_source=None, size_status="sin_confirmar", size_notice=None)
    unknown = replace(catalog, products={**catalog.products, product.id: replace(product, variants=(variant,))})
    request = Selection(product.id, UNKNOWN_SIZE, ())
    quote = calculate_quote(unknown, request)
    assert quote.total_cents == 7000 and "Porción sin confirmar" in quote.notices[0]
    with pytest.raises(ClarificationRequired):
        budget_options(unknown, [request], 7000, stock, requested=request)


def test_group_rules_apply_to_all_products_without_per_product_restrictions(catalog):
    for product in catalog.products.values():
        if product.extra_group is not None:
            for extra in catalog.extras:
                selection = Selection(product.id, product.variants[0].size, (extra,))
                assert calculate_quote(catalog, selection).extras_cents == 1500
    extra = replace(catalog.extras["leche_avena"], groups=("frias",))
    limited = replace(catalog, extras={**catalog.extras, extra.id: extra})
    with pytest.raises(ClarificationRequired) as error:
        calculate_quote(limited, Selection("hot_latte", "grande", (extra.id,)))
    assert error.value.code == "invalid_extra_group"


@pytest.mark.parametrize("value", [True, -1, 1.0, "100"])
def test_budget_rejects_non_integer_cents(catalog, stock, value):
    request = Selection("hot_latte", "mediano", ())
    with pytest.raises(ValueError):
        budget_options(catalog, [request], value, stock, requested=request)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "1.001", "1e2", "1,00", "", 75.1, True])
def test_invalid_money_is_not_reinterpreted(value):
    with pytest.raises((ValueError, TypeError)):
        parse_mxn(value)


def test_centavo_exact_and_format():
    assert parse_mxn(" 75.01 ") == 7501
    assert parse_mxn("75.1") == 7510
    assert format_mxn(7501) == "$75.01 MXN"
    assert parse_mxn("123456789012345678901234567890.12") == 12345678901234567890123456789012
