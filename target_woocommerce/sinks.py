"""Woocommerce target sink class, which handles writing streams."""
import re

from hotglue_models_ecommerce.ecommerce import SalesOrder, Product, OrderNote
from target_woocommerce.local_models import UpdateInventory

from target_woocommerce.client import WoocommerceSink
from backports.cached_property import cached_property

class SalesOrdersSink(WoocommerceSink):
    """Woocommerce order target sink class."""

    endpoint = "orders"
    unified_schema = SalesOrder
    name = SalesOrder.Stream.name
    # Match tax rates within this absolute percentage-point tolerance.
    TAX_RATE_TOLERANCE = 0.05

    @staticmethod
    def _wc_amount(value) -> str:
        return f"{float(value):.2f}"

    @staticmethod
    def _normalize_tax_class(tax_class) -> str:
        if not tax_class or tax_class == "standard":
            return ""
        return tax_class

    @cached_property
    def tax_rates(self):
        return self.get_reference_data("taxes")

    @cached_property
    def tax_classes(self):
        return self.request_api("GET", "taxes/classes").json()

    def _resolve_product_id(self, line: dict):
        if line.get("product_id"):
            return line["product_id"]
        if line.get("sku"):
            product = self.get_reference_data(
                "products", filter={"sku": line["sku"]}
            )
            return next(p["id"] for p in product)
        raise Exception("Product not found.")

    @staticmethod
    def _tax_rates_for_amount(tax_amount, line_total) -> list:
        """Candidate % rates that would produce tax_amount for line_total.

        Prefers whole-number rates (e.g. 10% GST) when both exclusive and
        inclusive interpretations work, otherwise prefers the exclusive rate so
        awkward state taxes like 6.63% are preserved.
        """
        tax_amount = float(tax_amount)
        line_total = float(line_total)
        if tax_amount == 0:
            return [0.0]
        if line_total <= 0:
            return []

        candidates = []

        def add_candidate(rate, source):
            rate = float(rate)
            if source == "exclusive":
                if abs(round(line_total * rate / 100, 2) - tax_amount) > 0.01:
                    return
            else:
                if abs(round(line_total * rate / (100 + rate), 2) - tax_amount) > 0.01:
                    return
                # Skip near-duplicates of an exclusive candidate.
                if any(abs(rate - existing) <= 0.001 for existing, _ in candidates):
                    return
            candidates.append((rate, source))

        exclusive = tax_amount / line_total * 100
        for rate in (exclusive, round(exclusive, 2), round(exclusive, 4), round(exclusive)):
            add_candidate(rate, "exclusive")

        net = line_total - tax_amount
        if net > 0:
            inclusive = tax_amount / net * 100
            for rate in (inclusive, round(inclusive, 2), round(inclusive, 4), round(inclusive)):
                add_candidate(rate, "inclusive")

        def sort_key(item):
            rate, source = item
            return (
                abs(rate - round(rate)) > 0.001,  # whole numbers first
                abs(rate - round(rate, 2)) > 1e-9,  # then exact 2dp
                0 if source == "exclusive" else 1,
                abs(rate - round(rate, 2)),
                rate,
            )

        ordered = []
        seen = set()
        for rate, _source in sorted(candidates, key=sort_key):
            key = round(rate, 4)
            if key in seen:
                continue
            seen.add(key)
            ordered.append(rate)
        return ordered

    @staticmethod
    def _rate_applies_to_address(rate: dict, country: str, state: str) -> bool:
        rate_country = (rate.get("country") or "").upper()
        rate_state = (rate.get("state") or "").upper()
        country = (country or "").upper()
        state = (state or "").upper()
        if rate_country and country and rate_country != country:
            return False
        if rate_state and state and rate_state != state:
            return False
        return True

    def _find_matching_tax_rate(
        self, rate_pcts: list, country: str, state: str, tax_amount=None, line_total=None
    ):
        applicable = [
            rate
            for rate in self.tax_rates
            if self._rate_applies_to_address(rate, country, state)
        ]

        def rate_value(rate):
            try:
                return float(rate.get("rate") or 0)
            except (TypeError, ValueError):
                return None

        def produces_tax(existing_rate):
            """True if this WC rate would yield tax_amount on line_total."""
            if tax_amount is None or line_total is None or line_total <= 0:
                return False
            tax_amount_f = float(tax_amount)
            line_total_f = float(line_total)
            expected_exclusive = round(line_total_f * existing_rate / 100, 2)
            if abs(expected_exclusive - tax_amount_f) <= 0.01:
                return True
            expected_inclusive = round(
                line_total_f * existing_rate / (100 + existing_rate), 2
            )
            return abs(expected_inclusive - tax_amount_f) <= 0.01

        def matches(rate):
            existing = rate_value(rate)
            if existing is None:
                return False
            if any(
                abs(existing - candidate) <= self.TAX_RATE_TOLERANCE
                for candidate in rate_pcts
            ):
                return True
            # Catch awkward state rates (e.g. 6.63%) where rounding the
            # derived % drifts slightly from the configured rate.
            return produces_tax(existing)

        matches_found = [rate for rate in applicable if matches(rate)]
        if not matches_found:
            return None

        # Prefer country+state specific rates, then country-only, then global.
        preferred = rate_pcts[0] if rate_pcts else 0

        def specificity(rate):
            return (
                0 if rate.get("state") else 1,
                0 if rate.get("country") else 1,
                abs(rate_value(rate) - preferred),
            )

        return min(matches_found, key=specificity)

    def _ensure_tax_class(self, name: str, slug_hint: str) -> str:
        for tax_class in self.tax_classes:
            if tax_class.get("name") == name or tax_class.get("slug") == slug_hint:
                return self._normalize_tax_class(tax_class.get("slug"))

        self.logger.info(f"Creating WooCommerce tax class '{name}'")
        created = self.request_api(
            "POST", "taxes/classes", request_data={"name": name}
        ).json()
        self.tax_classes.append(created)
        return self._normalize_tax_class(created.get("slug") or slug_hint)

    def _create_tax_rate(
        self, rate_pct: float, country: str, state: str, apply_to_shipping: bool = False
    ) -> dict:
        # Keep two decimal places so rates like 6.63% survive creation.
        rate_pct = round(float(rate_pct), 2)
        label = f"{rate_pct:g}%"
        class_name = f"Trove {label}"
        slug_hint = f"trove-{rate_pct:g}".replace(".", "-")
        tax_class = self._ensure_tax_class(class_name, slug_hint)

        payload = {
            "country": country or "",
            "state": state or "",
            "rate": f"{rate_pct:.4f}",
            "name": class_name,
            "class": tax_class or "standard",
            # WooCommerce only taxes shipping when the matching rate has this set.
            "shipping": bool(apply_to_shipping),
        }
        self.logger.info(
            f"Creating WooCommerce tax rate {label} for "
            f"country={country or '*'} state={state or '*'} "
            f"(shipping={apply_to_shipping})"
        )
        created = self.request_api("POST", "taxes", request_data=payload).json()
        self.tax_rates.append(created)
        return created

    @staticmethod
    def _rate_has_shipping(rate: dict) -> bool:
        return rate.get("shipping") in (True, 1, "1", "true", "True")

    def _enable_shipping_on_rate(self, rate: dict) -> dict:
        rate_id = rate.get("id")
        if rate_id is None or self._rate_has_shipping(rate):
            return rate

        self.logger.info(
            f"Enabling shipping on WooCommerce tax rate "
            f"{rate.get('rate')}% (id={rate_id})"
        )
        updated = self.request_api(
            "PUT", f"taxes/{rate_id}", request_data={"shipping": True}
        ).json()
        for index, existing in enumerate(self.tax_rates):
            if existing.get("id") == rate_id:
                self.tax_rates[index] = updated
                break
        else:
            self.tax_rates.append(updated)
        return updated

    def _resolve_tax_rate(
        self,
        tax_amount,
        base_amount,
        country: str,
        state: str,
        apply_to_shipping: bool = False,
    ):
        if tax_amount is None or base_amount is None:
            return None

        rate_pcts = self._tax_rates_for_amount(tax_amount, base_amount)
        if not rate_pcts:
            self.logger.warning(
                f"Could not derive tax % from tax_amount={tax_amount} "
                f"and base_amount={base_amount}"
            )
            return None

        matched = self._find_matching_tax_rate(
            rate_pcts,
            country,
            state,
            tax_amount=tax_amount,
            line_total=base_amount,
        )
        if matched:
            self.logger.info(
                f"Matched tax rate {matched.get('rate')}% "
                f"(class={matched.get('class') or 'standard'}) "
                f"for tax_amount={tax_amount}"
            )
            if apply_to_shipping:
                matched = self._enable_shipping_on_rate(matched)
            return matched

        return self._create_tax_rate(
            rate_pcts[0], country, state, apply_to_shipping=apply_to_shipping
        )

    def _resolve_tax_class_for_line(
        self, line: dict, line_total, country: str, state: str
    ) -> str:
        if line.get("tax_amount") is None:
            return None
        if line_total is None:
            self.logger.warning(
                "Skipping tax class resolution: line total unavailable for tax_amount"
            )
            return None

        rate = self._resolve_tax_rate(
            line["tax_amount"], line_total, country, state
        )
        if not rate:
            return None
        return self._normalize_tax_class(rate.get("class"))

    def _build_line_item(self, line: dict, record: dict) -> dict:
        quantity = line.get("quantity") or 0
        item = {
            "product_id": self._resolve_product_id(line),
            "quantity": quantity,
        }
        if line.get("product_name"):
            item["name"] = line["product_name"]

        discount = line.get("discount_amount") or 0
        unit_price = line.get("unit_price")
        total_price = line.get("total_price")

        if unit_price is not None:
            subtotal = float(unit_price) * float(quantity)
        elif total_price is not None:
            subtotal = float(total_price) + float(discount)
        else:
            subtotal = None

        if total_price is not None:
            total = float(total_price)
        elif subtotal is not None:
            total = subtotal - float(discount)
        else:
            total = None

        if subtotal is not None:
            item["subtotal"] = self._wc_amount(subtotal)
        if total is not None:
            item["total"] = self._wc_amount(total)

        if line.get("tax_code"):
            item["tax_class"] = line["tax_code"]
        elif line.get("tax_amount") is not None:
            billing = record.get("billing_address") or {}
            shipping = record.get("shipping_address") or {}
            country = billing.get("country") or shipping.get("country") or ""
            state = billing.get("state") or shipping.get("state") or ""
            tax_class = self._resolve_tax_class_for_line(
                line, total if total is not None else subtotal, country, state
            )
            if tax_class is not None:
                item["tax_class"] = tax_class

        return item

    def _build_shipping_lines(self, record: dict) -> list:
        shipping_lines = record.get("shipping_lines") or []
        billing = record.get("billing_address") or {}
        shipping = record.get("shipping_address") or {}
        country = billing.get("country") or shipping.get("country") or ""
        state = billing.get("state") or shipping.get("state") or ""

        if shipping_lines:
            mapped = []
            for line in shipping_lines:
                amount = line.get("total_price")
                if amount is None:
                    amount = line.get("subtotal")
                if amount is None:
                    continue
                method = line.get("code") or line.get("carrier") or "Shipping"
                # shipping_lines.total_tax is read-only in the WC API. Tax is
                # applied during calculate_totals when a matching rate has
                # shipping=true, so resolve/create that rate here.
                if line.get("total_tax") is not None:
                    self._resolve_tax_rate(
                        line["total_tax"],
                        amount,
                        country,
                        state,
                        apply_to_shipping=True,
                    )
                mapped.append(
                    {
                        "method_id": line.get("id") or method,
                        "method_title": method,
                        "total": self._wc_amount(amount),
                    }
                )
            if mapped:
                return mapped
        if record.get("total_shipping") is not None:
            return [{"total": self._wc_amount(record["total_shipping"])}]
        return []

    def _build_fee_lines(self, record: dict) -> list:
        total_discount = record.get("total_discount")
        if not total_discount:
            return []
        return [
            {
                "name": "Discount",
                "total": self._wc_amount(-abs(float(total_discount))),
                "tax_status": "none",
            }
        ]

    def preprocess_record(self, record: dict, context: dict) -> dict:
        record = self.validate_input(record)
        if record.get("customer_name"):
            customer_name = record.get("customer_name").split(" ")
            first_name = customer_name[0]
            last_name = " ".join(customer_name[1:])
        else:
            first_name = ""
            last_name = ""
        mapping = {}
        billing_address = record.get("billing_address", {})
        shipping_address = record.get("shipping_address", {})
        order_id = record.get("id") or record.get("order_number")
        if isinstance(order_id, str):
            order_id = order_id.replace("#", "")
            order_id = int(order_id)

        mapping["id"] = order_id
        mapping["status"] = record.get("status")

        if billing_address:
            mapping["billing"] = {
                "first_name": first_name,
                "last_name": last_name,
                "address_1": billing_address.get("line1"),
                "address_2": billing_address.get("line2"),
                "city": billing_address.get("city"),
                "state": billing_address.get("state"),
                "postcode": billing_address.get("postal_code"),
                "country": billing_address.get("country"),
                "email": record.get("billing_email") or record.get("customer_email"),
            }
        if shipping_address:
            mapping["shipping"] = {
                "first_name": first_name,
                "last_name": last_name,
                "address_1": shipping_address.get("line1"),
                "address_2": shipping_address.get("line2"),
                "city": shipping_address.get("city"),
                "state": shipping_address.get("state"),
                "postcode": shipping_address.get("postal_code"),
                "country": shipping_address.get("country"),
            }

        if record.get("currency"):
            mapping["currency"] = record["currency"]
        if record.get("payment_method"):
            mapping["payment_method"] = record["payment_method"]

        status = record.get("status")
        fulfilled = record.get("fulfilled")
        if fulfilled:
            mapping["status"] = "completed"
        if fulfilled is False:
            mapping["status"] = status
        if status:
            mapping["status"] = status
        if status == "completed":
            mapping["set_paid"] = True
        else:
            mapping["set_paid"] = record.get("paid", False)
        if record.get("customer_id"):
            mapping["customer_id"] = mapping["customer_id"]
        elif record.get("customer_email"):
            customer = self.get_reference_data(
                "customers", filter={"email": record["customer_email"]}
            )
            id = next((c["id"] for c in customer), None)
            mapping["customer_id"] = id

        # Resolve product tax classes before shipping so shipping can reuse the
        # same rate and flip shipping=true when shipping tax is present.
        if record["line_items"]:
            mapping["line_items"] = [
                self._build_line_item(line, record) for line in record["line_items"]
            ]

        shipping_lines = self._build_shipping_lines(record)
        if shipping_lines:
            mapping["shipping_lines"] = shipping_lines

        fee_lines = self._build_fee_lines(record)
        if fee_lines:
            mapping["fee_lines"] = fee_lines

        return self.validate_output(mapping)

    def upsert_record(self, record: dict, context: dict) -> None:
        if "id" in record:
            endpoint = f"orders/{record['id']}"
            response = self.request_api(
                "PUT", endpoint=endpoint, request_data=record
            )
            order_response = response.json()
            id = order_response.get("id")
            self.logger.info(f"{self.name} {id} updated.")
            return id, response.ok, {"updated": True}
        else:
            response = self.request_api("POST", request_data=record)
            id = response.json().get("id")
            self.logger.info(f"{self.name} created with id: {id}")
            return id, response.ok, dict()



