# -*- coding: utf-8 -*-
"""Lógica comum de navegação para a app desktop e para o protótipo Android."""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import networkx as nx


BASE_DIR = Path(__file__).resolve().parent
OSM_DIR = BASE_DIR / "OSM Pisos"
IMAGE_DIR = BASE_DIR / "Imagens ECT2"

OSM_FILES = {
    "Exterior": OSM_DIR / "Exterior.osm",
    "Piso1": OSM_DIR / "Piso1.osm",
    "Piso2": OSM_DIR / "Piso2.osm",
    "Piso3": OSM_DIR / "Piso3.osm",
}
FLOOR_IMAGES = {
    "Piso1": IMAGE_DIR / "Piso 1.jpg",
    "Piso2": IMAGE_DIR / "Piso2.png",
    "Piso3": IMAGE_DIR / "Piso 3.jpg",
}
INDOOR_FLOORS = ["Piso1", "Piso2", "Piso3"]
APP_FLOORS = ["Exterior", *INDOOR_FLOORS]
BUILDING_ORDER = ["ECT1", "ECT2", "ECHS2"]
WEB_MERCATOR_LIMIT = 20037508.342789244
OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
OSM_TILE_USER_AGENT = "UTADIndoorNavigationPrototype/0.2 (academic project)"
EXTERIOR_TILE_ZOOM = 18


@dataclass
class RouteState:
    graph: nx.Graph
    path: list[str]
    distance: float
    current_index: int = 0


@dataclass(frozen=True)
class SelectableNode:
    node_id: str
    nodeid: str
    name: str
    building: str
    floor: str
    node_type: str
    label: str


def read_int_tag(tags: dict[str, str], keys: tuple[str, ...], default: int) -> int:
    for key in keys:
        value = tags.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except ValueError:
            return default
    return default


def parse_osm(filepath: Path):
    if not filepath.exists():
        raise FileNotFoundError(f"Não encontrei o ficheiro OSM: {filepath}")

    root = ET.parse(filepath).getroot()
    nodes = {}
    edges = []

    for node_elem in root.findall("node"):
        node_id = node_elem.get("id")
        tags = {}
        for tag in node_elem.findall("tag"):
            key = tag.get("k")
            value = tag.get("v")
            if key:
                tags[key.lower()] = value

        nodes[node_id] = {
            "lat": float(node_elem.get("lat")),
            "lon": float(node_elem.get("lon")),
            "tags": tags,
        }

    for way_elem in root.findall("way"):
        way_id = way_elem.get("id")
        refs = [nd.get("ref") for nd in way_elem.findall("nd")]
        way_tags = {}
        for tag in way_elem.findall("tag"):
            key = tag.get("k")
            value = tag.get("v")
            if key:
                way_tags[key.lower()] = value

        for index in range(len(refs) - 1):
            edges.append((refs[index], refs[index + 1], way_id, way_tags))

    return nodes, edges


def haversine(lat1, lon1, lat2, lon2):
    radius = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    value = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return radius * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def build_graph(nodes, edges):
    graph = nx.Graph()

    for node_id, data in nodes.items():
        graph.add_node(
            node_id,
            lat=data["lat"],
            lon=data["lon"],
            **data["tags"],
        )

    for node_a, node_b, way_id, way_tags in edges:
        if node_a not in nodes or node_b not in nodes:
            continue

        distance = haversine(
            nodes[node_a]["lat"],
            nodes[node_a]["lon"],
            nodes[node_b]["lat"],
            nodes[node_b]["lon"],
        )
        graph.add_edge(
            node_a,
            node_b,
            weight=distance,
            length=round(distance, 2),
            way_id=way_id,
            edge_type=way_tags.get("type", "connection"),
            accessibility=read_int_tag(
                way_tags,
                keys=("accessibility", "accessibilty"),
                default=1,
            ),
        )

    return graph


def floor_number(floor: str) -> int | None:
    digits = "".join(char for char in str(floor) if char.isdigit())
    return int(digits) if digits else None


def floor_node_id(floor: str, node_id: str) -> str:
    return f"{floor}:{node_id}"


def vertical_edge_weight(from_node: dict, to_node: dict, edge_type: str) -> float:
    first_floor = int(from_node.get("floor", 0) or 0)
    second_floor = int(to_node.get("floor", 0) or 0)
    floor_delta = max(1, abs(first_floor - second_floor))
    return 8.0 * floor_delta if edge_type == "elevator" else 12.0 * floor_delta


