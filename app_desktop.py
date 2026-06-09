# -*- coding: utf-8 -*-
"""
Protótipo desktop para navegação pedestre indoor na UTAD.

Esta interface reutiliza a lógica existente em navegacao_campus_vscode.py e
mantém os ficheiros .osm sem alterações.
"""

from __future__ import annotations

import math
import tkinter as tk
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk

import networkx as nx
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib import image as mpimg
from matplotlib.transforms import Affine2D

from navegacao_campus_vscode import (
    BASE_DIR,
    OSM_FILES,
    build_graph,
    get_node_color,
    get_node_label,
    parse_osm,
    resolve_node,
)


# Caminhos e constantes usados pela app desktop. A pasta BASE_DIR vem do script
# principal e permite correr a app a partir de qualquer terminal sem caminhos
# absolutos dependentes do computador.
IMAGE_DIR = BASE_DIR / "Imagens ECT2"
FLOOR_IMAGES = {
    "Piso1": IMAGE_DIR / "Piso 1.jpg",
    "Piso2": IMAGE_DIR / "Piso2.png",
    "Piso3": IMAGE_DIR / "Piso 3.jpg",
}
INDOOR_FLOORS = ["Piso1", "Piso2", "Piso3"]
APP_FLOORS = ["Exterior", *INDOOR_FLOORS]
BUILDING_ORDER = ["ECT1", "ECT2", "ECHS2"]
TILE_CACHE_DIR = BASE_DIR / ".tile_cache" / "osm_carto"
OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
OSM_TILE_USER_AGENT = "UTADIndoorNavigationPrototype/0.1 (academic project)"
EXTERIOR_TILE_ZOOM = 18
WEB_MERCATOR_LIMIT = 20037508.342789244


@dataclass
class RouteState:
    """Guarda a rota atual e o ponto onde o utilizador está na navegação."""

    graph: object
    path: list[str]
    distance: float
    current_index: int = 0


def floor_number(floor: str) -> int | None:
    """Extrai o número de um piso como 'Piso3'. Devolve None para 'Exterior'."""

    digits = "".join(char for char in floor if char.isdigit())
    return int(digits) if digits else None


def floor_node_id(floor: str, node_id: str) -> str:
    """Cria um ID único no grafo global juntando piso e ID original OSM."""

    return f"{floor}:{node_id}"


def vertical_edge_weight(from_node: dict, to_node: dict, edge_type: str) -> float:
    """
    Define o custo artificial de mudar de piso por elevador ou escadas.

    Nos corredores normais, o peso da aresta vem da distância real entre dois
    nós. Nas mudanças de piso não existe uma distância horizontal útil para
    representar o esforço, por isso usamos um custo por piso:
    - elevador: 8 unidades por piso, porque é mais cómodo/acessível;
    - escadas: 12 unidades por piso, porque exige mais esforço.

    Este valor entra no mesmo campo `weight` usado pelo Dijkstra/NetworkX.
    """

    first_floor = int(from_node.get("floor", 0) or 0)
    second_floor = int(to_node.get("floor", 0) or 0)
    floor_delta = max(1, abs(first_floor - second_floor))
    return 8.0 * floor_delta if edge_type == "elevator" else 12.0 * floor_delta


def build_campus_graph():
    """
    Junta todos os ficheiros OSM num único grafo navegável.

    Cada piso/exterior é lido como grafo separado, depois os nós são copiados
    para um grafo global com prefixo do piso. No fim são acrescentadas ligações
    verticais e transições identificadas pelas labels dos nós OSM.
    """

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
    """Liga nós de elevador entre pisos adjacentes."""

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
    """Escolhe o tipo de aresta de transição com base nos tipos dos nós ligados."""

    types = {data_a.get("type"), data_b.get("type")}
    if "elevator" in types:
        return "elevator"
    if "stairs" in types:
        return "stairs"
    if "rampa" in types or "ramp" in types:
        return "ramp"
    return "connection"


def add_exterior_transition_connections(campus_graph):
    """Procura nós com tag 'transition' e liga os que pertencem ao mesmo grupo."""

    transition_nodes = [
        (node_id, data)
        for node_id, data in campus_graph.nodes(data=True)
        if data.get("transition")
    ]

    add_matching_transition_group_edges(campus_graph, transition_nodes)


def add_matching_transition_group_edges(campus_graph, transition_nodes):
    """Agrupa transições pela mesma label e cria arestas entre todos os pares."""

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
    """Lê uma ou mais labels de transição separadas por ponto e vírgula."""

    value = str(data.get("transition", "")).strip()
    if not value:
        return []
    return [
        label.strip().upper()
        for label in value.split(";")
        if label.strip()
    ]


