"""WoocommerceSink target sink class, which handles writing streams."""

import hashlib
import json

from singer_sdk.sinks import RecordSink
from typing import Dict, List, Optional

from target_woocommerce.rest import Rest
from singer_sdk.plugin_base import PluginBase


class WoocommerceSink(RecordSink, Rest):
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

            if resp and int(total_pages) > page:
                page += 1
                params.update({"page": page})
            else:
                break
        return data

    def init_state(self):
        self.latest_state = self._state or {"bookmarks": {}, "summary": {}}
        if self.name not in self.latest_state["bookmarks"]:
            if not self.latest_state["bookmarks"].get(self.name):
                self.latest_state["bookmarks"][self.name] = []
        if not self.summary_init:
            self.latest_state["summary"] = {}
            if not self.latest_state["summary"].get(self.name):
                self.latest_state["summary"][self.name] = {
                    "success": 0,
                    "fail": 0,
                    "existing": 0,
                    "updated": 0,
                }

            self.summary_init = True

    def process_record(self, record: dict, context: dict) -> None:
        """Process the record."""
        hash = hashlib.sha256(json.dumps(record).encode()).hexdigest()
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
