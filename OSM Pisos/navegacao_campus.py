# -*- coding: utf-8 -*-
"""
Navegação Pedestre no Campus da UTAD
=====================================
Script para carregar o mapa topológico (.osm) criado no JOSM,
construir um grafo de navegação e calcular rotas com Dijkstra.

Requisitos (Anaconda): 
    conda install matplotlib networkx
    pip install geopy
    
    (matplotlib e networkx já vêm no Anaconda por defeito)

Autor: Projeto LEI 2025-2026
"""

import xml.etree.ElementTree as ET
import networkx as nx
import matplotlib.pyplot as plt
import math
import heapq
from collections import defaultdict


# =============================================================================
# 1. PARSER DO FICHEIRO .OSM
# =============================================================================

def parse_osm(filepath):
    """
    Lê um ficheiro .osm exportado do JOSM e extrai nós e arestas (ways).
    
    Retorna:
        nodes: dict {id: {'lat': float, 'lon': float, 'tags': dict}}
        edges: list [(node1_id, node2_id, way_id)]
    """
    tree = ET.parse(filepath)
    root = tree.getroot()
    
    nodes = {}
    edges = []
    
    # Extrair nós
    for node_elem in root.findall('node'):
        node_id = node_elem.get('id')
        lat = float(node_elem.get('lat'))
        lon = float(node_elem.get('lon'))
        
        tags = {}
        for tag in node_elem.findall('tag'):
            key = tag.get('k')
            value = tag.get('v')
            tags[key.lower()] = value  # normalizar keys para minúsculas
        
        nodes[node_id] = {
            'lat': lat,
            'lon': lon,
            'tags': tags
        }
    
    # Extrair arestas (ways)
    # No JOSM, cada way liga 2 nós (se seguiste a convenção SHIFT)
    for way_elem in root.findall('way'):
        way_id = way_elem.get('id')
        nd_refs = [nd.get('ref') for nd in way_elem.findall('nd')]
        
        way_tags = {}
        for tag in way_elem.findall('tag'):
            way_tags[tag.get('k').lower()] = tag.get('v')
        
        # Criar aresta entre cada par consecutivo de nós na way
        for i in range(len(nd_refs) - 1):
            edges.append((nd_refs[i], nd_refs[i + 1], way_id, way_tags))
    
    return nodes, edges


# =============================================================================
# 2. CÁLCULO DE DISTÂNCIA (HAVERSINE)
# =============================================================================

def haversine(lat1, lon1, lat2, lon2):
    """
    Calcula a distância em metros entre dois pontos geográficos
    usando a fórmula de Haversine.
    """
    R = 6371000  # Raio da Terra em metros
    
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    
    a = (math.sin(delta_phi / 2) ** 2 +
         math.cos(phi1) * math.cos(phi2) *
         math.sin(delta_lambda / 2) ** 2)
    
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    return R * c


# =============================================================================
# 3. CONSTRUÇÃO DO GRAFO
# =============================================================================

def build_graph(nodes, edges):
    """
    Constrói um grafo NetworkX a partir dos nós e arestas do OSM.
    Cada aresta recebe o peso = distância em metros (Haversine).
    """
    G = nx.Graph()
    
    # Adicionar nós com atributos
    for node_id, data in nodes.items():
        G.add_node(
            node_id,
            lat=data['lat'],
            lon=data['lon'],
            **data['tags']
        )
    
    # Adicionar arestas com peso = distância
    for n1, n2, way_id, way_tags in edges:
        if n1 in nodes and n2 in nodes:
            dist = haversine(
                nodes[n1]['lat'], nodes[n1]['lon'],
                nodes[n2]['lat'], nodes[n2]['lon']
            )
            
            # Extrair acessibilidade da aresta (default = 5, totalmente acessível)
            accessibility = int(way_tags.get('accessibility', 5))
            edge_type = way_tags.get('type', 'connection')
            
            G.add_edge(
                n1, n2,
                weight=dist,
                length=round(dist, 2),
                way_id=way_id,
                edge_type=edge_type,
                accessibility=accessibility
            )
    
    return G


# =============================================================================
# 4. ALGORITMO DE DIJKSTRA (implementação manual)
# =============================================================================

