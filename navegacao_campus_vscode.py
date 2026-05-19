# -*- coding: utf-8 -*-
"""
Navegação Pedestre no Campus da UTAD
=====================================
Versão preparada para usar no Visual Studio Code.

O que mudou em relação à versão usada no Spyder/Anaconda:
    - usa pathlib para encontrar ficheiros a partir da pasta deste script;
    - permite escolher o piso pelo terminal: --piso Piso1 / Piso2 / Piso3 / Exterior;
    - permite indicar origem/destino pelo nodeID lógico ou pelo ID OSM;
    - evita caminhos fixos dependentes do Spyder;
    - aceita tanto a tag "accessibility" como a tag escrita no OSM como "accessibilty".

Requisitos:
    pip install matplotlib networkx

Exemplos no terminal do VS Code:
    python navegacao_campus_vscode.py --piso Piso1
    python navegacao_campus_vscode.py --piso Piso2 --origem 32 --destino 19
    python navegacao_campus_vscode.py --ficheiro "OSM Pisos/Piso3.osm" --origem 1 --destino 38
"""

from __future__ import annotations

import argparse
import heapq
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import matplotlib.pyplot as plt
import networkx as nx


# =============================================================================
# 0. CONFIGURAÇÃO DE CAMINHOS PARA VS CODE
# =============================================================================

# Pasta onde este ficheiro .py está guardado.
# Assim o programa funciona mesmo que o terminal do VS Code abra noutra pasta.
BASE_DIR = Path(__file__).resolve().parent

# Pasta esperada para os ficheiros .osm dentro do trabalho.
OSM_DIR = BASE_DIR / "OSM Pisos"

# Ficheiros conhecidos do projecto.
OSM_FILES = {
    "Piso1": OSM_DIR / "Piso1.osm",
    "Piso2": OSM_DIR / "Piso2.osm",
    "Piso3": OSM_DIR / "Piso3.osm",
    "Exterior": OSM_DIR / "Exterior.osm",
}


# =============================================================================
# 1. FUNÇÕES GERAIS
# =============================================================================

def read_int_tag(tags: dict[str, str], keys: tuple[str, ...], default: int) -> int:
    """
    Lê uma tag inteira com tolerância para nomes diferentes.
    No teu OSM apareceu várias vezes "accessibilty" sem o segundo "i".
    """
    for key in keys:
        value = tags.get(key)
        if value is not None:
            try:
                return int(value)
            except ValueError:
                return default
    return default


def resolve_osm_file(piso: str | None, ficheiro: str | None) -> Path:
    """Resolve o ficheiro OSM a abrir."""
    if ficheiro:
        path = Path(ficheiro)
        if not path.is_absolute():
            path = BASE_DIR / path
        return path

    if piso is None:
        piso = "Piso1"

    if piso not in OSM_FILES:
        pisos = ", ".join(OSM_FILES.keys())
        raise ValueError(f"Piso inválido: {piso}. Usa um destes: {pisos}")

    return OSM_FILES[piso]


def get_tag(data: dict, key: str, default: str = "") -> str:
    """Lê uma tag de um nó NetworkX."""
    value = data.get(key, default)
    return value if value is not None else default


# =============================================================================
# 2. PARSER DO FICHEIRO .OSM
# =============================================================================