def build_campus_graph():
    campus_graph = nx.Graph()
    floor_graphs = {}

    for floor in APP_FLOORS:
        graph = build_graph(*parse_osm(OSM_FILES[floor]))
        floor_graphs[floor] = graph

        for node_id, data in graph.nodes(data=True):
            campus_graph.add_node(
                floor_node_id(floor, node_id),
                **data,
                original_id=node_id,
                floor_key=floor,
            )

        for node_a, node_b, data in graph.edges(data=True):
            campus_graph.add_edge(
                floor_node_id(floor, node_a),
                floor_node_id(floor, node_b),
                **data,
                vertical=False,
            )

    add_vertical_connections(campus_graph)
    add_exterior_transition_connections(campus_graph)
    return campus_graph, floor_graphs


def add_vertical_connections(campus_graph):
    nodes = list(campus_graph.nodes(data=True))
    elevator_nodes = [
        (node_id, data)
        for node_id, data in nodes
        if data.get("type") == "elevator"
    ]
    elevator_nodes.sort(key=lambda item: floor_number(item[1].get("floor_key", "")) or 0)
    for index, (node_id, data) in enumerate(elevator_nodes):
        for other_id, other_data in elevator_nodes[index + 1:]:
            if abs(
                (floor_number(data.get("floor_key", "")) or 0)
                - (floor_number(other_data.get("floor_key", "")) or 0)
            ) == 1:
                add_vertical_edge(campus_graph, node_id, data, other_id, other_data, "elevator")


def exterior_transition_edge_type(data_a: dict, data_b: dict):
    types = {data_a.get("type"), data_b.get("type")}
    if "elevator" in types:
        return "elevator"
    if "stairs" in types:
        return "stairs"
    if "rampa" in types or "ramp" in types:
        return "ramp"
    return "connection"


def add_exterior_transition_connections(campus_graph):
    transition_nodes = [
        (node_id, data)
        for node_id, data in campus_graph.nodes(data=True)
        if data.get("transition")
    ]
    add_matching_transition_group_edges(campus_graph, transition_nodes)


def add_matching_transition_group_edges(campus_graph, transition_nodes):
    groups = {}
    for node_id, data in transition_nodes:
        for label in transition_labels(data):
            groups.setdefault(label, []).append((node_id, data))

    for group_nodes in groups.values():
        if len(group_nodes) < 2:
            continue
        for index, (node_id, data) in enumerate(group_nodes):
            for other_id, other_data in group_nodes[index + 1:]:
                if campus_graph.has_edge(node_id, other_id):
                    continue
                edge_type = exterior_transition_edge_type(data, other_data)
                add_transition_edge(campus_graph, node_id, data, other_id, other_data, edge_type)


def transition_labels(data: dict):
    value = str(data.get("transition", "")).strip()
    if not value:
        return []
    return [label.strip().upper() for label in value.split(";") if label.strip()]


def add_vertical_edge(campus_graph, node_a, data_a, node_b, data_b, edge_type):
    accessibility = 3 if edge_type == "elevator" else 2
    weight = vertical_edge_weight(data_a, data_b, edge_type)
    campus_graph.add_edge(
        node_a,
        node_b,
        weight=weight,
        length=weight,
        way_id=f"{edge_type}:{node_a}->{node_b}",
        edge_type=edge_type,
        accessibility=accessibility,
        vertical=True,
    )


def add_transition_edge(campus_graph, node_a, data_a, node_b, data_b, edge_type):
    distance = math.dist(
        lonlat_to_web_mercator(data_a["lat"], data_a["lon"]),
        lonlat_to_web_mercator(data_b["lat"], data_b["lon"]),
    )
    accessibility = 3 if edge_type == "elevator" else 2 if edge_type == "stairs" else 4
    campus_graph.add_edge(
        node_a,
        node_b,
        weight=distance,
        length=round(distance, 2),
        way_id=f"transition:{node_a}->{node_b}",
        edge_type=edge_type,
        accessibility=accessibility,
        vertical=edge_type in {"stairs", "elevator"},
        transition=True,
    )


