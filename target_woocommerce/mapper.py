def products_from_unified(record):

    mapp = {
        "name": "name",
        "price": "regular_price",
        "description": "description",
        "short_description": "short_description",
        "sku": "sku",
        "category": "categories",
        "available_quantity": "stock_quantity",
    }

    products = dict(
        (mapp[key], value) for (key, value) in record.items() if key in mapp.keys()
    )

    if products.get("categories"):
        products["categories"] = products["categories"]["name"]

    if not isinstance(products.get("regular_price"), str):
        products["regular_price"] = str(products["regular_price"])

    return products


def orders_from_unified(record):

    mapp = {
        "billing_address": "billing",
        "shipping_address": "shipping",
        "line_items": "line_items",
        "currency": "currency",
    }

    orders = dict(
        (mapp[key], value) for (key, value) in record.items() if key in mapp.keys()
    )

    if orders.get("currency"):
        # Ensuring the currency is uppercase (WooCommerce requirement)
        orders["currency"] = orders["currency"].upper()

    if orders.get("line_items"):
        orders["line_items"] = line_items_from_unified(orders["line_items"])

    if orders.get("date_paid"):
        orders["date_paid"] = orders["date_paid"].replace("Z", "")

    if record.get("customer_name") is not None:
        if orders.get("billing"):
            orders["billing"] = address_from_unified(orders["billing"])
            orders["billing"]["first_name"] = record.get("customer_name").split()[0]
            orders["billing"]["last_name"] = record.get("customer_name").split()[-1]

        if orders.get("shipping"):
            orders["shipping"] = address_from_unified(orders["shipping"])
            orders["shipping"]["first_name"] = record.get("customer_name").split()[0]
            orders["shipping"]["last_name"] = record.get("customer_name").split()[-1]

    return orders


def line_items_from_unified(line_items):

    mapp = {
        "sku": "sku",
        "quantity": "quantity",
        "product_id": "product_id",
        "product_name": "product_name",
    }

    items = []
    for item in line_items:

        if item.get("discount_amount") and item.get("total_price"):
            item["discount_amount"] = item["total_price"] - item["discount_amount"]

        items.append(
            dict(
                (mapp[key], value)
                for (key, value) in item.items()
                if key in mapp.keys()
            )
        )

    return items


def address_from_unified(address):

    mapp = {
        "line1": "address_1",
        "line2": "address_2",
        "city": "city",
        "state": "state",
        "postal_code": "postcode",
        "country": "country",
        "firstName": "first_name",
        "lastName": "last_name",
    }

    addres = dict(
        (mapp[key], value) for (key, value) in address.items() if key in mapp.keys()
    )

    return addres