def parse_osm(filepath: Path):
    """
    Lê um ficheiro .osm exportado do JOSM e extrai nós e arestas.

    Retorna:
        nodes: dict {id: {'lat': float, 'lon': float, 'tags': dict}}
        edges: list [(node1_id, node2_id, way_id, way_tags)]
    """
    if not filepath.exists():
        raise FileNotFoundError(
            f"Não encontrei o ficheiro OSM:\n  {filepath}\n\n"
            "Confirma se abriste a pasta certa no VS Code ou usa --ficheiro."
        )

    tree = ET.parse(filepath)
    root = tree.getroot()

    nodes = {}
    edges = []

    # Extrair nós
    for node_elem in root.findall("node"):
        node_id = node_elem.get("id")
        lat = float(node_elem.get("lat"))
        lon = float(node_elem.get("lon"))

        tags = {}
        for tag in node_elem.findall("tag"):
            key = tag.get("k")
            value = tag.get("v")
            if key:
                tags[key.lower()] = value

        nodes[node_id] = {
            "lat": lat,
            "lon": lon,
            "tags": tags,
        }

    # Extrair arestas/ways
    for way_elem in root.findall("way"):
        way_id = way_elem.get("id")
        nd_refs = [nd.get("ref") for nd in way_elem.findall("nd")]

        way_tags = {}
        for tag in way_elem.findall("tag"):
            key = tag.get("k")
            value = tag.get("v")
            if key:
                way_tags[key.lower()] = value

        # Criar aresta entre cada par consecutivo de nós na way
        for i in range(len(nd_refs) - 1):
            edges.append((nd_refs[i], nd_refs[i + 1], way_id, way_tags))

    return nodes, edges


# =============================================================================
# 3. CÁLCULO DE DISTÂNCIA (HAVERSINE)
# =============================================================================

def haversine(lat1, lon1, lat2, lon2):
    """Calcula a distância em metros entre dois pontos geográficos."""
    radius = 6371000  # Raio da Terra em metros

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return radius * c


# =============================================================================
# 4. CONSTRUÇÃO DO GRAFO
# =============================================================================

def build_graph(nodes, edges):
    """
    Constrói um grafo NetworkX a partir dos nós e arestas do OSM.
    Cada aresta recebe o peso = distância em metros.
    """
    graph = nx.Graph()

    # Adicionar nós com atributos
    for node_id, data in nodes.items():
        graph.add_node(
            node_id,
            lat=data["lat"],
            lon=data["lon"],
            **data["tags"],
        )

    # Adicionar arestas com peso = distância.
    # Cada "way" do OSM liga dois ou mais nós. Para cada par de nós consecutivos,
    # calculamos a distância geográfica entre latitude/longitude e guardamos essa
    # distância no atributo `weight`. É este `weight` que o Dijkstra soma para
    # decidir qual é o caminho mais curto.
    for n1, n2, way_id, way_tags in edges:
        if n1 in nodes and n2 in nodes:
            # Haversine calcula a distância aproximada em metros sobre a Terra.
            # Para corredores e caminhos exteriores curtos, esta aproximação é
            # suficientemente boa para apresentar metros e comparar rotas.
            dist = haversine(
                nodes[n1]["lat"],
                nodes[n1]["lon"],
                nodes[n2]["lat"],
                nodes[n2]["lon"],
            )

            # Aceita "accessibility" e também "accessibilty".
            accessibility = read_int_tag(
                way_tags,
                keys=("accessibility", "accessibilty"),
                default=1,
            )
            edge_type = way_tags.get("type", "connection")

            graph.add_edge(
                n1,
                n2,
                # `weight` é usado pelo algoritmo de caminho mais curto.
                weight=dist,
                # `length` é a mesma distância, mas arredondada para mostrar ao
                # utilizador nas instruções e nos resumos.
                length=round(dist, 2),
                way_id=way_id,
                edge_type=edge_type,
                accessibility=accessibility,
            )

    return graph


# =============================================================================
# 5. ALGORITMO DE DIJKSTRA
# =============================================================================

