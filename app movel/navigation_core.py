# -*- coding: utf-8 -*-
"""
Core autonomo da app movel de navegacao pedestre indoor.

Este modulo concentra a leitura dos ficheiros OSM, a construcao do grafo do
campus, as regras de mobilidade, o calculo de rotas com Dijkstra e a geracao de
instrucoes textuais. Mantive uma copia propria dentro de `app movel/` para a
app Android nao depender da pasta `App Desktop/`.
"""

from __future__ import annotations

import math
import heapq
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


# Caminhos por defeito da app movel. Os OSM e imagens usados no APK ficam
# dentro desta mesma pasta para o Buildozer os empacotar.
BASE_DIR = Path(__file__).resolve().parent
OSM_DIR = BASE_DIR / "OSM Pisos"
IMAGE_DIR = BASE_DIR / "Imagens ECT2"

# Cada chave identifica uma camada da aplicacao. Optei por tratar o exterior
# como mais um piso para simplificar a criacao do grafo global.
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
# Ordem usada nas comboboxes/spinners e na logica que atravessa pisos.
INDOOR_FLOORS = ["Piso1", "Piso2", "Piso3"]
APP_FLOORS = ["Exterior", *INDOOR_FLOORS]
BUILDING_ORDER = ["ECT1", "ECT2", "ECHS2"]

# Constantes usadas para converter GPS em Web Mercator e alinhar os tiles
# OpenStreetMap que servem de fundo ao mapa exterior.
WEB_MERCATOR_LIMIT = 20037508.342789244
OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
OSM_TILE_USER_AGENT = "UTADIndoorNavigationPrototype/0.2 (academic project)"
EXTERIOR_TILE_ZOOM = 18

# Optei por usar estes multiplicadores apenas no custo interno do Dijkstra, para
# preferir caminhos mais confortaveis quando existe uma alternativa semelhante.
# A distancia real fica em `length`, que continua a ser o valor apresentado nas
# instrucoes.
EDGE_TYPE_COST_MULTIPLIER = {
    "connection": 1.0,
    "sidewalk": 1.0,
    "corridor": 1.0,
    "crosswalk": 1.05,
    "ramp": 1.15,
    "street": 1.4,
    "stairs": 1.3,
    "elevator": 1.0,
}


def configure_paths(base_dir: Path | str):
    """
    Atualiza os caminhos do core sem duplicar a logica.

    Por defeito o core usa a pasta `app movel`. Esta funcao continua a existir
    para testes e validadores poderem apontar explicitamente para este dataset.
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
    """
    Estado minimo de uma rota em navegacao.

    `path` contem os node_ids pela ordem da rota. `current_index` indica o
    ponto atual do utilizador; quando confirma chegada, a app avanca este indice
    em vez de recalcular a rota.
    """

    graph: object
    path: list[str]
    distance: float
    current_index: int = 0


@dataclass(frozen=True)
class SelectableNode:
    """
    Representa um ponto que pode aparecer na UI como origem/destino.

    Nem todos os nos OSM devem aparecer ao utilizador: pontos de corredor,
    calcada, passadeira ou rampa exterior servem so para deslocacao. Esta
    estrutura guarda apenas os pontos selecionaveis, ja com label pronta para a
    interface.
    """

    node_id: str
    nodeid: str
    name: str
    building: str
    floor: str
    node_type: str
    accessibility: str
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
    Grafo nao dirigido minimo usado pelo core da app.

    Comecei por usar NetworkX, mas no APK essa biblioteca trouxe uma dependencia
    nativa (`_bz2`) indisponivel. Por isso optei por implementar so as operacoes
    necessarias: adicionar nos/arestas, iterar nos/arestas,
    consultar vizinhos e aceder a `graph[a][b]`.
    """

    def __init__(self):
        self._nodes = {}
        self._adj = {}
        self.nodes = NodeAccessor(self)
        self.edges = EdgeAccessor(self)

    def add_node(self, node_id, **data):
        """Adiciona ou atualiza os atributos de um no."""

        self._nodes.setdefault(node_id, {}).update(data)
        self._adj.setdefault(node_id, {})

    def add_edge(self, node_a, node_b, **data):
        """Adiciona uma aresta nao dirigida entre dois nos."""

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
    """Le uma tag numerica e devolve `default` quando esta ausente/invalida."""

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
    """
    Le um ficheiro `.osm` exportado/editado no JOSM.

    Devolve dois objetos:
    - `nodes`: dicionario node_id -> latitude, longitude e tags;
    - `edges`: lista de segmentos consecutivos das ways.

    Como uma way com varios `<nd>` representa varios trocos, a funcao transforma
    a way em arestas entre pares consecutivos e copia as tags da way para cada
    segmento.
    """

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
    """Calcula a distancia aproximada em metros entre duas coordenadas GPS."""

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


