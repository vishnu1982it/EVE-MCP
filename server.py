from __future__ import annotations

import os
from typing import List, Optional

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from eve_api import EveClient

load_dotenv()

EVE_BASE_URL = os.getenv("EVE_BASE_URL", "").strip()
EVE_USERNAME = os.getenv("EVE_USERNAME", "").strip()
EVE_PASSWORD = os.getenv("EVE_PASSWORD", "").strip()
EVE_DEFAULT_AUTHOR = os.getenv("EVE_DEFAULT_AUTHOR", "MCP")
EVE_DEFAULT_DESCRIPTION = os.getenv("EVE_DEFAULT_DESCRIPTION", "Created by MCP")

if not (EVE_BASE_URL and EVE_USERNAME and EVE_PASSWORD):
    raise RuntimeError("Missing EVE_BASE_URL / EVE_USERNAME / EVE_PASSWORD in .env")

mcp = FastMCP("eve-ng-mcp")

eve = EveClient(
    base_url=EVE_BASE_URL,
    username=EVE_USERNAME,
    password=EVE_PASSWORD,
    author=EVE_DEFAULT_AUTHOR,
    description=EVE_DEFAULT_DESCRIPTION,
)
eve.login()


@mcp.tool()
def eve_create_lab(name: str, folder_path: Optional[str] = None) -> dict:
    """
    Create an EVE-NG lab in the given folder.
    - name: Lab name (without .unl)
    - folder_path: EVE folder path like "/User1" or "/" (optional; defaults to your user folder)
    """
    return eve.create_lab(name=name, folder_path=folder_path)


@mcp.tool()
def eve_delete_lab(name: str, folder_path: Optional[str] = None) -> dict:
    """
    Delete an EVE-NG lab from the given folder.
    - name: Lab name (without .unl)
    - folder_path: EVE folder path like "/User1" or "/" (optional; defaults to your user folder)
    """
    return eve.delete_lab(name=name, folder_path=folder_path)


@mcp.tool()
def eve_build_router_switch_topology(
    lab_name: str,
    router_names: List[str],
    folder_path: Optional[str] = None,
    switch_name: str = "SW1",
    # Defaults tuned to YOUR images:
    router_template: str = "vios",
    router_image: Optional[str] = None,
    switch_template: str = "viosl2",
    switch_image: Optional[str] = None,
    # Defaults tuned to YOUR interface names:
    router_uplink_intf: str = "GigabitEthernet0/0",
    switch_port_list: Optional[List[str]] = None,
    start_nodes: bool = True,
) -> dict:
    """
    Create 1 vIOS L2 switch + N routers and connect each router's uplink to a unique switch port.
    Uses per-link EVE networks (bridge/cloud objects) under the hood.

    IMPORTANT: Network create + wiring must mimic EVE legacy UI behavior in CE.
    """

    if not router_names or len(router_names) < 1:
        raise RuntimeError("router_names must contain at least one router name, e.g. ['R1','R2'].")

    # Default switch ports if not provided (good for up to 7 routers on your switch)
    if switch_port_list is None:
        switch_port_list = [
            "GigabitEthernet0/1",
            "GigabitEthernet0/2",
            "GigabitEthernet0/3",
            "GigabitEthernet1/0",
            "GigabitEthernet1/1",
            "GigabitEthernet1/2",
            "GigabitEthernet1/3",
        ]

    if len(router_names) > len(switch_port_list):
        raise RuntimeError(
            f"Not enough switch ports provided. routers={len(router_names)} ports={len(switch_port_list)}. "
            f"Provide switch_port_list with more ports."
        )

    # 1) Create switch
    eve.add_node(
        lab_name=lab_name,
        folder_path=folder_path,
        node_name=switch_name,
        node_type="qemu",
        template=switch_template,
        image=switch_image,
        ethernet=8,
        icon="Switch.png",
    )

    # 2) Create routers
    for r in router_names:
        eve.add_node(
            lab_name=lab_name,
            folder_path=folder_path,
            node_name=r,
            node_type="qemu",
            template=router_template,
            image=router_image,
            ethernet=4,
            icon="Router.png",
        )

    sw_id = eve.get_node_id_by_name(lab_name, switch_name, folder_path)
    if not sw_id:
        raise RuntimeError(f"Could not find switch node id for {switch_name}")

    router_ids = {}
    for r in router_names:
        rid = eve.get_node_id_by_name(lab_name, r, folder_path)
        if not rid:
            raise RuntimeError(f"Could not find router node id for {r}")
        router_ids[r] = rid

    # 3) Connect each router to switch via a per-link network
    # Place link clouds in a row so they don't overlap.
    base_left = 450
    base_top = 330
    step_left = 140

    links = []
    for i, r in enumerate(router_names):
        sw_intf = switch_port_list[i]
        net_name = f"L_{r}_{switch_name}"

        # Create bridge network using UI-compatible method
        net_resp = eve.add_network(
            lab_name=lab_name,
            folder_path=folder_path,
            network_name=net_name,
            network_type="bridge",
            left=base_left + (i * step_left),
            top=base_top,
            visibility=1,
            icon="01-Cloud-Default.svg",
        )

        net_id = str(net_resp.get("data", {}).get("id") or "")
        if not net_id:
            # fallback lookup
            net_id = eve.get_network_id_by_name(lab_name, net_name, folder_path) or ""
        if not net_id:
            raise RuntimeError(f"Could not determine network id for {net_name}")

        # Router side -> network
        eve.connect_node_interface_to_network(
            lab_name=lab_name,
            folder_path=folder_path,
            node_id=router_ids[r],
            interface_name=router_uplink_intf,
            network_id=net_id,
        )

        # Switch side -> same network
        eve.connect_node_interface_to_network(
            lab_name=lab_name,
            folder_path=folder_path,
            node_id=sw_id,
            interface_name=sw_intf,
            network_id=net_id,
        )

        links.append(
            {
                "router": r,
                "router_intf": router_uplink_intf,
                "switch": switch_name,
                "switch_intf": sw_intf,
                "net": net_name,
                "net_id": net_id,
            }
        )

    # 4) Start
    start_resp = eve.start_all_nodes(lab_name=lab_name, folder_path=folder_path) if start_nodes else None

    return {
        "status": "success",
        "lab": lab_name,
        "folder": folder_path or eve.default_folder,
        "routers": router_names,
        "switch": switch_name,
        "links": links,
        "started": start_nodes,
        "start_response": start_resp,
    }


if __name__ == "__main__":
    try:
        mcp.run()
    except KeyboardInterrupt:
        pass