def dijkstra(graph, start, end, accessibility_min=1):
    """
    Implementação manual do Dijkstra para encontrar o caminho mais curto.

    accessibility_min:
        1 = aceita tudo;
        2 = evita caminhos que tenham accessibility 1;
        3 = exige caminhos mais acessíveis, se existirem no OSM.

    Esta função mostra explicitamente o que o NetworkX faz por baixo na app
    desktop: começa com distância infinita para todos os nós, mete a origem a
    zero, e vai sempre expandindo o nó ainda não visitado com menor distância
    acumulada.
    """
    if start not in graph.nodes:
        print(f"ERRO: Nó de origem '{start}' não existe no grafo.")
        return None, float("inf")

    if end not in graph.nodes:
        print(f"ERRO: Nó de destino '{end}' não existe no grafo.")
        return None, float("inf")

    # Melhor distância conhecida desde a origem até cada nó.
    # No início só conhecemos a origem, por isso todos os outros ficam a infinito.
    distances = {node: float("inf") for node in graph.nodes}
    distances[start] = 0

    # Guarda o nó anterior no melhor caminho encontrado até cada nó.
    # No fim permite reconstruir a rota de trás para a frente.
    previous = {node: None for node in graph.nodes}

    # Fila de prioridade: o heap garante que retiramos sempre o nó com menor
    # distância acumulada. Cada item é (distância_atual, node_id).
    priority_queue = [(0, start)]
    visited = set()

    while priority_queue:
        # Retira o nó mais promissor: aquele que, até agora, tem menor custo.
        current_dist, current_node = heapq.heappop(priority_queue)

        if current_node in visited:
            continue
        visited.add(current_node)

        if current_node == end:
            break

        for neighbor in graph.neighbors(current_node):
            if neighbor in visited:
                continue

            edge_data = graph[current_node][neighbor]

            # Filtrar por acessibilidade
            if edge_data.get("accessibility", 1) < accessibility_min:
                continue

            # Peso da aresta entre o nó atual e o vizinho.
            # Normalmente é a distância em metros; nalgumas ligações especiais
            # pode ser um custo artificial, como escadas/elevador na app desktop.
            weight = edge_data["weight"]
            new_dist = current_dist + weight

            if new_dist < distances[neighbor]:
                # Se chegar ao vizinho por este caminho for melhor do que o que
                # conhecíamos antes, guardamos a nova distância e o nó anterior.
                distances[neighbor] = new_dist
                previous[neighbor] = current_node
                heapq.heappush(priority_queue, (new_dist, neighbor))

    if distances[end] == float("inf"):
        print("Não foi possível encontrar um caminho.")
        return None, float("inf")

    # Reconstrução da rota: começamos no destino e vamos seguindo `previous`
    # até à origem. Depois invertemos a lista para ficar origem -> destino.
    path = []
    node = end
    while node is not None:
        path.append(node)
        node = previous[node]
    path.reverse()

    return path, round(distances[end], 2)


# =============================================================================
# 6. PROCURA DE NÓS POR nodeID, ID OSM OU NOME
# =============================================================================

def resolve_node(graph, value: str | None):
    """
    Converte um valor dado pelo utilizador para ID OSM.

    Aceita:
        - ID OSM real, por exemplo -27046;
        - nodeID lógico, por exemplo 1, 23, 32;
        - nome/roomname, por exemplo F3.11 ou Entrada P3.
    """
    if not value:
        return None

    value = str(value).strip()

    # 1) ID OSM exacto
    if value in graph.nodes:
        return value

    # 2) nodeID lógico
    for node_id, data in graph.nodes(data=True):
        if str(data.get("nodeid", "")).strip() == value:
            return node_id

    # 3) roomname/name
    value_lower = value.lower()
    matches = []
    for node_id, data in graph.nodes(data=True):
        roomname = str(data.get("roomname", "")).lower()
        name = str(data.get("name", "")).lower()
        if value_lower in (roomname, name):
            matches.append(node_id)

    if len(matches) == 1:
        return matches[0]

    if len(matches) > 1:
        print(f"AVISO: encontrei vários nós com o nome '{value}': {matches}")
        print("Usa o nodeID ou o ID OSM para não haver ambiguidade.")
        return None

    print(f"AVISO: não encontrei nenhum nó para '{value}'.")
    return None


# =============================================================================
# 7. VISUALIZAÇÃO DO MAPA
# =============================================================================