def segment_edge_type(way_edge_type: str, node_a: dict, node_b: dict) -> str:
    """
    Ajusta o tipo da way ao segmento concreto que esta a ser criado.

    Uma way OSM pode ter varios nos. Optei por usar `edge_type` da propria way
    como fonte de verdade para escadas, porque uma escada pode ligar dois
    patamares que nao tem `type=stairs`. Para passadeiras e calcadas, o tipo dos
    extremos ainda ajuda a distinguir aproximacao, travessia e saida.
    """

    edge_type = (way_edge_type or "connection").strip() or "connection"
    endpoint_types = {str(node_a.get("type", "")).lower(), str(node_b.get("type", "")).lower()}

    if edge_type == "stairs":
        return "stairs"
    if edge_type == "elevator" and "elevator" not in endpoint_types:
        return "connection"
    if edge_type == "ramp" and endpoint_types.isdisjoint({"ramp", "rampa"}):
        return "connection"
    if edge_type == "crosswalk":
        if "sidewalk" in endpoint_types:
            return "sidewalk"
        if "crosswalk" not in endpoint_types:
            return "connection"
    if edge_type == "sidewalk" and "sidewalk" not in endpoint_types:
        return "connection"
    return edge_type


def edge_cost(length: float, edge_type: str) -> float:
    """Converte metros reais no custo interno usado pelo Dijkstra."""

    multiplier = EDGE_TYPE_COST_MULTIPLIER.get(str(edge_type).lower(), 1.0)
    return length * multiplier


def path_real_distance(graph, path: list[str]) -> float:
    """Soma os metros reais de uma rota a partir do campo `length`."""

    total = 0.0
    for index in range(len(path) - 1):
        edge = graph[path[index]][path[index + 1]]
        total += edge.get("length", edge.get("weight", 0))
    return total


def build_graph(nodes, edges):
    """
    Constroi o grafo de um ficheiro OSM.

    Cada no OSM vira um no do grafo. Cada ligacao entre dois nos consecutivos de
    uma way vira uma aresta com `length` em metros reais e `weight` como custo
    interno. O Dijkstra soma `weight`, mas a interface mostra `length` ao
    utilizador.
    """

    graph = SimpleGraph()

    # Primeiro entram todos os nos com as tags originais do OSM. Essas tags
    # alimentam filtros, labels, cores e ligacoes entre pisos.
    for node_id, data in nodes.items():
        graph.add_node(
            node_id,
            lat=data["lat"],
            lon=data["lon"],
            **data["tags"],
        )

    # Depois entram as arestas de cada way, ja com `length` real e `weight`
    # ponderado para o Dijkstra.
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
        edge_type = segment_edge_type(
            way_tags.get("edge_type", "connection"),
            nodes[node_a]["tags"],
            nodes[node_b]["tags"],
        )

        graph.add_edge(
            node_a,
            node_b,
            # `weight` e o custo usado no algoritmo de caminho mais curto.
            weight=edge_cost(distance, edge_type),
            # `length` guarda a distancia real apresentada nas instrucoes.
            length=round(distance, 2),
            way_id=way_id,
            edge_type=edge_type,
            accessibility=read_int_tag(
                way_tags,
                keys=("accessibility",),
                default=1,
            ),
        )

    return graph


def floor_number(floor: str) -> int | None:
    """Extrai o numero de uma string como `Piso3`; `Exterior` devolve None."""

    digits = "".join(char for char in str(floor) if char.isdigit())
    return int(digits) if digits else None


def floor_node_id(floor: str, node_id: str) -> str:
    """Cria IDs unicos no grafo global, mesmo que os .osm repitam node ids."""

    return f"{floor}:{node_id}"


