# -*- coding: utf-8 -*-
"""
Protótipo desktop para navegação pedestre indoor na UTAD.

Este ficheiro fica responsavel apenas pela interface desktop: controlos Tkinter,
desenho Matplotlib, zoom/arrasto do mapa e apresentacao das instrucoes. A
leitura dos OSM, a construcao do grafo, o Dijkstra e o texto base da navegacao
ficam no `navigation_core.py`.
"""

from __future__ import annotations

import math
import tkinter as tk
import urllib.error
import urllib.request
from pathlib import Path
from tkinter import messagebox, ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib import image as mpimg
from matplotlib.transforms import Affine2D

import navigation_core as nav


# Estes atalhos para nomes do core mantem o codigo da interface mais curto. A
# separacao importante e: este ficheiro desenha e reage a eventos; o core trata
# de OSM, grafo, rota, filtros e instrucoes.
BASE_DIR = nav.BASE_DIR
FLOOR_IMAGES = nav.FLOOR_IMAGES
INDOOR_FLOORS = nav.INDOOR_FLOORS
APP_FLOORS = nav.APP_FLOORS
BUILDING_ORDER = nav.BUILDING_ORDER
RouteState = nav.RouteState
build_campus_graph = nav.build_campus_graph
calculate_path = nav.calculate_path
get_node_color = nav.node_color
get_node_label = nav.node_label
lonlat_to_web_mercator = nav.lonlat_to_web_mercator
read_world_file = nav.read_world_file
world_file_corners = nav.world_file_corners
world_file_path = nav.world_file_path
web_mercator_to_tile = nav.web_mercator_to_tile
tile_web_mercator_extent = nav.tile_web_mercator_extent
TILE_CACHE_DIR = BASE_DIR / ".tile_cache" / "osm_carto"
OSM_TILE_URL = nav.OSM_TILE_URL
OSM_TILE_USER_AGENT = nav.OSM_TILE_USER_AGENT
EXTERIOR_TILE_ZOOM = nav.EXTERIOR_TILE_ZOOM


def read_piclayer_calibration(image_path: Path):
    """
    Le ficheiros `.cal` do plugin PicLayer/JOSM, se existirem.

    Mantive este suporte porque algumas imagens foram calibradas diretamente no
    JOSM/PicLayer antes de passar a preferir world files (`.jgw`, `.pgw`, etc.).
    Assim, a app continua a conseguir abrir calibracoes antigas.
    """

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


def cached_osm_tile(tile_x: int, tile_y: int, zoom: int):
    """
    Obtem um tile OSM do cache local ou descarrega-o se ainda nao existir.

    A cache evita fazer pedidos repetidos ao OpenStreetMap sempre que a app e
    aberta ou o mapa exterior e redesenhado.
    """

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
    """Cria a transformacao Matplotlib a partir dos coeficientes do world file."""

    return Affine2D().from_values(
        world_file["a"],
        world_file["d"],
        world_file["b"],
        world_file["e"],
        world_file["c"],
        world_file["f"],
    )


def image_pixel_to_world_transform(calibration: dict, image_shape):
    """Cria a transformacao da imagem usando a calibracao PicLayer."""

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
    """Converte um ponto da imagem em coordenadas do mundo pela calibracao .cal."""

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
    """Calcula os quatro cantos da imagem ja transformados para o mundo."""

    height, width = image_shape[:2]
    return [
        pixel_to_world(calibration, image_shape, 0, 0),
        pixel_to_world(calibration, image_shape, width, 0),
        pixel_to_world(calibration, image_shape, width, height),
        pixel_to_world(calibration, image_shape, 0, height),
    ]


