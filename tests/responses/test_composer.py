from dataclasses import replace
from pathlib import Path

import pytest

from coffee_house.domain.catalog import (
    PROVISIONAL_NOTICE,
    PROVISIONAL_SIZE_NOTICE,
    SINGLE_PRESENTATION,
    load_catalog,
)
from coffee_house.domain.pricing import (
    Availability,
    AvailabilityDetails,
    ClarificationRequired,
    Selection,
)
from coffee_house.responses import InvalidFacts, ResponseComposer
from coffee_house.retrieval.queries import GroundedQueries
from coffee_house.storage.sqlite import AvailabilityStore, Change, StorageUnavailable


@pytest.fixture
def setup(tmp_path):
    catalog = load_catalog(Path(__file__).resolve().parents[2] / "data/catalog.json")
    store = AvailabilityStore(tmp_path / "stock.sqlite3", catalog)
    store.initialize()
    queries = GroundedQueries(catalog, None, store)  # No búsqueda ni modelo: verifica solo composición.
    return catalog, store, queries, ResponseComposer(queries)


def select(*extras):
    return Selection("hot_latte", "mediano", extras)


def test_total_and_sources_ignore_free_text(setup):
    _, _, queries, composer = setup
    publication = queries.publish(queries.prepare(select("espresso_extra")))
    reply = composer.publication(replace(publication, message="Agotado; cuesta 999 pesos"))
    assert "$85.00 MXN" in reply.text and "999" not in reply.text
    assert reply.status == "available" and reply.source_references
    assert reply.origin == "verified_data" and reply.simulated
    assert "simulación" not in reply.text and "ficha" not in reply.text


@pytest.mark.parametrize("kind,identifier", [("product", "hot_latte"), ("extra", "espresso_extra")])
def test_product_and_extra_exhausted_are_distinct(setup, kind, identifier):
    _, store, _, composer = setup
    store.save("exhaust", "fixture-admin", 0, [Change(kind, identifier, False)])
    reply = composer.stock(select("espresso_extra"))
    assert reply.status == "out"
    if kind == "extra":
        assert "todavía tenemos" in reply.text and "no tenemos Espresso extra" in reply.text
        assert "Se nos terminó" not in reply.text
    else:
        assert "Se nos terminó" in reply.text


def test_missing_size_only_blocks_price_not_whole_product_stock(setup):
    _, _, _, composer = setup
    selection = Selection("hot_latte", None, ())
    assert composer.stock(selection).status == "available"
    assert composer.quote(selection).text == "¿Qué tamaño prefieres?"


def test_change_during_generation_and_alternative_never_adopted(setup):
    _, store, queries, composer = setup
    selection = select()
    prepared = queries.prepare(selection)
    store.save("changed", "fixture-admin", 0, [Change("product", "hot_latte", False)])
    alternative = Selection("hot_cappuccino", "mediano", ())
    reply = composer.quote(selection, prepared=prepared, candidates=(alternative,), budget_cents=7000)
    assert reply.status == "out" and "Disculpa" in reply.text
    assert "¿Te gustaría considerar" in reply.text and reply.stock_revision == 1
    assert prepared.quote.selection == selection and len(store.history()) == 1


def test_unrelated_change_does_not_apologize_for_exhaustion(setup):
    _, store, queries, composer = setup
    prepared = queries.prepare(select())
    store.save("unrelated", "fixture-admin", 0, [Change("product", "hot_cappuccino", False)])
    reply = composer.quote(select(), prepared=prepared)
    assert reply.status == "available" and "Disculpa" not in reply.text


def test_stale_publication_rejected(setup):
    _, store, queries, composer = setup
    publication = queries.publish(queries.prepare(select()))
    store.save("new", "fixture-admin", 0, [Change("product", "hot_latte", False)])
    with pytest.raises(ClarificationRequired) as error:
        composer.publication(publication)
    assert error.value.code == "stock_changed"