def vertical_edge_weight(from_node: dict, to_node: dict, edge_type: str) -> float:
    """
    Define o custo artificial para mudar de piso.

    A distancia horizontal entre dois nos de pisos diferentes nao representa bem
    o esforco de subir/descer. Por isso, sao atribuidos custos por piso a
    escadas e elevador, no mesmo campo `weight` que o Dijkstra usa:
    - elevador: 8 unidades por piso;
    - escadas: 12 unidades por piso.
    """

    first_floor = int(from_node.get("floor", 0) or 0)
    second_floor = int(to_node.get("floor", 0) or 0)
    floor_delta = max(1, abs(first_floor - second_floor))
    return 8.0 * floor_delta if edge_type == "elevator" else 12.0 * floor_delta


def build_campus_graph():
    """
    Junta exterior e pisos interiores num unico grafo navegavel.

    Como os IDs originais dos nos podem repetir-se entre ficheiros OSM, cada no
    recebe um prefixo com o piso, por exemplo `Piso1:12`, antes de entrar no
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
    """
    Liga automaticamente elevadores entre pisos interiores adjacentes.

    Se existirem nos `type=elevator` no Piso1, Piso2 e Piso3, a funcao cria
    arestas verticais entre pisos consecutivos. Depois, na navegacao, uma
    sequencia Piso1 -> Piso2 -> Piso3 por elevador e apresentada como uma so
    acao visual.
    """

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
    """Determina se uma transicao exterior/interior e escada, elevador ou rampa."""

    types = {data_a.get("type"), data_b.get("type")}
    if "elevator" in types:
        return "elevator"
    if "stairs" in types:
        return "stairs"
    if "rampa" in types or "ramp" in types:
        return "ramp"
    return "connection"


def add_exterior_transition_connections(campus_graph):
    """Liga nos com a mesma tag `transition`, mesmo estando em ficheiros OSM diferentes."""

    transition_nodes = [
        (node_id, data)
        for node_id, data in campus_graph.nodes(data=True)
        if data.get("transition")
    ]
    add_matching_transition_group_edges(campus_graph, transition_nodes)


def add_matching_transition_group_edges(campus_graph, transition_nodes):
    """
    Agrupa transicoes pelo nome e cria as arestas em falta.

    Exemplo: um no no `Exterior.osm` e outro no `Piso1.osm` com
    `transition=ENTRADA_P1` passam a ficar ligados no grafo global.
    """

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
    """Normaliza uma tag `transition`, permitindo varios valores separados por `;`."""

    value = str(data.get("transition", "")).strip()
    if not value:
        return []
    return [label.strip().upper() for label in value.split(";") if label.strip()]


def add_vertical_edge(campus_graph, node_a, data_a, node_b, data_b, edge_type):
    """Adiciona uma ligacao vertical interior, normalmente escada ou elevador."""

    accessibility = 3 if edge_type == "elevator" else 2
    weight = vertical_edge_weight(data_a, data_b, edge_type)
    # Nas ligacoes verticais, `weight` e `length` usam o mesmo custo artificial.
    # Isto permite comparar escadas/elevador com o resto do caminho.
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
    """Adiciona uma ligacao entre exterior e interior atraves da tag `transition`."""

    # As transicoes entre exterior e interior sao medidas em Web Mercator para
    # ficarem no mesmo sistema de coordenadas usado no desenho dos mapas.
    distance = math.dist(
        lonlat_to_web_mercator(data_a["lat"], data_a["lon"]),
        lonlat_to_web_mercator(data_b["lat"], data_b["lon"]),
    )
    accessibility = 3 if edge_type == "elevator" else 2 if edge_type == "stairs" else 4
    # `weight` guarda o custo completo usado pelo Dijkstra. `length` guarda o
    # valor arredondado que aparece nas instrucoes.
    campus_graph.add_edge(
        node_a,
        node_b,
        weight=edge_cost(distance, edge_type),
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

    O caminho escolhido e aquele cuja soma de `weight` e menor. Esses pesos
    podem ter penalizacoes por `edge_type`; no fim a funcao devolve a soma de
    `length`, para a interface continuar a apresentar metros reais.
    """

    if origin not in graph.nodes or destination not in graph.nodes:
        return None, float("inf")
    if not graph_node_allowed_for_profile(graph, origin, mobility_reduced):
        return None, float("inf")
    if not graph_node_allowed_for_profile(graph, destination, mobility_reduced):
        return None, float("inf")

    # Dijkstra classico:
    # - `distances` guarda o melhor custo conhecido ate cada no;
    # - `previous` guarda de onde veio esse melhor custo para reconstruir a rota;
    # - `queue` e uma fila de prioridade pelo menor custo acumulado.
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
            # A restricao por perfil entra aqui, durante a expansao de vizinhos.
            # Assim, o algoritmo nunca considera uma aresta proibida para o
            # utilizador atual.
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

    return path, round(path_real_distance(graph, path), 2)