def calculate_path(graph, origin, destination, mobility_reduced=False):
    route_graph = nx.Graph()
    route_graph.add_nodes_from(graph.nodes(data=True))
    for node_a, node_b, data in graph.edges(data=True):
        edge_type = data.get("edge_type")
        if mobility_reduced and edge_type == "stairs":
            continue
        if not mobility_reduced and edge_type == "elevator":
            continue
        route_graph.add_edge(node_a, node_b, **data)

    try:
        path = nx.shortest_path(route_graph, origin, destination, weight="weight")
        distance = nx.shortest_path_length(route_graph, origin, destination, weight="weight")
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None, float("inf")

    return path, round(distance, 2)


def lonlat_to_web_mercator(lat: float, lon: float):
    earth_radius = 6378137
    x_value = earth_radius * math.radians(lon)
    y_value = earth_radius * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    return x_value, y_value


def web_mercator_tile_size(zoom: int):
    return (WEB_MERCATOR_LIMIT * 2) / (2 ** zoom)


def web_mercator_to_tile(x_value: float, y_value: float, zoom: int):
    tile_size = web_mercator_tile_size(zoom)
    tile_x = math.floor((x_value + WEB_MERCATOR_LIMIT) / tile_size)
    tile_y = math.floor((WEB_MERCATOR_LIMIT - y_value) / tile_size)
    max_tile = (2 ** zoom) - 1
    return (
        max(0, min(max_tile, tile_x)),
        max(0, min(max_tile, tile_y)),
    )


def tile_web_mercator_extent(tile_x: int, tile_y: int, zoom: int):
    tile_size = web_mercator_tile_size(zoom)
    min_x = -WEB_MERCATOR_LIMIT + tile_x * tile_size
    max_x = min_x + tile_size
    max_y = WEB_MERCATOR_LIMIT - tile_y * tile_size
    min_y = max_y - tile_size
    return min_x, max_x, min_y, max_y


def world_file_path(image_path: Path):
    candidates = [
        image_path.with_suffix(".jgw"),
        image_path.with_suffix(".pgw"),
        image_path.with_suffix(".jpgw"),
        image_path.with_suffix(".pngw"),
    ]
    return next((path for path in candidates if path.exists()), None)