def add_vertical_edge(campus_graph, node_a, data_a, node_b, data_b, edge_type):
    """Acrescenta uma aresta vertical, usada sobretudo para o elevador."""

    accessibility = 3 if edge_type == "elevator" else 2
    weight = vertical_edge_weight(data_a, data_b, edge_type)
    # `weight` é o custo usado no cálculo da rota. `length` é o valor mostrado
    # ao utilizador quando for necessário apresentar uma distância/ação.
    # Aqui os dois ficam iguais porque a ligação vertical usa custo artificial.
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
    """Acrescenta uma ligação entre mapas/pisos baseada em tags de transição."""

    # Para ligar exterior/interior, primeiro convertemos latitude/longitude para
    # Web Mercator. Assim `math.dist` calcula uma distância aproximada em metros
    # no mesmo sistema de coordenadas usado para desenhar os mapas.
    distance = math.dist(
        lonlat_to_web_mercator(data_a["lat"], data_a["lon"]),
        lonlat_to_web_mercator(data_b["lat"], data_b["lon"]),
    )
    accessibility = 3 if edge_type == "elevator" else 2 if edge_type == "stairs" else 4
    # `weight` fica com a distância completa para o algoritmo; `length` é
    # arredondado só para ser apresentado de forma mais limpa na interface.
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
    Calcula o caminho mais curto respeitando o perfil do utilizador.

    - Mobilidade reduzida: remove arestas de escadas.
    - Perfil normal: remove arestas de elevador.

    O algoritmo usado aqui é o Dijkstra através do NetworkX. Quando chamamos
    `nx.shortest_path(..., weight="weight")`, o NetworkX procura a sequência de
    nós cuja soma dos pesos das arestas é a menor possível. Na prática:
    - cada corredor tem `weight` igual aos metros entre os nós OSM;
    - cada transição exterior/interior tem `weight` aproximado em metros;
    - escadas/elevador têm `weight` artificial para representar esforço/tempo.
    """

    # Primeiro criamos uma cópia filtrada do grafo. É mais simples remover as
    # arestas proibidas pelo perfil do utilizador e depois correr Dijkstra sobre
    # o grafo resultante.
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
        # Dijkstra devolve a lista de nós da rota. Exemplo:
        # Exterior:1 -> Piso1:3 -> Piso1:4 -> Piso2:...
        path = nx.shortest_path(route_graph, origin, destination, weight="weight")
        # Aqui calculamos a soma dos mesmos `weight` usados para escolher a rota.
        # É este valor que aparece como distância total na app.
        distance = nx.shortest_path_length(route_graph, origin, destination, weight="weight")
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None, float("inf")

    return path, round(distance, 2)


def read_piclayer_calibration(image_path: Path):
    """Lê ficheiros .cal do plugin PicLayer/JOSM, se existirem."""

    calibration_path = Path(str(image_path) + ".cal")
    if not calibration_path.exists():
        return None

    calibration = {}
    for line in calibration_path.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        try:
            calibration[key] = float(value)
        except ValueError:
            continue

    required_keys = {"M00", "M01", "M10", "M11", "POSITION_X", "POSITION_Y"}
    if not required_keys.issubset(calibration):
        return None

    return calibration


def world_file_path(image_path: Path):
    """Procura o world file correspondente à imagem do piso."""

    candidates = [
        image_path.with_suffix(".jgw"),
        image_path.with_suffix(".pgw"),
        image_path.with_suffix(".jpgw"),
        image_path.with_suffix(".pngw"),
    ]
    return next((path for path in candidates if path.exists()), None)


def read_world_file(image_path: Path):
    """Lê os seis valores de um world file (.jgw/.pgw/etc.)."""

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


def lonlat_to_web_mercator(lat: float, lon: float):
    """Converte latitude/longitude para Web Mercator, usado por OSM tiles."""

    earth_radius = 6378137
    x_value = earth_radius * math.radians(lon)
    y_value = earth_radius * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    return x_value, y_value


def web_mercator_tile_size(zoom: int):
    """Calcula o tamanho, em metros Web Mercator, de um tile para o zoom dado."""

    return (WEB_MERCATOR_LIMIT * 2) / (2 ** zoom)


def web_mercator_to_tile(x_value: float, y_value: float, zoom: int):
    """Converte coordenadas Web Mercator para índice de tile OSM."""

    tile_size = web_mercator_tile_size(zoom)
    tile_x = math.floor((x_value + WEB_MERCATOR_LIMIT) / tile_size)
    tile_y = math.floor((WEB_MERCATOR_LIMIT - y_value) / tile_size)
    max_tile = (2 ** zoom) - 1
    return (
        max(0, min(max_tile, tile_x)),
        max(0, min(max_tile, tile_y)),
    )


def tile_web_mercator_extent(tile_x: int, tile_y: int, zoom: int):
    """Devolve a caixa Web Mercator ocupada por um tile OSM."""

    tile_size = web_mercator_tile_size(zoom)
    min_x = -WEB_MERCATOR_LIMIT + tile_x * tile_size
    max_x = min_x + tile_size
    max_y = WEB_MERCATOR_LIMIT - tile_y * tile_size
    min_y = max_y - tile_size
    return min_x, max_x, min_y, max_y


def cached_osm_tile(tile_x: int, tile_y: int, zoom: int):
    """Obtém um tile OSM do cache local ou descarrega-o se ainda não existir."""

    tile_path = TILE_CACHE_DIR / str(zoom) / str(tile_x) / f"{tile_y}.png"
    if tile_path.exists():
        return tile_path

    tile_path.parent.mkdir(parents=True, exist_ok=True)
    url = OSM_TILE_URL.format(z=zoom, x=tile_x, y=tile_y)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": OSM_TILE_USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            tile_path.write_bytes(response.read())
    except (urllib.error.URLError, TimeoutError, OSError):
        return None

    return tile_path


def image_pixel_to_world_from_world_file(world_file: dict):
    """Cria uma transformação Matplotlib a partir dos valores do world file."""

    return Affine2D().from_values(
        world_file["a"],
        world_file["d"],
        world_file["b"],
        world_file["e"],
        world_file["c"],
        world_file["f"],
    )


def image_pixel_to_world_transform(calibration: dict, image_shape):
    """Cria a transformação da imagem usando a calibração PicLayer."""

    height, width = image_shape[:2]
    scale = (calibration.get("INITIAL_SCALE", 100.0) or 100.0) / 100.0
    m00 = calibration["M00"] * scale
    m01 = calibration["M01"] * scale
    m02 = calibration.get("M02", 0.0) * scale
    m10 = calibration["M10"] * scale
    m11 = calibration["M11"] * scale
    m12 = calibration.get("M12", 0.0) * scale
    offset_x = (
        calibration["POSITION_X"]
        + m02
        - m00 * width / 2
        - m01 * height / 2
    )
    offset_y = (
        calibration["POSITION_Y"]
        + m12
        - m10 * width / 2
        - m11 * height / 2
    )
    return Affine2D().from_values(
        m00,
        m10,
        m01,
        m11,
        offset_x,
        offset_y,
    )


def pixel_to_world(calibration: dict, image_shape, pixel_x: float, pixel_y: float):
    """Converte um ponto da imagem em coordenadas do mundo pela calibração .cal."""

    height, width = image_shape[:2]
    scale = (calibration.get("INITIAL_SCALE", 100.0) or 100.0) / 100.0
    m00 = calibration["M00"] * scale
    m01 = calibration["M01"] * scale
    m02 = calibration.get("M02", 0.0) * scale
    m10 = calibration["M10"] * scale
    m11 = calibration["M11"] * scale
    m12 = calibration.get("M12", 0.0) * scale
    relative_x = pixel_x - width / 2
    relative_y = pixel_y - height / 2
    world_x = (
        calibration["POSITION_X"]
        + m00 * relative_x
        + m01 * relative_y
        + m02
    )
    world_y = (
        calibration["POSITION_Y"]
        + m10 * relative_x
        + m11 * relative_y
        + m12
    )
    return world_x, world_y


def image_world_corners(calibration: dict, image_shape):
    """Calcula os quatro cantos da imagem já transformados para o mundo."""

    height, width = image_shape[:2]
    return [
        pixel_to_world(calibration, image_shape, 0, 0),
        pixel_to_world(calibration, image_shape, width, 0),
        pixel_to_world(calibration, image_shape, width, height),
        pixel_to_world(calibration, image_shape, 0, height),
    ]


def world_file_corners(world_file: dict, image_shape):
    """Calcula os quatro cantos da imagem usando um world file."""

    height, width = image_shape[:2]

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


class DesktopNavigationApp(tk.Tk):
    """Janela principal Tkinter da aplicação desktop."""

    def __init__(self):
        """Inicializa estado da aplicação, variáveis Tkinter e layout principal."""

        super().__init__()
        self.title("Navegação Pedestre Indoor - UTAD")
        self.geometry("1320x820")
        self.minsize(1100, 680)

        self.graph = None
        self.floor_graphs = {}
        self.named_nodes: list[dict[str, str]] = []
        self.route: RouteState | None = None

        # Variáveis Tkinter que ficam ligadas aos controlos da interface.
        # Quando o utilizador escolhe valores nas comboboxes, estas variáveis
        # são atualizadas automaticamente.
        self.profile_var = tk.StringVar(value="normal")
        self.origin_building_var = tk.StringVar(value="ECT2")
        self.destination_building_var = tk.StringVar(value="ECT2")
        self.origin_floor_var = tk.StringVar(value="Piso1")
        self.destination_floor_var = tk.StringVar(value="Piso1")
        self.visible_floor_var = tk.StringVar(value="Piso1")
        self.origin_var = tk.StringVar()
        self.destination_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Escolhe o edifício, a origem e o destino.")
        self.step_var = tk.StringVar(value="Ainda não há rota calculada.")
        self.show_labels_var = tk.BooleanVar(value=False)
        self.zoom_var = tk.StringVar(value="100%")
        self.map_zoom = 1.0
        self.map_center = None
        self.map_center_floor = None
        self.map_axis = None
        self.map_drag_start = None

        self._build_layout()
        self.load_campus()

    def _build_layout(self):
        """Constrói a barra lateral de controlos e a área principal do mapa."""

        # A janela tem duas zonas: coluna 0 com controlos e coluna 1 com o mapa.
        # A coluna do mapa recebe o peso para crescer quando a janela aumenta.
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(self, padding=14)
        sidebar.grid(row=0, column=0, sticky="ns")

        # Perfil influencia o cálculo da rota:
        # normal não usa elevador; mobilidade reduzida não usa escadas.
        ttk.Label(sidebar, text="Perfil").grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(
            sidebar,
            text="Normal",
            variable=self.profile_var,
            value="normal",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Radiobutton(
            sidebar,
            text="Mobilidade reduzida",
            variable=self.profile_var,
            value="reduced",
        ).grid(row=2, column=0, sticky="w")

        ttk.Separator(sidebar).grid(row=3, column=0, sticky="ew", pady=14)

        # Origem e destino são escolhidos em três níveis para simplificar a UI:
        # edifício -> piso/área -> sala/entrada.
        ttk.Label(sidebar, text="Edifício de origem").grid(row=4, column=0, sticky="w")
        self.origin_building_combo = ttk.Combobox(
            sidebar,
            textvariable=self.origin_building_var,
            values=BUILDING_ORDER,
            state="readonly",
            width=32,
        )
        self.origin_building_combo.grid(row=5, column=0, sticky="ew", pady=(4, 10))
        self.origin_building_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_node_lists())

        ttk.Label(sidebar, text="Piso/área de origem").grid(row=6, column=0, sticky="w")
        self.origin_floor_combo = ttk.Combobox(
            sidebar,
            textvariable=self.origin_floor_var,
            values=APP_FLOORS,
            state="readonly",
            width=32,
        )
        self.origin_floor_combo.grid(row=7, column=0, sticky="ew", pady=(4, 10))
        self.origin_floor_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_node_lists())

        ttk.Label(sidebar, text="Origem").grid(row=8, column=0, sticky="w")
        self.origin_combo = ttk.Combobox(sidebar, textvariable=self.origin_var, width=32)
        self.origin_combo.grid(row=9, column=0, sticky="ew", pady=(4, 10))

        ttk.Label(sidebar, text="Edifício de destino").grid(row=10, column=0, sticky="w")
        self.destination_building_combo = ttk.Combobox(
            sidebar,
            textvariable=self.destination_building_var,
            values=BUILDING_ORDER,
            state="readonly",
            width=32,
        )
        self.destination_building_combo.grid(row=11, column=0, sticky="ew", pady=(4, 10))
        self.destination_building_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_node_lists())

        ttk.Label(sidebar, text="Piso/área de destino").grid(row=12, column=0, sticky="w")
        self.destination_floor_combo = ttk.Combobox(
            sidebar,
            textvariable=self.destination_floor_var,
            values=APP_FLOORS,
            state="readonly",
            width=32,
        )
        self.destination_floor_combo.grid(row=13, column=0, sticky="ew", pady=(4, 10))
        self.destination_floor_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_node_lists())

        ttk.Label(sidebar, text="Destino").grid(row=14, column=0, sticky="w")
        self.destination_combo = ttk.Combobox(
            sidebar,
            textvariable=self.destination_var,
            width=32,
        )
        self.destination_combo.grid(row=15, column=0, sticky="ew", pady=(4, 10))

        ttk.Button(
            sidebar,
            text="Calcular rota",
            command=self.calculate_route,
        ).grid(row=16, column=0, sticky="ew", pady=(4, 8))

        ttk.Button(
            sidebar,
            text="Confirmar chegada",
            command=self.confirm_next_step,
        ).grid(row=17, column=0, sticky="ew")

        ttk.Separator(sidebar).grid(row=18, column=0, sticky="ew", pady=14)

        # O piso visível serve apenas para inspeção do mapa. A rota continua a
        # existir mesmo quando se muda manualmente a vista.
        ttk.Label(sidebar, text="Piso visível").grid(row=19, column=0, sticky="w")
        self.visible_floor_combo = ttk.Combobox(
            sidebar,
            textvariable=self.visible_floor_var,
            values=APP_FLOORS,
            state="readonly",
            width=32,
        )
        self.visible_floor_combo.grid(row=20, column=0, sticky="ew", pady=(4, 12))
        self.visible_floor_combo.bind("<<ComboboxSelected>>", lambda _event: self.draw_map())

        ttk.Checkbutton(
            sidebar,
            text="Mostrar labels no grafo",
            variable=self.show_labels_var,
            command=self.draw_map,
        ).grid(row=21, column=0, sticky="w", pady=(0, 12))

        ttk.Label(sidebar, text="Navegação").grid(row=22, column=0, sticky="w")
        step_label = ttk.Label(
            sidebar,
            textvariable=self.step_var,
            wraplength=280,
            justify="left",
        )
        step_label.grid(row=23, column=0, sticky="ew", pady=(6, 12))

        ttk.Label(sidebar, text="Estado").grid(row=24, column=0, sticky="w")
        status_label = ttk.Label(
            sidebar,
            textvariable=self.status_var,
            wraplength=280,
            justify="left",
        )
        status_label.grid(row=25, column=0, sticky="ew", pady=(6, 0))

        # Matplotlib fica embutido no Tkinter através do FigureCanvasTkAgg.
        # Nesta área são desenhados fundo calibrado, grafo e rota.
        main_area = ttk.Frame(self, padding=(0, 10, 10, 10))
        main_area.grid(row=0, column=1, sticky="nsew")
        main_area.columnconfigure(0, weight=1)
        main_area.columnconfigure(1, weight=0)
        main_area.rowconfigure(0, weight=1)

        self.figure = Figure(figsize=(10, 7), dpi=100)
        self.figure.subplots_adjust(left=0.015, right=0.985, bottom=0.02, top=0.94)
        self.canvas = FigureCanvasTkAgg(self.figure, master=main_area)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")
        self.canvas.mpl_connect("button_press_event", self._on_map_press)
        self.canvas.mpl_connect("motion_notify_event", self._on_map_drag)
        self.canvas.mpl_connect("button_release_event", self._on_map_release)
        self.canvas.mpl_connect("scroll_event", self._on_map_scroll)

        zoom_controls = ttk.Frame(main_area, padding=(8, 0, 0, 0))
        zoom_controls.grid(row=0, column=1, sticky="ns")
        zoom_controls.rowconfigure(4, weight=1)
        ttk.Button(
            zoom_controls,
            text="+",
            width=8,
            command=self.zoom_in_map,
        ).grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(
            zoom_controls,
            text="-",
            width=8,
            command=self.zoom_out_map,
        ).grid(row=1, column=0, sticky="ew")
        ttk.Button(
            zoom_controls,
            text="Centrar",
            width=8,
            command=self.center_map_on_current_step,
        ).grid(row=2, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(
            zoom_controls,
            textvariable=self.zoom_var,
            anchor="center",
        ).grid(row=3, column=0, sticky="ew", pady=(8, 0))

    def load_campus(self):
        """Carrega mapas OSM, cria o grafo global e inicializa a interface."""

        try:
            self.graph, self.floor_graphs = build_campus_graph()
        except Exception as error:
            messagebox.showerror("Erro ao carregar mapas", str(error))
            return

        self.route = None
        self.refresh_node_lists()
        self.status_var.set(
            f"Campus carregado: {self.graph.number_of_nodes()} nós, "
            f"{self.graph.number_of_edges()} arestas, incluindo ligações entre pisos."
        )
        self.step_var.set("Escolhe a origem e o destino para começar.")
        self.draw_map()

    def refresh_node_lists(self):
        """Atualiza edifícios, pisos e pontos selecionáveis nas comboboxes."""

        if self.graph is None:
            return

        self.named_nodes = self._collect_named_nodes()
        self._refresh_building_values()
        self._refresh_floor_combo(
            self.origin_building_var,
            self.origin_floor_var,
            self.origin_floor_combo,
        )
        self._refresh_floor_combo(
            self.destination_building_var,
            self.destination_floor_var,
            self.destination_floor_combo,
        )
        # Após escolher edifício e piso, só mostramos pontos desse contexto.
        origin_values = [
            item["label"]
            for item in self.named_nodes
            if item["building"] == self.origin_building_var.get()
            and item["floor"] == self.origin_floor_var.get()
        ]
        destination_values = [
            item["label"]
            for item in self.named_nodes
            if item["building"] == self.destination_building_var.get()
            and item["floor"] == self.destination_floor_var.get()
        ]
        self.origin_combo["values"] = origin_values
        self.destination_combo["values"] = destination_values

        self.origin_var.set(origin_values[0] if origin_values else "")
        self.destination_var.set(destination_values[-1] if destination_values else "")

        if self.route is None:
            self.visible_floor_var.set(self.origin_floor_var.get())
        self.draw_map()

    def _refresh_building_values(self):
        """Preenche as listas de edifícios a partir dos pontos selecionáveis."""

        buildings = sorted(
            {
                item["building"]
                for item in self.named_nodes
                if item["building"] and item["building"] != "Exterior"
            },
            key=self._building_sort_key,
        )
        self.origin_building_combo["values"] = buildings
        self.destination_building_combo["values"] = buildings
        if buildings:
            if self.origin_building_var.get() not in buildings:
                self.origin_building_var.set(buildings[0])
            if self.destination_building_var.get() not in buildings:
                self.destination_building_var.set(buildings[0])

    def _refresh_floor_combo(self, building_var, floor_var, floor_combo):
        """Mostra apenas pisos/áreas existentes para o edifício selecionado."""

        floors = sorted(
            {
                item["floor"]
                for item in self.named_nodes
                if item["building"] == building_var.get()
            },
            key=self._floor_sort_key,
        )
        floor_combo["values"] = floors
        floor_combo["state"] = "readonly" if len(floors) > 1 else "disabled"
        if floors and floor_var.get() not in floors:
            floor_var.set(floors[0])

    def _building_sort_key(self, building):
        """Ordena edifícios conhecidos pela ordem definida em BUILDING_ORDER."""

        try:
            return (BUILDING_ORDER.index(building), building)
        except ValueError:
            return (len(BUILDING_ORDER), building)

    def _floor_sort_key(self, floor):
        """Ordena primeiro Exterior e depois os pisos interiores."""

        try:
            return (APP_FLOORS.index(floor), floor)
        except ValueError:
            return (len(APP_FLOORS), floor)

    def _collect_named_nodes(self):
        """Recolhe pontos que podem aparecer como origem/destino na interface."""

        nodes = []
        for node_id, data in self.graph.nodes(data=True):
            name = data.get("roomname") or data.get("name")
            nodeid = str(data.get("nodeid", "")).strip()
            building = str(data.get("building", "")).strip()
            floor = data.get("floor_key", "")
            node_type = str(data.get("type", "")).lower()
            if not name:
                continue
            if not building or building == "Exterior":
                continue
            if node_type == "connection":
                continue
            if self._is_exterior_movement_point(name, floor, node_type):
                continue

            # A label mantém o nodeID quando existe, para facilitar validação
            # cruzada com o JOSM/OSM durante testes e apresentação.
            prefix = f"{nodeid} - " if nodeid else ""
            nodes.append(
                {
                    "node_id": node_id,
                    "nodeid": nodeid,
                    "name": name,
                    "building": building,
                    "floor": floor,
                    "type": node_type,
                    "label": f"{prefix}{name}",
                }
            )

        def sort_key(item):
            try:
                return int(item["nodeid"])
            except (TypeError, ValueError):
                return 999999

        return sorted(nodes, key=sort_key)

    def _is_exterior_movement_point(self, name: str, floor: str, node_type: str):
        """Remove pontos exteriores usados apenas como nós intermédios do grafo."""

        if floor != "Exterior":
            return False
        normalized_name = name.strip().lower()
        return normalized_name in {"calçada", "calcada", "passadeira"} or normalized_name.startswith("rampa")

    def _selected_node(self, selected_label: str, building: str, floor: str):
        """Converte o texto da combobox para o ID interno usado pelo grafo."""

        for item in self.named_nodes:
            if (
                item["label"] == selected_label
                and item["building"] == building
                and item["floor"] == floor
            ):
                return item["node_id"]
        return resolve_node(self.graph, selected_label)

    def calculate_route(self):
        """Valida origem/destino, calcula a rota e mostra a primeira instrução."""

        if self.graph is None:
            messagebox.showwarning("Sem mapa", "Carrega primeiro os mapas.")
            return

        # As comboboxes mostram texto legível; aqui voltamos ao ID interno
        # usado pelo NetworkX para calcular caminhos.
        origin = self._selected_node(
            self.origin_var.get(),
            self.origin_building_var.get(),
            self.origin_floor_var.get(),
        )
        destination = self._selected_node(
            self.destination_var.get(),
            self.destination_building_var.get(),
            self.destination_floor_var.get(),
        )

        if origin is None or destination is None:
            messagebox.showwarning(
                "Origem ou destino inválido",
                "Não consegui encontrar a origem ou o destino no grafo.",
            )
            return

        path, distance = calculate_path(
            self.graph,
            origin,
            destination,
            mobility_reduced=self.profile_var.get() == "reduced",
        )

        if not path:
            messagebox.showwarning(
                "Rota indisponível",
                "Não foi possível encontrar uma rota para as opções escolhidas.",
            )
            return

        self.route = RouteState(
            graph=self.graph,
            path=path,
            distance=distance,
        )
        # Ao calcular, a vista muda automaticamente para onde a rota começa.
        self.visible_floor_var.set(self.graph.nodes[path[0]].get("floor_key", self.visible_floor_var.get()))
        self.center_map_on_current_step(redraw=False)
        self.status_var.set(
            f"Rota pronta: {distance:.1f} m."
        )
        self._update_step_text()
        self.draw_map()

    def confirm_next_step(self):
        """Avança a navegação quando o utilizador confirma que chegou ao ponto."""

        if self.route is None:
            messagebox.showinfo("Sem rota", "Calcula primeiro uma rota.")
            return

        if self.route.current_index >= len(self.route.path) - 1:
            self.step_var.set("Chegaste ao destino.")
            return

        self.route.current_index = self._next_navigation_index()
        current = self.route.path[self.route.current_index]
        # Se o próximo ponto estiver noutro piso, a vista acompanha a rota.
        self.visible_floor_var.set(self.graph.nodes[current].get("floor_key", self.visible_floor_var.get()))
        self.center_map_on_current_step(redraw=False)
        self._update_step_text()
        self.draw_map()

    def _next_navigation_index(self):
        """Escolhe o próximo índice da rota a apresentar ao utilizador."""

        if self.route is None:
            return 0

        path = self.route.path
        index = self.route.current_index
        if index >= len(path) - 1:
            return index

        edge = self.graph[path[index]][path[index + 1]]
        if edge.get("vertical") and edge.get("edge_type") == "elevator":
            # Um elevador pode atravessar vários pisos. Para o utilizador isso
            # deve ser uma única ação: entrar e sair no piso correto.
            while index < len(path) - 1:
                next_edge = self.graph[path[index]][path[index + 1]]
                if not next_edge.get("vertical") or next_edge.get("edge_type") != "elevator":
                    break
                index += 1
            return index

        return index + 1

    def _update_step_text(self):
        """Constrói o texto da instrução atual na barra lateral."""

        if self.route is None:
            return

        path = self.route.path
        index = self.route.current_index

        if index >= len(path) - 1:
            current = path[-1]
            self.step_var.set(
                f"Destino alcançado\n\n{self._navigation_point_name(current)}"
            )
            return

        current = path[index]
        next_node = path[index + 1]
        edge = self.graph[current][next_node]
        progress = f"Passo {index + 1} de {len(path) - 1}"
        current_text = self._navigation_point_name(current)
        if edge.get("vertical"):
            # Ligações verticais são explicadas por ação, não por metros.
            if edge.get("edge_type") == "elevator":
                exit_index = self._elevator_exit_index(index)
                exit_node = path[exit_index]
                next_text = self._navigation_point_name(exit_node)
                self.step_var.set(
                    f"{progress}\n\n"
                    f"Local atual: {current_text}\n"
                    f"Próximo ponto: {next_text}\n\n"
                    "Ação: usa o elevador.\n"
                    "Depois confirma a chegada."
                )
            else:
                next_text = self._navigation_point_name(next_node)
                self.step_var.set(
                    f"{progress}\n\n"
                    f"Local atual: {current_text}\n"
                    f"Próximo ponto: {next_text}\n\n"
                    f"Ação: usa {self._edge_name(edge)}.\n"
                    "Depois confirma a chegada."
                )
        else:
            next_text = self._navigation_point_name(next_node)
            # Nas arestas normais, length vem do cálculo de distância entre nós.
            self.step_var.set(
                f"{progress}\n\n"
                f"Local atual: {current_text}\n"
                f"Próximo ponto: {next_text}\n\n"
                f"Ação: avança {edge.get('length', edge.get('weight', 0)):.1f} m.\n"
                "Depois confirma a chegada."
            )

    def _elevator_exit_index(self, start_index):
        """Encontra o nó onde termina uma sequência contínua de elevador."""

        if self.route is None:
            return start_index

        path = self.route.path
        index = start_index
        while index < len(path) - 1:
            edge = self.graph[path[index]][path[index + 1]]
            if not edge.get("vertical") or edge.get("edge_type") != "elevator":
                break
            index += 1
        return index

    def _node_display_name(self, node_id):
        """Formata um nó com nodeID e piso para mensagens técnicas/debug."""

        data = self.graph.nodes[node_id]
        name = data.get("roomname") or data.get("name") or "ponto intermédio"
        nodeid = data.get("nodeid")
        floor = data.get("floor_key", "")
        base = f"nodeID {nodeid} ({name})" if nodeid else name
        return f"{base} - {floor}" if floor else base

    def _navigation_point_name(self, node_id):
        """Formata um nó em linguagem simples para as instruções da rota."""

        data = self.graph.nodes[node_id]
        name = data.get("roomname") or data.get("name") or "ponto intermédio"
        if data.get("type") == "connection":
            name = "Ponto de passagem"
        floor = data.get("floor_key", "")
        building = data.get("building", "")
        location = " · ".join(part for part in [building, floor] if part)
        return f"{name} ({location})" if location else name

    def _edge_name(self, edge):
        """Traduz o tipo de ligação vertical para texto em português."""

        edge_type = edge.get("edge_type", "")
        if edge_type == "elevator":
            return "o elevador"
        if edge_type == "stairs":
            return "as escadas"
        return "a ligação vertical"

    def draw_map(self):
        """Redesenha mapa visível, grafo, rota e marcadores de navegação."""

        self.figure.clear()
        route_path = self.route.path if self.route else None
        current_index = self.route.current_index if self.route else None
        visible_floor = self.visible_floor_var.get()
        image_path = FLOOR_IMAGES.get(visible_floor)
        axis = self.figure.add_subplot(1, 1, 1)
        self.map_axis = axis

        if visible_floor == "Exterior":
            self._draw_exterior_tiles(axis, visible_floor)
        elif image_path and image_path.exists():
            self._draw_calibrated_image(axis, image_path)

        # O grafo é desenhado depois do fundo para ficar por cima da imagem/mapa.
        self._draw_graph_axis(axis, route_path, current_index, visible_floor, image_path)
        self.figure.subplots_adjust(left=0.015, right=0.985, bottom=0.02, top=0.94)
        self.canvas.draw()

    def zoom_in_map(self):
        """Aproxima a vista atual em passos de 10%."""

        self._change_map_zoom(1.1)

    def zoom_out_map(self):
        """Afasta a vista atual em passos de 10%."""

        self._change_map_zoom(1 / 1.1)

    def center_map_on_current_step(self, redraw=True):
        """Centra a vista no ponto atual da rota, mantendo o zoom escolhido."""

        if self.route is None or self.graph is None or not self.route.path:
            self.map_center = None
            self.map_center_floor = None
            if redraw:
                self.draw_map()
            return

        current = self.route.path[min(self.route.current_index, len(self.route.path) - 1)]
        data = self.graph.nodes[current]
        self.map_center = lonlat_to_web_mercator(data["lat"], data["lon"])
        self.map_center_floor = data.get("floor_key")
        if redraw:
            self.visible_floor_var.set(self.map_center_floor or self.visible_floor_var.get())
            self.draw_map()

    def _change_map_zoom(self, multiplier, center=None):
        """Atualiza o zoom e usa o centro atual, o cursor ou o ponto da rota."""

        visible_floor = self.visible_floor_var.get()
        if center is not None:
            self.map_center = center
            self.map_center_floor = visible_floor
        elif self.map_center is None or self.map_center_floor != visible_floor:
            self.center_map_on_current_step(redraw=False)

        self.map_zoom = max(0.5, min(8.0, self.map_zoom * multiplier))
        self.zoom_var.set(f"{self.map_zoom * 100:.0f}%")
        self.draw_map()

    def _on_map_press(self, event):
        """Guarda o ponto inicial para permitir arrastar o mapa com o rato."""

        if event.inaxes != self.map_axis or event.button != 1:
            return

        self.map_drag_start = {
            "mouse_x": event.x,
            "mouse_y": event.y,
            "xlim": self.map_axis.get_xlim(),
            "ylim": self.map_axis.get_ylim(),
        }

    def _on_map_drag(self, event):
        """Move os limites do eixo enquanto o utilizador arrasta o mapa."""

        if self.map_drag_start is None or self.map_axis is None:
            return

        start = self.map_drag_start
        xlim = start["xlim"]
        ylim = start["ylim"]
        axis_width = max(self.map_axis.bbox.width, 1)
        axis_height = max(self.map_axis.bbox.height, 1)
        delta_x = (event.x - start["mouse_x"]) * (xlim[1] - xlim[0]) / axis_width
        delta_y = (event.y - start["mouse_y"]) * (ylim[1] - ylim[0]) / axis_height
        self.map_axis.set_xlim(xlim[0] - delta_x, xlim[1] - delta_x)
        self.map_axis.set_ylim(ylim[0] - delta_y, ylim[1] - delta_y)
        self._store_current_axis_center()
        self.canvas.draw_idle()

    def _on_map_release(self, _event):
        """Termina o arrasto e guarda o novo centro da vista."""

        if self.map_drag_start is not None:
            self._store_current_axis_center()
        self.map_drag_start = None

    def _on_map_scroll(self, event):
        """Permite aproximar/afastar com a roda do rato sobre o mapa."""

        if event.inaxes != self.map_axis or event.xdata is None or event.ydata is None:
            return

        multiplier = 1.1 if event.button == "up" else 1 / 1.1
        self._change_map_zoom(multiplier)

    def _store_current_axis_center(self):
        """Guarda o centro da vista depois de uma interação manual."""

        if self.map_axis is None:
            return

        min_x, max_x = self.map_axis.get_xlim()
        min_y, max_y = self.map_axis.get_ylim()
        self.map_center = ((min_x + max_x) / 2, (min_y + max_y) / 2)
        self.map_center_floor = self.visible_floor_var.get()

    def _draw_exterior_tiles(self, axis, visible_floor):
        """Desenha o exterior com tiles OpenStreetMap Carto em Web Mercator."""

        if self.graph is None:
            return

        floor_positions = [
            lonlat_to_web_mercator(data["lat"], data["lon"])
            for _node_id, data in self.graph.nodes(data=True)
            if data.get("floor_key") == visible_floor
        ]
        if not floor_positions:
            return

        x_values = [point[0] for point in floor_positions]
        y_values = [point[1] for point in floor_positions]
        margin = 90
        min_x = min(x_values) - margin
        max_x = max(x_values) + margin
        min_y = min(y_values) - margin
        max_y = max(y_values) + margin

        min_tile_x, max_tile_y = web_mercator_to_tile(min_x, min_y, EXTERIOR_TILE_ZOOM)
        max_tile_x, min_tile_y = web_mercator_to_tile(max_x, max_y, EXTERIOR_TILE_ZOOM)

        # Só são pedidos/desenhados os tiles necessários para cobrir os nós do
        # exterior. Depois ficam guardados em cache local.
        for tile_x in range(min_tile_x, max_tile_x + 1):
            for tile_y in range(min_tile_y, max_tile_y + 1):
                tile_path = cached_osm_tile(tile_x, tile_y, EXTERIOR_TILE_ZOOM)
                if tile_path is None:
                    continue

                image = mpimg.imread(tile_path)
                extent = tile_web_mercator_extent(tile_x, tile_y, EXTERIOR_TILE_ZOOM)
                axis.imshow(
                    image,
                    origin="upper",
                    extent=extent,
                    zorder=0,
                )

        axis.text(
            0.01,
            0.01,
            "© OpenStreetMap contributors",
            transform=axis.transAxes,
            fontsize=8,
            color="#333333",
            bbox={
                "boxstyle": "round,pad=0.2",
                "facecolor": "white",
                "edgecolor": "none",
                "alpha": 0.75,
            },
            zorder=10,
        )

    def _draw_calibrated_image(self, axis, image_path: Path):
        """Desenha a imagem de piso alinhada com os nós através da calibração."""

        image = mpimg.imread(image_path)
        world_file = read_world_file(image_path)
        if world_file is not None:
            # World file é a calibração preferida porque já dá a transformação
            # direta de píxeis da imagem para coordenadas do mapa.
            height, width = image.shape[:2]
            transform = image_pixel_to_world_from_world_file(world_file)
            axis.imshow(
                image,
                origin="upper",
                extent=(0, width, height, 0),
                transform=transform + axis.transData,
                zorder=0,
            )
            return world_file, image.shape

        calibration = read_piclayer_calibration(image_path)
        if calibration is None:
            # Sem calibração, a imagem é mostrada sem garantia de alinhamento.
            axis.imshow(image)
            axis.axis("off")
            return None

        height, width = image.shape[:2]
        transform = image_pixel_to_world_transform(calibration, image.shape)
        axis.imshow(
            image,
            origin="upper",
            extent=(0, width, height, 0),
            transform=transform + axis.transData,
            zorder=0,
        )
        return calibration, image.shape

    def _draw_graph_axis(self, axis, path=None, current_index=None, visible_floor=None, image_path=None):
        """Desenha nós, arestas, labels opcionais e segmentos da rota."""

        if self.graph is None:
            return

        floor_nodes = [
            node_id
            for node_id, data in self.graph.nodes(data=True)
            if data.get("floor_key") == visible_floor
        ]
        floor_node_set = set(floor_nodes)
        # O grafo é convertido para Web Mercator para ficar compatível com
        # tiles OSM e imagens calibradas.
        pos = {
            node_id: lonlat_to_web_mercator(data["lat"], data["lon"])
            for node_id, data in self.graph.nodes(data=True)
            if node_id in floor_node_set
        }

        for node_a, node_b, data in self.graph.edges(data=True):
            if data.get("vertical") or node_a not in floor_node_set or node_b not in floor_node_set:
                continue
            # Arestas verticais entram na rota, mas não são desenhadas num piso
            # porque representam mudança entre mapas diferentes.
            x_values = [pos[node_a][0], pos[node_b][0]]
            y_values = [pos[node_a][1], pos[node_b][1]]
            axis.plot(x_values, y_values, color="#424242", linewidth=1.2, alpha=0.75, zorder=1)

        route_node_set = set(path or [])
        for node_id, (x_value, y_value) in pos.items():
            color = get_node_color(self.graph, node_id)
            size = 18
            alpha = 0.25 if path and node_id not in route_node_set else 0.9
            # A cor do nó vem da lógica base e distingue salas, corredores,
            # escadas, elevadores e outros pontos.
            axis.scatter(
                x_value,
                y_value,
                s=size,
                color=color,
                edgecolor="white",
                linewidth=0.6,
                alpha=alpha,
                zorder=2,
            )

        if self.show_labels_var.get():
            # Labels ficam opcionais para não poluir visualmente o mapa.
            labeled_nodes = [
                node_id
                for node_id, data in self.graph.nodes(data=True)
                if node_id in floor_node_set and (data.get("roomname") or data.get("name"))
            ]
            for node_id in labeled_nodes:
                x_value, y_value = pos[node_id]
                axis.text(
                    x_value,
                    y_value,
                    get_node_label(self.graph, node_id, show_nodeid=True),
                    fontsize=7,
                    ha="center",
                    va="bottom",
                    color="#111111",
                    zorder=3,
                    bbox={
                        "boxstyle": "round,pad=0.15",
                        "facecolor": "white",
                        "edgecolor": "none",
                        "alpha": 0.65,
                    },
                )

        if path and len(path) > 1:
            for index in range(len(path) - 1):
                node_a = path[index]
                node_b = path[index + 1]
                if node_a not in floor_node_set or node_b not in floor_node_set:
                    continue
                # Vermelho indica caminho por fazer; verde indica caminho já
                # confirmado pelo utilizador.
                x_values = [pos[node_a][0], pos[node_b][0]]
                y_values = [pos[node_a][1], pos[node_b][1]]
                color = "#d32f2f"
                width = 3.2
                if current_index is not None and index < current_index:
                    color = "#43a047"
                    width = 2.4
                axis.plot(x_values, y_values, color=color, linewidth=width, zorder=4)

            self._draw_route_points(axis, path, current_index, floor_node_set, pos)

        axis.set_title(f"Mapa calibrado e rota - {visible_floor}")
        axis.set_aspect("equal", adjustable="datalim", anchor="C")
        self._set_map_limits(axis, pos, image_path)
        self._apply_map_view(axis, visible_floor)
        axis.axis("off")

    def _draw_route_points(self, axis, path, current_index, floor_node_set, pos):
        """Destaca e numera apenas os pontos que pertencem à rota calculada."""

        current_index = current_index or 0
        destination_index = len(path) - 1

        for index, node_id in enumerate(path):
            if node_id not in floor_node_set:
                continue

            x_value, y_value = pos[node_id]
            fill_color = "#ffffff"
            edge_color = "#d32f2f"
            text_color = "#111111"
            marker_size = 95
            line_width = 1.3
            zorder = 6

            if index < current_index:
                fill_color = "#43a047"
                edge_color = "white"
                text_color = "white"
                marker_size = 85
            elif index == current_index:
                fill_color = "#fbc02d"
                edge_color = "black"
                text_color = "#111111"
                marker_size = 175
                line_width = 1.8
                zorder = 8
            elif index == current_index + 1:
                fill_color = "#fb8c00"
                edge_color = "black"
                text_color = "white"
                marker_size = 140
                line_width = 1.6
                zorder = 7
            elif index == destination_index:
                fill_color = "#1e88e5"
                edge_color = "white"
                text_color = "white"
                marker_size = 125

            axis.scatter(
                x_value,
                y_value,
                s=marker_size,
                color=fill_color,
                edgecolor=edge_color,
                linewidth=line_width,
                zorder=zorder,
            )
            axis.text(
                x_value,
                y_value,
                str(index + 1),
                fontsize=7,
                fontweight="bold",
                ha="center",
                va="center",
                color=text_color,
                zorder=zorder + 1,
            )

    def _set_map_limits(self, axis, positions, image_path):
        """Ajusta o enquadramento para incluir nós e cantos da imagem calibrada."""

        points = list(positions.values())
        if image_path and image_path.exists():
            world_file = read_world_file(image_path)
            if world_file is not None:
                # Incluir os cantos da imagem evita que o enquadramento fique
                # apertado apenas nos nós do grafo.
                image = mpimg.imread(image_path)
                points.extend(world_file_corners(world_file, image.shape))

        if not points:
            return

        x_values = [point[0] for point in points]
        y_values = [point[1] for point in points]
        width = max(x_values) - min(x_values)
        height = max(y_values) - min(y_values)
        margin_x = width * 0.18 if width else 1
        margin_y = height * 0.18 if height else 1
        axis.set_xlim(min(x_values) - margin_x, max(x_values) + margin_x)
        axis.set_ylim(min(y_values) - margin_y, max(y_values) + margin_y)

    def _apply_map_view(self, axis, visible_floor):
        """Aplica zoom e centro sem deixar o eixo encolher dentro da figura."""

        min_x, max_x = axis.get_xlim()
        min_y, max_y = axis.get_ylim()
        center = self.map_center
        if self.map_center_floor != visible_floor:
            center = None

        if center is None:
            center = ((min_x + max_x) / 2, (min_y + max_y) / 2)

        target_ratio = self._map_axes_ratio(axis)
        view_width = abs(max_x - min_x) / self.map_zoom
        view_height = abs(max_y - min_y) / self.map_zoom
        current_ratio = view_width / view_height if view_height else target_ratio

        if current_ratio < target_ratio:
            view_width = view_height * target_ratio
        elif current_ratio > target_ratio:
            view_height = view_width / target_ratio

        half_width = view_width / 2
        half_height = view_height / 2
        axis.set_xlim(center[0] - half_width, center[0] + half_width)
        axis.set_ylim(center[1] - half_height, center[1] + half_height)

    def _map_axes_ratio(self, axis):
        """Calcula a proporção largura/altura da área real onde o mapa é desenhado."""

        bbox = axis.bbox
        width = bbox.width
        height = bbox.height
        if width <= 1 or height <= 1:
            widget = self.canvas.get_tk_widget()
            width = max(widget.winfo_width(), 1)
            height = max(widget.winfo_height(), 1)
        return max(width / height, 0.01)


def main():
    """Ponto de entrada quando se executa app_desktop.py diretamente."""

    app = DesktopNavigationApp()
    app.mainloop()


if __name__ == "__main__":
    main()

