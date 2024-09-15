"""Woocommerce target sink class, which handles writing streams."""
import re
import json
import hashlib

from hotglue_models_ecommerce.ecommerce import SalesOrder, Product, OrderNote
from target_woocommerce.local_models import UpdateInventory

from target_woocommerce.client import WoocommerceSink
from backports.cached_property import cached_property


class DummySink(WoocommerceSink):
    """Woocommerce order target sink class."""

    name = "dummy_sink"

    def process_record(self, record: dict, context: dict) -> None:
        pass


class SalesOrdersSink(WoocommerceSink):
    """Woocommerce order target sink class."""

    endpoint = "orders"
    unified_schema = SalesOrder
    name = SalesOrder.Stream.name

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
                "email": billing_address.get("customer_email"),
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
            if shipping_address.get("total_shipping"):
                mapping["shipping_lines"] = [
                    {"total": shipping_address["total_shipping"]}
                ]
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

        if record["line_items"]:
            if "line_items" not in mapping:
                mapping["line_items"] = []
            for line in record["line_items"]:
                if line.get("product_id"):
                    item = {
                        "product_id": line["product_id"],
                        "quantity": line["quantity"],
                    }
                elif line.get("sku"):
                    product = self.get_reference_data(
                        "products", filter={"sku": line["sku"]}
                    )
                    id = next(p["id"] for p in product)
                    item = {"product_id": id, "quantity": line["quantity"]}
                else:
                    raise Exception("Product not found.")
                mapping["line_items"].append(item)

        return self.validate_output(mapping)

    def process_record(self, record: dict, context: dict) -> None:
        """Process the record."""
        hash = hashlib.sha256(json.dumps(record).encode()).hexdigest()
        if not self.latest_state:
            self.init_state()
        states = self.latest_state["bookmarks"][self.name]
        existing_state = next(
            (s for s in states if hash == s.get("hash") and s.get("success")), None
        )
        if existing_state:
            self.logger.info(
                f"Record of type {self.name} already exists with id: {existing_state['id']}"
            )
            self.latest_state["summary"][self.name]["existing"] += 1
            return
        state = {"hash": hash}
        try:
            if "id" in record:
                endpoint = f"orders/{record['id']}"
                response = self.request_api(
                    "PUT", endpoint=endpoint, request_data=record
                )
                order_response = response.json()
                id = order_response.get("id")
                state["id"] = id
                state["success"] = True
                state["updated"] = True
                self.latest_state["summary"][self.name]["updated"] += 1
                self.logger.info(f"{self.name} {id} updated.")

            else:
                response = self.request_api("POST", request_data=record)
                id = response.json().get("id")
                state["id"] = id
                state["success"] = True
                self.latest_state["summary"][self.name]["success"] += 1
                self.logger.info(f"{self.name} created with id: {id}")
        except:
            state["success"] = False
            self.latest_state["summary"][self.name]["fail"] += 1
        self.latest_state["bookmarks"][self.name].append(state)


class UpdateInventorySink(WoocommerceSink):
    """Woocommerce order target sink class."""

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
        if product_id:
            product = next((p for p in self.products if p["id"] == product_id), None)
        elif product_sku:
            product_list = [p for p in self.products if p.get("sku")==product_sku]
            if len(product_list) > 1:
                self.logger.info(f"More than one product was found with sku {product_sku}, filtering product by name...")
                product = next((p for p in product_list if p.get("name") == product_name), None)
            elif len(product_list) == 1:
                product = product_list[0]
        elif product_name:
            product = next((p for p in self.products if p.get("name") == product_name), None)

        if not product:
            # If it didn't work with main products, check the variants
            if product_id:
                product = next(
                    (p for p in self.product_variants if p["id"] == product_id), None
                )
            elif product_sku:
                product_list = [p for p in self.product_variants if p.get("sku")==product_sku]
                if len(product_list) > 1:
                    self.logger.info(f"More than one product was found with sku {product_sku}, filtering product by name...")
                    product = next((p for p in product_list if p.get("name") == product_name), None)
                elif len(product_list) == 1:
                    product = product_list[0]
            elif product_name:
                product = next(
                    (p for p in self.product_variants if p.get("name") == product_name), None
                )

            if not product and product_name:
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
            _product = product.copy()
            in_stock = True
            current_stock = _product.get("stock_quantity", 0)
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

    def process_record(self, record: dict, context: dict) -> None:
        """Process the record."""
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
        self.logger.info(f"{self.name} updated for id: {id}")


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

    def process_record(self, record: dict, context: dict) -> None:
        """Process the record."""
        if "id" in record:
            endpoint = f"products/{record['id']}"
            response = self.request_api("PUT", endpoint=endpoint, request_data=record)
            product_response = response.json()
            id = product_response.get("id")
            self.logger.info(f"{self.name} {id} updated.")
            if record["type"] == "variable":
                self.process_variation(record, product_response)
        else:
            response = self.request_api("POST", request_data=record)
            product_response = response.json()
            id = product_response.get("id")
            self.logger.info(f"{self.name} created with id: {id}")
            if record["type"] == "variable":
                self.process_variation(record, product_response)

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
    
    def process_record(self, record: dict, context: dict) -> None:
        """Process the record."""
        if "order_id" in record:
            endpoint = f"orders/{record['order_id']}/notes"
            response = self.request_api("POST", endpoint=endpoint, request_data=record)
            product_response = response.json()
            id = product_response.get("id")
            self.logger.info(f"{self.name} {id} added.")
            
        else:
            
            self.logger.warn(f"{self.name} had no order_id skipped note {record.get('note')}")