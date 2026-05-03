

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _find_net(blueprint: Dict[str, Any], net_name: str) -> Optional[Dict[str, Any]]:
    for net in blueprint.get("nets", []):
        if net.get("name") == net_name:
            return net
    return None


def _find_component(blueprint: Dict[str, Any], component_ref: str) -> Optional[Dict[str, Any]]:
    for comp in blueprint.get("components", []):
        if comp.get("ref") == component_ref:
            return comp
    return None


def _valid_pin_names(component: Dict[str, Any]) -> List[str]:
    return [p.get("name") for p in component.get("pins", []) if p.get("name") is not None]


def _available_net_names(blueprint: Dict[str, Any]) -> List[str]:
    return [n.get("name", "<unnamed>") for n in blueprint.get("nets", [])]


def _available_component_refs(blueprint: Dict[str, Any]) -> List[str]:
    return [c.get("ref", "<unnamed>") for c in blueprint.get("components", [])]


def _connection_matches(conn: Dict[str, Any], component_ref: str, pin_name: str) -> bool:
    return conn.get("component_ref") == component_ref and conn.get("pin_name") == pin_name


def update_net_connection(
    blueprint: Dict[str, Any],
    net_name: str,
    component_ref: str,
    old_pin_name: str,
    new_pin_name: str,
) -> str:

    if not isinstance(blueprint, dict):
        return "blueprint is not a dict."

    net = _find_net(blueprint, net_name)
    if net is None:
        return (
            f"failure net '{net_name}' does not exist. "
            f"Available nets: {_available_net_names(blueprint)}."
        )

    component = _find_component(blueprint, component_ref)
    if component is None:
        return (
            f"faliure component '{component_ref}' does not exist. "
            f"Available refs: {_available_component_refs(blueprint)}."
        )

    valid_pins = _valid_pin_names(component)
    if valid_pins and new_pin_name not in valid_pins:
        return (
            f"failure pin '{new_pin_name}' is not a valid pin on component "
            f"'{component_ref}'. Valid pins: {valid_pins}."
        )

    connections = net.get("connections", [])
    for conn in connections:
        if _connection_matches(conn, component_ref, old_pin_name):
            target_exists = any(
                _connection_matches(c, component_ref, new_pin_name) for c in connections
            )
            if target_exists:
                return (
                    f"failure net '{net_name}' already contains "
                    f"{component_ref}.{new_pin_name}; renaming would create a duplicate."
                )
            conn["pin_name"] = new_pin_name
            return (
                f"SUCCESS: net '{net_name}' connection "
                f"{component_ref}.{old_pin_name} → {component_ref}.{new_pin_name}."
            )

    existing_pins_for_ref = sorted(
        c.get("pin_name") for c in connections if c.get("component_ref") == component_ref
    )
    return (
        f"failure no connection {component_ref}.{old_pin_name} in net '{net_name}'. "
        f"{component_ref} pins currently in this net: {existing_pins_for_ref}."
    )


def add_component_to_net(
    blueprint: Dict[str, Any],
    net_name: str,
    component_ref: str,
    pin_name: str,
) -> str:
    if not isinstance(blueprint, dict):
        return "FAILURE: blueprint must be a dict."

    net = _find_net(blueprint, net_name)
    if net is None:
        return (
            f"FAILURE: net '{net_name}' does not exist. "
            f"Available nets: {_available_net_names(blueprint)}."
        )

    component = _find_component(blueprint, component_ref)
    if component is None:
        return (
            f"FAILURE: component '{component_ref}' does not exist. "
            f"Available refs: {_available_component_refs(blueprint)}."
        )

    valid_pins = _valid_pin_names(component)
    if valid_pins and pin_name not in valid_pins:
        return (
            f"FAILURE: pin '{pin_name}' is not a valid pin on component "
            f"'{component_ref}'. Valid pins: {valid_pins}."
        )

    connections = net.setdefault("connections", [])

    if any(_connection_matches(c, component_ref, pin_name) for c in connections):
        return (
            f"FAILURE: net '{net_name}' already contains {component_ref}.{pin_name}; "
            f"no change made."
        )

    for other_net in blueprint.get("nets", []):
        if other_net is net:
            continue
        for c in other_net.get("connections", []):
            if _connection_matches(c, component_ref, pin_name):
                return (
                    f"FAILURE: pin {component_ref}.{pin_name} is already assigned to "
                    f"net '{other_net.get('name')}'. Remove it from that net first "
                    f"(use remove_component_from_net), then add it here."
                )

    connections.append({"component_ref": component_ref, "pin_name": pin_name})
    return f"SUCCESS: added {component_ref}.{pin_name} to net '{net_name}'."


