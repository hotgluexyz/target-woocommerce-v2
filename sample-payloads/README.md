# Sample Singer payloads

These files are small, runnable Singer inputs for the target's supported
streams. Run them in this order after starting the sandbox:

```bash
cat sample-payloads/products.singer | target-woocommerce-v2 --config .secrets/config.json
cat sample-payloads/update-inventory.singer | target-woocommerce-v2 --config .secrets/config.json
cat sample-payloads/sales-orders.singer | target-woocommerce-v2 --config .secrets/config.json
cat sample-payloads/order-notes.singer | target-woocommerce-v2 --config .secrets/config.json
```

The product payload creates a variable product with two variations. The
inventory payload then updates the variation by SKU, and the sales order uses
the created product and variation SKUs. The sales order is created by the
target, so copy its returned WooCommerce order ID into `order-notes.singer`'s
`order_id` field before running the order note payload.
