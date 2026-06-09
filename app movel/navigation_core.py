# -*- coding: utf-8 -*-
"""Lógica comum de navegação para a app desktop e para o protótipo Android."""

from __future__ import annotations

import math
import heapq
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


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


def configure_paths(base_dir: Path | str):
    """
    Atualiza os caminhos usados pelo core sem duplicar a lógica.

    A app móvel usa por defeito a pasta `app movel`. A app desktop importa este
    mesmo core através do wrapper da raiz e aponta-o para a pasta principal do
    repositório, onde também existem `OSM Pisos` e `Imagens ECT2`.
    """

    global BASE_DIR, OSM_DIR, IMAGE_DIR, OSM_FILES, FLOOR_IMAGES

    BASE_DIR = Path(base_dir).resolve()
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
    return BASE_DIR


@dataclass
class RouteState:
    graph: object
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


class NodeAccessor:
    """Permite usar `graph.nodes[...]` e `graph.nodes(data=True)` sem NetworkX."""

    def __init__(self, graph):
        self.graph = graph

    def __call__(self, data=False):
        return self.graph._nodes.items() if data else self.graph._nodes.keys()

    def __getitem__(self, node_id):
        return self.graph._nodes[node_id]

    def __contains__(self, node_id):
        return node_id in self.graph._nodes

    def __iter__(self):
        return iter(self.graph._nodes)


class EdgeAccessor:
    """Permite usar `graph.edges(data=True)` como no NetworkX."""

    def __init__(self, graph):
        self.graph = graph

    def __call__(self, data=False):
        seen = set()
        for node_a, neighbors in self.graph._adj.items():
            for node_b, edge_data in neighbors.items():
                key = tuple(sorted((node_a, node_b)))
                if key in seen:
                    continue
                seen.add(key)
                yield (node_a, node_b, edge_data) if data else (node_a, node_b)