class UpdateInventorySink(WoocommerceSink):
    """Woocommerce inventory target sink class."""

    endpoint = "products/{id}"
    unified_schema = UpdateInventory
    name = UpdateInventory.Stream.name

    @cached_property
    def products(self):
        endpoint = "products"
        fields = ["id", "name", "sku", "stock_quantity", "type"]
        return self.get_reference_data(endpoint, fields)

    @cached_property
    def product_variants(self):
        variants = []
        products = self.products
        fields = ["id", "name", "sku", "stock_quantity"]

        for product in products:
            if product["type"] != "variable":
                continue

            parent_id = product["id"]
            data = self.get_reference_data(
                f"products/{parent_id}/variations", fields, fallback_url="products/"
            )
            for d in data:
                # save the parent_id so we know it is a variant
                d["parent_id"] = parent_id
            variants += data

        return variants

    def _get_alnum_string(self, input):
        return re.sub(r"\W+", "", input)

    def preprocess_record(self, record: dict, context: dict) -> dict:
        if "product_name" in record.keys():
            record["name"] = record["product_name"]
        record = self.validate_input(record)

        product = None
        product_id = record.get("id")
        product_sku = record.get("sku")
        product_name = record.get("name")
        # check product and variant id first
        if product_id:
            product = next((p for p in self.products if str(p["id"]) == str(product_id)), None)
        if product_id and not product:
            self.logger.info(f"Product {product_id} not found in main products, checking variants...")
            product = next(
                (p for p in self.product_variants if str(p["id"]) == str(product_id)), None
            )
        # check main product sku
        if product_sku and not product:
            self.logger.info(f"Attempting to match product with sku {product_sku}")
            product_list = [p for p in self.products if p.get("sku")==product_sku]
            if len(product_list) > 1:
                self.logger.info(f"More than one product was found with sku {product_sku}, filtering product by name...")
                product = next((p for p in product_list if p.get("name") == product_name), None)
            elif len(product_list) == 1:
                product = product_list[0]
        if product_name and not product:
            self.logger.info(f"Attempting to match product with name {product_name}")
            product = next((p for p in self.products if p.get("name") == product_name), None)

        if not product:
            # If it didn't work with main products, check the variants
            if product_sku:
                self.logger.info(f"Attempting to match product with sku {product_sku} in variants...")
                product_list = [p for p in self.product_variants if p.get("sku")==product_sku]
                if len(product_list) > 1:
                    self.logger.info(f"More than one product was found with sku {product_sku}, filtering product by name...")
                    product = next((p for p in product_list if p.get("name") == product_name), None)
                elif len(product_list) == 1:
                    product = product_list[0]
            elif product_name:
                self.logger.info(f"Attempting to match product with name {product_name} in variants...")
                product = next(
                    (p for p in self.product_variants if p.get("name") == product_name), None
                )

            if not product and product_name:
                self.logger.info(f"Attempting to match product with sanitized name in variants...")
                # Some items may vary on naming and special characters
                product = next(
                    (
                        p
                        for p in self.product_variants
                        if self._get_alnum_string(p.get("name"))
                        == self._get_alnum_string(product_name)
                    ),
                    None,
                )

        if product:
            self.logger.info(f"Found product: {product}")
            _product = product.copy()
            in_stock = True
            current_stock = _product.get("stock_quantity") or 0
            self.logger.info(f"product with sku '{_product.get('sku')}' and id {_product['id']} current stock: {current_stock}, executing operation '{record['operation']}' with quantity {record['quantity']}")

            if record["operation"] == "subtract":
                current_stock = current_stock - int(record["quantity"])
            if record["operation"] == "set":
                current_stock = int(record["quantity"])
            else:
                current_stock = current_stock + int(record["quantity"])

            if current_stock <= 0:
                in_stock = False

            _product.update(
                {
                    "stock_quantity": current_stock,
                    "manage_stock": True,
                    "in_stock": in_stock,
                }
            )
            # remove sku from payload to avoid duplicate sku issues
            _product.pop("sku", None)
        else:
            raise Exception(
                f"Could not find product with through id, sku or name. Failing product: {record}"
            )

        return self.validate_output(_product)

    def upsert_record(self, record: dict, context: dict) -> None:
        """Upsert the record."""
        if record.get("parent_id"):
            endpoint = (
                self.endpoint.format(id=record["parent_id"])
                + "/variations/"
                + str(record["id"])
            )
        else:
            endpoint = self.endpoint.format(id=record["id"])

        response = self.request_api("PUT", endpoint, request_data=record)
        product_response = response.json()
        id = product_response.get("id")
        self.logger.info(f"{self.name} updated for id: {id}, new stock: {product_response.get('stock_quantity')}.")
        return id, response.ok, {"updated": True}