def lonlat_to_web_mercator(lat: float, lon: float):
    """Converte latitude/longitude para metros no sistema Web Mercator."""

    earth_radius = 6378137
    x_value = earth_radius * math.radians(lon)
    y_value = earth_radius * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    return x_value, y_value


def web_mercator_tile_size(zoom: int):
    """Calcula o tamanho de um tile OSM, em metros Web Mercator, para um zoom."""

    return (WEB_MERCATOR_LIMIT * 2) / (2 ** zoom)


def web_mercator_to_tile(x_value: float, y_value: float, zoom: int):
    """Converte coordenadas Web Mercator para indices x/y de tile OSM."""

    tile_size = web_mercator_tile_size(zoom)
    tile_x = math.floor((x_value + WEB_MERCATOR_LIMIT) / tile_size)
    tile_y = math.floor((WEB_MERCATOR_LIMIT - y_value) / tile_size)
    max_tile = (2 ** zoom) - 1
    return (
        max(0, min(max_tile, tile_x)),
        max(0, min(max_tile, tile_y)),
    )


def tile_web_mercator_extent(tile_x: int, tile_y: int, zoom: int):
    """Devolve os limites Web Mercator cobertos por um tile OSM."""

    tile_size = web_mercator_tile_size(zoom)
    min_x = -WEB_MERCATOR_LIMIT + tile_x * tile_size
    max_x = min_x + tile_size
    max_y = WEB_MERCATOR_LIMIT - tile_y * tile_size
    min_y = max_y - tile_size
    return min_x, max_x, min_y, max_y


def world_file_path(image_path: Path):
    """Procura o world file associado a uma imagem calibrada no JOSM/PicLayer."""

    candidates = [
        image_path.with_suffix(".jgw"),
        image_path.with_suffix(".pgw"),
        image_path.with_suffix(".jpgw"),
        image_path.with_suffix(".pngw"),
    ]
    return next((path for path in candidates if path.exists()), None)


def read_world_file(image_path: Path):
    """
    Le um world file e devolve os coeficientes da transformacao afim.

    Estes valores permitem desenhar a imagem do piso nas mesmas coordenadas dos
    nos OSM, mantendo rotacao, escala e deslocamento calibrados no JOSM.
    """

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
    """Calcula os quatro cantos da imagem ja transformados para coordenadas do mapa."""

    if len(image_size) >= 3:
        # Arrays de imagem costumam vir como (altura, largura, canais).
        height, width = image_size[:2]
    else:
        # Tamanhos simples de imagem costumam vir como (largura, altura).
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
    """Identifica pontos exteriores que servem so para deslocacao, nao selecao."""

    if floor != "Exterior":
        return False
    normalized = name.strip().lower()
    return normalized in {"calçada", "calcada", "passadeira"} or normalized.startswith("rampa")


def collect_selectable_nodes(graph) -> list[SelectableNode]:
    """
    Recolhe salas/entradas que podem ser escolhidas pelo utilizador.

    O grafo contem muitos nos auxiliares. Optei por remover corredores,
    passadeiras, calcadas e outros pontos de passagem para que a interface
    mostre apenas locais relevantes como origem ou destino.
    """

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
                accessibility=str(data.get("accessibility", "1")).strip() or "1",
                label=f"{prefix}{name}",
            )
        )

    return sorted(nodes, key=selectable_sort_key)


def node_data_allowed_for_profile(node_data: dict, mobility_reduced: bool):
    """Confirma se os dados de um no sao compativeis com o perfil escolhido."""

    accessibility = str(node_data.get("accessibility", "1")).strip()
    node_type = str(node_data.get("type", "")).lower()
    if mobility_reduced:
        return accessibility != "2" and node_type != "stairs"
    return accessibility != "3" and node_type != "elevator"


