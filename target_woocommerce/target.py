"""Woocommerce target class."""

from typing import Type

from singer_sdk import typing as th
from target_hotglue.target import TargetHotglue
from singer_sdk.sinks import Sink

from target_woocommerce.sinks import (
    ProductSink,
    UpdateInventorySink,
    SalesOrdersSink,
    OrderNotesSink,
)

SINK_TYPES = [ProductSink, UpdateInventorySink, SalesOrdersSink,OrderNotesSink]

class TargetWoocommerce(TargetHotglue):
    """Sample target for Woocommerce."""

    SINK_TYPES = [ProductSink, UpdateInventorySink, SalesOrdersSink,OrderNotesSink]

    def __init__(
        self,
        config=None,
        parse_env_config: bool = False,
        validate_config: bool = True,
        state: str = None,
    ) -> None:
        self.config_file = config[0]
        super().__init__(
            config=config,
            parse_env_config=parse_env_config,
            validate_config=validate_config,
        )

    name = "target-woocommerce"
    config_jsonschema = th.PropertiesList(
        th.Property("consumer_key", th.StringType, required=True),
        th.Property("consumer_secret", th.StringType, required=True),
        th.Property("site_url", th.StringType, required=True),
    ).to_dict()



if __name__ == "__main__":
    TargetWoocommerce.cli()
