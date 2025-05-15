"""WoocommerceSink target sink class, which handles writing streams."""


from datetime import datetime
from typing import Dict, List, Optional


from singer_sdk.plugin_base import PluginBase
from target_hotglue.client import HotglueSink
from base64 import b64encode

MAX_PARALLELISM = 10

class WoocommerceSink(HotglueSink):
    """WoocommerceSink target sink class."""

    def __init__(
        self,
        target: PluginBase,
        stream_name: str,
        schema: Dict,
        key_properties: Optional[List[str]],
    ) -> None:
        self._state = dict(target._state)
        super().__init__(target, stream_name, schema, key_properties)

    summary_init = False
    available_names = []

    @property
    def name(self):
        raise NotImplementedError

    @property
    def endpoint(self):
        raise NotImplementedError

    @property
    def unified_schema(self):
        raise NotImplementedError
    
    @property
    def authenticator(self):
        user = self.config.get("consumer_key")
        passwd = self.config.get("consumer_secret")
        token = b64encode(f"{user}:{passwd}".encode()).decode()
        return f"Basic {token}"

    @property
    def http_headers(self) -> dict:
        """Return the http headers needed."""
        headers = {}
        headers["Content-Type"] = "application/json"
        headers.update({"Authorization": self.authenticator})
        if "user_agent" in self.config:
            headers["User-Agent"] = self.config.get("user_agent")
        return headers

    @property
    def base_url(self):
        site_url = self.config["site_url"].strip("/")
        return f"{site_url}/wp-json/wc/v3/"

    def url(self, endpoint=None):
        if not endpoint:
            endpoint = self.endpoint
        return f"{self.base_url}{endpoint}"

    def validate_input(self, record: dict):
        return self.unified_schema(**record).dict()

    def validate_output(self, mapping):
        payload = self.clean_payload(mapping)
        # Add validation logic here
        return payload

    def check_payload_for_fields(self, payload, fields):
        return all([field in payload for field in fields])

    def get_if_missing_fields(self, response, fields, fallback_url):
        if fallback_url is None:
            return response

        modified_response = []
        for resp in response:
            if not self.check_payload_for_fields(resp, fields):
                modified_response.append(
                    self.request_api("GET", f"{fallback_url}{resp['id']}").json()
                )
            else:
                modified_response.append(resp)
        return modified_response

    def get_reference_data(self, stream, fields=None, filter={}, fallback_url=None):
        self.logger.info(f"Getting reference data for {stream}")
        page = 1
        data = []
        params = {"per_page": 100, "order": "asc", "page": page}
        params.update(filter)
        while True:
            resp = self.request_api("GET", stream, params)
            total_pages = resp.headers.get("X-WP-TotalPages")
            resp = resp.json()
            if fields:
                resp = [
                    {k: v for k, v in r.items() if k in fields}
                    for r in self.get_if_missing_fields(resp, fields, fallback_url)
                ]
            data += resp

            if page % 10 == 0:
                self.logger.info(f"Fetched {len(data)} records for {stream} page {page}")

            if resp and int(total_pages) > page:
                page += 1
                params.update({"page": page})
            else:
                break
        self.logger.info(f"Reference data for {stream} fetched successfully. {len(data)} records found.")
        return data

    @staticmethod
    def clean_dict_items(dict):
        return {k: v for k, v in dict.items() if v not in [None, ""]}

    def clean_payload(self, item):
        item = self.clean_dict_items(item)
        output = {}
        for k, v in item.items():
            if isinstance(v, datetime):
                dt_str = v.strftime("%Y-%m-%dT%H:%M:%S%z")
                if len(dt_str) > 20:
                    output[k] = f"{dt_str[:-2]}:{dt_str[-2:]}"
                else:
                    output[k] = dt_str
            elif isinstance(v, dict):
                output[k] = self.clean_payload(v)
            else:
                output[k] = v
        return output