def remove_component_from_net(
    blueprint: Dict[str, Any],
    net_name: str,
    component_ref: str,
    pin_name: str,
) -> str:
    if not isinstance(blueprint, dict):
        return "failure: blueprint must be a dict."

    net = _find_net(blueprint, net_name)
    if net is None:
        return (
            f"failıure: net '{net_name}' does not exist. "
            f"Available nets: {_available_net_names(blueprint)}."
        )

    connections = net.get("connections", [])
    for idx, conn in enumerate(connections):
        if _connection_matches(conn, component_ref, pin_name):
            connections.pop(idx)
            return (
                f"SUCCESS: removed {component_ref}.{pin_name} from net '{net_name}' "
                f"(remaining connections: {len(connections)})."
            )

    present_refs = sorted({c.get("component_ref") for c in connections})
    return (
        f"failure: {component_ref}.{pin_name} is not a connection of net '{net_name}'. "
        f"Refs currently in this net: {present_refs}."
    )


TOOL_FUNCTIONS = {
    "update_net_connection": update_net_connection,
    "add_component_to_net": add_component_to_net,
    "remove_component_from_net": remove_component_from_net,
}


TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "name": "update_net_connection",
        "description": (
            "Rename a pin on an existing connection inside a specific net. "
            "Use this when a net already references the right component but "
            "the pin_name is wrong (e.g. 'RESET' must be '~{RST}'). "
            "Fails if the net, component, or the (component_ref, old_pin_name) "
            "connection does not exist, or if new_pin_name is not a declared "
            "pin of the component."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "net_name": {
                    "type": "string",
                    "description": "Exact name of the net to modify.",
                },
                "component_ref": {
                    "type": "string",
                    "description": "Reference designator of the component (e.g. 'R1', 'U2').",
                },
                "old_pin_name": {
                    "type": "string",
                    "description": "The current (incorrect) pin_name in the net's connections list.",
                },
                "new_pin_name": {
                    "type": "string",
                    "description": (
                        "The correct pin name. MUST be a case-sensitive exact match "
                        "to a `name` entry in the component's `pins` array."
                    ),
                },
            },
            "required": ["net_name", "component_ref", "old_pin_name", "new_pin_name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "add_component_to_net",
        "description": (
            "Append a new (component_ref, pin_name) connection to an existing net. "
            "Fails if the net or component does not exist, if pin_name is not a "
            "declared pin of the component, if the connection is already in this "
            "net, or if the pin is already assigned to another net (pin-uniqueness "
            "rule — remove it from the other net first)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "net_name": {
                    "type": "string",
                    "description": "Exact name of the target net.",
                },
                "component_ref": {
                    "type": "string",
                    "description": "Reference designator of the component to attach.",
                },
                "pin_name": {
                    "type": "string",
                    "description": (
                        "Pin to connect. MUST be a case-sensitive exact match to a "
                        "`name` entry in the component's `pins` array."
                    ),
                },
            },
            "required": ["net_name", "component_ref", "pin_name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "remove_component_from_net",
        "description": (
            "Remove a single (component_ref, pin_name) connection from a net. "
            "Fails if the net does not exist or if that exact connection is not "
            "currently present in the net."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "net_name": {
                    "type": "string",
                    "description": "Exact name of the net to modify.",
                },
                "component_ref": {
                    "type": "string",
                    "description": "Reference designator of the component to detach.",
                },
                "pin_name": {
                    "type": "string",
                    "description": "Exact pin name of the connection to remove.",
                },
            },
            "required": ["net_name", "component_ref", "pin_name"],
            "additionalProperties": False,
        },
    },
]


def dispatch_tool_call(
    blueprint: Dict[str, Any],
    tool_name: str,
    tool_args: Dict[str, Any],
) -> str:
    fn = TOOL_FUNCTIONS.get(tool_name)
    if fn is None:
        return f"failure: unknown tool '{tool_name}'. Available: {list(TOOL_FUNCTIONS)}."
    try:
        return fn(blueprint=blueprint, **tool_args)
    except TypeError as exc:
        return f"failure: bad arguments for '{tool_name}': {exc}."


def create_new_net(blueprint: Dict[str, Any], net_name: str) -> str:
    if not isinstance(blueprint, dict):
        return "failure: blueprint must be a dict."
    if not isinstance(net_name, str) or not net_name.strip():
        return "failure: net_name must be a non-empty string."

    if _find_net(blueprint, net_name) is not None:
        return (
            f"failure: net '{net_name}' already exists; use add_pin_to_net to "
            f"append to it instead of re-creating."
        )

    nets = blueprint.setdefault("nets", [])
    nets.append({"name": net_name, "connections": []})
    return f"SUCCESS: created empty net '{net_name}' (total nets: {len(nets)})."