def get_node_label(graph, node_id, show_nodeid=False):
    """Retorna o label para um nó."""
    data = graph.nodes[node_id]
    roomname = get_tag(data, "roomname")
    name = get_tag(data, "name")
    nodeid = get_tag(data, "nodeid")

    label = roomname or name or node_id[-4:]

    if show_nodeid and nodeid:
        return f"{nodeid}: {label}"

    return label


def get_node_color(graph, node_id):
    """Retorna a cor de um nó com base no seu tipo."""
    data = graph.nodes[node_id]
    node_type = data.get("type", "unknown")

    colors = {
        "outdoor": "#4CAF50",      # Verde
        "room": "#FF9800",         # Laranja
        "space": "#2196F3",        # Azul
        "connection": "#9E9E9E",   # Cinzento
        "elevator": "#9C27B0",     # Roxo
        "stairs": "#F44336",       # Vermelho
        "transition": "#FF5722",   # Vermelho-laranja
    }
    return colors.get(node_type, "#BDBDBD")


def visualize_map(graph, path=None, title="Mapa Topológico - UTAD Campus", show_nodeid=False):
    """
    Visualiza o grafo do mapa topológico.
    Se um caminho for fornecido, destaca-o a vermelho.
    """
    fig, ax = plt.subplots(1, 1, figsize=(14, 10))

    # Posições dos nós: longitude = X, latitude = Y
    pos = {}
    for node_id in graph.nodes:
        data = graph.nodes[node_id]
        pos[node_id] = (data["lon"], data["lat"])

    nx.draw_networkx_edges(
        graph,
        pos,
        edge_color="#CCCCCC",
        width=1.5,
        alpha=0.6,
        ax=ax,
    )

    labeled_nodes = [
        n for n in graph.nodes
        if graph.nodes[n].get("roomname") or graph.nodes[n].get("name")
    ]
    unlabeled_nodes = [n for n in graph.nodes if n not in labeled_nodes]

    labeled_colors = [get_node_color(graph, n) for n in labeled_nodes]
    unlabeled_colors = ["#E0E0E0" for _ in unlabeled_nodes]

    nx.draw_networkx_nodes(
        graph,
        pos,
        nodelist=unlabeled_nodes,
        node_color=unlabeled_colors,
        node_size=30,
        alpha=0.5,
        ax=ax,
    )

    nx.draw_networkx_nodes(
        graph,
        pos,
        nodelist=labeled_nodes,
        node_color=labeled_colors,
        node_size=220,
        edgecolors="white",
        linewidths=2,
        alpha=0.9,
        ax=ax,
    )

    labels = {n: get_node_label(graph, n, show_nodeid=show_nodeid) for n in labeled_nodes}
    nx.draw_networkx_labels(
        graph,
        pos,
        labels=labels,
        font_size=7,
        font_weight="bold",
        font_color="#333333",
        ax=ax,
    )

    if path and len(path) > 1:
        path_edges = [(path[i], path[i + 1]) for i in range(len(path) - 1)]

        nx.draw_networkx_edges(
            graph,
            pos,
            edgelist=path_edges,
            edge_color="#E53935",
            width=4,
            alpha=0.9,
            ax=ax,
        )

        nx.draw_networkx_nodes(
            graph,
            pos,
            nodelist=path,
            node_color="#E53935",
            node_size=130,
            edgecolors="white",
            linewidths=2,
            ax=ax,
        )

        nx.draw_networkx_nodes(
            graph,
            pos,
            nodelist=[path[0]],
            node_color="#43A047",
            node_size=350,
            edgecolors="white",
            linewidths=3,
            ax=ax,
            label="Origem",
        )
        nx.draw_networkx_nodes(
            graph,
            pos,
            nodelist=[path[-1]],
            node_color="#1E88E5",
            node_size=350,
            edgecolors="white",
            linewidths=3,
            ax=ax,
            label="Destino",
        )

    legend_items = [
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor="#4CAF50", markersize=10, label="Outdoor"),
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor="#FF9800", markersize=10, label="Sala"),
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor="#9E9E9E", markersize=10, label="Corredor"),
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor="#9C27B0", markersize=10, label="Elevador"),
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor="#F44336", markersize=10, label="Escadas"),
    ]
    ax.legend(handles=legend_items, loc="upper left", fontsize=9, framealpha=0.9, fancybox=True)

    ax.set_title(title, fontsize=16, fontweight="bold", pad=20)
    ax.set_xlabel("Longitude", fontsize=10)
    ax.set_ylabel("Latitude", fontsize=10)
    ax.tick_params(axis="both", labelsize=8)
    ax.set_facecolor("#FAFAFA")
    fig.set_facecolor("white")

    plt.tight_layout()
    plt.show()


