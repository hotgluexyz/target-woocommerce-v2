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
inventory payload then updates the variation by SKU, the sales order uses the
created product and variation SKUs, and the order note targets the order
created by the preceding payload.