def dijkstra(graph, start, end, accessibility_min=1):
    """
    Implementação manual do Dijkstra para encontrar o caminho
    mais curto entre dois nós.
    
    Parâmetros:
        graph: grafo NetworkX
        start: ID do nó de origem
        end: ID do nó de destino
        accessibility_min: nível mínimo de acessibilidade das arestas (1-5)
                          1 = aceita tudo, 5 = só arestas totalmente acessíveis
    
    Retorna:
        path: lista de nós do caminho
        distance: distância total em metros
    """
    # Verificar se os nós existem
    if start not in graph.nodes:
        print(f"ERRO: Nó de origem '{start}' não existe no grafo.")
        return None, float('inf')
    if end not in graph.nodes:
        print(f"ERRO: Nó de destino '{end}' não existe no grafo.")
        return None, float('inf')
    
    # Inicialização
    distances = {node: float('inf') for node in graph.nodes}
    distances[start] = 0
    previous = {node: None for node in graph.nodes}
    priority_queue = [(0, start)]
    visited = set()
    
    while priority_queue:
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
            if edge_data.get('accessibility', 5) < accessibility_min:
                continue
            
            weight = edge_data['weight']
            new_dist = current_dist + weight
            
            if new_dist < distances[neighbor]:
                distances[neighbor] = new_dist
                previous[neighbor] = current_node
                heapq.heappush(priority_queue, (new_dist, neighbor))
    
    # Reconstruir o caminho
    if distances[end] == float('inf'):
        print("Não foi possível encontrar um caminho.")
        return None, float('inf')
    
    path = []
    node = end
    while node is not None:
        path.append(node)
        node = previous[node]
    path.reverse()
    
    return path, round(distances[end], 2)


# =============================================================================
# 5. VISUALIZAÇÃO DO MAPA
# =============================================================================

def get_node_label(graph, node_id):
    """Retorna o label para um nó (roomname ou ID curto)."""
    data = graph.nodes[node_id]
    roomname = data.get('roomname', '')
    if roomname:
        return roomname
    return node_id[-4:]  # Últimos 4 chars do ID


def get_node_color(graph, node_id):
    """Retorna a cor de um nó com base no seu tipo."""
    data = graph.nodes[node_id]
    node_type = data.get('type', 'unknown')
    
    colors = {
        'outdoor': '#4CAF50',      # Verde
        'room': '#FF9800',         # Laranja
        'space': '#2196F3',        # Azul
        'connection': '#9E9E9E',   # Cinzento
        'elevator': '#9C27B0',     # Roxo
        'stairs': '#F44336',       # Vermelho
        'transition': '#FF5722',   # Vermelho-laranja
    }
    return colors.get(node_type, '#BDBDBD')