# =============================================================================
# 8. FUNÇÕES AUXILIARES
# =============================================================================

def list_named_nodes(graph):
    """Lista todos os nós com nome para referência."""
    print("\n" + "=" * 90)
    print("PONTOS DE INTERESSE NO MAPA")
    print("=" * 90)

    named = []
    for node_id in graph.nodes:
        data = graph.nodes[node_id]
        roomname = data.get("roomname") or data.get("name")
        if roomname:
            named.append({
                "nodeid": data.get("nodeid", ""),
                "id": node_id,
                "nome": roomname,
                "tipo": data.get("type", "?"),
                "piso": data.get("floor", "?"),
                "edificio": data.get("building", "?"),
                "accessibility": data.get("accessibility", data.get("accessibilty", "?")),
            })

    def sort_key(item):
        try:
            return int(item["nodeid"])
        except (TypeError, ValueError):
            return 999999

    named.sort(key=sort_key)

    for n in named:
        print(
            f"  nodeID: {str(n['nodeid']):>3} | "
            f"ID OSM: {n['id']:>8} | "
            f"{n['nome']:<18} | "
            f"Tipo: {n['tipo']:<11} | "
            f"Piso: {n['piso']} | "
            f"Acess.: {n['accessibility']}"
        )

    print(f"\nTotal: {len(named)} pontos de interesse")
    print("=" * 90)
    return named


def graph_stats(graph):
    """Mostra estatísticas do grafo."""
    print("\n" + "=" * 60)
    print("ESTATÍSTICAS DO GRAFO")
    print("=" * 60)
    print(f"  Nós totais:          {graph.number_of_nodes()}")
    print(f"  Arestas totais:      {graph.number_of_edges()}")
    print(f"  Nós com nome:        {sum(1 for n in graph.nodes if graph.nodes[n].get('roomname') or graph.nodes[n].get('name'))}")
    print(f"  Nós sem nome:        {sum(1 for n in graph.nodes if not graph.nodes[n].get('roomname') and not graph.nodes[n].get('name'))}")
    print(f"  Grafo conexo:        {'Sim' if nx.is_connected(graph) else 'Não'}")

    if not nx.is_connected(graph):
        components = list(nx.connected_components(graph))
        print(f"  Componentes conexas: {len(components)}")
        for i, comp in enumerate(components):
            named_in_comp = [n for n in comp if graph.nodes[n].get("roomname") or graph.nodes[n].get("name")]
            names = [graph.nodes[n].get("roomname") or graph.nodes[n].get("name") for n in named_in_comp]
            print(f"    Componente {i + 1}: {len(comp)} nós ({', '.join(names) if names else 'nenhum'})")

    print("=" * 60)


def print_path(graph, path, distance):
    """Mostra o percurso encontrado na consola."""
    print(f"  Caminho encontrado! Distância: {distance:.2f} metros")
    print(f"  Nós no caminho: {len(path)}")
    print("  Percurso:")

    for i, node_id in enumerate(path, start=1):
        data = graph.nodes[node_id]
        name = data.get("roomname") or data.get("name") or "(ponto intermédio)"
        node_type = data.get("type", "?")
        nodeid = data.get("nodeid", "?")
        print(f"    {i}. nodeID {nodeid} | {name} [{node_type}] | ID OSM {node_id}")