class DesktopNavigationApp(tk.Tk):
    """
    Janela principal Tkinter da aplicacao desktop.

    Esta classe guarda o estado da interface e coordena tres tarefas:
    selecionar origem/destino, pedir ao core para calcular a rota e desenhar o
    mapa/rota no canvas Matplotlib.
    """

    def __init__(self):
        """Inicializa estado da aplicação, variáveis Tkinter e layout principal."""

        super().__init__()
        self.title("Navegação Pedestre Indoor - UTAD")
        self.geometry("1320x820")
        self.minsize(1100, 680)

        # Estado principal vindo do core.
        self.graph = None
        self.floor_graphs = {}
        self.named_nodes: list[dict[str, str]] = []
        self.route: RouteState | None = None

        # Variaveis Tkinter ligadas aos controlos da interface.
        # Quando o utilizador escolhe valores nas comboboxes, estas variáveis
        # sao atualizadas automaticamente.
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
        # Estado da vista do mapa. `map_zoom` controla a escala, `map_center`
        # guarda o centro manual/automatico e `map_drag_start` existe apenas
        # enquanto o utilizador esta a arrastar o mapa com o rato.
        self.zoom_var = tk.StringVar(value="100%")
        self.map_zoom = 1.0
        self.map_center = None
        self.map_center_floor = None
        self.map_axis = None
        self.map_drag_start = None

        self._build_layout()
        self.load_campus()

    def _build_layout(self):
        """Constroi a barra lateral de controlos e a area principal do mapa."""

        # A janela tem duas zonas: coluna 0 com controlos e coluna 1 com o mapa.
        # A coluna do mapa recebe o peso para crescer quando a janela aumenta.
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(self, padding=14)
        sidebar.grid(row=0, column=0, sticky="ns")

        # O perfil influencia o calculo da rota:
        # normal nao usa elevador; mobilidade reduzida nao usa escadas.
        ttk.Label(sidebar, text="Perfil").grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(
            sidebar,
            text="Normal",
            variable=self.profile_var,
            value="normal",
            command=self.refresh_node_lists,
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Radiobutton(
            sidebar,
            text="Mobilidade reduzida",
            variable=self.profile_var,
            value="reduced",
            command=self.refresh_node_lists,
        ).grid(row=2, column=0, sticky="w")

        ttk.Separator(sidebar).grid(row=3, column=0, sticky="ew", pady=14)

        # Optei por escolher origem/destino em tres niveis para escalar melhor:
        # edificio -> piso/area -> sala/entrada.
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

        # O piso visivel serve apenas para inspecao do mapa. A rota continua a
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

        # Matplotlib fica embutido no Tkinter atraves do FigureCanvasTkAgg. Aqui
        # sao desenhados fundo calibrado, grafo e rota.
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
        """Atualiza edificios, pisos e pontos selecionaveis nas comboboxes."""

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
        # Apos escolher edificio e piso, a UI mostra apenas pontos desse
        # contexto. Isto evita listas gigantes quando houver mais edificios.
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
        """Preenche as listas de edificios a partir dos pontos selecionaveis."""

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
        """Mostra apenas pisos/areas existentes para o edificio selecionado."""

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
        """Ordena edificios conhecidos pela ordem definida em BUILDING_ORDER."""

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
        """
        Recolhe pontos que podem aparecer como origem/destino na interface.

        O grafo contem muitos pontos auxiliares de deslocacao. Aqui ficam de
        fora corredores, ligacoes internas e pontos exteriores como calcada ou
        passadeira, porque o utilizador nao deve escolhe-los diretamente.
        """

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
            if not self._profile_allows_selectable_node(data, node_type):
                continue

            # Mantive o nodeID na label para facilitar validacao cruzada com o
            # JOSM/OSM durante testes e apresentacao.
            prefix = f"{nodeid} - " if nodeid else ""
            nodes.append(
                {
                    "node_id": node_id,
                    "nodeid": nodeid,
                    "name": name,
                    "building": building,
                    "floor": floor,
                    "type": node_type,
                    "accessibility": str(data.get("accessibility", "1")).strip() or "1",
                    "label": f"{prefix}{name}",
                }
            )

        def sort_key(item):
            try:
                return int(item["nodeid"])
            except (TypeError, ValueError):
                return 999999

        return sorted(nodes, key=sort_key)

    def _profile_allows_selectable_node(self, data, node_type: str):
        """Filtra origens/destinos que nao sao adequados ao perfil ativo."""

        accessibility = str(data.get("accessibility", "1")).strip()
        if self.profile_var.get() == "reduced":
            return accessibility != "2" and node_type != "stairs"
        return accessibility != "3" and node_type != "elevator"

    def _is_exterior_movement_point(self, name: str, floor: str, node_type: str):
        """Remove pontos exteriores usados apenas como nos intermedios do grafo."""

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
        """
        Valida origem/destino, calcula a rota e mostra a primeira instrucao.

        Esta funcao e a ponte principal entre UI e core: recolhe a selecao das
        comboboxes, transforma labels em node_ids e chama `calculate_path`.
        """

        if self.graph is None:
            messagebox.showwarning("Sem mapa", "Carrega primeiro os mapas.")
            return

        # As comboboxes mostram texto legivel; aqui voltamos ao ID interno
        # usado pelo core para calcular caminhos.
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

        # Mesmo que a combobox ja filtre por perfil, mantenho esta validacao
        # antes do Dijkstra para proteger estados manuais/inconsistentes da UI.
        mobility_reduced = self.profile_var.get() == "reduced"
        if not nav.graph_node_allowed_for_profile(self.graph, origin, mobility_reduced):
            messagebox.showwarning(
                "Origem incompatível",
                "A origem escolhida não é adequada ao perfil selecionado.",
            )
            return
        if not nav.graph_node_allowed_for_profile(self.graph, destination, mobility_reduced):
            messagebox.showwarning(
                "Destino incompatível",
                "O destino escolhido não é adequado ao perfil selecionado.",
            )
            return

        path, distance = calculate_path(
            self.graph,
            origin,
            destination,
            mobility_reduced=mobility_reduced,
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
        # Ao calcular, a vista muda automaticamente para onde a rota comeca.
        self.visible_floor_var.set(self.graph.nodes[path[0]].get("floor_key", self.visible_floor_var.get()))
        self.center_map_on_current_step(redraw=False)
        self.status_var.set(
            f"Rota pronta: {distance:.1f} m."
        )
        self._update_step_text()
        self.draw_map()

    def confirm_next_step(self):
        """
        Avanca a navegacao quando o utilizador confirma que chegou ao ponto.

        A app nao assume posicionamento automatico. O utilizador confirma cada
        ponto para receber a proxima indicacao, como definido no objetivo do
        prototipo.
        """

        if self.route is None:
            messagebox.showinfo("Sem rota", "Calcula primeiro uma rota.")
            return

        if self.route.current_index >= len(self.route.path) - 1:
            self.step_var.set("Chegaste ao destino.")
            return

        self.route.current_index = self._next_navigation_index()
        current = self.route.path[self.route.current_index]
        # Se o proximo ponto estiver noutro piso, a vista acompanha a rota.
        self.visible_floor_var.set(self.graph.nodes[current].get("floor_key", self.visible_floor_var.get()))
        self.center_map_on_current_step(redraw=False)
        self._update_step_text()
        self.draw_map()

    def _next_navigation_index(self):
        """Escolhe o proximo indice da rota a apresentar ao utilizador."""

        if self.route is None:
            return 0

        path = self.route.path
        index = self.route.current_index
        if index >= len(path) - 1:
            return index

        edge = self.graph[path[index]][path[index + 1]]
        if edge.get("vertical") and edge.get("edge_type") == "elevator":
            # Um elevador pode atravessar varios pisos. Para o utilizador isso
            # deve ser uma unica acao: entrar e sair no piso correto.
            while index < len(path) - 1:
                next_edge = self.graph[path[index]][path[index + 1]]
                if not next_edge.get("vertical") or next_edge.get("edge_type") != "elevator":
                    break
                index += 1
            return index

        return index + 1

    def _update_step_text(self):
        """Constroi o texto da instrucao atual na barra lateral."""

        if self.route is None:
            return

        self.step_var.set(nav.navigation_instruction(self.graph, self.route))

    def _elevator_exit_index(self, start_index):
        """Encontra o no onde termina uma sequencia continua de elevador."""

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
        """Formata um no com nodeID e piso para mensagens tecnicas/debug."""

        data = self.graph.nodes[node_id]
        name = data.get("roomname") or data.get("name") or "ponto intermédio"
        nodeid = data.get("nodeid")
        floor = data.get("floor_key", "")
        base = f"nodeID {nodeid} ({name})" if nodeid else name
        return f"{base} - {floor}" if floor else base

    def _navigation_point_name(self, node_id):
        """Formata um no em linguagem simples para as instrucoes da rota."""

        data = self.graph.nodes[node_id]
        name = data.get("roomname") or data.get("name") or "ponto intermédio"
        if data.get("type") == "connection":
            name = "Ponto de passagem"
        floor = data.get("floor_key", "")
        building = data.get("building", "")
        location = " · ".join(part for part in [building, floor] if part)
        return f"{name} ({location})" if location else name

    def _edge_name(self, edge):
        """Traduz o tipo de ligacao vertical para texto em portugues."""

        edge_type = edge.get("edge_type", "")
        if edge_type == "elevator":
            return "o elevador"
        if edge_type == "stairs":
            return "as escadas"
        return "a ligação vertical"

    def draw_map(self):
        """
        Redesenha mapa visivel, grafo, rota e marcadores de navegacao.

        A ordem de desenho e importante: primeiro fundo (tiles/imagem), depois
        grafo, depois rota e marcadores. Assim a rota fica sempre visivel.
        """

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

        # O grafo e desenhado depois do fundo para ficar por cima da imagem/mapa.
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
        """
        Centra a vista no ponto atual da rota, mantendo o zoom escolhido.

        Isto torna a navegacao visual mais pratica: quando o utilizador confirma
        um ponto, o mapa acompanha automaticamente o ponto seguinte da rota.
        """

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
        """Atualiza o zoom usando o centro atual, o cursor ou o ponto da rota."""

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
        """
        Move os limites do eixo enquanto o utilizador arrasta o mapa.

        Como o mapa esta num eixo Matplotlib, arrastar significa deslocar
        `xlim` e `ylim`, nao mover widgets Tkinter.
        """

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
        """Guarda o centro da vista depois de uma interacao manual."""

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

        # So sao pedidos/desenhados os tiles necessarios para cobrir os nos do
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
        """
        Desenha a imagem de piso alinhada com os nos atraves da calibracao.

        O world file e preferido quando existe, porque contem diretamente a
        transformacao afim entre pixel da imagem e coordenada do mapa.
        """

        image = mpimg.imread(image_path)
        world_file = read_world_file(image_path)
        if world_file is not None:
            # World file e a calibracao preferida porque ja da a transformacao
            # direta de pixeis da imagem para coordenadas do mapa.
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
            # Sem calibracao, a imagem e mostrada sem garantia de alinhamento.
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
        """Desenha nos, arestas, labels opcionais e segmentos da rota."""

        if self.graph is None:
            return

        floor_nodes = [
            node_id
            for node_id, data in self.graph.nodes(data=True)
            if data.get("floor_key") == visible_floor
        ]
        floor_node_set = set(floor_nodes)
        # O grafo e convertido para Web Mercator para ficar compativel com
        # tiles OSM e imagens calibradas.
        pos = {
            node_id: lonlat_to_web_mercator(data["lat"], data["lon"])
            for node_id, data in self.graph.nodes(data=True)
            if node_id in floor_node_set
        }

        for node_a, node_b, data in self.graph.edges(data=True):
            if data.get("vertical") or node_a not in floor_node_set or node_b not in floor_node_set:
                continue
            # Arestas verticais entram na rota, mas nao sao desenhadas num piso
            # porque representam mudanca entre mapas diferentes.
            x_values = [pos[node_a][0], pos[node_b][0]]
            y_values = [pos[node_a][1], pos[node_b][1]]
            axis.plot(x_values, y_values, color="#424242", linewidth=1.2, alpha=0.75, zorder=1)

        route_node_set = set(path or [])
        for node_id, (x_value, y_value) in pos.items():
            color = get_node_color(self.graph, node_id)
            size = 18
            alpha = 0.25 if path and node_id not in route_node_set else 0.9
            # A cor do no vem da logica base e distingue salas, corredores,
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
            # Labels ficam opcionais para nao poluir visualmente o mapa.
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
                # Vermelho indica caminho por fazer; verde indica caminho ja
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
        """
        Destaca e numera apenas os pontos que pertencem a rota calculada.

        Os restantes pontos do grafo continuam visiveis de forma discreta, mas a
        rota calculada recebe marcadores numerados para o utilizador perceber a
        sequencia de deslocacao.
        """

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

            # Cores/tamanhos indicam estado do ponto na rota:
            # verde = ja confirmado, amarelo = atual, laranja = proximo,
            # azul = destino final, branco/vermelho = passos futuros.
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
        """Ajusta o enquadramento para incluir nos e cantos da imagem calibrada."""

        points = list(positions.values())
        if image_path and image_path.exists():
            world_file = read_world_file(image_path)
            if world_file is not None:
                # Incluir os cantos da imagem evita que o enquadramento fique
                # apertado apenas nos nos do grafo.
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
        """
        Aplica zoom e centro sem deixar o eixo encolher dentro da figura.

        O Matplotlib tenta preservar proporcoes do eixo. Por isso ajusto a
        largura/altura da vista ao ratio real do canvas, evitando que o mapa
        "encolha" visualmente quando se faz zoom.
        """

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
        """Calcula a proporcao largura/altura da area real onde o mapa e desenhado."""

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