def graph_node_allowed_for_profile(graph, node_id: str, mobility_reduced: bool):
    """Protege o calculo contra origens/destinos invalidos para o perfil."""

    if node_id not in graph.nodes:
        return False
    return node_data_allowed_for_profile(graph.nodes[node_id], mobility_reduced)


def selectable_node_allowed_for_profile(node: SelectableNode, mobility_reduced: bool):
    """Confirma se um ponto deve aparecer para o perfil escolhido."""

    return node_data_allowed_for_profile(
        {"accessibility": node.accessibility, "type": node.node_type},
        mobility_reduced,
    )


def filter_selectable_nodes_for_profile(nodes: list[SelectableNode], mobility_reduced: bool):
    """Filtra uma lista de pontos selecionaveis para o perfil ativo."""

    return [
        node
        for node in nodes
        if selectable_node_allowed_for_profile(node, mobility_reduced)
    ]


def selectable_sort_key(item: SelectableNode):
    """Ordena pontos por edificio, piso e nodeID para manter listas previsiveis."""

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
    """Lista edificios disponiveis nos pontos selecionaveis."""

    return sorted({node.building for node in nodes}, key=building_sort_key)


def available_floors(nodes: list[SelectableNode], building: str):
    """Lista pisos/areas disponiveis para um edificio."""

    return sorted(
        {node.floor for node in nodes if node.building == building},
        key=floor_sort_key,
    )


def nodes_for(nodes: list[SelectableNode], building: str, floor: str):
    """Filtra pontos selecionaveis por edificio e piso/area."""

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
    """Converte a escolha textual da UI no node_id interno usado pelo grafo."""

    for node in nodes:
        if node.label == selected_label and node.building == building and node.floor == floor:
            return node.node_id
    return None


def node_label(graph, node_id, show_nodeid=False):
    """Cria a label curta usada no desenho do grafo."""

    data = graph.nodes[node_id]
    name = data.get("roomname") or data.get("name") or node_id[-4:]
    nodeid = data.get("nodeid")
    if show_nodeid and nodeid:
        return f"{nodeid}: {name}"
    return name


def node_color(graph, node_id):
    """Escolhe a cor visual de um no conforme o seu tipo."""

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
    """Cria o nome legivel de um ponto para as instrucoes de navegacao."""

    data = graph.nodes[node_id]
    name = data.get("roomname") or data.get("name") or "ponto intermédio"
    if data.get("type") == "connection":
        name = "Ponto de passagem"
    floor = data.get("floor_key", "")
    building = data.get("building", "")
    location = " · ".join(part for part in [building, floor] if part)
    return f"{name} ({location})" if location else name


def edge_name(edge):
    """Cria o nome legivel de uma aresta vertical generica."""

    edge_type = edge.get("edge_type", "")
    if edge_type == "elevator":
        return "o elevador"
    if edge_type == "stairs":
        return "as escadas"
    return "a ligação vertical"


def floor_display_name(floor: str):
    """Formata `Piso3` como texto natural para as instrucoes."""

    if not floor:
        return "piso indicado"
    if floor == "Exterior":
        return "Exterior"
    number = floor_number(floor)
    if number is not None:
        return f"Piso {number}"
    return str(floor)


def vertical_direction(from_floor: str, to_floor: str):
    """Devolve sobe/desce quando os dois pisos tem numeracao conhecida."""

    from_number = floor_number(from_floor)
    to_number = floor_number(to_floor)
    if from_number is None or to_number is None:
        return None
    if to_number > from_number:
        return "sobe"
    if to_number < from_number:
        return "desce"
    return None


def vertical_action(edge_type: str, from_floor: str, to_floor: str):
    """Gera uma acao clara para escadas/elevador entre pisos interiores."""

    target_floor = floor_display_name(to_floor)
    direction = vertical_direction(from_floor, to_floor)

    if edge_type == "elevator":
        if direction:
            return f"{direction} pelo elevador até ao {target_floor}"
        return f"usa o elevador até ao {target_floor}"

    if edge_type == "stairs":
        if direction:
            return f"{direction} as escadas até ao {target_floor}"
        return f"usa as escadas até ao {target_floor}"

    return f"usa {edge_name({'edge_type': edge_type})} até ao {target_floor}"


