# -*- coding: utf-8 -*-
"""Protótipo Android em Kivy para navegação pedestre indoor na UTAD."""

from __future__ import annotations

import urllib.error
import urllib.request
import ssl
from pathlib import Path

from kivy.app import App
from kivy.clock import Clock
from kivy.core.image import Image as CoreImage
from kivy.core.text import Label as CoreLabel
from kivy.graphics import Color, Ellipse, Line, Mesh, Rectangle
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.screenmanager import Screen, ScreenManager
from kivy.uix.spinner import Spinner
from kivy.uix.stencilview import StencilView
from kivy.uix.widget import Widget
from kivy.utils import get_color_from_hex

import navigation_core as nav


class Surface(BoxLayout):
    """Painel simples com fundo sólido para separar texto e controlos do mapa."""

    def __init__(self, background="#F7F8FA", **kwargs):
        super().__init__(**kwargs)
        self._background = get_color_from_hex(background)
        with self.canvas.before:
            Color(*self._background)
            self._background_rect = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._update_background, size=self._update_background)

    def _update_background(self, *_args):
        """Mantém o retângulo de fundo alinhado com o tamanho do painel."""

        self._background_rect.pos = self.pos
        self._background_rect.size = self.size


class GraphMapWidget(StencilView):
    """Canvas simples para ver o grafo e a rota no ecrã móvel."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._draw_area = Widget()
        self.add_widget(self._draw_area)
        self._draw_area.pos = self.pos
        self._draw_area.size = self.size
        self.graph = None
        self.route = None
        self.visible_floor = "Exterior"
        self.show_labels = False
        self.map_zoom = 1.0
        self.map_center = None
        self.map_center_floor = None
        self._route_focus_key = None
        self._drag_touch_id = None
        self._drag_start = None
        self._drag_start_center = None
        self._last_world_scale = 1.0
        self._last_view_center = None
        self._texture_cache = {}
        self._image_cache = {}
        self.tile_cache_dir = nav.BASE_DIR / ".tile_cache" / "osm_carto_android"
        self.bind(pos=self._on_geometry_change, size=self._on_geometry_change)

    def _on_geometry_change(self, *_args):
        """Atualiza a área interna e redesenha quando o widget muda de geometria."""

        self._draw_area.pos = self.pos
        self._draw_area.size = self.size
        self.redraw()

    def set_state(self, graph, visible_floor, route=None, show_labels=False):
        """Recebe o estado atual da app e dispara novo desenho do mapa."""

        floor_changed = visible_floor != self.visible_floor
        self.graph = graph
        self.visible_floor = visible_floor
        self.route = route
        self.show_labels = show_labels
        if floor_changed:
            self.map_center = None
            self.map_center_floor = None

        focus_key = self._current_route_focus_key()
        if focus_key is not None and focus_key != self._route_focus_key:
            self.center_on_current_route(redraw=False)
        self._route_focus_key = focus_key
        self.redraw()

    def zoom_in(self):
        """Aproxima a vista mantendo o centro atual do mapa."""

        self._change_zoom(1.1)

    def zoom_out(self):
        """Afasta a vista mantendo o centro atual do mapa."""

        self._change_zoom(1 / 1.1)

    def center_on_current_route(self, redraw=True):
        """Centra o mapa no ponto atual da rota ou no centro do piso se não houver rota."""

        if self.graph is None or self.route is None or not self.route.path:
            self.map_center = None
            self.map_center_floor = None
            if redraw:
                self.redraw()
            return

        current = self.route.path[min(self.route.current_index, len(self.route.path) - 1)]
        data = self.graph.nodes[current]
        self.map_center = nav.lonlat_to_web_mercator(data["lat"], data["lon"])
        self.map_center_floor = data.get("floor_key")
        if redraw:
            self.redraw()

    def _change_zoom(self, multiplier):
        """Atualiza o fator de zoom e redesenha o mapa."""

        if self.map_center is None or self.map_center_floor != self.visible_floor:
            self.map_center = self._last_view_center
            self.map_center_floor = self.visible_floor if self.map_center else None
            if self.map_center is None:
                self.center_on_current_route(redraw=False)

        self.map_zoom = max(0.6, min(8.0, self.map_zoom * multiplier))
        self.redraw()

    def _current_route_focus_key(self):
        """Identifica o ponto atual para recentrar apenas quando a navegação avança."""

        if self.route is None or not self.route.path:
            return None

        current_index = min(self.route.current_index, len(self.route.path) - 1)
        current_node = self.route.path[current_index]
        floor = self.graph.nodes[current_node].get("floor_key") if self.graph else self.visible_floor
        return current_node, current_index, floor

    def redraw(self):
        """Limpa o canvas e desenha mapa, grafo, rota e labels do piso visível."""

        canvas = self._draw_area.canvas
        canvas.clear()
        with canvas:
            Color(0.96, 0.97, 0.98, 1)
            Rectangle(pos=self.pos, size=self.size)

        if self.graph is None or not self.visible_floor:
            self._draw_center_text("Mapa ainda não carregado.")
            return

        floor_nodes = [
            node_id
            for node_id, data in self.graph.nodes(data=True)
            if data.get("floor_key") == self.visible_floor
        ]
        if not floor_nodes:
            self._draw_center_text("Sem dados para este piso.")
            return

        floor_node_set = set(floor_nodes)
        # As coordenadas OSM vêm em latitude/longitude. Para desenhar todos os
        # mapas no mesmo sistema, convertemos para Web Mercator.
        positions = {
            node_id: nav.lonlat_to_web_mercator(data["lat"], data["lon"])
            for node_id, data in self.graph.nodes(data=True)
            if node_id in floor_node_set
        }
        image_path, image_corners = self._floor_image_corners()
        tile_extents = self._exterior_tile_extents(positions)
        tile_points = self._extent_points(tile_extents)
        # A escala do ecrã é calculada com todos os pontos relevantes: nós do
        # grafo, cantos da imagem calibrada e tiles do exterior.
        points = list(positions.values()) + image_corners + tile_points
        world_to_screen = self._world_to_screen_factory(points)
        if world_to_screen is None:
            return

        with canvas:
            if self.visible_floor == "Exterior":
                self._draw_osm_tiles(tile_extents, world_to_screen)

            if image_path and image_corners:
                self._draw_floor_image(image_path, image_corners, world_to_screen)

            self._draw_edges(floor_node_set, positions, world_to_screen)
            self._draw_nodes(positions, world_to_screen)
            self._draw_route(floor_node_set, positions, world_to_screen)

            if self.show_labels:
                self._draw_labels(floor_node_set, positions, world_to_screen)
            if self.visible_floor == "Exterior":
                self._draw_osm_attribution()

    def _floor_image_corners(self):
        """Obtém a imagem calibrada do piso e os seus cantos no mundo real."""

        image_path = nav.FLOOR_IMAGES.get(self.visible_floor)
        if not image_path or not image_path.exists():
            return None, []

        image = self._image_for(image_path)
        world_file = nav.read_world_file(image_path)
        if image is None or world_file is None:
            return None, []

        # A calibração foi feita com os píxeis reais da imagem. Usar o tamanho
        # da textura pode introduzir erro em Android se o backend ajustar a
        # textura internamente.
        return image_path, nav.world_file_corners(world_file, image.size)

    def _image_for(self, image_path):
        """Carrega e guarda CoreImage para obter textura e tamanho real."""

        key = str(image_path)
        if key not in self._image_cache:
            try:
                self._image_cache[key] = CoreImage(key)
            except Exception:
                self._image_cache[key] = None
        return self._image_cache[key]

    def _texture_for(self, image_path):
        """Devolve a textura Kivy associada a uma imagem carregada em cache."""

        image = self._image_for(image_path)
        return image.texture if image is not None else None

    def _world_to_screen_factory(self, points):
        """Cria uma função que transforma coordenadas do mapa em píxeis no ecrã."""

        if not points:
            return None

        x_values = [point[0] for point in points]
        y_values = [point[1] for point in points]
        min_x, max_x = min(x_values), max(x_values)
        min_y, max_y = min(y_values), max(y_values)
        range_x = max(max_x - min_x, 1)
        range_y = max(max_y - min_y, 1)
        margin = dp(18)
        usable_width = max(self.width - margin * 2, 1)
        usable_height = max(self.height - margin * 2, 1)
        target_ratio = usable_width / usable_height
        view_width = range_x
        view_height = range_y
        current_ratio = view_width / view_height if view_height else target_ratio
        if current_ratio < target_ratio:
            view_width = view_height * target_ratio
        elif current_ratio > target_ratio:
            view_height = view_width / target_ratio

        center = self.map_center
        if self.map_center_floor != self.visible_floor:
            center = None
        if center is None:
            center = ((min_x + max_x) / 2, (min_y + max_y) / 2)

        view_width /= self.map_zoom
        view_height /= self.map_zoom
        min_view_x = center[0] - view_width / 2
        min_view_y = center[1] - view_height / 2
        scale = min(usable_width / view_width, usable_height / view_height)
        offset_x = self.x + margin - min_view_x * scale
        offset_y = self.y + margin - min_view_y * scale
        self._last_world_scale = scale
        self._last_view_center = center

        def world_to_screen(point):
            return offset_x + point[0] * scale, offset_y + point[1] * scale

        return world_to_screen

    def on_touch_down(self, touch):
        """Começa o arrasto do mapa quando o utilizador toca dentro do widget."""

        if not self.collide_point(*touch.pos):
            return super().on_touch_down(touch)
        if self._drag_touch_id is not None:
            return super().on_touch_down(touch)

        self._drag_touch_id = touch.uid
        self._drag_start = touch.pos
        self._drag_start_center = self.map_center or self._last_view_center
        self.map_center_floor = self.visible_floor
        return True

    def on_touch_move(self, touch):
        """Desloca a vista do mapa enquanto o dedo/mouse é arrastado."""

        if touch.uid != self._drag_touch_id or self._drag_start_center is None:
            return super().on_touch_move(touch)

        scale = max(self._last_world_scale, 0.000001)
        delta_x = (touch.x - self._drag_start[0]) / scale
        delta_y = (touch.y - self._drag_start[1]) / scale
        self.map_center = (
            self._drag_start_center[0] - delta_x,
            self._drag_start_center[1] - delta_y,
        )
        self.map_center_floor = self.visible_floor
        self.redraw()
        return True

    def on_touch_up(self, touch):
        """Termina o arrasto atual do mapa."""

        if touch.uid != self._drag_touch_id:
            return super().on_touch_up(touch)

        self._drag_touch_id = None
        self._drag_start = None
        self._drag_start_center = None
        return True

    def _point_inside_map(self, point, inset=0):
        """Confirma se um ponto de ecrã está dentro da caixa visível do mapa."""

        x_value, y_value = point
        return (
            self.x + inset <= x_value <= self.right - inset
            and self.y + inset <= y_value <= self.top - inset
        )

    def _clip_segment_to_map(self, start, end):
        """Corta uma linha ao retângulo do mapa para impedir desenho fora da caixa."""

        min_x, min_y = self.x, self.y
        max_x, max_y = self.right, self.top
        x1, y1 = start
        x2, y2 = end
        dx = x2 - x1
        dy = y2 - y1
        t0 = 0.0
        t1 = 1.0

        for edge_delta, edge_distance in (
            (-dx, x1 - min_x),
            (dx, max_x - x1),
            (-dy, y1 - min_y),
            (dy, max_y - y1),
        ):
            if edge_delta == 0:
                if edge_distance < 0:
                    return None
                continue

            ratio = edge_distance / edge_delta
            if edge_delta < 0:
                if ratio > t1:
                    return None
                if ratio > t0:
                    t0 = ratio
            else:
                if ratio < t0:
                    return None
                if ratio < t1:
                    t1 = ratio

        clipped_start = (x1 + t0 * dx, y1 + t0 * dy)
        clipped_end = (x1 + t1 * dx, y1 + t1 * dy)
        return clipped_start, clipped_end

    def _draw_floor_image(self, image_path, corners, world_to_screen):
        """Desenha uma imagem calibrada do piso como malha de quatro cantos."""

        texture = self._texture_for(image_path)
        if texture is None:
            return

        screen_corners = [world_to_screen(point) for point in corners]
        # `texture.tex_coords` respeita eventuais inversões internas feitas pelo
        # loader Kivy. A ordem dos cantos do world file é:
        # top-left, top-right, bottom-right, bottom-left.
        tex = texture.tex_coords
        tex_coords = [
            (tex[6], tex[7]),
            (tex[4], tex[5]),
            (tex[2], tex[3]),
            (tex[0], tex[1]),
        ]
        vertices = []
        for point, tex_coord in zip(screen_corners, tex_coords):
            vertices.extend([point[0], point[1], tex_coord[0], tex_coord[1]])

        Color(1, 1, 1, 1)
        Mesh(
            vertices=vertices,
            indices=[0, 1, 2, 2, 3, 0],
            mode="triangles",
            texture=texture,
        )

    def _exterior_tile_extents(self, positions):
        """Calcula que tiles OpenStreetMap são necessários para cobrir o exterior."""

        if self.visible_floor != "Exterior" or not positions:
            return []

        x_values = [point[0] for point in positions.values()]
        y_values = [point[1] for point in positions.values()]
        margin = 90
        min_x = min(x_values) - margin
        max_x = max(x_values) + margin
        min_y = min(y_values) - margin
        max_y = max(y_values) + margin

        min_tile_x, max_tile_y = nav.web_mercator_to_tile(min_x, min_y, nav.EXTERIOR_TILE_ZOOM)
        max_tile_x, min_tile_y = nav.web_mercator_to_tile(max_x, max_y, nav.EXTERIOR_TILE_ZOOM)

        extents = []
        for tile_x in range(min_tile_x, max_tile_x + 1):
            for tile_y in range(min_tile_y, max_tile_y + 1):
                extents.append(
                    (
                        tile_x,
                        tile_y,
                        nav.tile_web_mercator_extent(tile_x, tile_y, nav.EXTERIOR_TILE_ZOOM),
                    )
                )
        return extents

    def _extent_points(self, tile_extents):
        """Transforma caixas de tiles em pontos usados para calcular o enquadramento."""

        points = []
        for _tile_x, _tile_y, extent in tile_extents:
            min_x, max_x, min_y, max_y = extent
            points.extend([(min_x, min_y), (max_x, max_y)])
        return points

    def _draw_osm_tiles(self, tile_extents, world_to_screen):
        """Desenha os tiles OpenStreetMap do exterior no canvas."""

        for tile_x, tile_y, extent in tile_extents:
            tile_path = self._cached_osm_tile(tile_x, tile_y, nav.EXTERIOR_TILE_ZOOM)
            if tile_path is None:
                continue

            texture = self._texture_for(tile_path)
            if texture is None:
                continue

            min_x, max_x, min_y, max_y = extent
            left, bottom = world_to_screen((min_x, min_y))
            right, top = world_to_screen((max_x, max_y))
            Color(1, 1, 1, 1)
            Rectangle(
                texture=texture,
                pos=(left, bottom),
                size=(right - left, top - bottom),
            )

    def _cached_osm_tile(self, tile_x: int, tile_y: int, zoom: int):
        """Obtém um tile do cache local ou descarrega-o da API pública OSM."""

        tile_path = self.tile_cache_dir / str(zoom) / str(tile_x) / f"{tile_y}.png"
        if tile_path.exists():
            return tile_path

        tile_path.parent.mkdir(parents=True, exist_ok=True)
        url = nav.OSM_TILE_URL.format(z=zoom, x=tile_x, y=tile_y)
        request = urllib.request.Request(
            url,
            headers={"User-Agent": nav.OSM_TILE_USER_AGENT},
        )
        try:
            # Em alguns builds Android, o Python consegue usar SSL mas não
            # encontra a store de certificados do sistema. Para o protótipo,
            # aceitamos a tile HTTPS sem validação para não perder o fundo OSM.
            context = ssl._create_unverified_context() if url.startswith("https://") else None
            with urllib.request.urlopen(request, timeout=8, context=context) as response:
                tile_path.write_bytes(response.read())
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            print(f"Erro ao descarregar tile OSM por HTTPS {zoom}/{tile_x}/{tile_y}: {error}")
            fallback_url = url.replace("https://", "http://", 1)
            fallback_request = urllib.request.Request(
                fallback_url,
                headers={"User-Agent": nav.OSM_TILE_USER_AGENT},
            )
            try:
                with urllib.request.urlopen(fallback_request, timeout=8) as response:
                    tile_path.write_bytes(response.read())
            except (urllib.error.URLError, TimeoutError, OSError) as fallback_error:
                print(f"Erro ao descarregar tile OSM por HTTP {zoom}/{tile_x}/{tile_y}: {fallback_error}")
                return None

        return tile_path

    def _draw_edges(self, floor_node_set, positions, world_to_screen):
        """Desenha as arestas normais do grafo no piso visível."""

        Color(0.18, 0.19, 0.20, 0.65)
        for node_a, node_b, data in self.graph.edges(data=True):
            if data.get("vertical") or node_a not in floor_node_set or node_b not in floor_node_set:
                continue
            # Arestas verticais ligam pisos diferentes e por isso entram no
            # cálculo da rota, mas não são desenhadas como linha neste piso.
            start = world_to_screen(positions[node_a])
            end = world_to_screen(positions[node_b])
            clipped = self._clip_segment_to_map(start, end)
            if clipped is None:
                continue
            (x1, y1), (x2, y2) = clipped
            Line(points=[x1, y1, x2, y2], width=dp(1.1))

    def _draw_route(self, floor_node_set, positions, world_to_screen):
        """Desenha a rota atual, destacando progresso, origem, destino e ponto atual."""

        if self.route is None or len(self.route.path) < 2:
            return

        for index in range(len(self.route.path) - 1):
            node_a = self.route.path[index]
            node_b = self.route.path[index + 1]
            if node_a not in floor_node_set or node_b not in floor_node_set:
                continue

            # Verde: segmento já confirmado. Vermelho: segmento ainda por fazer.
            color = "#43A047" if index < self.route.current_index else "#D32F2F"
            Color(*get_color_from_hex(color))
            start = world_to_screen(positions[node_a])
            end = world_to_screen(positions[node_b])
            clipped = self._clip_segment_to_map(start, end)
            if clipped is None:
                continue
            (x1, y1), (x2, y2) = clipped
            Line(points=[x1, y1, x2, y2], width=dp(3.2))

        origin = self.route.path[0]
        destination = self.route.path[-1]
        if origin in floor_node_set:
            self._draw_marker(world_to_screen(positions[origin]), "#43A047", dp(9))
        if destination in floor_node_set:
            self._draw_marker(world_to_screen(positions[destination]), "#1E88E5", dp(9))
        current = self.route.path[self.route.current_index]
        if current in floor_node_set:
            self._draw_marker(world_to_screen(positions[current]), "#FBC02D", dp(11))

    def _draw_nodes(self, positions, world_to_screen):
        """Desenha todos os nós do piso visível com a cor definida no core."""

        for node_id, point in positions.items():
            self._draw_marker(
                world_to_screen(point),
                nav.node_color(self.graph, node_id),
                dp(4.5),
            )

    def _draw_marker(self, point, color, radius):
        """Desenha um círculo com contorno branco para representar um nó/marcador."""

        if not self._point_inside_map(point, inset=radius):
            return

        Color(*get_color_from_hex(color))
        Ellipse(pos=(point[0] - radius, point[1] - radius), size=(radius * 2, radius * 2))
        Color(1, 1, 1, 0.85)
        Line(circle=(point[0], point[1], radius), width=dp(0.8))

    def _draw_labels(self, floor_node_set, positions, world_to_screen):
        """Desenha labels opcionais dos nós quando o utilizador ativa a checkbox."""

        for node_id, point in positions.items():
            if node_id not in floor_node_set:
                continue
            data = self.graph.nodes[node_id]
            if not (data.get("roomname") or data.get("name")):
                continue
            x_value, y_value = world_to_screen(point)
            self._draw_text(nav.node_label(self.graph, node_id, show_nodeid=True), x_value, y_value + dp(8))

    def _draw_center_text(self, text):
        """Mostra uma mensagem centrada quando não há mapa ou dados disponíveis."""

        self._draw_text(text, self.center_x, self.center_y)

    def _draw_text(self, text, x_value, y_value):
        """Desenha texto no canvas usando CoreLabel, com fundo branco discreto."""

        if not self._point_inside_map((x_value, y_value)):
            return

        label = CoreLabel(text=text, font_size=sp(11), color=(0.04, 0.04, 0.04, 1))
        label.refresh()
        texture = label.texture
        width, height = texture.size
        with self._draw_area.canvas:
            Color(1, 1, 1, 0.76)
            Rectangle(
                pos=(x_value - width / 2 - dp(3), y_value - dp(2)),
                size=(width + dp(6), height + dp(4)),
            )
            Color(1, 1, 1, 1)
            Rectangle(
                texture=texture,
                pos=(x_value - width / 2, y_value),
                size=texture.size,
            )

    def _draw_osm_attribution(self):
        """Mostra a atribuição obrigatória dos tiles OpenStreetMap."""

        label = CoreLabel(
            text="© OpenStreetMap contributors",
            font_size=sp(10),
            color=(0.04, 0.04, 0.04, 1),
        )
        label.refresh()
        texture = label.texture
        padding = dp(5)
        x_value = self.right - texture.size[0] - padding * 2
        y_value = self.y + padding
        with self._draw_area.canvas:
            Color(1, 1, 1, 0.78)
            Rectangle(
                pos=(x_value - padding, y_value - padding / 2),
                size=(texture.size[0] + padding * 2, texture.size[1] + padding),
            )
            Color(1, 1, 1, 1)
            Rectangle(texture=texture, pos=(x_value, y_value), size=texture.size)


class _LegacyAndroidNavigationApp(App):
    """Primeira versão da app num único ecrã, mantida como referência."""

    title = "Navegação UTAD"

    def build(self):
        """Monta a interface antiga: controlos, mapa, instruções e botões."""

        self.graph = None
        self.floor_graphs = {}
        self.selectable_nodes = []
        self.route = None
        self._refreshing_options = False

        root = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(8))

        title = Label(
            text="Navegação Pedestre UTAD",
            bold=True,
            font_size=sp(20),
            size_hint_y=None,
            height=dp(34),
        )
        root.add_widget(title)

        root.add_widget(self._build_controls())

        self.map_widget = GraphMapWidget(size_hint_y=1)
        self.map_widget.tile_cache_dir = Path(self.user_data_dir) / "osm_carto_tiles"
        root.add_widget(self.map_widget)

        instruction_panel = Surface(
            orientation="vertical",
            background="#F7F8FA",
            size_hint_y=None,
            height=dp(150),
            padding=dp(10),
        )
        self.navigation_label = Label(
            text="A carregar mapas...",
            color=get_color_from_hex("#111111"),
            halign="left",
            valign="top",
            size_hint_y=None,
            height=dp(132),
        )
        self.navigation_label.bind(
            size=lambda instance, value: setattr(instance, "text_size", value),
        )
        instruction_panel.add_widget(self.navigation_label)
        root.add_widget(instruction_panel)

        buttons = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        buttons.add_widget(Button(text="Calcular rota", on_release=lambda *_: self.calculate_route()))
        buttons.add_widget(Button(text="Confirmar chegada", on_release=lambda *_: self.confirm_arrival()))
        root.add_widget(buttons)

        Clock.schedule_once(lambda *_: self.load_campus(), 0)
        return root

    def _build_controls(self):
        """Cria os controlos antigos de perfil, origem, destino e piso visível."""

        scroll = ScrollView(size_hint_y=None, height=dp(284), do_scroll_x=False)
        grid = GridLayout(cols=2, spacing=dp(6), size_hint_y=None)
        grid.bind(minimum_height=grid.setter("height"))
        scroll.add_widget(grid)

        self.profile_spinner = self._add_spinner(
            grid,
            "Perfil",
            ["Normal", "Mobilidade reduzida"],
            "Normal",
        )
        self.origin_building_spinner = self._add_spinner(grid, "Edifício origem", [], "")
        self.origin_floor_spinner = self._add_spinner(grid, "Piso origem", [], "")
        self.origin_spinner = self._add_spinner(grid, "Origem", [], "")
        self.destination_building_spinner = self._add_spinner(grid, "Edifício destino", [], "")
        self.destination_floor_spinner = self._add_spinner(grid, "Piso destino", [], "")
        self.destination_spinner = self._add_spinner(grid, "Destino", [], "")
        self.visible_floor_spinner = self._add_spinner(grid, "Mapa visível", nav.APP_FLOORS, "Exterior")

        grid.add_widget(Label(text="Mostrar labels", halign="left", size_hint_y=None, height=dp(38)))
        self.show_labels_checkbox = CheckBox(size_hint_y=None, height=dp(38))
        grid.add_widget(self.show_labels_checkbox)

        for spinner in [
            self.origin_building_spinner,
            self.origin_floor_spinner,
            self.origin_spinner,
            self.destination_building_spinner,
            self.destination_floor_spinner,
            self.destination_spinner,
            self.visible_floor_spinner,
        ]:
            spinner.bind(text=lambda *_: self.on_selection_changed())
        self.show_labels_checkbox.bind(active=lambda *_: self.refresh_map())

        return scroll

    def _add_spinner(self, parent, label_text, values, text):
        """Adiciona uma label e uma Spinner ao layout indicado."""

        parent.add_widget(Label(text=label_text, halign="left", size_hint_y=None, height=dp(38)))
        spinner = Spinner(
            text=text or "-",
            values=values,
            size_hint_y=None,
            height=dp(38),
        )
        parent.add_widget(spinner)
        return spinner

    def _fit_label_height(self, instance, texture_size):
        """Ajusta a altura de uma label ao texto que ela contém."""

        instance.height = max(dp(112), texture_size[1] + dp(18))

    def load_campus(self):
        """Carrega os grafos OSM e prepara as listas de pontos selecionáveis."""

        try:
            self.graph, self.floor_graphs = nav.build_campus_graph()
            self.selectable_nodes = nav.collect_selectable_nodes(self.graph)
        except Exception as error:
            self.navigation_label.text = f"Erro ao carregar mapas:\n{error}"
            return

        self.route = None
        self.refresh_option_lists(force_defaults=True)
        self.navigation_label.text = "Escolhe a origem e o destino para começar."
        self.refresh_map()

    def on_selection_changed(self):
        """Reage a alterações nas spinners e atualiza listas/mapa."""

        if self._refreshing_options:
            return
        if not self.selectable_nodes:
            return
        self.refresh_option_lists(force_defaults=False)
        if self.route is None and self.origin_floor_spinner.text in nav.APP_FLOORS:
            self._set_spinner_text_silently(self.visible_floor_spinner, self.origin_floor_spinner.text)
        self.refresh_map()

    def refresh_option_lists(self, force_defaults=False):
        """Atualiza edifícios, pisos e pontos disponíveis nos controlos antigos."""

        self._refreshing_options = True
        try:
            buildings = nav.available_buildings(self.selectable_nodes)
            self._set_spinner_values(
                self.origin_building_spinner,
                buildings,
                "ECT2" if "ECT2" in buildings else None,
                force_defaults,
            )
            self._set_spinner_values(
                self.destination_building_spinner,
                buildings,
                "ECT2" if "ECT2" in buildings else None,
                force_defaults,
            )

            self._refresh_floor_spinner(self.origin_building_spinner, self.origin_floor_spinner, force_defaults)
            self._refresh_floor_spinner(
                self.destination_building_spinner,
                self.destination_floor_spinner,
                force_defaults,
            )
            self._refresh_node_spinner(
                self.origin_building_spinner,
                self.origin_floor_spinner,
                self.origin_spinner,
                force_defaults,
                choose_last=False,
            )
            self._refresh_node_spinner(
                self.destination_building_spinner,
                self.destination_floor_spinner,
                self.destination_spinner,
                force_defaults,
                choose_last=True,
            )
        finally:
            self._refreshing_options = False

    def _refresh_floor_spinner(self, building_spinner, floor_spinner, force_defaults):
        """Mostra apenas pisos existentes para o edifício selecionado."""

        floors = nav.available_floors(self.selectable_nodes, building_spinner.text)
        floor_spinner.disabled = len(floors) <= 1
        default = "Piso1" if "Piso1" in floors else (floors[0] if floors else None)
        self._set_spinner_values(floor_spinner, floors, default, force_defaults)

    def _refresh_node_spinner(
        self,
        building_spinner,
        floor_spinner,
        node_spinner,
        force_defaults,
        choose_last,
    ):
        """Mostra apenas salas/entradas existentes no edifício e piso escolhidos."""

        nodes = nav.nodes_for(self.selectable_nodes, building_spinner.text, floor_spinner.text)
        labels = [node.label for node in nodes]
        default = labels[-1] if choose_last and labels else labels[0] if labels else None
        self._set_spinner_values(node_spinner, labels, default, force_defaults)

    def _set_spinner_values(self, spinner, values, default=None, force_defaults=False):
        """Define opções de uma Spinner e escolhe um valor válido."""

        spinner.values = values
        if not values:
            spinner.text = "-"
            return
        if force_defaults or spinner.text not in values:
            spinner.text = default if default in values else values[0]

    def _set_spinner_text_silently(self, spinner, value):
        """Altera uma Spinner sem disparar uma cascata de atualizações."""

        self._refreshing_options = True
        try:
            spinner.text = value
        finally:
            self._refreshing_options = False

    def calculate_route(self):
        """Calcula rota na versão antiga e mostra a primeira instrução."""

        if self.graph is None:
            self.navigation_label.text = "Os mapas ainda não foram carregados."
            return

        origin = nav.find_selectable_node(
            self.selectable_nodes,
            self.origin_spinner.text,
            self.origin_building_spinner.text,
            self.origin_floor_spinner.text,
        )
        destination = nav.find_selectable_node(
            self.selectable_nodes,
            self.destination_spinner.text,
            self.destination_building_spinner.text,
            self.destination_floor_spinner.text,
        )
        if origin is None or destination is None:
            self.navigation_label.text = "Escolhe uma origem e um destino válidos."
            return

        path, distance = nav.calculate_path(
            self.graph,
            origin,
            destination,
            mobility_reduced=self.profile_spinner.text == "Mobilidade reduzida",
        )
        if not path:
            self.route = None
            self.navigation_label.text = "Não foi possível encontrar uma rota para essas opções."
            self.refresh_map()
            return

        self.route = nav.RouteState(graph=self.graph, path=path, distance=distance)
        self.visible_floor_spinner.text = self.graph.nodes[path[0]].get("floor_key", self.visible_floor_spinner.text)
        self.navigation_label.text = nav.navigation_instruction(self.graph, self.route)
        self.refresh_map()

    def confirm_arrival(self):
        """Avança para o próximo ponto da rota na versão antiga."""

        if self.route is None:
            self.navigation_label.text = "Calcula primeiro uma rota."
            return
        if self.route.current_index >= len(self.route.path) - 1:
            self.navigation_label.text = "Chegaste ao destino."
            return

        self.route.current_index = nav.next_navigation_index(self.graph, self.route)
        current = self.route.path[self.route.current_index]
        self.visible_floor_spinner.text = self.graph.nodes[current].get("floor_key", self.visible_floor_spinner.text)
        self.navigation_label.text = nav.navigation_instruction(self.graph, self.route)
        self.refresh_map()

    def refresh_map(self):
        """Atualiza o GraphMapWidget da interface antiga."""

        if not hasattr(self, "map_widget"):
            return
        self.map_widget.set_state(
            self.graph,
            self.visible_floor_spinner.text,
            self.route,
            self.show_labels_checkbox.active,
        )


class AndroidNavigationApp(App):
    """Aplicação Android atual com ecrã de perfil, planeamento e navegação."""

    title = "Navegação UTAD"

    def build(self):
        """Inicializa estado global, cria os ecrãs e agenda o carregamento dos mapas."""

        self.graph = None
        self.floor_graphs = {}
        self.selectable_nodes = []
        self.route = None
        self.profile = None
        self._refreshing_options = False

        self.screen_manager = ScreenManager()
        self.screen_manager.add_widget(self._build_profile_screen())
        self.screen_manager.add_widget(self._build_planner_screen())
        self.screen_manager.add_widget(self._build_navigation_screen())

        Clock.schedule_once(lambda *_: self.load_campus(), 0)
        return self.screen_manager

    def _build_profile_screen(self):
        """Cria o primeiro ecrã, onde o utilizador escolhe o tipo de perfil."""

        screen = Screen(name="profile")
        root = BoxLayout(orientation="vertical", spacing=dp(14), padding=dp(22))
        screen.add_widget(root)

        root.add_widget(Widget(size_hint_y=0.16))
        root.add_widget(
            Label(
                text="Navegação Pedestre UTAD",
                bold=True,
                font_size=sp(24),
                size_hint_y=None,
                height=dp(42),
            )
        )
        root.add_widget(
            Label(
                text="Escolhe o teu perfil para calcular percursos adequados.",
                halign="center",
                valign="middle",
                size_hint_y=None,
                height=dp(70),
            )
        )
        root.add_widget(
            Button(
                text="Normal",
                size_hint_y=None,
                height=dp(58),
                on_release=lambda *_: self.select_profile("Normal"),
            )
        )
        root.add_widget(
            Button(
                text="Mobilidade reduzida",
                size_hint_y=None,
                height=dp(58),
                on_release=lambda *_: self.select_profile("Mobilidade reduzida"),
            )
        )

        self.profile_status_label = Label(
            text="A carregar mapas...",
            halign="center",
            valign="middle",
            size_hint_y=None,
            height=dp(80),
        )
        self.profile_status_label.bind(
            width=lambda instance, value: setattr(instance, "text_size", (value, None)),
        )
        root.add_widget(self.profile_status_label)
        root.add_widget(Widget())
        return screen

    def _build_planner_screen(self):
        """Cria o ecrã de escolha de origem/destino e pré-visualização do mapa."""

        screen = Screen(name="planner")
        root = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(8))
        screen.add_widget(root)

        top_bar = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        self.planner_profile_label = Label(
            text="Perfil: por escolher",
            halign="left",
            valign="middle",
        )
        self.planner_profile_label.bind(
            width=lambda instance, value: setattr(instance, "text_size", (value, None)),
        )
        top_bar.add_widget(self.planner_profile_label)
        top_bar.add_widget(
            Button(
                text="Alterar",
                size_hint_x=None,
                width=dp(96),
                on_release=lambda *_: self.change_profile(),
            )
        )
        root.add_widget(top_bar)

        root.add_widget(self._build_location_controls())

        self.planner_map_widget = GraphMapWidget(size_hint_y=1)
        self.planner_map_widget.tile_cache_dir = Path(self.user_data_dir) / "osm_carto_tiles"
        root.add_widget(self.planner_map_widget)
        root.add_widget(self._build_map_zoom_controls(self.planner_map_widget))

        self.planner_status_label = Label(
            text="Escolhe a origem e o destino para começar.",
            halign="left",
            valign="middle",
            size_hint_y=None,
            height=dp(46),
        )
        self.planner_status_label.bind(
            width=lambda instance, value: setattr(instance, "text_size", (value, None)),
        )
        root.add_widget(self.planner_status_label)
        root.add_widget(
            Button(
                text="Calcular rota",
                size_hint_y=None,
                height=dp(50),
                on_release=lambda *_: self.calculate_route(),
            )
        )
        return screen

    def _build_navigation_screen(self):
        """Cria o ecrã de navegação passo a passo após calcular uma rota."""

        screen = Screen(name="navigation")
        root = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(8))
        screen.add_widget(root)

        top_bar = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        self.route_summary_label = Label(
            text="Rota",
            halign="left",
            valign="middle",
        )
        self.route_summary_label.bind(
            width=lambda instance, value: setattr(instance, "text_size", (value, None)),
        )
        top_bar.add_widget(self.route_summary_label)
        top_bar.add_widget(
            Button(
                text="Cancelar",
                size_hint_x=None,
                width=dp(104),
                on_release=lambda *_: self.cancel_route(),
            )
        )
        root.add_widget(top_bar)

        instruction_panel = Surface(
            orientation="vertical",
            background="#F7F8FA",
            size_hint_y=None,
            height=dp(150),
            padding=dp(10),
        )
        self.navigation_label = Label(
            text="Calcula uma rota para começar.",
            color=get_color_from_hex("#111111"),
            halign="left",
            valign="top",
            size_hint_y=None,
            height=dp(132),
        )
        self.navigation_label.bind(
            size=lambda instance, value: setattr(instance, "text_size", value),
        )
        instruction_panel.add_widget(self.navigation_label)
        root.add_widget(instruction_panel)

        self.route_map_widget = GraphMapWidget(size_hint_y=1)
        self.route_map_widget.tile_cache_dir = Path(self.user_data_dir) / "osm_carto_tiles"
        root.add_widget(self.route_map_widget)
        root.add_widget(self._build_map_zoom_controls(self.route_map_widget))

        buttons = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(8))
        buttons.add_widget(Button(text="Próximo ponto", on_release=lambda *_: self.confirm_arrival()))
        buttons.add_widget(Button(text="Cancelar", on_release=lambda *_: self.cancel_route()))
        root.add_widget(buttons)
        return screen

    def _build_map_zoom_controls(self, map_widget):
        """Cria controlos simples de zoom para o mapa indicado."""

        controls = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(6))
        controls.add_widget(Button(text="-", on_release=lambda *_: map_widget.zoom_out()))
        controls.add_widget(Button(text="+", on_release=lambda *_: map_widget.zoom_in()))
        controls.add_widget(Button(text="Centrar", on_release=lambda *_: map_widget.center_on_current_route()))
        return controls

    def _build_location_controls(self):
        """Cria os controlos de edifício, piso, sala/entrada e mapa visível."""

        scroll = ScrollView(size_hint_y=None, height=dp(250), do_scroll_x=False)
        grid = GridLayout(cols=2, spacing=dp(6), size_hint_y=None)
        grid.bind(minimum_height=grid.setter("height"))
        scroll.add_widget(grid)

        self.origin_building_spinner = self._add_spinner(grid, "Edifício origem", [], "")
        self.origin_floor_spinner = self._add_spinner(grid, "Piso origem", [], "")
        self.origin_spinner = self._add_spinner(grid, "Origem", [], "")
        self.destination_building_spinner = self._add_spinner(grid, "Edifício destino", [], "")
        self.destination_floor_spinner = self._add_spinner(grid, "Piso destino", [], "")
        self.destination_spinner = self._add_spinner(grid, "Destino", [], "")
        self.visible_floor_spinner = self._add_spinner(grid, "Mapa visível", nav.APP_FLOORS, "Exterior")

        grid.add_widget(Label(text="Mostrar labels", halign="left", size_hint_y=None, height=dp(38)))
        self.show_labels_checkbox = CheckBox(size_hint_y=None, height=dp(38))
        grid.add_widget(self.show_labels_checkbox)

        for spinner in [
            self.origin_building_spinner,
            self.origin_floor_spinner,
            self.origin_spinner,
            self.destination_building_spinner,
            self.destination_floor_spinner,
            self.destination_spinner,
        ]:
            spinner.bind(text=lambda *_: self.on_selection_changed())
        self.visible_floor_spinner.bind(text=lambda *_: self.refresh_maps())
        self.show_labels_checkbox.bind(active=lambda *_: self.refresh_maps())
        return scroll

    def _add_spinner(self, parent, label_text, values, text):
        """Adiciona uma linha de seleção ao formulário da app."""

        parent.add_widget(Label(text=label_text, halign="left", size_hint_y=None, height=dp(38)))
        spinner = Spinner(
            text=text or "-",
            values=values,
            size_hint_y=None,
            height=dp(38),
        )
        parent.add_widget(spinner)
        return spinner

    def _fit_label_height(self, instance, texture_size):
        """Ajusta altura de uma label quando o texto ocupa várias linhas."""

        instance.height = max(dp(112), texture_size[1] + dp(18))

    def load_campus(self):
        """Carrega o grafo global e os pontos que podem ser escolhidos pelo utilizador."""

        try:
            self.graph, self.floor_graphs = nav.build_campus_graph()
            self.selectable_nodes = nav.collect_selectable_nodes(self.graph)
        except Exception as error:
            error_text = f"Erro ao carregar mapas:\n{error}"
            self.profile_status_label.text = error_text
            self.planner_status_label.text = error_text
            self.navigation_label.text = error_text
            return

        self.route = None
        self.refresh_option_lists(force_defaults=True)
        self.profile_status_label.text = "Mapas carregados. Escolhe o perfil para continuar."
        self.planner_status_label.text = "Escolhe a origem e o destino para começar."
        self.refresh_maps()

    def select_profile(self, profile):
        """Guarda o perfil escolhido e avança para o ecrã de planeamento."""

        self.profile = profile
        self._sync_profile_labels()
        self.screen_manager.current = "planner"
        self.refresh_maps()

    def change_profile(self):
        """Volta ao ecrã inicial para permitir alterar o perfil."""

        self.screen_manager.current = "profile"

    def _sync_profile_labels(self):
        """Atualiza textos que mostram o perfil atualmente selecionado."""

        profile = self.profile or "por escolher"
        self.planner_profile_label.text = f"Perfil: {profile}"
        self.route_summary_label.text = f"Perfil: {profile}"

    def on_selection_changed(self):
        """Atualiza opções dependentes quando muda edifício, piso ou ponto."""

        if self._refreshing_options:
            return
        if not self.selectable_nodes:
            return
        self.refresh_option_lists(force_defaults=False)
        if self.route is None and self.origin_floor_spinner.text in nav.APP_FLOORS:
            self._set_spinner_text_silently(self.visible_floor_spinner, self.origin_floor_spinner.text)
        self.refresh_maps()

    def refresh_option_lists(self, force_defaults=False):
        """Reconstrói as opções das spinners com base no estado atual."""

        self._refreshing_options = True
        try:
            buildings = nav.available_buildings(self.selectable_nodes)
            self._set_spinner_values(
                self.origin_building_spinner,
                buildings,
                "ECT2" if "ECT2" in buildings else None,
                force_defaults,
            )
            self._set_spinner_values(
                self.destination_building_spinner,
                buildings,
                "ECT2" if "ECT2" in buildings else None,
                force_defaults,
            )

            self._refresh_floor_spinner(self.origin_building_spinner, self.origin_floor_spinner, force_defaults)
            self._refresh_floor_spinner(
                self.destination_building_spinner,
                self.destination_floor_spinner,
                force_defaults,
            )
            self._refresh_node_spinner(
                self.origin_building_spinner,
                self.origin_floor_spinner,
                self.origin_spinner,
                force_defaults,
                choose_last=False,
            )
            self._refresh_node_spinner(
                self.destination_building_spinner,
                self.destination_floor_spinner,
                self.destination_spinner,
                force_defaults,
                choose_last=True,
            )
        finally:
            self._refreshing_options = False

    def _refresh_floor_spinner(self, building_spinner, floor_spinner, force_defaults):
        """Atualiza a lista de pisos disponíveis para um edifício."""

        floors = nav.available_floors(self.selectable_nodes, building_spinner.text)
        floor_spinner.disabled = len(floors) <= 1
        default = "Piso1" if "Piso1" in floors else (floors[0] if floors else None)
        self._set_spinner_values(floor_spinner, floors, default, force_defaults)

    def _refresh_node_spinner(
        self,
        building_spinner,
        floor_spinner,
        node_spinner,
        force_defaults,
        choose_last,
    ):
        """Atualiza a lista de salas/entradas para edifício e piso selecionados."""

        nodes = nav.nodes_for(self.selectable_nodes, building_spinner.text, floor_spinner.text)
        labels = [node.label for node in nodes]
        default = labels[-1] if choose_last and labels else labels[0] if labels else None
        self._set_spinner_values(node_spinner, labels, default, force_defaults)

    def _set_spinner_values(self, spinner, values, default=None, force_defaults=False):
        """Define opções de uma Spinner sem deixar texto inválido selecionado."""

        spinner.values = values
        if not values:
            spinner.text = "-"
            return
        if force_defaults or spinner.text not in values:
            spinner.text = default if default in values else values[0]

    def _set_spinner_text_silently(self, spinner, value):
        """Altera uma Spinner evitando recursão nos callbacks de seleção."""

        self._refreshing_options = True
        try:
            spinner.text = value
        finally:
            self._refreshing_options = False

    def calculate_route(self):
        """Valida perfil/origem/destino, calcula rota e abre o ecrã de navegação."""

        if self.graph is None:
            self.planner_status_label.text = "Os mapas ainda não foram carregados."
            return
        if self.profile is None:
            self.screen_manager.current = "profile"
            self.profile_status_label.text = "Escolhe um perfil para calcular a rota."
            return

        origin = nav.find_selectable_node(
            self.selectable_nodes,
            self.origin_spinner.text,
            self.origin_building_spinner.text,
            self.origin_floor_spinner.text,
        )
        destination = nav.find_selectable_node(
            self.selectable_nodes,
            self.destination_spinner.text,
            self.destination_building_spinner.text,
            self.destination_floor_spinner.text,
        )
        if origin is None or destination is None:
            self.planner_status_label.text = "Escolhe uma origem e um destino válidos."
            return

        path, distance = nav.calculate_path(
            self.graph,
            origin,
            destination,
            mobility_reduced=self.profile == "Mobilidade reduzida",
        )
        if not path:
            self.route = None
            self.planner_status_label.text = "Não foi possível encontrar uma rota para essas opções."
            self.refresh_maps()
            return

        self.route = nav.RouteState(graph=self.graph, path=path, distance=distance)
        self._set_spinner_text_silently(
            self.visible_floor_spinner,
            self.graph.nodes[path[0]].get("floor_key", self.visible_floor_spinner.text),
        )
        self.route_summary_label.text = f"Rota: {distance:.1f} m · {self.profile}"
        self.navigation_label.text = nav.navigation_instruction(self.graph, self.route)
        self.planner_status_label.text = f"Rota pronta: {distance:.1f} m."
        self.screen_manager.current = "navigation"
        self.refresh_maps()

    def confirm_arrival(self):
        """Avança a rota quando o utilizador confirma que chegou ao ponto indicado."""

        if self.route is None:
            self.navigation_label.text = "Calcula primeiro uma rota."
            return
        if self.route.current_index >= len(self.route.path) - 1:
            self.navigation_label.text = "Chegaste ao destino."
            return

        self.route.current_index = nav.next_navigation_index(self.graph, self.route)
        current = self.route.path[self.route.current_index]
        self._set_spinner_text_silently(
            self.visible_floor_spinner,
            self.graph.nodes[current].get("floor_key", self.visible_floor_spinner.text),
        )
        self.navigation_label.text = nav.navigation_instruction(self.graph, self.route)
        self.refresh_maps()

    def cancel_route(self):
        """Cancela a navegação atual e regressa ao planeamento."""

        self.route = None
        self.navigation_label.text = "Rota cancelada."
        self.planner_status_label.text = "Escolhe a origem e o destino para começar."
        self.screen_manager.current = "planner"
        self.refresh_maps()

    def refresh_maps(self):
        """Atualiza o mapa do planeamento e o mapa da navegação."""

        show_labels = self.show_labels_checkbox.active if hasattr(self, "show_labels_checkbox") else False
        visible_floor = self.visible_floor_spinner.text if hasattr(self, "visible_floor_spinner") else "Exterior"
        if hasattr(self, "planner_map_widget"):
            self.planner_map_widget.set_state(
                self.graph,
                visible_floor,
                None,
                show_labels,
            )
        if hasattr(self, "route_map_widget"):
            self.route_map_widget.set_state(
                self.graph,
                visible_floor,
                self.route,
                show_labels,
            )


def main():
    """Ponto de entrada quando o ficheiro é executado diretamente."""

    AndroidNavigationApp().run()


if __name__ == "__main__":
    main()