def visualize_map(graph, path=None, title="Mapa Topológico - UTAD Campus"):
    """
    Visualiza o grafo do mapa topológico.
    Se um caminho (path) for fornecido, destaca-o a vermelho.
    """
    fig, ax = plt.subplots(1, 1, figsize=(14, 10))
    
    # Posições dos nós (usar longitude como x, latitude como y)
    pos = {}
    for node_id in graph.nodes:
        data = graph.nodes[node_id]
        pos[node_id] = (data['lon'], data['lat'])
    
    # Desenhar todas as arestas (cinzento claro)
    nx.draw_networkx_edges(
        graph, pos,
        edge_color='#CCCCCC',
        width=1.5,
        alpha=0.6,
        ax=ax
    )
    
    # Separar nós com tags e sem tags
    labeled_nodes = [n for n in graph.nodes if graph.nodes[n].get('roomname')]
    unlabeled_nodes = [n for n in graph.nodes if not graph.nodes[n].get('roomname')]
    
    # Cores dos nós com tags
    labeled_colors = [get_node_color(graph, n) for n in labeled_nodes]
    unlabeled_colors = ['#E0E0E0' for _ in unlabeled_nodes]
    
    # Desenhar nós sem tags (pequenos, cinzentos)
    nx.draw_networkx_nodes(
        graph, pos,
        nodelist=unlabeled_nodes,
        node_color=unlabeled_colors,
        node_size=30,
        alpha=0.5,
        ax=ax
    )
    
    # Desenhar nós com tags (maiores, coloridos)
    nx.draw_networkx_nodes(
        graph, pos,
        nodelist=labeled_nodes,
        node_color=labeled_colors,
        node_size=200,
        edgecolors='white',
        linewidths=2,
        alpha=0.9,
        ax=ax
    )
    
    # Labels dos nós com tags
    labels = {n: get_node_label(graph, n) for n in labeled_nodes}
    nx.draw_networkx_labels(
        graph, pos,
        labels=labels,
        font_size=7,
        font_weight='bold',
        font_color='#333333',
        ax=ax
    )
    
    # Destacar o caminho se existir
    if path and len(path) > 1:
        path_edges = [(path[i], path[i + 1]) for i in range(len(path) - 1)]
        
        nx.draw_networkx_edges(
            graph, pos,
            edgelist=path_edges,
            edge_color='#E53935',
            width=4,
            alpha=0.9,
            ax=ax
        )
        
        nx.draw_networkx_nodes(
            graph, pos,
            nodelist=path,
            node_color='#E53935',
            node_size=120,
            edgecolors='white',
            linewidths=2,
            ax=ax
        )
        
        # Marcar início e fim
        nx.draw_networkx_nodes(
            graph, pos,
            nodelist=[path[0]],
            node_color='#43A047',
            node_size=350,
            edgecolors='white',
            linewidths=3,
            ax=ax,
            label='Origem'
        )
        nx.draw_networkx_nodes(
            graph, pos,
            nodelist=[path[-1]],
            node_color='#1E88E5',
            node_size=350,
            edgecolors='white',
            linewidths=3,
            ax=ax,
            label='Destino'
        )
    
    # Legenda
    legend_items = [
        plt.Line2D([0], [0], marker='o', color='w', markerfacecolor='#4CAF50',
                    markersize=10, label='Outdoor'),
        plt.Line2D([0], [0], marker='o', color='w', markerfacecolor='#FF9800',
                    markersize=10, label='Sala'),
        plt.Line2D([0], [0], marker='o', color='w', markerfacecolor='#2196F3',
                    markersize=10, label='Corredor'),
        plt.Line2D([0], [0], marker='o', color='w', markerfacecolor='#9C27B0',
                    markersize=10, label='Elevador'),
        plt.Line2D([0], [0], marker='o', color='w', markerfacecolor='#F44336',
                    markersize=10, label='Escadas'),
        plt.Line2D([0], [0], marker='o', color='w', markerfacecolor='#E0E0E0',
                    markersize=10, label='Ponto intermédio'),
    ]
    ax.legend(handles=legend_items, loc='upper left', fontsize=9,
              framealpha=0.9, fancybox=True)
    
    ax.set_title(title, fontsize=16, fontweight='bold', pad=20)
    ax.set_xlabel('Longitude', fontsize=10)
    ax.set_ylabel('Latitude', fontsize=10)
    ax.tick_params(axis='both', labelsize=8)
    ax.set_facecolor('#FAFAFA')
    fig.set_facecolor('white')
    
    plt.tight_layout()
    plt.show()


# =============================================================================
# 6. FUNÇÕES AUXILIARES
# =============================================================================

def list_named_nodes(graph):
    """Lista todos os nós com nome (roomname) para referência."""
    print("\n" + "=" * 60)
    print("PONTOS DE INTERESSE NO MAPA")
    print("=" * 60)
    
    named = []
    for node_id in graph.nodes:
        data = graph.nodes[node_id]
        if data.get('roomname'):
            named.append({
                'id': node_id,
                'nome': data['roomname'],
                'tipo': data.get('type', '?'),
                'piso': data.get('floor', '?'),
                'edifício': data.get('building', '?')
            })
    
    for n in named:
        print(f"  ID: {n['id']:>8}  |  {n['nome']:<15}  |  "
              f"Tipo: {n['tipo']:<12}  |  Piso: {n['piso']}  |  "
              f"Edifício: {n['edifício']}")
    
    print(f"\nTotal: {len(named)} pontos de interesse")
    print("=" * 60)
    return named


