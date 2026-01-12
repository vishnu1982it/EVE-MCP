from __future__ import annotations

import os
import re
import select
import socket
import time
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from eve_api import EveClient

# --------------------------
# Env + client
# --------------------------
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


# --------------------------
# Console driver (raw TCP to EVE telnet port)
# --------------------------
class IOSConsole:
    ANY_PROMPT_RE = re.compile(r"(>|#)\s*$")

    def __init__(self, host: str, port: int, timeout: float = 12.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock: Optional[socket.socket] = None

    def connect(self) -> "IOSConsole":
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect((self.host, self.port))
        s.setblocking(False)
        self.sock = s
        time.sleep(0.6)
        return self

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def _recv_nonblock(self, wait: float = 0.4) -> str:
        if not self.sock:
            return ""
        buf = b""
        end = time.time() + wait
        while time.time() < end:
            r, _, _ = select.select([self.sock], [], [], 0.1)
            if not r:
                continue
            try:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                if len(chunk) < 4096:
                    break
            except BlockingIOError:
                break
            except Exception:
                break
        return buf.decode(errors="ignore")

    def _drain(self, seconds: float = 0.8) -> str:
        out = ""
        end = time.time() + seconds
        while time.time() < end:
            out += self._recv_nonblock(0.2)
        return out

    def send_raw(self, s: str) -> None:
        if not self.sock:
            raise RuntimeError("Console not connected")
        self.sock.sendall(s.encode())

    def send_and_collect(self, s: str, wait: float = 0.6) -> str:
        self.send_raw(s)
        return self._recv_nonblock(wait)

    def read_until_any(self, patterns: List[str], max_wait: float = 90.0) -> str:
        buf = ""
        regs = [re.compile(p, re.I) for p in patterns]
        start = time.time()
        while time.time() - start < max_wait:
            buf += self._recv_nonblock(0.7)
            for rg in regs:
                if rg.search(buf):
                    return buf
        return buf

    def read_until_prompt(self, max_wait: float = 35.0) -> str:
        buf = ""
        start = time.time()
        while time.time() - start < max_wait:
            buf += self._recv_nonblock(0.7)
            if self.ANY_PROMPT_RE.search(buf):
                return buf
        return buf

    def ensure_prompt(self, max_wait: float = 180.0) -> str:
        # press enter a couple times to wake console
        self.send_and_collect("\r", 0.8)
        self.send_and_collect("\r", 0.8)
        return self.read_until_any(
            patterns=[
                r"Press RETURN to get started",
                r"Would you like to enter the initial configuration dialog\?\s*\[yes/no\]:",
                r"%\s*Please answer 'yes' or 'no'\.",
                r"initial configuration dialog",
                r"autoconfig|autoinstall",
                r">|#",
            ],
            max_wait=max_wait,
        )

    def bootstrap_ios(self) -> str:
        """
        Handles first-boot prompts reliably for your IOSv image:
        - Press RETURN to get started
        - Would you like to enter initial configuration dialog? [yes/no]:
          (and the re-prompt: % Please answer 'yes' or 'no'.)
        - autoinstall/autoconfig prompts
        - get to enable mode
        - terminal length 0
        Returns buffer for debug.
        """
        screen = self.ensure_prompt(max_wait=180.0)

        # Press RETURN prompt
        if re.search(r"Press RETURN to get started", screen, re.I):
            self.send_and_collect("\r", 1.0)
            screen += self._drain(1.2)

        # Initial config dialog question (answer NO)
        if re.search(r"Would you like to enter the initial configuration dialog\?\s*\[yes/no\]:", screen, re.I):
            self.send_and_collect("no\r", 1.0)
            screen += self._drain(1.2)

        # Some IOS images re-prompt with "Please answer yes/no"
        for _ in range(8):
            if re.search(r"%\s*Please answer 'yes' or 'no'\.", screen, re.I) or re.search(
                r"Would you like to enter the initial configuration dialog\?\s*\[yes/no\]:", screen, re.I
            ):
                self.send_and_collect("no\r", 1.0)
                self.send_and_collect("\r", 0.8)
                screen += self._drain(1.0)
            else:
                break

        # Autoinstall/autoconfig prompts (rare but safe)
        if re.search(r"autoconfig|autoinstall", screen, re.I):
            self.send_and_collect("no\r", 1.0)
            self.send_and_collect("\r", 0.8)
            screen += self._drain(1.0)

        # Force prompt
        self.send_and_collect("\r", 0.6)
        screen += self.read_until_prompt(max_wait=60.0)

        # If in user exec, go enable
        if ">" in screen and "#" not in screen:
            self.send_and_collect("enable\r", 0.8)
            # If it asks for password, blank enter
            self.send_and_collect("\r", 0.8)
            screen += self.read_until_prompt(max_wait=30.0)

        # Disable paging
        self._drain(0.3)
        self.send_and_collect("terminal length 0\r", 0.8)
        screen += self.read_until_prompt(max_wait=15.0)

        return screen

    def run_cmd(self, cmd: str, max_wait: float = 25.0) -> str:
        self._drain(0.4)
        self.send_raw(cmd.rstrip() + "\r")
        return self.read_until_prompt(max_wait=max_wait)

    def push_config(self, lines: List[str]) -> str:
        transcript = ""
        transcript += self.run_cmd("conf t", max_wait=20.0)

        for line in lines:
            self.send_raw(line.rstrip() + "\r")
            time.sleep(0.15)
            transcript += self._recv_nonblock(0.5)

        transcript += self.run_cmd("end", max_wait=20.0)
        transcript += self.run_cmd("wr mem", max_wait=60.0)
        return transcript


# --------------------------
# MCP tools
# --------------------------
@mcp.tool()
def eve_create_lab(name: str, folder_path: Optional[str] = None) -> dict:
    """
    Create an EVE-NG lab in the given folder.
    """
    return eve.create_lab(name=name, folder_path=folder_path)


@mcp.tool()
def eve_delete_lab(name: str, folder_path: Optional[str] = None) -> dict:
    """
    Delete an EVE-NG lab from the given folder.
    """
    return eve.delete_lab(name=name, folder_path=folder_path)


@mcp.tool()
def eve_count_labs_in_folder(folder_path: str = "/") -> dict:
    """
    Small sanity tool to confirm MCP server is running and EVE session is valid.
    Tries to list /api/folders and counts labs under the folder by scanning names.
    """
    # Not all EVE builds expose a direct "list labs" endpoint consistently for CE,
    # so we just return auth+default folder as a heartbeat.
    auth = eve.get_auth()
    return {
        "status": "success",
        "folder_path": folder_path,
        "default_folder": eve.default_folder,
        "auth_status": auth.get("status"),
        "auth_user": (auth.get("data") or {}).get("email"),
    }


def _build_ospf_config(router_name: str) -> List[str]:
    ip_map = {"R1": "192.168.123.1", "R2": "192.168.123.2", "R3": "192.168.123.3"}
    lo_map = {"R1": "1.1.1.1", "R2": "2.2.2.2", "R3": "3.3.3.3"}

    if router_name not in ip_map:
        raise RuntimeError(f"Router name must be one of {list(ip_map.keys())}, got '{router_name}'")

    gi_ip = ip_map[router_name]
    lo_ip = lo_map[router_name]

    return [
        f"hostname {router_name}",
        "no ip domain-lookup",
        "interface gigabitEthernet0/0",
        f" ip address {gi_ip} 255.255.255.0",
        " no shutdown",
        "exit",
        "interface loopback0",
        f" ip address {lo_ip} 255.255.255.255",
        "exit",
        "router ospf 1",
        f" router-id {lo_ip}",
        " network 192.168.123.0 0.0.0.255 area 0",
        f" network {lo_ip} 0.0.0.0 area 0",
        "exit",
    ]


@mcp.tool()
def eve_configure_ospf_triangle(
    lab_name: str,
    folder_path: Optional[str] = None,
    routers: Optional[List[str]] = None,
    wait_after_start_seconds: int = 120,
) -> dict:
    """
    Console into IOSv routers, handle first boot prompts, configure:
      - Gi0/0 IPs: 192.168.123.1/24, .2/24, .3/24
      - Loopbacks: 1.1.1.1/32, 2.2.2.2/32, 3.3.3.3/32
      - OSPF process 1 area 0
    Return show outputs.
    """
    if routers is None:
        routers = ["R1", "R2", "R3"]

    if wait_after_start_seconds and wait_after_start_seconds > 0:
        time.sleep(wait_after_start_seconds)

    results = []

    for r in routers:
        endpoint = eve.get_console_endpoint(lab_name=lab_name, node_name=r, folder_path=folder_path)
        host = endpoint["host"]
        port = int(endpoint["port"])

        con = IOSConsole(host, port).connect()
        try:
            boot_screen = con.bootstrap_ios()

            cfg_transcript = con.push_config(_build_ospf_config(r))

            # Give OSPF a moment
            time.sleep(4.0)

            ip_int = con.run_cmd("show ip interface brief", max_wait=30.0)
            ospf_nei = con.run_cmd("show ip ospf neighbor", max_wait=30.0)
            ospf_route = con.run_cmd("show ip route ospf", max_wait=30.0)

            results.append(
                {
                    "router": r,
                    "console": f"{host}:{port}",
                    "boot_screen_tail": boot_screen[-900:],
                    "config_transcript_tail": cfg_transcript[-1500:],
                    "show_ip_int_brief": ip_int,
                    "show_ip_ospf_neighbor": ospf_nei,
                    "show_ip_route_ospf": ospf_route,
                }
            )
        finally:
            con.close()

    return {"status": "success", "lab": lab_name, "folder": folder_path or eve.default_folder, "results": results}


@mcp.tool()
def eve_build_router_switch_topology(
    lab_name: str,
    router_names: List[str],
    folder_path: Optional[str] = None,
    switch_name: str = "SW1",
    router_template: str = "vios",
    router_image: Optional[str] = None,
    switch_template: str = "viosl2",
    switch_image: Optional[str] = None,
    router_uplink_intf: str = "GigabitEthernet0/0",
    switch_port_list: Optional[List[str]] = None,
    start_nodes: bool = True,
) -> dict:
    """
    Create 1 vIOS L2 switch + N routers and connect each router's uplink to a unique switch port.
    Uses per-link EVE networks (bridge/cloud objects) under the hood.
    """

    if not router_names or len(router_names) < 1:
        raise RuntimeError("router_names must contain at least one router name, e.g. ['R1','R2'].")

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
            f"Not enough switch ports provided. routers={len(router_names)} ports={len(switch_port_list)}."
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

    router_ids: Dict[str, str] = {}
    for r in router_names:
        rid = eve.get_node_id_by_name(lab_name, r, folder_path)
        if not rid:
            raise RuntimeError(f"Could not find router node id for {r}")
        router_ids[r] = rid

    # 3) Connect each router to switch via a per-link network
    base_left = 450
    base_top = 330
    step_left = 140

    links = []
    for i, r in enumerate(router_names):
        sw_intf = switch_port_list[i]
        net_name = f"L_{r}_{switch_name}"

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
            net_id = eve.get_network_id_by_name(lab_name, net_name, folder_path) or ""
        if not net_id:
            raise RuntimeError(f"Could not determine network id for {net_name}")

        eve.connect_node_interface_to_network(
            lab_name=lab_name,
            folder_path=folder_path,
            node_id=router_ids[r],
            interface_name=router_uplink_intf,
            network_id=net_id,
        )

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


@mcp.tool()
def eve_debug_console(lab_name: str, node_name: str, folder_path: Optional[str] = None) -> dict:
    """
    Debug helper: ALWAYS returns node_detail; tries console_endpoint.
    """
    node_id = eve.get_node_id_by_name(lab_name, node_name, folder_path)
    if not node_id:
        return {"status": "error", "message": f"node '{node_name}' not found"}

    detail = eve.get_node_detail(lab_name, node_id, folder_path)

    endpoint = None
    endpoint_error = None
    try:
        endpoint = eve.get_console_endpoint(lab_name, node_name, folder_path)
    except Exception as e:
        endpoint_error = str(e)

    return {
        "status": "success",
        "lab": lab_name,
        "node_name": node_name,
        "node_id": node_id,
        "node_detail": detail,
        "console_endpoint": endpoint,
        "console_endpoint_error": endpoint_error,
    }


if __name__ == "__main__":
    try:
        mcp.run()
    except KeyboardInterrupt:
        pass