def add_pin_to_net(
    blueprint: Dict[str, Any],
    net_name: str,
    component_ref: str,
    pin_name: str,
) -> str:

    if not isinstance(blueprint, dict):
        return "failure: blueprint must be a dict."
    if not isinstance(net_name, str) or not net_name.strip():
        return "failure: net_name must be a non-empty string."

    component = _find_component(blueprint, component_ref)
    if component is None:
        return (
            f"failure: component '{component_ref}' does not exist. "
            f"Available refs: {_available_component_refs(blueprint)}."
        )

    valid_pins = _valid_pin_names(component)
    if valid_pins and pin_name not in valid_pins:
        return (
            f"failure: pin '{pin_name}' is not a valid pin on component "
            f"'{component_ref}'. Valid pins: {valid_pins}."
        )

    net = _find_net(blueprint, net_name)
    net_was_created = False
    if net is None:
        net = {"name": net_name, "connections": []}
        blueprint.setdefault("nets", []).append(net)
        net_was_created = True

    connections = net.setdefault("connections", [])

    if any(_connection_matches(c, component_ref, pin_name) for c in connections):
        return (
            f"failure: net '{net_name}' already contains {component_ref}.{pin_name}; "
            f"no change made."
        )

    is_sentinel = net_name.strip().upper() in {"NC", "UNCONNECTED"}
    if not is_sentinel:
        for other_net in blueprint.get("nets", []):
            if other_net is net:
                continue
            other_name = (other_net.get("name") or "").strip().upper()
            if other_name in {"NC", "UNCONNECTED"}:
                continue
            for c in other_net.get("connections", []):
                if _connection_matches(c, component_ref, pin_name):
                    return (
                        f"failure: pin {component_ref}.{pin_name} is already "
                        f"assigned to net '{other_net.get('name')}'. "
                        f"Use remove_pin_from_net on that net first, then retry."
                    )

    connections.append({"component_ref": component_ref, "pin_name": pin_name})
    created_msg = " (net was auto-created)" if net_was_created else ""
    return (
        f"SUCCESS: added {component_ref}.{pin_name} to net '{net_name}'{created_msg}."
    )


def remove_pin_from_net(
    blueprint: Dict[str, Any],
    net_name: str,
    component_ref: str,
    pin_name: str,
) -> str:
    if not isinstance(blueprint, dict):
        return "failure: blueprint must be a dict."

    net = _find_net(blueprint, net_name)
    if net is None:
        return (
            f"failure: net '{net_name}' does not exist. "
            f"Available nets: {_available_net_names(blueprint)}."
        )

    connections = net.get("connections", [])
    for idx, conn in enumerate(connections):
        if _connection_matches(conn, component_ref, pin_name):
            connections.pop(idx)
            return (
                f"SUCCESS: removed {component_ref}.{pin_name} from net "
                f"'{net_name}' (remaining connections: {len(connections)})."
            )

    present = [
        f"{c.get('component_ref')}.{c.get('pin_name')}" for c in connections
    ]
    return (
        f"failure: {component_ref}.{pin_name} is not a connection of net "
        f"'{net_name}'. Current connections: {present}."
    )


TOOL_FUNCTIONS.update({
    "create_new_net":      create_new_net,
    "add_pin_to_net":      add_pin_to_net,
    "remove_pin_from_net": remove_pin_from_net,
})


OPENAI_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "add_pin_to_net",
            "description": (
                "Append a (component_ref, pin_name) connection to a net. "
                "If the net does not yet exist, it is created automatically "
                "before the pin is appended. Use this as the primary tool "
                "for wiring pins during repair. Fails if component_ref is "
                "not in blueprint.components, if pin_name is not a declared "
                "pin of that component, if the connection already exists on "
                "this net, or if the pin is already assigned to another "
                "(non-NC) net."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "net_name": {
                        "type": "string",
                        "description": (
                            "Exact name of the target net. If no net with "
                            "this name exists, it is created as an empty "
                            "net and then the pin is appended."
                        ),
                    },
                    "component_ref": {
                        "type": "string",
                        "description": (
                            "Reference designator of the component to attach "
                            "(e.g. 'R1', 'U2'). Must already be present in "
                            "blueprint.components."
                        ),
                    },
                    "pin_name": {
                        "type": "string",
                        "description": (
                            "Pin to connect. MUST be a case-sensitive exact "
                            "match to a `name` entry in the component's "
                            "`pins` array."
                        ),
                    },
                },
                "required": ["net_name", "component_ref", "pin_name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_pin_from_net",
            "description": (
                "Remove a single (component_ref, pin_name) connection from "
                "a net. The net itself is preserved even if its connection "
                "list becomes empty. Fails if the net does not exist or if "
                "that exact connection is not currently present in the net."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "net_name": {
                        "type": "string",
                        "description": "Exact name of the net to modify.",
                    },
                    "component_ref": {
                        "type": "string",
                        "description": "Reference designator of the component to detach.",
                    },
                    "pin_name": {
                        "type": "string",
                        "description": "Exact pin name of the connection to remove.",
                    },
                },
                "required": ["net_name", "component_ref", "pin_name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_new_net",
            "description": (
                "Create a new, empty net with the given name. Use this when "
                "you want to reserve a net name before wiring pins (for "
                "example, to keep the repair plan auditable). If the net "
                "already exists, the call fails so that existing "
                "connections are never silently discarded — use "
                "add_pin_to_net to append to an existing net."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "net_name": {
                        "type": "string",
                        "description": (
                            "Exact name of the net to create. Must be a "
                            "non-empty string and must not already exist in "
                            "blueprint.nets."
                        ),
                    },
                },
                "required": ["net_name"],
                "additionalProperties": False,
            },
        },
    },
]
