# -*- coding: utf-8 -*-
"""
Protótipo desktop para navegação pedestre indoor na UTAD.

Esta interface reutiliza a lógica existente em navegacao_campus_vscode.py e
mantém os ficheiros .osm sem alterações.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk

import networkx as nx
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib import image as mpimg

from navegacao_campus_vscode import (
    BASE_DIR,
    OSM_FILES,
    build_graph,
    dijkstra,
    get_node_color,
    get_node_label,
    parse_osm,
    resolve_node,
)


IMAGE_DIR = BASE_DIR / "Imagens ECT2"
FLOOR_IMAGES = {
    "Piso1": IMAGE_DIR / "Piso 1.jpg",
    "Piso2": IMAGE_DIR / "Piso2.png",
    "Piso3": IMAGE_DIR / "Piso 3.jpg",
}
INDOOR_FLOORS = ["Piso1", "Piso2", "Piso3"]


@dataclass
class RouteState:
    graph: object
    path: list[str]
    distance: float
    current_index: int = 0


def floor_number(floor: str) -> int | None:
    digits = "".join(char for char in floor if char.isdigit())
    return int(digits) if digits else None


def floor_node_id(floor: str, node_id: str) -> str:
    return f"{floor}:{node_id}"


def transition_target(value: str | None) -> str | None:
    if not value:
        return None

    parts = value.upper().split("-")
    if len(parts) == 2 and parts[0].startswith("P") and parts[1].startswith("P"):
        return f"{parts[1]}-{parts[0]}"
    return None


def vertical_edge_weight(from_node: dict, to_node: dict, edge_type: str) -> float:
    first_floor = int(from_node.get("floor", 0) or 0)
    second_floor = int(to_node.get("floor", 0) or 0)
    floor_delta = max(1, abs(first_floor - second_floor))
    return 8.0 * floor_delta if edge_type == "elevator" else 12.0 * floor_delta


def build_campus_graph():
    campus_graph = nx.Graph()
    floor_graphs = {}

    for floor in INDOOR_FLOORS:
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
    return campus_graph, floor_graphs


def add_vertical_connections(campus_graph):
    nodes = list(campus_graph.nodes(data=True))
    transition_nodes = [
        (node_id, data)
        for node_id, data in nodes
        if data.get("transition")
    ]

    for node_id, data in transition_nodes:
        expected_reverse = transition_target(data.get("transition"))
        if not expected_reverse:
            continue

        for other_id, other_data in transition_nodes:
            if node_id == other_id:
                continue
            if other_data.get("transition", "").upper() != expected_reverse:
                continue
            add_vertical_edge(campus_graph, node_id, data, other_id, other_data, "stairs")

    for node_id, data in transition_nodes:
        transition = data.get("transition", "").upper()
        parts = transition.split("-")
        if len(parts) != 2 or not parts[1].startswith("P"):
            continue

        target_floor = f"Piso{parts[1][1:]}"
        if any(
            campus_graph.has_edge(node_id, other_id)
            for other_id, other_data in nodes
            if other_data.get("floor_key") == target_floor
        ):
            continue

        source_floor_number = floor_number(data.get("floor_key", ""))
        target_number = floor_number(target_floor)
        if source_floor_number is None or target_number is None:
            continue

        candidates = [
            (other_id, other_data)
            for other_id, other_data in nodes
            if other_data.get("floor_key") == target_floor
            and other_data.get("type") == data.get("type")
        ]
        if not candidates:
            continue

        wanted = f"P{source_floor_number}"
        candidates.sort(
            key=lambda item: (
                wanted not in str(
                    item[1].get("transition")
                    or item[1].get("name")
                    or item[1].get("roomname")
                    or ""
                ).upper(),
                item[1].get("nodeid", ""),
            )
        )
        other_id, other_data = candidates[0]
        add_vertical_edge(campus_graph, node_id, data, other_id, other_data, data.get("type", "stairs"))

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


def calculate_path(graph, origin, destination, mobility_reduced=False):
    if not mobility_reduced:
        return dijkstra(graph, origin, destination, accessibility_min=1)

    accessible_graph = nx.Graph()
    accessible_graph.add_nodes_from(graph.nodes(data=True))
    for node_a, node_b, data in graph.edges(data=True):
        if data.get("edge_type") == "stairs":
            continue
        accessible_graph.add_edge(node_a, node_b, **data)

    try:
        path = nx.shortest_path(accessible_graph, origin, destination, weight="weight")
        distance = nx.shortest_path_length(accessible_graph, origin, destination, weight="weight")
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None, float("inf")

    return path, round(distance, 2)


class DesktopNavigationApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Navegação Pedestre Indoor - UTAD")
        self.geometry("1320x820")
        self.minsize(1100, 680)

        self.graph = None
        self.floor_graphs = {}
        self.named_nodes: list[dict[str, str]] = []
        self.route: RouteState | None = None

        self.profile_var = tk.StringVar(value="normal")
        self.origin_floor_var = tk.StringVar(value="Piso1")
        self.destination_floor_var = tk.StringVar(value="Piso1")
        self.visible_floor_var = tk.StringVar(value="Piso1")
        self.origin_var = tk.StringVar()
        self.destination_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Escolhe o piso, a origem e o destino.")
        self.step_var = tk.StringVar(value="Ainda não há rota calculada.")

        self._build_layout()
        self.load_campus()

    def _build_layout(self):
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(self, padding=14)
        sidebar.grid(row=0, column=0, sticky="ns")

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

        ttk.Label(sidebar, text="Piso de origem").grid(row=4, column=0, sticky="w")
        self.origin_floor_combo = ttk.Combobox(
            sidebar,
            textvariable=self.origin_floor_var,
            values=INDOOR_FLOORS,
            state="readonly",
            width=32,
        )
        self.origin_floor_combo.grid(row=5, column=0, sticky="ew", pady=(4, 10))
        self.origin_floor_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_node_lists())

        ttk.Label(sidebar, text="Origem").grid(row=6, column=0, sticky="w")
        self.origin_combo = ttk.Combobox(sidebar, textvariable=self.origin_var, width=32)
        self.origin_combo.grid(row=7, column=0, sticky="ew", pady=(4, 10))

        ttk.Label(sidebar, text="Piso de destino").grid(row=8, column=0, sticky="w")
        self.destination_floor_combo = ttk.Combobox(
            sidebar,
            textvariable=self.destination_floor_var,
            values=INDOOR_FLOORS,
            state="readonly",
            width=32,
        )
        self.destination_floor_combo.grid(row=9, column=0, sticky="ew", pady=(4, 10))
        self.destination_floor_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_node_lists())

        ttk.Label(sidebar, text="Destino").grid(row=10, column=0, sticky="w")
        self.destination_combo = ttk.Combobox(
            sidebar,
            textvariable=self.destination_var,
            width=32,
        )
        self.destination_combo.grid(row=11, column=0, sticky="ew", pady=(4, 10))

        ttk.Button(
            sidebar,
            text="Calcular rota",
            command=self.calculate_route,
        ).grid(row=12, column=0, sticky="ew", pady=(4, 8))

        ttk.Button(
            sidebar,
            text="Cheguei ao ponto indicado",
            command=self.confirm_next_step,
        ).grid(row=13, column=0, sticky="ew")

        ttk.Separator(sidebar).grid(row=14, column=0, sticky="ew", pady=14)

        ttk.Label(sidebar, text="Piso visível").grid(row=15, column=0, sticky="w")
        self.visible_floor_combo = ttk.Combobox(
            sidebar,
            textvariable=self.visible_floor_var,
            values=INDOOR_FLOORS,
            state="readonly",
            width=32,
        )
        self.visible_floor_combo.grid(row=16, column=0, sticky="ew", pady=(4, 12))
        self.visible_floor_combo.bind("<<ComboboxSelected>>", lambda _event: self.draw_map())

        ttk.Label(sidebar, text="Navegação").grid(row=17, column=0, sticky="w")
        step_label = ttk.Label(
            sidebar,
            textvariable=self.step_var,
            wraplength=280,
            justify="left",
        )
        step_label.grid(row=18, column=0, sticky="ew", pady=(6, 12))

        ttk.Label(sidebar, text="Estado").grid(row=19, column=0, sticky="w")
        status_label = ttk.Label(
            sidebar,
            textvariable=self.status_var,
            wraplength=280,
            justify="left",
        )
        status_label.grid(row=20, column=0, sticky="ew", pady=(6, 0))

        main_area = ttk.Frame(self, padding=(0, 10, 10, 10))
        main_area.grid(row=0, column=1, sticky="nsew")
        main_area.columnconfigure(0, weight=1)
        main_area.rowconfigure(0, weight=1)

        self.figure = Figure(figsize=(10, 7), dpi=100)
        self.canvas = FigureCanvasTkAgg(self.figure, master=main_area)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")

    def load_campus(self):
        try:
            self.graph, self.floor_graphs = build_campus_graph()
        except Exception as error:
            messagebox.showerror("Erro ao carregar mapas", str(error))
            return

        self.route = None
        self.refresh_node_lists()
        self.status_var.set(
            f"ECT2 carregado: {self.graph.number_of_nodes()} nós, "
            f"{self.graph.number_of_edges()} arestas, incluindo ligações entre pisos."
        )
        self.step_var.set("Escolhe origem e destino para calcular uma rota.")
        self.draw_map()

    def refresh_node_lists(self):
        if self.graph is None:
            return

        self.named_nodes = self._collect_named_nodes()
        origin_values = [
            item["label"]
            for item in self.named_nodes
            if item["floor"] == self.origin_floor_var.get()
        ]
        destination_values = [
            item["label"]
            for item in self.named_nodes
            if item["floor"] == self.destination_floor_var.get()
        ]
        self.origin_combo["values"] = origin_values
        self.destination_combo["values"] = destination_values

        self.origin_var.set(origin_values[0] if origin_values else "")
        self.destination_var.set(destination_values[-1] if destination_values else "")

        if self.route is None:
            self.visible_floor_var.set(self.origin_floor_var.get())
        self.draw_map()

    def _collect_named_nodes(self):
        nodes = []
        for node_id, data in self.graph.nodes(data=True):
            name = data.get("roomname") or data.get("name")
            nodeid = str(data.get("nodeid", "")).strip()
            floor = data.get("floor_key", "")
            if not name:
                continue

            prefix = f"{nodeid} - " if nodeid else ""
            nodes.append(
                {
                    "node_id": node_id,
                    "nodeid": nodeid,
                    "name": name,
                    "floor": floor,
                    "label": f"{prefix}{name}",
                }
            )

        def sort_key(item):
            try:
                return int(item["nodeid"])
            except (TypeError, ValueError):
                return 999999

        return sorted(nodes, key=sort_key)

    def _selected_node(self, selected_label: str, floor: str):
        for item in self.named_nodes:
            if item["label"] == selected_label and item["floor"] == floor:
                return item["node_id"]
        return resolve_node(self.graph, selected_label)

    def calculate_route(self):
        if self.graph is None:
            messagebox.showwarning("Sem piso", "Carrega primeiro um piso.")
            return

        origin = self._selected_node(self.origin_var.get(), self.origin_floor_var.get())
        destination = self._selected_node(
            self.destination_var.get(),
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
        self.visible_floor_var.set(self.graph.nodes[path[0]].get("floor_key", self.visible_floor_var.get()))
        self.status_var.set(
            f"Rota calculada com {len(path)} pontos e {distance:.1f} metros."
        )
        self._update_step_text()
        self.draw_map()

    def confirm_next_step(self):
        if self.route is None:
            messagebox.showinfo("Sem rota", "Calcula primeiro uma rota.")
            return

        if self.route.current_index >= len(self.route.path) - 1:
            self.step_var.set("Chegaste ao destino.")
            return

        self.route.current_index += 1
        current = self.route.path[self.route.current_index]
        self.visible_floor_var.set(self.graph.nodes[current].get("floor_key", self.visible_floor_var.get()))
        self._update_step_text()
        self.draw_map()

    def _update_step_text(self):
        if self.route is None:
            return

        path = self.route.path
        index = self.route.current_index

        if index >= len(path) - 1:
            current = path[-1]
            self.step_var.set(
                f"Destino alcançado: {self._node_display_name(current)}."
            )
            return

        current = path[index]
        next_node = path[index + 1]
        edge = self.graph[current][next_node]
        if edge.get("vertical"):
            self.step_var.set(
                f"Estás em {self._node_display_name(current)}.\n"
                f"Usa {self._edge_name(edge)} para ir para "
                f"{self._node_display_name(next_node)}.\n"
                "Quando chegares ao novo piso, confirma para receber a próxima indicação."
            )
        else:
            self.step_var.set(
                f"Estás em {self._node_display_name(current)}.\n"
                f"Segue para {self._node_display_name(next_node)}.\n"
                f"Distância deste passo: {edge.get('length', edge.get('weight', 0)):.1f} m.\n"
                "Quando lá chegares, confirma para receber a próxima indicação."
            )

    def _node_display_name(self, node_id):
        data = self.graph.nodes[node_id]
        name = data.get("roomname") or data.get("name") or "ponto intermédio"
        nodeid = data.get("nodeid")
        floor = data.get("floor_key", "")
        base = f"nodeID {nodeid} ({name})" if nodeid else name
        return f"{base} - {floor}" if floor else base

    def _edge_name(self, edge):
        edge_type = edge.get("edge_type", "")
        if edge_type == "elevator":
            return "o elevador"
        if edge_type == "stairs":
            return "as escadas"
        return "a ligação vertical"

    def draw_map(self):
        self.figure.clear()
        route_path = self.route.path if self.route else None
        current_index = self.route.current_index if self.route else None
        visible_floor = self.visible_floor_var.get()
        image_path = FLOOR_IMAGES.get(visible_floor)

        if image_path and image_path.exists():
            image_axis = self.figure.add_subplot(1, 2, 1)
            image_axis.imshow(mpimg.imread(image_path))
            image_axis.set_title("Mapa do piso")
            image_axis.axis("off")

            graph_axis = self.figure.add_subplot(1, 2, 2)
        else:
            graph_axis = self.figure.add_subplot(1, 1, 1)

        self._draw_graph_axis(graph_axis, route_path, current_index, visible_floor)
        self.figure.tight_layout()
        self.canvas.draw()

    def _draw_graph_axis(self, axis, path=None, current_index=None, visible_floor=None):
        if self.graph is None:
            return

        floor_nodes = [
            node_id
            for node_id, data in self.graph.nodes(data=True)
            if data.get("floor_key") == visible_floor
        ]
        floor_node_set = set(floor_nodes)
        pos = {
            node_id: (data["lon"], data["lat"])
            for node_id, data in self.graph.nodes(data=True)
            if node_id in floor_node_set
        }

        for node_a, node_b, data in self.graph.edges(data=True):
            if data.get("vertical") or node_a not in floor_node_set or node_b not in floor_node_set:
                continue
            x_values = [pos[node_a][0], pos[node_b][0]]
            y_values = [pos[node_a][1], pos[node_b][1]]
            axis.plot(x_values, y_values, color="#b8b8b8", linewidth=1.0, zorder=1)

        for node_id, (x_value, y_value) in pos.items():
            color = get_node_color(self.graph, node_id)
            size = 18
            axis.scatter(x_value, y_value, s=size, color=color, zorder=2)

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
                color="#222222",
                zorder=3,
            )

        if path and len(path) > 1:
            for index in range(len(path) - 1):
                node_a = path[index]
                node_b = path[index + 1]
                if node_a not in floor_node_set or node_b not in floor_node_set:
                    continue
                x_values = [pos[node_a][0], pos[node_b][0]]
                y_values = [pos[node_a][1], pos[node_b][1]]
                color = "#d32f2f"
                width = 3.2
                if current_index is not None and index < current_index:
                    color = "#43a047"
                    width = 2.4
                axis.plot(x_values, y_values, color=color, linewidth=width, zorder=4)

            origin = path[0]
            destination = path[-1]
            if origin in floor_node_set:
                axis.scatter(*pos[origin], s=120, color="#43a047", edgecolor="white", zorder=5)
            if destination in floor_node_set:
                axis.scatter(*pos[destination], s=120, color="#1e88e5", edgecolor="white", zorder=5)

            if current_index is not None:
                current = path[current_index]
                if current in floor_node_set:
                    axis.scatter(*pos[current], s=150, color="#fbc02d", edgecolor="black", zorder=6)

        axis.set_title(f"Grafo e rota - {visible_floor}")
        axis.set_xlabel("Longitude")
        axis.set_ylabel("Latitude")
        axis.tick_params(labelsize=8)
        axis.set_aspect("equal", adjustable="datalim")
        axis.grid(True, color="#eeeeee")


def main():
    app = DesktopNavigationApp()
    app.mainloop()


if __name__ == "__main__":
    main()
