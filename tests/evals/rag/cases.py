"""Hand-labeled ground truth for semantic retrieval over the catalog.
Each case names a real catalog id that should appear in the top-K results
for that query -- checked against the actual catalog, not guessed.
"""

CASES = [
    {
        "id": "quiet_work",
        "query": "somewhere quiet to work for a few hours",
        "expect_id": "romain_rolland_library",
        "note": (
            "The known miss case from the load test: semantic-only surfaced "
            "temples/a basilica/a convent and missed this entirely, despite "
            "it being explicitly tagged 'quiet' with a real description."
        ),
    },
    {
        "id": "romantic_anniversary",
        "query": "something romantic for an anniversary dinner",
        "expect_id": "villa_shanti",
        "note": "Semantic-only already got this right (also accepts la_maison_rose/villa_helena as near-miss context).",
    },
    {
        "id": "local_not_touristy",
        "query": "feels local, not touristy",
        "expect_id": "surguru",
        "note": "A cheap, genuinely local pure-veg South Indian spot, not a tourist-facing restaurant.",
    },
    {
        "id": "sunset_spot",
        "query": "good spot to watch the sunset",
        "expect_id": "sunset_promenade",
        "note": "A dedicated catalog entry for exactly this (Rock Beach & Gandhi Statue, best_slots=evening).",
    },
    {
        "id": "family_activity",
        "query": "fun activity for the whole family",
        "expect_id": "chunnambar_boat_house",
        "note": "group_fit includes 'family', category=activity.",
    },
    {
        "id": "cheap_breakfast",
        "query": "cheap filling breakfast",
        "expect_id": "indian_coffee_house",
        "note": "cost_pp=100, price_band=low, the cheapest verified breakfast option in the catalog.",
    },
]

TOP_K = 5