def read_world_file(image_path: Path):
    path = world_file_path(image_path)
    if path is None:
        return None

    values = [
        float(line.strip())
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(values) != 6:
        return None

    return {
        "path": path,
        "a": values[0],
        "d": values[1],
        "b": values[2],
        "e": values[3],
        "c": values[4],
        "f": values[5],
    }


def world_file_corners(world_file: dict, image_size: tuple[int, int]):
    width, height = image_size

    def transform(pixel_x, pixel_y):
        return (
            world_file["a"] * pixel_x + world_file["b"] * pixel_y + world_file["c"],
            world_file["d"] * pixel_x + world_file["e"] * pixel_y + world_file["f"],
        )

    return [
        transform(0, 0),
        transform(width, 0),
        transform(width, height),
        transform(0, height),
    ]


def is_exterior_movement_point(name: str, floor: str):
    if floor != "Exterior":
        return False
    normalized = name.strip().lower()
    return normalized in {"calçada", "calcada", "passadeira"} or normalized.startswith("rampa")


def collect_selectable_nodes(graph) -> list[SelectableNode]:
    nodes = []
    for node_id, data in graph.nodes(data=True):
        name = data.get("roomname") or data.get("name")
        nodeid = str(data.get("nodeid", "")).strip()
        building = str(data.get("building", "")).strip()
        floor = data.get("floor_key", "")
        node_type = str(data.get("type", "")).lower()

        if not name or not building or building == "Exterior":
            continue
        if node_type == "connection":
            continue
        if is_exterior_movement_point(name, floor):
            continue

        prefix = f"{nodeid} - " if nodeid else ""
        nodes.append(
            SelectableNode(
                node_id=node_id,
                nodeid=nodeid,
                name=name,
                building=building,
                floor=floor,
                node_type=node_type,
                label=f"{prefix}{name}",
            )
        )

    return sorted(nodes, key=selectable_sort_key)


def selectable_sort_key(item: SelectableNode):
    try:
        node_number = int(item.nodeid)
    except (TypeError, ValueError):
        node_number = 999999
    return (building_sort_key(item.building), floor_sort_key(item.floor), node_number, item.name)


def building_sort_key(building: str):
    try:
        return (BUILDING_ORDER.index(building), building)
    except ValueError:
        return (len(BUILDING_ORDER), building)


def floor_sort_key(floor: str):
    try:
        return (APP_FLOORS.index(floor), floor)
    except ValueError:
        return (len(APP_FLOORS), floor)


def available_buildings(nodes: list[SelectableNode]):
    return sorted({node.building for node in nodes}, key=building_sort_key)


def available_floors(nodes: list[SelectableNode], building: str):
    return sorted(
        {node.floor for node in nodes if node.building == building},
        key=floor_sort_key,
    )


def nodes_for(nodes: list[SelectableNode], building: str, floor: str):
    return [
        node
        for node in nodes
        if node.building == building and node.floor == floor
    ]


def find_selectable_node(
    nodes: list[SelectableNode],
    selected_label: str,
    building: str,
    floor: str,
):
    for node in nodes:
        if node.label == selected_label and node.building == building and node.floor == floor:
            return node.node_id
    return None


def node_label(graph, node_id, show_nodeid=False):
    data = graph.nodes[node_id]
    name = data.get("roomname") or data.get("name") or node_id[-4:]
    nodeid = data.get("nodeid")
    if show_nodeid and nodeid:
        return f"{nodeid}: {name}"
    return name


def node_color(graph, node_id):
    node_type = graph.nodes[node_id].get("type", "unknown")
    colors = {
        "outdoor": "#4CAF50",
        "room": "#FF9800",
        "space": "#2196F3",
        "connection": "#9E9E9E",
        "elevator": "#9C27B0",
        "stairs": "#F44336",
        "transition": "#FF5722",
    }
    return colors.get(node_type, "#BDBDBD")


def navigation_point_name(graph, node_id):
    data = graph.nodes[node_id]
    name = data.get("roomname") or data.get("name") or "ponto intermédio"
    if data.get("type") == "connection":
        name = "Ponto de passagem"
    floor = data.get("floor_key", "")
    building = data.get("building", "")
    location = " · ".join(part for part in [building, floor] if part)
    return f"{name} ({location})" if location else name


def edge_name(edge):
    edge_type = edge.get("edge_type", "")
    if edge_type == "elevator":
        return "o elevador"
    if edge_type == "stairs":
        return "as escadas"
    return "a ligação vertical"


def elevator_exit_index(graph, route: RouteState, start_index):
    index = start_index
    while index < len(route.path) - 1:
        edge = graph[route.path[index]][route.path[index + 1]]
        if not edge.get("vertical") or edge.get("edge_type") != "elevator":
            break
        index += 1
    return index


def next_navigation_index(graph, route: RouteState):
    index = route.current_index
    if index >= len(route.path) - 1:
        return index

    edge = graph[route.path[index]][route.path[index + 1]]
    if edge.get("vertical") and edge.get("edge_type") == "elevator":
        return elevator_exit_index(graph, route, index)

    return index + 1


def navigation_instruction(graph, route: RouteState):
    path = route.path
    index = route.current_index

    if index >= len(path) - 1:
        return f"Destino alcançado\n\n{navigation_point_name(graph, path[-1])}"

    current = path[index]
    next_node = path[index + 1]
    edge = graph[current][next_node]
    progress = f"Passo {index + 1} de {len(path) - 1}"
    current_text = navigation_point_name(graph, current)

    if edge.get("vertical"):
        if edge.get("edge_type") == "elevator":
            exit_node = path[elevator_exit_index(graph, route, index)]
            next_text = navigation_point_name(graph, exit_node)
            action = "usa o elevador"
        else:
            next_text = navigation_point_name(graph, next_node)
            action = f"usa {edge_name(edge)}"
        return (
            f"{progress}\n\n"
            f"Local atual: {current_text}\n"
            f"Próximo ponto: {next_text}\n\n"
            f"Ação: {action}.\n"
            "Depois confirma a chegada."
        )

    next_text = navigation_point_name(graph, next_node)
    distance = edge.get("length", edge.get("weight", 0))
    return (
        f"{progress}\n\n"
        f"Local atual: {current_text}\n"
        f"Próximo ponto: {next_text}\n\n"
        f"Ação: avança {distance:.1f} m.\n"
        "Depois confirma a chegada."
    )