def upcoming_destination_hint(graph, path: list[str], index: int):
    """Identifica se o proximo passo ja deve anunciar um elemento fisico."""

    next_node = path[index + 1]
    next_node_type = str(graph.nodes[next_node].get("type", "")).lower()
    next_edge_type = ""
    if index + 2 < len(path):
        next_edge_type = str(graph[path[index + 1]][path[index + 2]].get("edge_type", "")).lower()

    if "crosswalk" in {next_node_type, next_edge_type}:
        return "até à passadeira"
    if next_node_type in {"ramp", "rampa"} or next_edge_type == "ramp":
        return "até à rampa"
    if "stairs" in {next_node_type, next_edge_type}:
        return "até às escadas"
    if "elevator" in {next_node_type, next_edge_type}:
        return "até ao elevador"
    return None


def movement_phrase(graph, path: list[str], index: int):
    """
    Traduz o tipo da aresta para uma acao compreensivel pelo utilizador.

    A distancia continua a aparecer, mas o texto aproveita `edge_type` para
    distinguir passadeiras, calcadas, rampas, escadas e elevadores.
    """

    edge = graph[path[index]][path[index + 1]]
    edge_type = str(edge.get("edge_type", "connection")).lower()
    current_type = str(graph.nodes[path[index]].get("type", "")).lower()
    next_type = str(graph.nodes[path[index + 1]].get("type", "")).lower()
    current_floor = str(graph.nodes[path[index]].get("floor_key", ""))
    next_floor = str(graph.nodes[path[index + 1]].get("floor_key", ""))
    # Algumas escadas/elevadores estao desenhados como varios segmentos
    # consecutivos. Guardo o tipo anterior para nao voltar a anunciar o mesmo
    # elemento fisico quando o utilizador ja vem dele.
    previous_edge_type = ""
    if index > 0:
        previous_edge = graph[path[index - 1]][path[index]]
        previous_edge_type = str(previous_edge.get("edge_type", "")).lower()
    hint = upcoming_destination_hint(graph, path, index)

    if current_floor == "Exterior" and next_floor != "Exterior":
        return "entra no edifício"
    if current_floor != "Exterior" and next_floor == "Exterior":
        return "sai do edifício"

    if edge_type == "crosswalk":
        if current_type != "crosswalk":
            return "segue até à passadeira"
        if next_type != "crosswalk":
            return "segue até à saída da passadeira"
        return "atravessa a passadeira"
    if edge_type == "sidewalk":
        return f"segue pela calçada {hint}" if hint else "segue pela calçada"
    if edge_type == "street":
        return "atravessa a estrada"
    if edge_type == "ramp":
        if current_type not in {"ramp", "rampa"}:
            return "segue até à rampa"
        return "segue pela rampa"
    if edge_type == "stairs":
        # Se a aresta anterior tambem era escada, continuo a dar distancia, mas
        # nao volto a anunciar as escadas como se fossem um novo elemento.
        if previous_edge_type == "stairs":
            return "avança"
        return "segue até às escadas"
    if edge_type == "elevator":
        if previous_edge_type == "elevator" and current_type == "elevator":
            return "sai do elevador e avança"
        return "segue até ao elevador"
    if previous_edge_type == "elevator" and current_type == "elevator":
        return "sai do elevador e avança"
    if hint:
        return f"segue {hint}"
    return "avança"


def movement_with_distance(phrase: str, distance: float):
    """Acrescenta distancia sem tornar as frases naturais demasiado pesadas."""

    if phrase == "avança":
        return f"avança {distance:.1f} m"
    if phrase == "sai do elevador e avança":
        return f"sai do elevador e avança {distance:.1f} m"
    return f"{phrase} ({distance:.1f} m)"


