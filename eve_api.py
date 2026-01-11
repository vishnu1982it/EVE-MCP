from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import quote
import httpx


@dataclass
class EveClient:
    """
    Minimal EVE-NG Community Edition API client tailored to YOUR EVE behavior.

    Key behaviors discovered:
    - Network creation MUST mimic legacy UI:
        POST /api/labs/<lab>.unl/networks
        Content-Type: application/x-www-form-urlencoded; charset=UTF-8
        Body: raw JSON string (NOT key=value form, NOT application/json)
        Header: X-Requested-With: XMLHttpRequest
    - Interface wiring MUST mimic legacy UI:
        PUT /api/labs/<lab>.unl/nodes/<id>/interfaces
        Content-Type: application/x-www-form-urlencoded; charset=UTF-8
        Body: raw JSON string like {"0":2}
        Header: X-Requested-With: XMLHttpRequest
    """

    base_url: str
    username: str
    password: str
    author: str = "MCP"
    description: str = "Created by MCP"

    _client: Optional[httpx.Client] = None
    _default_folder: Optional[str] = None

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=30.0)

    # --------------------------
    # Helpers
    # --------------------------
    def _encode_folder(self, folder_path: str) -> str:
        folder_path = folder_path.strip("/")
        if not folder_path:
            return ""
        return "/".join(quote(part, safe="") for part in folder_path.split("/") if part)

    def _lab_url_path(self, lab_name: str, folder_path: Optional[str]) -> str:
        folder = folder_path or self.default_folder
        enc_folder = self._encode_folder(folder)
        enc_lab = quote(f"{lab_name}.unl", safe="")
        if enc_folder:
            return f"/api/labs/{enc_folder}/{enc_lab}"
        return f"/api/labs/{enc_lab}"

    @staticmethod
    def _norm_ifname(name: str) -> str:
        """
        Normalize interface names so aliases match:
          "Gi0/0" == "GigabitEthernet0/0"
        """
        n = (name or "").strip().replace(" ", "")
        n_low = n.lower()
        n_low = n_low.replace("gigabitethernet", "gi")
        n_low = n_low.replace("fastethernet", "fa")
        n_low = n_low.replace("ethernet", "e")
        return n_low

    @staticmethod
    def _ui_headers(accept: bool = False) -> Dict[str, str]:
        """
        Headers that mimic the legacy EVE UI requests.
        """
        h: Dict[str, str] = {
            "X-Requested-With": "XMLHttpRequest",
        }
        if accept:
            h["Accept"] = "application/json, text/javascript, */*; q=0.01"
        return h

    @staticmethod
    def _ui_post_content_type() -> Dict[str, str]:
        return {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}

    # --------------------------
    # Auth
    # --------------------------
    def login(self) -> None:
        assert self._client is not None
        payload = json.dumps({"username": self.username, "password": self.password})
        # DO NOT force Content-Type; your box accepts raw JSON string here.
        resp = self._client.post("/api/auth/login", data=payload)

        if resp.status_code != 200:
            raise RuntimeError(f"Login failed HTTP {resp.status_code}: {resp.text}")

        js = resp.json()
        if js.get("status") != "success":
            raise RuntimeError(f"Login failed: {js}")

        info = self.get_auth()
        folder = info.get("data", {}).get("folder")
        if folder:
            self._default_folder = folder

    def get_auth(self) -> Dict[str, Any]:
        assert self._client is not None
        resp = self._client.get("/api/auth")
        resp.raise_for_status()
        return resp.json()

    @property
    def default_folder(self) -> str:
        return self._default_folder or "/"

    # --------------------------
    # Labs
    # --------------------------
    def create_lab(self, name: str, folder_path: Optional[str] = None, version: str = "1") -> Dict[str, Any]:
        assert self._client is not None
        folder_path = folder_path or self.default_folder
        payload = {
            "path": folder_path,
            "name": name,
            "version": version,
            "author": self.author,
            "description": self.description,
            "body": "",
        }
        resp = self._client.post("/api/labs", json=payload)
        resp.raise_for_status()
        return resp.json()

    def delete_lab(self, name: str, folder_path: Optional[str] = None) -> Dict[str, Any]:
        assert self._client is not None
        url = self._lab_url_path(name, folder_path)
        headers = {"Content-type": "application/json"}
        resp = self._client.delete(url, headers=headers)
        if resp.status_code >= 400:
            raise RuntimeError(f"Delete failed HTTP {resp.status_code}: {resp.text}")
        return resp.json()

    # --------------------------
    # Networks (IMPORTANT FIX)
    # --------------------------
    def add_network(
        self,
        lab_name: str,
        network_name: str,
        folder_path: Optional[str] = None,
        network_type: str = "bridge",
        left: int = 600,
        top: int = 350,
        visibility: int = 1,
        icon: str = "01-Cloud-Default.svg",
    ) -> Dict[str, Any]:
        """
        UI-compatible network create.

        Legacy UI request:
          POST /api/labs/<lab>.unl/networks
          Content-Type: application/x-www-form-urlencoded; charset=UTF-8
          Body is RAW JSON STRING:
            {"count":"1","visibility":"1","name":"Net-UI-1","type":"bridge","icon":"01-Cloud-Default.svg","left":"601","top":"373","postfix":0}
        """
        assert self._client is not None
        lab_url = self._lab_url_path(lab_name, folder_path)

        payload_obj = {
            "count": "1",
            "visibility": str(int(visibility)),
            "name": network_name,
            "type": network_type,
            "icon": icon,
            "left": str(int(left)),
            "top": str(int(top)),
            "postfix": 0,
        }

        body_str = json.dumps(payload_obj, separators=(",", ":"))

        headers = {}
        headers.update(self._ui_headers(accept=True))
        headers.update(self._ui_post_content_type())

        resp = self._client.post(
            f"{lab_url}/networks",
            headers=headers,
            data=body_str,   # raw JSON string body
        )
        resp.raise_for_status()
        return resp.json()

    def list_networks(self, lab_name: str, folder_path: Optional[str] = None) -> Dict[str, Any]:
        """
        UI-compatible list networks (adds X-Requested-With + Accept like UI).
        """
        assert self._client is not None
        lab_url = self._lab_url_path(lab_name, folder_path)
        resp = self._client.get(
            f"{lab_url}/networks",
            headers=self._ui_headers(accept=True),
        )
        resp.raise_for_status()
        return resp.json()

    def get_network_id_by_name(self, lab_name: str, network_name: str, folder_path: Optional[str] = None) -> Optional[str]:
        nets = self.list_networks(lab_name, folder_path).get("data", {}) or {}
        for _, v in nets.items():
            if v.get("name") == network_name:
                return str(v.get("id"))
        return None

    # --------------------------
    # Nodes
    # --------------------------
    def add_node(
        self,
        lab_name: str,
        node_name: str,
        folder_path: Optional[str] = None,
        node_type: str = "qemu",
        template: str = "vios",
        image: Optional[str] = None,
        icon: str = "Router.png",
        left: str = "30%",
        top: str = "30%",
        ram: str = "1024",
        cpu: int = 1,
        ethernet: int = 4,
        console: str = "telnet",
        config: str = "Unconfigured",
        delay: int = 0,
    ) -> Dict[str, Any]:
        assert self._client is not None
        lab_url = self._lab_url_path(lab_name, folder_path)

        payload: Dict[str, Any] = {
            "type": node_type,
            "template": template,
            "config": config,
            "delay": delay,
            "icon": icon,
            "name": node_name,
            "left": left,
            "top": top,
            "ram": ram,
            "console": console,
            "cpu": cpu,
            "ethernet": ethernet,
        }
        if image:
            payload["image"] = image

        resp = self._client.post(f"{lab_url}/nodes", json=payload)
        resp.raise_for_status()
        return resp.json()

    def list_nodes(self, lab_name: str, folder_path: Optional[str] = None) -> Dict[str, Any]:
        assert self._client is not None
        lab_url = self._lab_url_path(lab_name, folder_path)
        resp = self._client.get(f"{lab_url}/nodes")
        resp.raise_for_status()
        return resp.json()

    def get_node_id_by_name(self, lab_name: str, node_name: str, folder_path: Optional[str] = None) -> Optional[str]:
        nodes = self.list_nodes(lab_name, folder_path).get("data", {}) or {}
        for _, v in nodes.items():
            if v.get("name") == node_name:
                return str(v.get("id"))
        return None

    def get_node_interfaces(self, lab_name: str, node_id: str, folder_path: Optional[str] = None) -> Dict[str, Any]:
        assert self._client is not None
        lab_url = self._lab_url_path(lab_name, folder_path)
        resp = self._client.get(f"{lab_url}/nodes/{node_id}/interfaces")
        resp.raise_for_status()
        return resp.json()

    def find_interface_index(
        self,
        lab_name: str,
        node_id: str,
        interface_name: str,
        folder_path: Optional[str] = None,
        media: str = "ethernet",
    ) -> Optional[int]:
        wanted = self._norm_ifname(interface_name)
        data = self.get_node_interfaces(lab_name, node_id, folder_path).get("data", {}) or {}
        iface_list = data.get(media, []) or []
        for idx, iface in enumerate(iface_list):
            have = self._norm_ifname(iface.get("name", ""))
            if have == wanted:
                return idx
        return None

    # --------------------------
    # Wiring (IMPORTANT FIX)
    # --------------------------
    def connect_node_interface_to_network(
        self,
        lab_name: str,
        node_id: str,
        interface_name: str,
        network_id: str,
        folder_path: Optional[str] = None,
        media: str = "ethernet",
    ) -> Dict[str, Any]:
        """
        UI-compatible interface wiring.

        PUT /api/labs/<lab>.unl/nodes/<id>/interfaces
        Content-Type: application/x-www-form-urlencoded; charset=UTF-8
        Body: raw JSON string like {"0":2}
        """
        assert self._client is not None
        lab_url = self._lab_url_path(lab_name, folder_path)

        idx = self.find_interface_index(lab_name, node_id, interface_name, folder_path, media)
        if idx is None:
            iface_dump = self.get_node_interfaces(lab_name, node_id, folder_path)
            raise RuntimeError(
                f"Interface '{interface_name}' not found on node_id={node_id}. "
                f"Available interfaces JSON: {iface_dump}"
            )

        body_str = json.dumps({str(idx): int(network_id)}, separators=(",", ":"))

        headers = {}
        headers.update(self._ui_headers(accept=False))
        headers.update(self._ui_post_content_type())

        resp = self._client.put(
            f"{lab_url}/nodes/{node_id}/interfaces",
            headers=headers,
            data=body_str,
        )
        resp.raise_for_status()
        return resp.json()

    # --------------------------
    # Start/Stop
    # --------------------------
    def start_all_nodes(self, lab_name: str, folder_path: Optional[str] = None) -> Dict[str, Any]:
        assert self._client is not None
        lab_url = self._lab_url_path(lab_name, folder_path)
        headers = {"Content-type": "application/json"}
        resp = self._client.get(f"{lab_url}/nodes/start", headers=headers)
        resp.raise_for_status()
        return resp.json()