def graph_stats(graph):
    """Mostra estatísticas do grafo."""
    print("\n" + "=" * 60)
    print("ESTATÍSTICAS DO GRAFO")
    print("=" * 60)
    print(f"  Nós totais:          {graph.number_of_nodes()}")
    print(f"  Arestas totais:      {graph.number_of_edges()}")
    print(f"  Nós com nome:        {sum(1 for n in graph.nodes if graph.nodes[n].get('roomname'))}")
    print(f"  Nós sem nome:        {sum(1 for n in graph.nodes if not graph.nodes[n].get('roomname'))}")
    print(f"  Grafo conexo:        {'Sim' if nx.is_connected(graph) else 'Não'}")
    
    if not nx.is_connected(graph):
        components = list(nx.connected_components(graph))
        print(f"  Componentes conexas: {len(components)}")
        for i, comp in enumerate(components):
            named_in_comp = [n for n in comp if graph.nodes[n].get('roomname')]
            names = [graph.nodes[n]['roomname'] for n in named_in_comp]
            print(f"    Componente {i+1}: {len(comp)} nós"
                  f" (nomeados: {', '.join(names) if names else 'nenhum'})")
    
    print("=" * 60)


# =============================================================================
# 7. PROGRAMA PRINCIPAL
# =============================================================================

if __name__ == "__main__":
    
    # ---- CONFIGURAÇÃO ----
    # Altera este caminho para o local do teu ficheiro .osm
    OSM_FILE = r"Piso1.osm"
    # Se o ficheiro estiver noutro sítio, usa o caminho completo, ex:
    # OSM_FILE = r"C:\Users\Alexandre\Desktop\...\OSM Pisos\Piso1.osm"
    
    
    # ---- CARREGAR E PROCESSAR ----
    print("A carregar o ficheiro OSM...")
    nodes, edges = parse_osm(OSM_FILE)
    print(f"  Nós encontrados: {len(nodes)}")
    print(f"  Arestas encontradas: {len(edges)}")
    
    print("\nA construir o grafo...")
    G = build_graph(nodes, edges)
    
    # Mostrar estatísticas
    graph_stats(G)
    
    # Listar pontos de interesse
    named_nodes = list_named_nodes(G)
    
    
    # ---- VISUALIZAR O MAPA ----
    print("\nA visualizar o mapa...")
    visualize_map(G, title="Mapa Topológico - ECT2 Piso 1 - UTAD")
    
    
    # ---- CALCULAR UMA ROTA (EXEMPLO) ----
    # Usar os IDs dos nós com nome para definir origem e destino
    # Podes alterar estes IDs para testar diferentes rotas
    
    origem = '-25463'   # Entrada (outdoor)
    destino = '-25759'  # Escada
    
    print(f"\nA calcular rota: {G.nodes[origem].get('roomname', origem)}"
          f" → {G.nodes[destino].get('roomname', destino)}")
    
    path, distance = dijkstra(G, origem, destino)
    
    if path:
        print(f"  Caminho encontrado! Distância: {distance:.2f} metros")
        print(f"  Nós no caminho: {len(path)}")
        print(f"  Percurso:")
        for i, node_id in enumerate(path):
            name = G.nodes[node_id].get('roomname', '(ponto intermédio)')
            node_type = G.nodes[node_id].get('type', '?')
            print(f"    {i+1}. {name} [{node_type}]")
        
        # Visualizar com a rota destacada
        visualize_map(G, path=path,
                      title=f"Rota: {G.nodes[origem].get('roomname')} → "
                            f"{G.nodes[destino].get('roomname')}"
                            f" ({distance:.1f}m)")
    
    
    # ---- CALCULAR OUTRA ROTA ----
    origem2 = '-25463'   # Entrada
    destino2 = '-25727'  # F1.2b
    
    print(f"\nA calcular rota: {G.nodes[origem2].get('roomname', origem2)}"
          f" → {G.nodes[destino2].get('roomname', destino2)}")
    
    path2, distance2 = dijkstra(G, origem2, destino2)
    
    if path2:
        print(f"  Caminho encontrado! Distância: {distance2:.2f} metros")
        print(f"  Percurso:")
        for i, node_id in enumerate(path2):
            name = G.nodes[node_id].get('roomname', '(ponto intermédio)')
            node_type = G.nodes[node_id].get('type', '?')
            print(f"    {i+1}. {name} [{node_type}]")
        
        visualize_map(G, path=path2,
                      title=f"Rota: {G.nodes[origem2].get('roomname')} → "
                            f"{G.nodes[destino2].get('roomname')}"
                            f" ({distance2:.1f}m)")
