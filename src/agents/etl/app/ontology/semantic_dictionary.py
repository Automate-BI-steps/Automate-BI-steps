SEMANTIC_DICTIONARY = {
    "domains": {

        "person": {
            "entities": [
                "customer",
                "employee",
                "shipper",
                "patient",
                "student",
                "driver",
                "user",
                "person",
                "agent",
                "manager",
            ]
        },

        "order": {
            "events": [
                "order",
                "purchase",
                "sale",
                "transaction",
                "invoice",
            ],

            "event_details": [
                "order_detail",
                "order_line",
                "order_item",
                "transaction_detail",
                "transaction_line",
            ],
        },

        "product": {
            "entities": [
                "product",
                "item",
                "product_variant",
            ],

            "classifications": [
                "category",
                "product_category",
                "brand",
                "product_attribute",
            ],
        },
    }
}


def get_semantic_dictionary():
    return SEMANTIC_DICTIONARY.copy()