def test_forged_total_rejected(setup):
    _, _, queries, composer = setup
    publication = queries.publish(queries.prepare(select()))
    with pytest.raises(InvalidFacts):
        composer.publication(replace(publication, facts=replace(publication.facts,
            quote=replace(publication.facts.quote, total_cents=1))))


@pytest.mark.parametrize("fault", ["revision", "excess", "stock"])
def test_unsafe_alternative_rejected(setup, fault):
    _, _, queries, composer = setup
    publication = queries.publish(queries.prepare(select()),
        candidates=(Selection("hot_cappuccino", "mediano", ()),), budget_cents=7000)
    proposal = publication.alternatives[0]
    if fault == "revision":
        proposal = replace(proposal, stock_revision=999)
    elif fault == "excess":
        proposal = replace(proposal, excess_cents=5001)
    else:
        from coffee_house.domain.pricing import calculate_quote
        proposal = replace(proposal, quote=calculate_quote(queries.catalog, Selection("iced_oreo_latte", queries.catalog.products["iced_oreo_latte"].variants[0].size, ())))
    with pytest.raises(InvalidFacts):
        composer.publication(replace(publication, alternatives=(proposal,)))


@pytest.mark.parametrize("budget,offered", [(2000, True), (1999, False)])
def test_maximum_fifty_pesos_over_budget(setup, budget, offered):
    _, _, _, composer = setup
    reply = composer.quote(select(), candidates=(select(),), budget_cents=budget)
    assert ("más de tu presupuesto" in reply.text) is offered
    if offered:
        assert "$50.00 MXN más" in reply.text and "¿Te gustaría" in reply.text


def test_database_missing_keeps_price_without_stock_claim(setup):
    _, store, _, composer = setup
    store.path.unlink()
    reply = composer.quote(select("espresso_extra"))
    assert reply.status == "stock_unavailable" and "$85.00 MXN" in reply.text
    assert "todavía tenemos" not in reply.text and reply.sources
    assert composer.stock(select()).status == "stock_unavailable"


@pytest.mark.parametrize("confirmed", [True, False])
def test_unknown_is_not_exhausted_or_disconnection(setup, confirmed):
    _, _, queries, composer = setup
    class Reader:
        def snapshot(self):
            return Availability({}, {}, 7, confirmed)
    queries.stock = Reader()
    reply = composer.stock(select("espresso_extra"))
    assert reply.status == ("unknown" if confirmed else "stock_unavailable")
    assert "Se nos terminó" not in reply.text and "Sí" not in reply.text


def test_unknown_extra_price_is_partial_never_silent_substitution(setup):
    _, _, _, composer = setup
    selection = select("extra_sin_documentar")
    reply = composer.quote(selection)
    assert reply.status == "partial_price" and "$70.00 MXN sin extras" in reply.text
    assert "confirmar el total" in reply.text and selection.extras == ("extra_sin_documentar",)


@pytest.mark.parametrize("extras", [("espresso_extra", "espresso_extra"), ("leche_avena", "leche_almendras")])
def test_restrictions_are_questions_not_adjustments(setup, extras):
    _, _, _, composer = setup
    selection = select(*extras)
    reply = composer.quote(selection)
    assert reply.status == "clarification" and "¿" in reply.text
    assert selection.extras == extras and "$" not in reply.text


@pytest.mark.parametrize("identifier,notice", [("espresso", PROVISIONAL_SIZE_NOTICE),
                                              ("hot_pumpkin_spice_latte", PROVISIONAL_NOTICE)])
def test_notices_and_sources_preserved(setup, identifier, notice):
    catalog, _, _, composer = setup
    selection = Selection(identifier, catalog.products[identifier].variants[0].size, ())
    reply = composer.quote(selection)
    assert notice in reply.text and notice in reply.notices and reply.source_references


