RELATIONSHIP_PRIORS = {
    # ---------------------------------------------------------
    # PERSON
    # ---------------------------------------------------------

    ("person", "person"): 0.50,

    ("person", "order"): 0.90,
    ("person", "product"): 0.40,

    # ---------------------------------------------------------
    # ORDER
    # ---------------------------------------------------------

    ("order", "person"): 0.90,
    ("order", "order"): 0.90,
    ("order", "product"): 0.90,

    # ---------------------------------------------------------
    # PRODUCT
    # ---------------------------------------------------------

    ("product", "product"): 0.95,
    ("product", "person"): 0.40,
    ("product", "order"): 0.90,
}


EVENT_DETAIL_PRIORS = {
    # Order detail belongs strongly to the order event
    ("order", "order"): 0.95,

    # Order detail references a product
    ("order", "product"): 0.90,

    # Product category relationship
    ("product", "product"): 0.95,
}