# =============================================================================
# 9. ARGUMENTOS DO TERMINAL
# =============================================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description="Carrega um piso OSM, mostra o grafo e calcula rotas."
    )

    parser.add_argument(
        "--piso",
        choices=list(OSM_FILES.keys()),
        default="Piso1",
        help="Piso a carregar quando usas a pasta 'OSM Pisos'. Default: Piso1.",
    )

    parser.add_argument(
        "--ficheiro",
        default=None,
        help="Caminho manual para um ficheiro .osm. Se usado, ignora --piso.",
    )

    parser.add_argument(
        "--origem",
        default=None,
        help="Origem da rota. Pode ser nodeID, ID OSM ou nome exacto da sala.",
    )

    parser.add_argument(
        "--destino",
        default=None,
        help="Destino da rota. Pode ser nodeID, ID OSM ou nome exacto da sala.",
    )

    parser.add_argument(
        "--acessibilidade",
        type=int,
        default=1,
        help="Nível mínimo de acessibilidade das arestas. Default: 1.",
    )

    parser.add_argument(
        "--sem-mapa",
        action="store_true",
        help="Não abre a janela do mapa. Útil só para testar na consola.",
    )

    parser.add_argument(
        "--mostrar-nodeid",
        action="store_true",
        help="Mostra o nodeID antes do nome na visualização do mapa.",
    )

    return parser.parse_args()


# =============================================================================
# 10. PROGRAMA PRINCIPAL
# =============================================================================

def main():
    args = parse_args()

    try:
        osm_file = resolve_osm_file(args.piso, args.ficheiro)
    except ValueError as error:
        print(error)
        sys.exit(1)

    print("A carregar o ficheiro OSM...")
    print(f"  Ficheiro: {osm_file}")

    try:
        nodes, edges = parse_osm(osm_file)
    except FileNotFoundError as error:
        print(error)
        sys.exit(1)

    print(f"  Nós encontrados: {len(nodes)}")
    print(f"  Arestas encontradas: {len(edges)}")

    print("\nA construir o grafo...")
    graph = build_graph(nodes, edges)

    graph_stats(graph)
    list_named_nodes(graph)

    title_base = f"Mapa Topológico - ECT2 {args.piso} - UTAD"

    # Se não for indicada origem/destino, mostra apenas o mapa.
    if not args.origem or not args.destino:
        print("\nOrigem/destino não indicados. Vou apenas mostrar o mapa.")
        print("Exemplo:")
        print(f"  python {Path(__file__).name} --piso {args.piso} --origem 1 --destino 10")

        if not args.sem_mapa:
            visualize_map(graph, title=title_base, show_nodeid=args.mostrar_nodeid)
        return

    origem = resolve_node(graph, args.origem)
    destino = resolve_node(graph, args.destino)

    if origem is None or destino is None:
        print("\nNão foi possível calcular a rota porque a origem ou o destino não foram encontrados.")
        sys.exit(1)

    origem_nome = graph.nodes[origem].get("roomname") or graph.nodes[origem].get("name") or origem
    destino_nome = graph.nodes[destino].get("roomname") or graph.nodes[destino].get("name") or destino

    print(f"\nA calcular rota: {origem_nome} → {destino_nome}")
    path, distance = dijkstra(graph, origem, destino, accessibility_min=args.acessibilidade)

    if path:
        print_path(graph, path, distance)

        if not args.sem_mapa:
            visualize_map(
                graph,
                path=path,
                title=f"Rota: {origem_nome} → {destino_nome} ({distance:.1f}m)",
                show_nodeid=args.mostrar_nodeid,
            )


if __name__ == "__main__":
    main()