def turn_instruction(graph, path: list[str], index: int):
    """
    Calcula a orientacao relativa no ponto atual da rota.

    Esta instrucao so faz sentido quando existe um ponto anterior no mesmo piso:
    o utilizador veio de `path[index - 1]`, está em `path[index]` e vai seguir
    para `path[index + 1]`. Optei por tratar angulos pequenos como seguir em
    frente, para evitar instrucoes ruidosas em pequenos desvios do grafo.
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
    """
    Agrupa varios segmentos verticais de elevador numa so instrucao.

    Exemplo: Piso1 -> Piso2 -> Piso3 aparece ao utilizador como "usa/sobe pelo
    elevador ate ao Piso 3", em vez de obrigar a confirmar o Piso2.
    """

    index = start_index
    while index < len(route.path) - 1:
        edge = graph[route.path[index]][route.path[index + 1]]
        if not edge.get("vertical") or edge.get("edge_type") != "elevator":
            break
        index += 1
    return index


def next_navigation_index(graph, route: RouteState):
    """Calcula para que indice a app deve avancar quando o utilizador confirma."""

    index = route.current_index
    if index >= len(route.path) - 1:
        return index

    edge = graph[route.path[index]][route.path[index + 1]]
    if edge.get("vertical") and edge.get("edge_type") == "elevator":
        return elevator_exit_index(graph, route, index)

    return index + 1


def navigation_instruction(graph, route: RouteState):
    """
    Gera o texto apresentado no painel de navegacao.

    A funcao combina dados da rota, tipo da aresta, piso atual e proximo no para
    produzir frases como "atravessa a estrada", "segue pela calcada" ou "sobe
    pelo elevador ate ao Piso 3".
    """

    path = route.path
    index = route.current_index

    if index >= len(path) - 1:
        return f"Destino alcançado\n\n{navigation_point_name(graph, path[-1])}"

    current = path[index]
    next_node = path[index + 1]
    edge = graph[current][next_node]
    progress = f"Passo {index + 1} de {len(path) - 1}"
    current_text = navigation_point_name(graph, current)

    # Transicoes verticais recebem texto proprio, porque "avanca X metros" nao
    # explica bem uma mudanca de piso por escadas ou elevador.
    if edge.get("vertical"):
        current_floor = str(graph.nodes[current].get("floor_key", ""))
        next_floor = str(graph.nodes[next_node].get("floor_key", ""))
        edge_type = edge.get("edge_type")
        if current_floor == "Exterior" and next_floor != "Exterior":
            exit_node = path[elevator_exit_index(graph, route, index)] if edge_type == "elevator" else next_node
            exit_floor = str(graph.nodes[exit_node].get("floor_key", next_floor))
            next_text = navigation_point_name(graph, exit_node)
            if edge_type == "stairs":
                action = f"entra no edifício pelas escadas até ao {floor_display_name(next_floor)}"
            elif edge_type == "elevator":
                action = f"entra no edifício pelo elevador até ao {floor_display_name(exit_floor)}"
            else:
                action = "entra no edifício"
        elif current_floor != "Exterior" and next_floor == "Exterior":
            next_text = navigation_point_name(graph, next_node)
            if edge_type == "stairs":
                action = "sai do edifício pelas escadas"
            elif edge_type == "elevator":
                action = "sai do edifício pelo elevador"
            else:
                action = "sai do edifício"
        elif edge_type == "elevator":
            exit_node = path[elevator_exit_index(graph, route, index)]
            exit_floor = str(graph.nodes[exit_node].get("floor_key", next_floor))
            next_text = navigation_point_name(graph, exit_node)
            action = vertical_action(edge_type, current_floor, exit_floor)
        else:
            next_text = navigation_point_name(graph, next_node)
            action = vertical_action(edge_type, current_floor, next_floor)
        return (
            f"{progress}\n\n"
            f"Local atual: {current_text}\n"
            f"Próximo ponto: {next_text}\n\n"
            f"Ação: {action}.\n"
            "Depois confirma a chegada."
        )

    # Nos passos horizontais, a instrucao combina a viragem geometrica com a
    # descricao semantica da aresta: calcada, estrada, passadeira, rampa, etc.
    next_text = navigation_point_name(graph, next_node)
    distance = edge.get("length", edge.get("weight", 0))
    turn_text = turn_instruction(graph, path, index)
    phrase = movement_phrase(graph, path, index)
    movement = movement_with_distance(phrase, distance)
    if turn_text:
        # Quando a frase semantica ficou reduzida a "avanca", a viragem ja e a
        # informacao util. Evito "vira a direita e avanca", que soa redundante.
        movement = f"{turn_text} ({distance:.1f} m)" if phrase == "avança" else f"{turn_text} e {movement}"
    return (
        f"{progress}\n\n"
        f"Local atual: {current_text}\n"
        f"Próximo ponto: {next_text}\n\n"
        f"Ação: {movement}.\n"
        "Depois confirma a chegada."
    )
