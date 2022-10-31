"""WooCommerce target class."""

from __future__ import annotations

from singer_sdk import typing as th
from singer_sdk.target_base import Target

from target_woocommerce.sinks import WooCommerceSink


class TargetWooCommerce(Target):
    """Sample target for WooCommerce."""

    name = "target-woocommerce"
    config_jsonschema = th.PropertiesList(
        th.Property("consumer_key", th.StringType, required=True),
        th.Property("consumer_secret", th.StringType, required=True),
        th.Property("site_url", th.StringType, required=True),
    ).to_dict()

    default_sink_class = WooCommerceSink


if __name__ == "__main__":
    TargetWooCommerce.cli()