class SimpleGraph:
    """
    Grafo não dirigido mínimo usado pelo core comum.

    O protótipo começou por usar NetworkX no desktop, mas no APK essa biblioteca
    trouxe uma dependência nativa (`_bz2`) indisponível. Esta classe implementa
    só as operações necessárias: adicionar nós/arestas, iterar nós/arestas,
    consultar vizinhos e aceder a `graph[a][b]`.
    """

    def __init__(self):
        self._nodes = {}
        self._adj = {}
        self.nodes = NodeAccessor(self)
        self.edges = EdgeAccessor(self)

    def add_node(self, node_id, **data):
        self._nodes.setdefault(node_id, {}).update(data)
        self._adj.setdefault(node_id, {})

    def add_edge(self, node_a, node_b, **data):
        self._adj.setdefault(node_a, {})
        self._adj.setdefault(node_b, {})
        self._adj[node_a][node_b] = data
        self._adj[node_b][node_a] = data

    def has_edge(self, node_a, node_b):
        return node_b in self._adj.get(node_a, {})

    def number_of_nodes(self):
        return len(self._nodes)

    def number_of_edges(self):
        return sum(len(neighbors) for neighbors in self._adj.values()) // 2

    def neighbors(self, node_id):
        return self._adj.get(node_id, {}).keys()

    def __getitem__(self, node_id):
        return self._adj[node_id]


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
    """Calcula a distância aproximada em metros entre duas coordenadas GPS."""

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
    """
    Constrói o grafo de um ficheiro OSM.

    Cada nó OSM vira um nó do grafo. Cada ligação entre dois nós consecutivos
    de um way vira uma aresta com `weight` igual à distância em metros. Esse
    `weight` é o valor que o Dijkstra soma para escolher a rota mais curta.
    """

    graph = SimpleGraph()

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

        # Haversine usa latitude/longitude para estimar a distância real entre
        # os dois pontos. Em corredores e caminhos curtos é suficiente para
        # comparar alternativas e apresentar metros ao utilizador.
        distance = haversine(
            nodes[node_a]["lat"],
            nodes[node_a]["lon"],
            nodes[node_b]["lat"],
            nodes[node_b]["lon"],
        )
        graph.add_edge(
            node_a,
            node_b,
            # `weight` é o custo usado pelo algoritmo de caminho mais curto.
            weight=distance,
            # `length` é o mesmo valor arredondado para as instruções visuais.
            length=round(distance, 2),
            way_id=way_id,
            edge_type=way_tags.get("edge_type", "connection"),
            accessibility=read_int_tag(
                way_tags,
                keys=("accessibility",),
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
    """
    Define o custo artificial para mudar de piso.

    A distância horizontal entre dois nós de pisos diferentes não representa bem
    o esforço de subir/descer. Por isso, escadas e elevador recebem um custo por
    piso que entra no mesmo campo `weight` usado pelo Dijkstra:
    - elevador: 8 unidades por piso;
    - escadas: 12 unidades por piso.
    """

    first_floor = int(from_node.get("floor", 0) or 0)
    second_floor = int(to_node.get("floor", 0) or 0)
    floor_delta = max(1, abs(first_floor - second_floor))
    return 8.0 * floor_delta if edge_type == "elevator" else 12.0 * floor_delta


def build_campus_graph():
    """
    Junta exterior e pisos interiores num único grafo navegável.

    Os IDs originais dos nós podem repetir-se entre ficheiros OSM. Por isso cada
    nó recebe um prefixo com o piso, por exemplo `Piso1:12`, antes de entrar no
    grafo global.
    """

    campus_graph = SimpleGraph()
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
    # Nas ligações verticais, `weight` e `length` usam o mesmo custo artificial.
    # Isto permite ao Dijkstra comparar escadas/elevador com o resto do caminho.
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
    # As transições entre exterior e interior são medidas em Web Mercator para
    # ficarem no mesmo sistema de coordenadas usado no desenho dos mapas.
    distance = math.dist(
        lonlat_to_web_mercator(data_a["lat"], data_a["lon"]),
        lonlat_to_web_mercator(data_b["lat"], data_b["lon"]),
    )
    accessibility = 3 if edge_type == "elevator" else 2 if edge_type == "stairs" else 4
    # `weight` guarda a distância/custo completo usado pelo Dijkstra.
    # `length` é arredondado para aparecer de forma limpa nas instruções.
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
    """
    Calcula a rota entre origem e destino com Dijkstra.

    O algoritmo procura o caminho cuja soma dos `weight` das arestas é menor.
    Esses pesos vêm das distâncias Haversine nos corredores/caminhos, das
    distâncias Web Mercator nas transições, e dos custos artificiais nas
    escadas/elevador.
    """

    if origin not in graph.nodes or destination not in graph.nodes:
        return None, float("inf")

    distances = {node_id: float("inf") for node_id in graph.nodes}
    previous = {node_id: None for node_id in graph.nodes}
    distances[origin] = 0.0
    queue = [(0.0, origin)]
    visited = set()

    while queue:
        current_distance, current_node = heapq.heappop(queue)
        if current_node in visited:
            continue
        visited.add(current_node)

        if current_node == destination:
            break

        for neighbor in graph.neighbors(current_node):
            if neighbor in visited:
                continue

            edge_data = graph[current_node][neighbor]
            edge_type = edge_data.get("edge_type")
            if mobility_reduced and edge_type == "stairs":
                continue
            if not mobility_reduced and edge_type == "elevator":
                continue

            new_distance = current_distance + edge_data.get("weight", 1)
            if new_distance < distances[neighbor]:
                distances[neighbor] = new_distance
                previous[neighbor] = current_node
                heapq.heappush(queue, (new_distance, neighbor))

    if distances[destination] == float("inf"):
        return None, float("inf")

    path = []
    node = destination
    while node is not None:
        path.append(node)
        node = previous[node]
    path.reverse()

    return path, round(distances[destination], 2)


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


def world_file_corners(world_file: dict, image_size: tuple[int, ...]):
    if len(image_size) >= 3:
        # matplotlib devolve image.shape como (altura, largura, canais).
        height, width = image_size[:2]
    else:
        # Kivy/CoreImage devolve image.size como (largura, altura).
        width, height = image_size[:2]

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


def turn_instruction(graph, path: list[str], index: int):
    """
    Calcula a orientação relativa no ponto atual da rota.

    A instrução só faz sentido quando existe um ponto anterior no mesmo piso:
    o utilizador veio de `path[index - 1]`, está em `path[index]` e vai seguir
    para `path[index + 1]`. Ângulos pequenos são tratados como seguir em frente
    para evitar instruções ruidosas em pequenos desvios do grafo.
    """

    if index <= 0 or index >= len(path) - 1:
        return None

    previous = path[index - 1]
    current = path[index]
    next_node = path[index + 1]
    previous_data = graph.nodes[previous]
    current_data = graph.nodes[current]
    next_data = graph.nodes[next_node]

    if previous_data.get("floor_key") != current_data.get("floor_key"):
        return None
    if current_data.get("floor_key") != next_data.get("floor_key"):
        return None

    previous_point = lonlat_to_web_mercator(previous_data["lat"], previous_data["lon"])
    current_point = lonlat_to_web_mercator(current_data["lat"], current_data["lon"])
    next_point = lonlat_to_web_mercator(next_data["lat"], next_data["lon"])
    incoming = (
        current_point[0] - previous_point[0],
        current_point[1] - previous_point[1],
    )
    outgoing = (
        next_point[0] - current_point[0],
        next_point[1] - current_point[1],
    )

    incoming_length = math.hypot(*incoming)
    outgoing_length = math.hypot(*outgoing)
    if incoming_length == 0 or outgoing_length == 0:
        return None

    cross = incoming[0] * outgoing[1] - incoming[1] * outgoing[0]
    dot = incoming[0] * outgoing[0] + incoming[1] * outgoing[1]
    angle = math.degrees(math.atan2(cross, dot))
    absolute_angle = abs(angle)

    if absolute_angle <= 25:
        return "continua em frente"
    if absolute_angle >= 150:
        return "inverte a direção"

    side = "esquerda" if angle > 0 else "direita"
    if absolute_angle < 60:
        return f"segue ligeiramente à {side}"
    return f"vira à {side}"


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
    turn_text = turn_instruction(graph, path, index)
    movement = f"{turn_text} e avança {distance:.1f} m" if turn_text else f"avança {distance:.1f} m"
    return (
        f"{progress}\n\n"
        f"Local atual: {current_text}\n"
        f"Próximo ponto: {next_text}\n\n"
        f"Ação: {movement}.\n"
        "Depois confirma a chegada."
    )
