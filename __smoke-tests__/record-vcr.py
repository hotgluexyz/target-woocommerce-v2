"""Colocated VCR smoke-test runner for target-woocommerce-v2."""

from hotglue_smoke_test.vcr.target import VCRTargetTestRunner


class TargetWoocommerceTestRunner(VCRTargetTestRunner):
    # Tax response fields are used to create the subsequent order request.
    PRESERVE_KEYS = {
        "class",
        "country",
        "id",
        "name",
        "rate",
        "shipping",
        "slug",
        "state",
        "value",
    }

    def module(self) -> str:
        return "target_woocommerce"

    def launch(self):
        from target_woocommerce.target import TargetWoocommerce

        TargetWoocommerce.cli()


if __name__ == "__main__":
    TargetWoocommerceTestRunner.main()