def test_unknown_extra_group_is_explicit(setup):
    _, _, _, composer = setup
    reply = composer.quote(Selection("espresso", SINGLE_PRESENTATION, ("espresso_extra",)))
    assert reply.status == "partial_price" and "si ese extra corresponde" in reply.text


def test_new_product_uses_same_patterns_without_assuming_seed(setup):
    catalog, store, _, _ = setup
    original = catalog.products["hot_latte"]
    product = replace(original, id="latte_nuevo", name="Latte Mistral",
                      variants=(replace(original.variants[0], price_cents=8825),))
    expanded = replace(catalog, products={**catalog.products, product.id: product})
    class Reader:
        def __init__(self):
            self.current = store.snapshot()
        def snapshot(self):
            return self.current
    reader = Reader()
    composer = ResponseComposer(GroundedQueries(expanded, None, reader))
    selection = Selection(product.id, product.variants[0].size, ("espresso_extra",))
    assert composer.stock(selection).status == "unknown"
    reader.current = replace(reader.current, products={**reader.current.products, product.id: True})
    reply = composer.quote(selection)
    assert reply.status == "available" and "Latte Mistral" in reply.text and "$103.25 MXN" in reply.text
    assert store.snapshot().revision == 0 and store.history() == ()
    # Un catálogo nuevo necesita migración explícita: no reinicia SQLite automáticamente.
    with pytest.raises(StorageUnavailable):
        AvailabilityStore(store.path, expanded).initialize()
    assert store.snapshot().revision == 0


def test_model_unavailable_does_not_impersonate_model(setup):
    _, _, _, composer = setup
    reply = composer.unavailable(model=True)
    assert reply.status == "model_unavailable" and "menú" in reply.text
    assert reply.origin == "verified_data"


def test_details_are_immutable_and_complete():
    with pytest.raises(ValueError):
        AvailabilityDetails(select("espresso_extra"), True, {}, 0, True)
    source = {"espresso_extra": True}
    details = AvailabilityDetails(select("espresso_extra"), True, source, 0, True)
    source["espresso_extra"] = False
    assert details.available is True


def test_both_exhausted_and_stock_transition(setup):
    _, store, _, composer = setup
    selection = select("espresso_extra")
    prepared = composer.prepare_stock(selection)
    store.save("both", "fixture-admin", 0,
               [Change("product", "hot_latte", False), Change("extra", "espresso_extra", False)])
    reply = composer.stock(selection, prepared=prepared)
    assert reply.status == "out" and "Se nos terminó" in reply.text
    assert "no tenemos Espresso extra" in reply.text and "Disculpa" in reply.text


def test_alternative_missing_confirmation_rejected(setup):
    _, _, queries, composer = setup
    publication = queries.publish(queries.prepare(select()),
        candidates=(Selection("hot_cappuccino", "mediano", ()),), budget_cents=7000)
    proposal = replace(publication.alternatives[0], requires_confirmation=False)
    with pytest.raises(InvalidFacts):
        composer.publication(replace(publication, alternatives=(proposal,)))


def test_within_budget_options_exclude_over_budget(setup):
    _, _, _, composer = setup
    reply = composer.quote(select(), candidates=(select(), select("espresso_extra")), budget_cents=7000)
    assert "$70.00 MXN" in reply.text and "$85.00 MXN" not in reply.text
    assert "más de tu presupuesto" not in reply.text and "¿Te gustaría" in reply.text


def test_snapshot_changes_just_before_composition_blocks_old_positive(setup):
    _, store, queries, composer = setup
    initial = store.snapshot()
    class Reader:
        def __init__(self):
            self.calls = 0
        def snapshot(self):
            self.calls += 1
            if self.calls < 3:
                return initial
            return replace(initial, revision=1, products={**initial.products, "hot_latte": False})
    queries.stock = Reader()
    reply = composer.quote(select())
    assert reply.status == "clarification" and "cambió" in reply.text
    assert "serían" not in reply.text