class ProductSink(WoocommerceSink):
    """Woocommerce order target sink class."""

    endpoint = "products"
    unified_schema = Product
    name = Product.Stream.name

    @cached_property
    def categories(self):
        endpoint = "products/categories"
        fields = ["id", "name", "slug"]
        return self.get_reference_data(endpoint, fields)

    @cached_property
    def attributes(self):
        endpoint = "products/attributes"
        fields = ["id", "name", "slug"]
        return self.get_reference_data(endpoint, fields)

    def get_existing_id(self, variant):
        if variant.get("id"):
            resp = self.request_api("GET", "products", {"include": [variant["id"]]})
            resp = resp.json()
            if resp:
                return {k: v for k, v in resp[0].items() if k in ["id", "type"]}
        if variant.get("sku"):
            resp = self.request_api("GET", "products", {"sku": variant["sku"]})
            resp = resp.json()
            if resp:
                return {
                    k: v for k, v in resp[0].items() if k in ["id", "type", "parent_id"]
                }

    def preprocess_record(self, record: dict, context: dict) -> dict:
        record = self.validate_input(record)

        for variant in record.get("variants") or []:
            product_id = self.get_existing_id(variant)
            if product_id:
                variant["id"] = product_id["id"]
                if product_id.get("parent_id"):
                    record["id"] = product_id["parent_id"]
                record["type"] = "variable" if product_id["parent_id"] else "simple"

        if not record.get("type"):
            record["type"] = "variable" if record.get("options") else "simple"

        mapping = {
            "name": record["name"],
            "sku": record.get("sku"),
            "description": record.get("description"),
            "short_description": record.get("short_description"),
            "type": record["type"],
        }

        if record.get("id"):
            mapping["id"] = record["id"]

        if record.get("image_urls"):
            mapping["images"] = [{"src": i} for i in record["image_urls"]]

        if record.get("category"):
            ctg = record["category"]
            if ctg.get("id"):
                mapping["categories"] = [{"id": ctg["id"]}]
            else:
                ctg = [
                    {"id": c["id"]} for c in self.categories if c["name"] == ctg["name"]
                ]
                mapping["categories"] = ctg
        elif record.get("categories"):
            categories = record["categories"]
            mapping["categories"] = []

            for ctg in categories:
                if ctg.get("id"):
                    mapping["categories"].append({"id": ctg["id"]})
                else:
                    ctg = [
                        {"id": c["id"]}
                        for c in self.categories
                        if c["name"] == ctg["name"]
                    ]
                    mapping["categories"] += ctg

        if record["type"] == "variable":
            mapping["variations"] = []
            for variant in record["variants"]:
                product_var = {
                    "sku": variant.get("sku"),
                    "regular_price": str(variant.get("price")),
                    "sale_price": str(variant.get("sale_price")),
                    "manage_stock": True,
                    "stock_quantity": variant.get("available_quantity"),
                    "weight": variant.get("weight"),
                    "description": variant.get("description"),
                    "dimensions": {
                        "width": variant.get("width"),
                        "length": variant.get("length"),
                        "height": variant.get("depth"),
                    },
                }

                if variant.get("id"):
                    product_var["id"] = variant["id"]

                product_var["attributes"] = []

                if variant.get("options"):
                    for option in variant["options"]:
                        product_var["attributes"].append(
                            dict(name=option["name"], option=option["value"])
                        )
                mapping["variations"].append(product_var)
            # Process attributes
            if variant.get("options"):
                variant_options = []
                for variant in record["variants"]:
                    variant_options += variant["options"]
                attributes = []
                default_attributes = []
                for option in record["options"]:
                    options = [
                        v["value"] for v in variant_options if v["name"] == option
                    ]
                    if not options:
                        continue
                    default_attribute = dict(option=options[0])
                    attribute = {
                        "position": 0,
                        "visible": False,
                        "variation": True,
                        "options": options,
                    }
                    id = next(
                        (a["id"] for a in self.attributes if a["name"] == option), None
                    )
                    if id:
                        attribute["id"] = id
                        default_attribute["id"] = id
                    else:
                        attribute["name"] = option
                        default_attribute["name"] = option
                    attributes.append(attribute)
                    default_attributes.append(default_attribute)

                    mapping["attributes"] = attributes
                    mapping["default_attributes"] = default_attributes
        else:
            if record.get("variants") and len(record["variants"]) > 0:
                variant = record["variants"][0]
                product_id = self.get_existing_id(variant)

                mapping.update(
                    {
                        "sku": variant.get("sku"),
                        "regular_price": str(variant.get("price")),
                        "manage_stock": True,
                        "stock_quantity": variant.get("available_quantity"),
                        "weight": variant.get("weight"),
                        "dimensions": {
                            "width": variant.get("width"),
                            "length": variant.get("length"),
                            "height": variant.get("depth"),
                        },
                    }
                )
                if product_id:
                    mapping["id"] = product_id["id"]

        return self.validate_output(mapping)

    def process_variation(self, record: dict, prod_response) -> None:
        """Process the record."""
        product_id = prod_response["id"]
        url = f"products/{product_id}/variations"
        for variation in record["variations"]:
            if "id" in variation:
                endpoint = f"{url}/{variation['id']}"
                response = self.request_api(
                    "PUT", endpoint=endpoint, request_data=variation
                )
                product_response = response.json()
                id = product_response.get("id")
                self.logger.info(f"Variation {id} updated.")
            else:
                for attr in variation["attributes"]:
                    sel_attr = next(
                        a
                        for a in prod_response["attributes"]
                        if a["name"] == attr["name"]
                    )
                    attr["id"] = sel_attr["id"]
                response = self.request_api(
                    "POST", endpoint=url, request_data=variation
                )
                variant_response = response.json()
                self.logger.info(f"Created variant with id: {variant_response['id']}")

    def upsert_record(self, record: dict, context: dict) -> None:
        if "id" in record:
            endpoint = f"products/{record['id']}"
            response = self.request_api("PUT", endpoint=endpoint, request_data=record)
            product_response = response.json()
            id = product_response.get("id")
            self.logger.info(f"{self.name} {id} updated.")
            if record["type"] == "variable":
                self.process_variation(record, product_response)
            return id, response.ok, {"updated": True}
        else:
            response = self.request_api("POST", request_data=record)
            product_response = response.json()
            id = product_response.get("id")
            self.logger.info(f"{self.name} created with id: {id}")
            if record["type"] == "variable":
                self.process_variation(record, product_response)
            return id, response.ok, dict()

        
