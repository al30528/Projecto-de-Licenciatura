# -*- coding: utf-8 -*-
"""Testes automáticos da navegação UTAD.

Este script testa a lógica comum usada pela app desktop e pela app móvel. Não
abre janelas: valida o grafo, as regras gerais dos perfis, a semântica das
instruções por tipo de aresta e alguns casos reais que já deram problemas.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
MOBILE_DIR = ROOT_DIR / "app movel"
ALLOWED_EDGE_TYPES = {
    "connection",
    "crosswalk",
    "elevator",
    "outdoor",
    "ramp",
    "sidewalk",
    "street",
    "stairs",
}
PROFILE_NAMES = {
    False: "normal",
    True: "mobilidade reduzida",
}
FORBIDDEN_EDGE_TYPES_BY_PROFILE = {
    False: {"elevator"},
    True: {"stairs"},
}


@dataclass(frozen=True)
class RouteCase:
    name: str
    origin_building: str
    origin_floor: str
    origin_label: str
    destination_building: str
    destination_floor: str
    destination_label: str
    mobility_reduced: bool = False
    must_use_edge_types: set[str] = field(default_factory=set)
    forbidden_edge_types: set[str] = field(default_factory=set)
    must_visit_nodes: tuple[tuple[str, str, str], ...] = ()
    forbidden_visit_nodes: tuple[tuple[str, str, str], ...] = ()
    instruction_checks: dict[int, tuple[str, ...]] = field(default_factory=dict)
    forbidden_instruction_checks: dict[int, tuple[str, ...]] = field(default_factory=dict)


ROUTE_CASES = [
    RouteCase(
        name="ECT1 Exterior -> ECT2 Piso1 F1.21b",
        origin_building="ECT1",
        origin_floor="Exterior",
        origin_label="2 - Entrada Piso 1",
        destination_building="ECT2",
        destination_floor="Piso1",
        destination_label="40 - F1.21b",
        forbidden_edge_types={"elevator"},
        must_use_edge_types={"stairs"},
        instruction_checks={
            0: ("segue até à passadeira",),
            1: ("atravessa a passadeira",),
            2: ("segue pela calçada",),
            15: ("segue até às escadas",),
            17: ("entra no edifício pelas escadas",),
        },
        forbidden_instruction_checks={
            0: ("atravessa a passadeira",),
            2: ("atravessa a passadeira",),
        },
    ),
    RouteCase(
        name="ECT1 Exterior -> ECT2 Piso3 F3.27 normal",
        origin_building="ECT1",
        origin_floor="Exterior",
        origin_label="2 - Entrada Piso 1",
        destination_building="ECT2",
        destination_floor="Piso3",
        destination_label="39 - F3.27",
        must_use_edge_types={"stairs"},
        forbidden_edge_types={"elevator"},
    ),
    RouteCase(
        name="ECT2 entrada Piso 0 -> ECT2 Piso3 F3.27 mobilidade reduzida",
        origin_building="ECT2",
        origin_floor="Exterior",
        origin_label="1 - entrada Piso 0",
        destination_building="ECT2",
        destination_floor="Piso3",
        destination_label="39 - F3.27",
        mobility_reduced=True,
        must_use_edge_types={"elevator"},
        forbidden_edge_types={"stairs"},
    ),
    RouteCase(
        name="ECT1 Entrada Piso 0 -> ECT2 Piso3 F3.27 mobilidade reduzida",
        origin_building="ECT1",
        origin_floor="Exterior",
        origin_label="37 - Entrada Piso 0",
        destination_building="ECT2",
        destination_floor="Piso3",
        destination_label="39 - F3.27",
        mobility_reduced=True,
        must_use_edge_types={"elevator"},
        forbidden_edge_types={"stairs"},
        must_visit_nodes=(("ECT2", "Exterior", "1 - entrada Piso 0"),),
        forbidden_visit_nodes=(("ECT1", "Exterior", "2 - Entrada Piso 1"),),
    ),
    RouteCase(
        name="ECHS2 Exterior -> ECT2 Piso1 F1.21b",
        origin_building="ECHS2",
        origin_floor="Exterior",
        origin_label="4 - entrada Piso 1",
        destination_building="ECT2",
        destination_floor="Piso1",
        destination_label="40 - F1.21b",
        forbidden_edge_types={"elevator"},
    ),
    RouteCase(
        name="ECT1 entrada Piso -1 -> ECT2 Piso1 F1.21b",
        origin_building="ECT1",
        origin_floor="Exterior",
        origin_label="3 - entrada Piso -1",
        destination_building="ECT2",
        destination_floor="Piso1",
        destination_label="40 - F1.21b",
        forbidden_edge_types={"elevator"},
        instruction_checks={
            0: ("segue até à rampa",),
            1: ("segue pela rampa",),
            4: ("segue pela calçada",),
            5: ("segue pela calçada",),
        },
        forbidden_instruction_checks={
            0: ("segue pela rampa",),
            4: ("escadas",),
            5: ("escadas",),
        },
    ),
    RouteCase(
        name="ECT2 Piso1 entrada Piso 1 -> ECT2 Piso3 F3.27 normal",
        origin_building="ECT2",
        origin_floor="Piso1",
        origin_label="1 - entrada Piso 1",
        destination_building="ECT2",
        destination_floor="Piso3",
        destination_label="39 - F3.27",
        must_use_edge_types={"stairs"},
        forbidden_edge_types={"elevator"},
    ),
]


LABEL_ALIASES = {
    "2 - Entrada Piso 1": ("2 - entradaP1",),
    "1 - entrada Piso 0": ("1 - entradaP0",),
    "4 - entrada Piso 1": ("4 - entradaP1",),
    "3 - entrada Piso -1": ("3 - entradaP0",),
    "1 - entrada Piso 1": ("1 - entradaP1",),
}


def load_desktop_core():
    import navigation_core as core

    return core


def load_mobile_core():
    module_path = MOBILE_DIR / "navigation_core.py"
    spec = importlib.util.spec_from_file_location("_utad_mobile_navigation_core_tests", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Não foi possível carregar {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.configure_paths(MOBILE_DIR)
    return module


def find_node(core, selectable_nodes, building: str, floor: str, label: str):
    for candidate in (label, *LABEL_ALIASES.get(label, ())):
        node_id = core.find_selectable_node(selectable_nodes, candidate, building, floor)
        if node_id:
            return node_id
    raise AssertionError(f"Ponto n?o encontrado: {building} / {floor} / {label}")


def route_edge_types(graph, path: list[str]) -> list[str]:
    return [
        str(graph[path[index]][path[index + 1]].get("edge_type", "connection"))
        for index in range(len(path) - 1)
    ]


def route_real_distance(graph, path: list[str]) -> float:
    return round(
        sum(
            graph[path[index]][path[index + 1]].get(
                "length", graph[path[index]][path[index + 1]].get("weight", 0)
            )
            for index in range(len(path) - 1)
        ),
        2,
    )


def assert_reported_distance_is_real(graph, path: list[str], distance: float, context: str):
    expected = route_real_distance(graph, path)
    if abs(distance - expected) > 0.01:
        raise AssertionError(
            f"{context}: distancia devolvida {distance} nao corresponde aos metros reais {expected}."
        )


def assert_instruction_contains(core, graph, path, distance, index: int, fragments: tuple[str, ...]):
    route = core.RouteState(graph, path, distance, current_index=index)
    instruction = core.navigation_instruction(graph, route).lower()
    missing = [fragment for fragment in fragments if fragment.lower() not in instruction]
    if missing:
        raise AssertionError(
            f"Instrução do passo {index + 1} não contém {missing}.\n"
            f"Instrução obtida:\n{instruction}"
        )


def assert_instruction_excludes(core, graph, path, distance, index: int, fragments: tuple[str, ...]):
    route = core.RouteState(graph, path, distance, current_index=index)
    instruction = core.navigation_instruction(graph, route).lower()
    unexpected = [fragment for fragment in fragments if fragment.lower() in instruction]
    if unexpected:
        raise AssertionError(
            f"Instrução do passo {index + 1} contém texto inesperado {unexpected}.\n"
            f"Instrução obtida:\n{instruction}"
        )


def profile_candidate_nodes(core, selectable_nodes, mobility_reduced: bool):
    return core.filter_selectable_nodes_for_profile(selectable_nodes, mobility_reduced)


def edge_allowed_for_profile(edge: dict, mobility_reduced: bool) -> bool:
    return edge.get("edge_type") not in FORBIDDEN_EDGE_TYPES_BY_PROFILE[mobility_reduced]


def reachable_nodes(graph, start: str, mobility_reduced: bool) -> set[str]:
    reached = {start}
    pending = [start]
    while pending:
        current = pending.pop(0)
        for neighbor in graph.neighbors(current):
            if neighbor in reached:
                continue
            if not edge_allowed_for_profile(graph[current][neighbor], mobility_reduced):
                continue
            reached.add(neighbor)
            pending.append(neighbor)
    return reached


def assert_route_instruction_sequence(core, graph, path, distance, context: str):
    for index in range(len(path) - 1):
        route = core.RouteState(graph, path, distance, current_index=index)
        instruction = core.navigation_instruction(graph, route).lower()
        if "ação:" not in instruction:
            raise AssertionError(f"{context}: instrução sem ação no passo {index + 1}:\n{instruction}")
        if "none" in instruction or "nan" in instruction:
            raise AssertionError(f"{context}: instrução inválida no passo {index + 1}:\n{instruction}")
        assert_vertical_instruction_semantics(core, graph, route, index, instruction, context)


def assert_vertical_instruction_semantics(core, graph, route, index: int, instruction: str, context: str):
    path = route.path
    edge = graph[path[index]][path[index + 1]]
    if not edge.get("vertical"):
        return

    current_floor = str(graph.nodes[path[index]].get("floor_key", ""))
    next_floor = str(graph.nodes[path[index + 1]].get("floor_key", ""))
    edge_type = str(edge.get("edge_type", "")).lower()

    if current_floor == "Exterior" and next_floor != "Exterior":
        if "entra no edifício" not in instruction:
            raise AssertionError(f"{context}: transição para interior sem entrada no passo {index + 1}:\n{instruction}")
    elif current_floor != "Exterior" and next_floor == "Exterior":
        if "sai do edifício" not in instruction:
            raise AssertionError(f"{context}: transição para exterior sem saída no passo {index + 1}:\n{instruction}")
    else:
        target_floor = next_floor
        if edge_type == "elevator":
            exit_index = core.elevator_exit_index(graph, route, index)
            target_floor = str(graph.nodes[path[exit_index]].get("floor_key", next_floor))
        expected_floor = core.floor_display_name(target_floor).lower()
        if expected_floor not in instruction:
            raise AssertionError(
                f"{context}: transição vertical sem piso de destino '{expected_floor}' no passo {index + 1}:\n"
                f"{instruction}"
            )
        if "sobe" not in instruction and "desce" not in instruction and "usa" not in instruction:
            raise AssertionError(f"{context}: transição vertical sem ação direcional no passo {index + 1}:\n{instruction}")

    if edge_type == "stairs" and "escadas" not in instruction:
        raise AssertionError(f"{context}: transição por escadas sem mencionar escadas no passo {index + 1}:\n{instruction}")
    if edge_type == "elevator" and "elevador" not in instruction:
        raise AssertionError(f"{context}: transição por elevador sem mencionar elevador no passo {index + 1}:\n{instruction}")


def assert_movement_semantics(core, graph, current: str, next_node: str, edge: dict):
    edge_type = str(edge.get("edge_type", "connection")).lower()
    current_data = graph.nodes[current]
    next_data = graph.nodes[next_node]
    current_type = str(current_data.get("type", "")).lower()
    next_type = str(next_data.get("type", "")).lower()
    current_floor = str(current_data.get("floor_key", ""))
    next_floor = str(next_data.get("floor_key", ""))

    if current_floor == "Exterior" and next_floor != "Exterior":
        instruction = core.navigation_instruction(
            graph,
            core.RouteState(graph, [current, next_node], edge.get("length", edge.get("weight", 0))),
        ).lower()
        if "entra no edifício" not in instruction:
            raise AssertionError(f"Transição exterior/interior sem 'entra no edifício':\n{instruction}")
        return

    phrase = core.movement_phrase(graph, [current, next_node], 0).lower()
    if not phrase:
        raise AssertionError(f"Frase vazia para aresta {current} -> {next_node}")

    if edge_type == "crosswalk":
        if current_type == "crosswalk" and next_type == "crosswalk":
            if "atravessa a passadeira" not in phrase:
                raise AssertionError(f"Passadeira não gera travessia: {current} -> {next_node}: {phrase}")
        else:
            if "atravessa a passadeira" in phrase:
                raise AssertionError(f"Aproximação/saída de passadeira não deve atravessar: {current} -> {next_node}: {phrase}")
            if "passadeira" not in phrase:
                raise AssertionError(f"Aproximação/saída de passadeira não menciona passadeira: {current} -> {next_node}: {phrase}")
    elif edge_type == "sidewalk":
        if "calçada" not in phrase:
            raise AssertionError(f"Calçada não menciona calçada: {current} -> {next_node}: {phrase}")
        if "escadas" in phrase:
            raise AssertionError(f"Calçada não deve mencionar escadas: {current} -> {next_node}: {phrase}")
    elif edge_type == "street":
        if "atravessa a estrada" not in phrase:
            raise AssertionError(f"Estrada n?o gera travessia: {current} -> {next_node}: {phrase}")
    elif edge_type == "ramp":
        if current_type in {"ramp", "rampa"}:
            expected = "segue pela rampa"
        else:
            expected = "segue até à rampa"
        if expected not in phrase:
            raise AssertionError(f"Rampa com frase inesperada: {current} -> {next_node}: {phrase}")
    elif edge_type == "stairs":
        is_building_transition = current_floor != next_floor and (
            current_floor == "Exterior" or next_floor == "Exterior"
        )
        if "escadas" not in phrase and not is_building_transition:
            raise AssertionError(f"Escadas não mencionam escadas: {current} -> {next_node}: {phrase}")
    elif edge_type == "elevator":
        is_building_transition = current_floor != next_floor and (
            current_floor == "Exterior" or next_floor == "Exterior"
        )
        if "elevador" not in phrase and not is_building_transition:
            raise AssertionError(f"Elevador não menciona elevador: {current} -> {next_node}: {phrase}")


def validate_graph_generically(core, graph, selectable_nodes):
    if not selectable_nodes:
        raise AssertionError("Não há nós selecionáveis.")

    seen_selectable_keys = set()
    for node in selectable_nodes:
        key = (node.building, node.floor, node.label)
        if key in seen_selectable_keys:
            raise AssertionError(f"Nó selecionável duplicado: {key}")
        seen_selectable_keys.add(key)
        if not node.label or not node.building or not node.floor:
            raise AssertionError(f"Nó selecionável incompleto: {node}")

    edge_count = 0
    for node_a, node_b, edge in graph.edges(data=True):
        edge_count += 1
        edge_type = str(edge.get("edge_type", "connection")).lower()
        if edge_type not in ALLOWED_EDGE_TYPES:
            raise AssertionError(f"Aresta {node_a} -> {node_b} tem edge_type inválido: {edge_type}")
        if edge.get("weight", 0) < 0:
            raise AssertionError(f"Aresta {node_a} -> {node_b} tem peso negativo: {edge.get('weight')}")
        assert_movement_semantics(core, graph, node_a, node_b, edge)
        assert_movement_semantics(core, graph, node_b, node_a, edge)

    if edge_count == 0:
        raise AssertionError("O grafo não tem arestas.")
    return edge_count


def validate_profile_route_matrix(core, graph, selectable_nodes, mobility_reduced: bool):
    candidates = profile_candidate_nodes(core, selectable_nodes, mobility_reduced)
    profile = PROFILE_NAMES[mobility_reduced]
    tested_routes = 0
    blocked_pairs = 0

    for origin in candidates:
        reached = reachable_nodes(graph, origin.node_id, mobility_reduced)
        for destination in candidates:
            if origin.node_id == destination.node_id:
                continue

            path, distance = core.calculate_path(
                graph,
                origin.node_id,
                destination.node_id,
                mobility_reduced=mobility_reduced,
            )
            context = f"{profile}: {origin.label} -> {destination.label}"

            if destination.node_id not in reached:
                blocked_pairs += 1
                if path:
                    raise AssertionError(f"{context}: rota encontrada apesar de estar bloqueada pelo perfil.")
                continue

            tested_routes += 1
            if not path:
                raise AssertionError(f"{context}: rota esperada mas indisponível.")
            if path[0] != origin.node_id or path[-1] != destination.node_id:
                raise AssertionError(f"{context}: rota começa/termina no nó errado.")
            if distance <= 0:
                raise AssertionError(f"{context}: distância inválida {distance}.")
            assert_reported_distance_is_real(graph, path, distance, context)

            forbidden_edges = FORBIDDEN_EDGE_TYPES_BY_PROFILE[mobility_reduced] & set(route_edge_types(graph, path))
            if forbidden_edges:
                raise AssertionError(f"{context}: rota usa edge_type proibidos {sorted(forbidden_edges)}.")

            assert_route_instruction_sequence(core, graph, path, distance, context)

    return tested_routes, blocked_pairs


def validate_profile_endpoint_guards(core, graph, selectable_nodes):
    """Garante que o Dijkstra recusa endpoints que o perfil nao pode escolher."""

    for mobility_reduced in (False, True):
        valid_nodes = core.filter_selectable_nodes_for_profile(selectable_nodes, mobility_reduced)
        invalid_nodes = [
            node
            for node in selectable_nodes
            if not core.selectable_node_allowed_for_profile(node, mobility_reduced)
        ]
        profile = PROFILE_NAMES[mobility_reduced]

        if not valid_nodes:
            raise AssertionError(f"{profile}: não há pontos válidos para testar endpoints.")
        if not invalid_nodes:
            raise AssertionError(f"{profile}: não há pontos inválidos para testar endpoints.")

        valid = valid_nodes[0]
        invalid = invalid_nodes[0]

        if core.graph_node_allowed_for_profile(graph, invalid.node_id, mobility_reduced):
            raise AssertionError(f"{profile}: ponto inválido aceite pelo core: {invalid.label}")

        path, _distance = core.calculate_path(
            graph,
            invalid.node_id,
            valid.node_id,
            mobility_reduced=mobility_reduced,
        )
        if path:
            raise AssertionError(f"{profile}: rota calculada a partir de origem inválida {invalid.label}.")

        path, _distance = core.calculate_path(
            graph,
            valid.node_id,
            invalid.node_id,
            mobility_reduced=mobility_reduced,
        )
        if path:
            raise AssertionError(f"{profile}: rota calculada para destino inválido {invalid.label}.")

    ect1_piso1 = find_node(core, selectable_nodes, "ECT1", "Exterior", "2 - Entrada Piso 1")
    if core.graph_node_allowed_for_profile(graph, ect1_piso1, mobility_reduced=True):
        raise AssertionError("Mobilidade reduzida ainda pode selecionar a Entrada Piso 1 da ECT1.")


def run_case(core, graph, selectable_nodes, case: RouteCase, check_instructions: bool = True):
    origin = find_node(core, selectable_nodes, case.origin_building, case.origin_floor, case.origin_label)
    destination = find_node(
        core,
        selectable_nodes,
        case.destination_building,
        case.destination_floor,
        case.destination_label,
    )

    path, distance = core.calculate_path(
        graph,
        origin,
        destination,
        mobility_reduced=case.mobility_reduced,
    )
    if not path:
        raise AssertionError(f"Rota indisponível: {case.name}")
    if distance <= 0:
        raise AssertionError(f"Distância inválida em {case.name}: {distance}")
    assert_reported_distance_is_real(graph, path, distance, case.name)

    edge_types = set(route_edge_types(graph, path))
    missing_edges = case.must_use_edge_types - edge_types
    forbidden_edges = case.forbidden_edge_types & edge_types
    if missing_edges:
        raise AssertionError(f"{case.name}: faltam edge_type esperados {sorted(missing_edges)}")
    if forbidden_edges:
        raise AssertionError(f"{case.name}: encontrou edge_type proibidos {sorted(forbidden_edges)}")

    for building, floor, label in case.must_visit_nodes:
        node_id = find_node(core, selectable_nodes, building, floor, label)
        if node_id not in path:
            raise AssertionError(f"{case.name}: rota não passa por {building} / {floor} / {label}")

    for building, floor, label in case.forbidden_visit_nodes:
        node_id = find_node(core, selectable_nodes, building, floor, label)
        if node_id in path:
            raise AssertionError(f"{case.name}: rota passa por ponto proibido {building} / {floor} / {label}")

    if check_instructions:
        for index, fragments in case.instruction_checks.items():
            assert_instruction_contains(core, graph, path, distance, index, fragments)
        for index, fragments in case.forbidden_instruction_checks.items():
            assert_instruction_excludes(core, graph, path, distance, index, fragments)

    return len(path), distance


def run_dataset(name: str, core):
    graph, _floors = core.build_campus_graph()
    selectable_nodes = core.collect_selectable_nodes(graph)

    print(f"\nDataset: {name}")
    edge_count = validate_graph_generically(core, graph, selectable_nodes)
    print(f"  [OK] validações genéricas do grafo ({edge_count} arestas)")

    for mobility_reduced in (False, True):
        tested_routes, blocked_pairs = validate_profile_route_matrix(
            core,
            graph,
            selectable_nodes,
            mobility_reduced,
        )
        profile = PROFILE_NAMES[mobility_reduced]
        print(
            f"  [OK] matriz de rotas {profile} "
            f"({tested_routes} rotas válidas, {blocked_pairs} pares bloqueados)"
        )

    validate_profile_endpoint_guards(core, graph, selectable_nodes)
    print("  [OK] proteção de origem/destino por perfil")

    for case in ROUTE_CASES:
        steps, distance = run_case(
            core,
            graph,
            selectable_nodes,
            case,
            check_instructions=True,
        )
        print(f"  [OK] {case.name} ({steps - 1} passos, {distance:.1f} m)")


def main():
    run_dataset("desktop", load_desktop_core())
    run_dataset("mobile", load_mobile_core())
    print("\nTodos os testes genéricos e de regressão passaram.")


if __name__ == "__main__":
    main()
