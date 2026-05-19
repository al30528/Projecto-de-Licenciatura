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


@dataclass
class RouteState:
    floor: str
    graph: object
    path: list[str]
    distance: float
    current_index: int = 0


class DesktopNavigationApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Navegação Pedestre Indoor - UTAD")
        self.geometry("1320x820")
        self.minsize(1100, 680)

        self.graph = None
        self.named_nodes: list[dict[str, str]] = []
        self.route: RouteState | None = None

        self.profile_var = tk.StringVar(value="normal")
        self.floor_var = tk.StringVar(value="Piso1")
        self.origin_var = tk.StringVar()
        self.destination_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Escolhe o piso, a origem e o destino.")
        self.step_var = tk.StringVar(value="Ainda não há rota calculada.")

        self._build_layout()
        self.load_floor()

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

        ttk.Label(sidebar, text="Piso").grid(row=4, column=0, sticky="w")
        self.floor_combo = ttk.Combobox(
            sidebar,
            textvariable=self.floor_var,
            values=list(OSM_FILES.keys()),
            state="readonly",
            width=32,
        )
        self.floor_combo.grid(row=5, column=0, sticky="ew", pady=(4, 10))
        self.floor_combo.bind("<<ComboboxSelected>>", lambda _event: self.load_floor())

        ttk.Label(sidebar, text="Origem").grid(row=6, column=0, sticky="w")
        self.origin_combo = ttk.Combobox(sidebar, textvariable=self.origin_var, width=32)
        self.origin_combo.grid(row=7, column=0, sticky="ew", pady=(4, 10))

        ttk.Label(sidebar, text="Destino").grid(row=8, column=0, sticky="w")
        self.destination_combo = ttk.Combobox(
            sidebar,
            textvariable=self.destination_var,
            width=32,
        )
        self.destination_combo.grid(row=9, column=0, sticky="ew", pady=(4, 10))

        ttk.Button(
            sidebar,
            text="Calcular rota",
            command=self.calculate_route,
        ).grid(row=10, column=0, sticky="ew", pady=(4, 8))

        ttk.Button(
            sidebar,
            text="Cheguei ao ponto indicado",
            command=self.confirm_next_step,
        ).grid(row=11, column=0, sticky="ew")

        ttk.Separator(sidebar).grid(row=12, column=0, sticky="ew", pady=14)

        ttk.Label(sidebar, text="Navegação").grid(row=13, column=0, sticky="w")
        step_label = ttk.Label(
            sidebar,
            textvariable=self.step_var,
            wraplength=280,
            justify="left",
        )
        step_label.grid(row=14, column=0, sticky="ew", pady=(6, 12))

        ttk.Label(sidebar, text="Estado").grid(row=15, column=0, sticky="w")
        status_label = ttk.Label(
            sidebar,
            textvariable=self.status_var,
            wraplength=280,
            justify="left",
        )
        status_label.grid(row=16, column=0, sticky="ew", pady=(6, 0))

        main_area = ttk.Frame(self, padding=(0, 10, 10, 10))
        main_area.grid(row=0, column=1, sticky="nsew")
        main_area.columnconfigure(0, weight=1)
        main_area.rowconfigure(0, weight=1)

        self.figure = Figure(figsize=(10, 7), dpi=100)
        self.canvas = FigureCanvasTkAgg(self.figure, master=main_area)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")

    def load_floor(self):
        floor = self.floor_var.get()
        osm_file = OSM_FILES[floor]

        try:
            nodes, edges = parse_osm(osm_file)
            self.graph = build_graph(nodes, edges)
        except Exception as error:
            messagebox.showerror("Erro ao carregar piso", str(error))
            return

        self.route = None
        self.named_nodes = self._collect_named_nodes()
        values = [item["label"] for item in self.named_nodes]
        self.origin_combo["values"] = values
        self.destination_combo["values"] = values

        if values:
            self.origin_var.set(values[0])
            self.destination_var.set(values[-1])
        else:
            self.origin_var.set("")
            self.destination_var.set("")

        self.status_var.set(
            f"{floor} carregado: {self.graph.number_of_nodes()} nós, "
            f"{self.graph.number_of_edges()} arestas."
        )
        self.step_var.set("Escolhe origem e destino para calcular uma rota.")
        self.draw_map()

    def _collect_named_nodes(self):
        nodes = []
        for node_id, data in self.graph.nodes(data=True):
            name = data.get("roomname") or data.get("name")
            nodeid = str(data.get("nodeid", "")).strip()
            if not name:
                continue

            prefix = f"{nodeid} - " if nodeid else ""
            nodes.append(
                {
                    "node_id": node_id,
                    "nodeid": nodeid,
                    "name": name,
                    "label": f"{prefix}{name}",
                }
            )

        def sort_key(item):
            try:
                return int(item["nodeid"])
            except (TypeError, ValueError):
                return 999999

        return sorted(nodes, key=sort_key)

    def _selected_node(self, selected_label: str):
        for item in self.named_nodes:
            if item["label"] == selected_label:
                return item["node_id"]
        return resolve_node(self.graph, selected_label)

    def calculate_route(self):
        if self.graph is None:
            messagebox.showwarning("Sem piso", "Carrega primeiro um piso.")
            return

        origin = self._selected_node(self.origin_var.get())
        destination = self._selected_node(self.destination_var.get())

        if origin is None or destination is None:
            messagebox.showwarning(
                "Origem ou destino inválido",
                "Não consegui encontrar a origem ou o destino no grafo.",
            )
            return

        accessibility_min = 2 if self.profile_var.get() == "reduced" else 1
        path, distance = dijkstra(
            self.graph,
            origin,
            destination,
            accessibility_min=accessibility_min,
        )

        if not path:
            messagebox.showwarning(
                "Rota indisponível",
                "Não foi possível encontrar uma rota para as opções escolhidas.",
            )
            return

        self.route = RouteState(
            floor=self.floor_var.get(),
            graph=self.graph,
            path=path,
            distance=distance,
        )
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
        return f"nodeID {nodeid} ({name})" if nodeid else name

    def draw_map(self):
        self.figure.clear()
        route_path = self.route.path if self.route else None
        current_index = self.route.current_index if self.route else None
        image_path = FLOOR_IMAGES.get(self.floor_var.get())

        if image_path and image_path.exists():
            image_axis = self.figure.add_subplot(1, 2, 1)
            image_axis.imshow(mpimg.imread(image_path))
            image_axis.set_title("Mapa do piso")
            image_axis.axis("off")

            graph_axis = self.figure.add_subplot(1, 2, 2)
        else:
            graph_axis = self.figure.add_subplot(1, 1, 1)

        self._draw_graph_axis(graph_axis, route_path, current_index)
        self.figure.tight_layout()
        self.canvas.draw()

    def _draw_graph_axis(self, axis, path=None, current_index=None):
        if self.graph is None:
            return

        pos = {
            node_id: (data["lon"], data["lat"])
            for node_id, data in self.graph.nodes(data=True)
        }

        for node_a, node_b in self.graph.edges:
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
            if data.get("roomname") or data.get("name")
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
            axis.scatter(*pos[origin], s=120, color="#43a047", edgecolor="white", zorder=5)
            axis.scatter(*pos[destination], s=120, color="#1e88e5", edgecolor="white", zorder=5)

            if current_index is not None:
                current = path[current_index]
                axis.scatter(*pos[current], s=150, color="#fbc02d", edgecolor="black", zorder=6)

        axis.set_title("Grafo e rota")
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