class OrderNotesSink(WoocommerceSink):
    """Woocommerce order target sink class."""

    endpoint = "orders/{order_id}/notes"
    unified_schema = OrderNote
    name = OrderNote.Stream.name
    available_names = [OrderNote.Stream.name, "OrderNote"]

    def preprocess_record(self, record: dict, context: dict) -> dict:
        record = self.validate_input(record)
        #Going to skip id because could not find PUT/Update endpoint for Notes
        mapping = {
          "order_id": record.get("order_id"),
          "author": record.get("author_name"),
          "note": record.get("note"),
          "date_created": record.get("created_at"),
        }
        if "customer_note" in record:
            try:
                mapping['customer_note'] = record.get("customer_note")
            except:
                mapping['customer_note'] = False


        return self.validate_output(mapping)
    
    def upsert_record(self, record: dict, context: dict) -> None:
        """Process the record."""
        if "order_id" in record:
            endpoint = f"orders/{record['order_id']}/notes"
            response = self.request_api("POST", endpoint=endpoint, request_data=record)
            product_response = response.json()
            id = product_response.get("id")
            self.logger.info(f"{self.name} {id} added.")
            return id, response.ok, dict()
        else:
            
            self.logger.warn(f"{self.name} had no order_id skipped note {record.get('note')}")