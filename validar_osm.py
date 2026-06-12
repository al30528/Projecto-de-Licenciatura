"""Validador dos ficheiros OSM usados pela navegação pedestre indoor.

O script não altera os ficheiros .osm. Ele lê os dados, procura problemas
comuns de modelação e confirma que o grafo final consegue calcular rotas.

Usei este ficheiro como uma rede de segurança para quando os mapas forem
alterados no JOSM: se faltar uma tag importante, se uma transição entre pisos
ficar incompleta, ou se uma rota crítica deixar de existir, o erro aparece aqui
antes de chegar à app.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType


ROOT_DIR = Path(__file__).resolve().parent
DESKTOP_DIR = ROOT_DIR / "App Desktop"
MOBILE_DIR = ROOT_DIR / "app movel"

# Cada app autónoma espera estes quatro ficheiros dentro da sua própria pasta.
# Se forem acrescentados novos edifícios/pisos, esta lista deve ser o primeiro
# sítio a rever.
OSM_FILENAMES = {
    "Exterior": "Exterior.osm",
    "Piso1": "Piso1.osm",
    "Piso2": "Piso2.osm",
    "Piso3": "Piso3.osm",
}


@dataclass(frozen=True)
class Issue:
    """Problema encontrado durante a validação.

    ``severity`` distingue erros que devem bloquear o uso dos dados de avisos
    que ainda merecem revisão manual.
    """

    severity: str
    dataset: str
    file: str
    target: str
    message: str

    def format(self) -> str:
        location = f"{self.dataset}/{self.file}"
        if self.target:
            location = f"{location}::{self.target}"
        return f"[{self.severity}] {location}: {self.message}"


@dataclass
class OsmNode:
    """Representação mínima de um node OSM para validação offline."""

    xml_id: str
    lat: float | None
    lon: float | None
    tags: dict[str, str]


@dataclass
class OsmWay:
    """Representação mínima de uma way OSM e das suas referências."""

    xml_id: str
    refs: list[str]
    tags: dict[str, str]


@dataclass
class OsmData:
    """Conjunto de dados de um ficheiro OSM já convertido para estruturas Python."""

    floor: str
    path: Path
    nodes: dict[str, OsmNode]
    ways: list[OsmWay]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Valida os ficheiros .osm usados pela app de navegação UTAD."
    )
    parser.add_argument(
        "--dataset",
        choices=("desktop", "mobile", "both"),
        default="both",
        help="Conjunto de ficheiros a validar. Por omissão valida desktop e app móvel.",
    )
    parser.add_argument(
        "--strict-way-tags",
        action="store_true",
        help="Trata ways sem edge_type/accessibility/bidirectional como erro em vez de aviso.",
    )
    return parser.parse_args()


def dataset_roots(selection: str) -> list[tuple[str, Path]]:
    """Resolve que datasets devem ser validados: desktop, mobile ou ambos."""

    datasets = []
    if DESKTOP_DIR.exists():
        datasets.append(("desktop", DESKTOP_DIR))
    if MOBILE_DIR.exists():
        datasets.append(("mobile", MOBILE_DIR))

    if selection == "desktop":
        return [item for item in datasets if item[0] == "desktop"]
    if selection == "mobile":
        return [item for item in datasets if item[0] == "mobile"]
    return datasets


def load_navigation_core(dataset: str, root: Path) -> ModuleType:
    """Carrega o navigation_core.py da app que está a ser validada."""

    module_path = root / "navigation_core.py"
    spec = importlib.util.spec_from_file_location(f"_utad_{dataset}_navigation_core_validation", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"não foi possível carregar {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.configure_paths(root)
    return module


def issue(
    issues: list[Issue],
    severity: str,
    dataset: str,
    file: str,
    target: str,
    message: str,
) -> None:
    """Regista um erro/aviso mantendo sempre o mesmo formato de saída."""

    issues.append(Issue(severity, dataset, file, target, message))


def normalized_tags(element: ET.Element) -> dict[str, str]:
    """Lê tags OSM e normaliza as chaves para comparação case-insensitive."""

    tags = {}
    for tag in element.findall("tag"):
        key = tag.get("k")
        value = tag.get("v")
        if key:
            tags[key.lower()] = value or ""
    return tags


def parse_osm_file(dataset: str, floor: str, path: Path, issues: list[Issue]) -> OsmData | None:
    """Carrega um ficheiro OSM e recolhe nodes/ways sem alterar o XML original."""

    if not path.exists():
        issue(issues, "ERRO", dataset, path.name, "", "ficheiro OSM não encontrado.")
        return None

    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as error:
        issue(issues, "ERRO", dataset, path.name, "", f"XML inválido: {error}.")
        return None

    nodes: dict[str, OsmNode] = {}
    for node_elem in root.findall("node"):
        xml_id = node_elem.get("id") or ""
        if not xml_id:
            issue(issues, "ERRO", dataset, path.name, "node", "nó sem atributo id.")
            continue
        if xml_id in nodes:
            issue(issues, "ERRO", dataset, path.name, f"node {xml_id}", "id XML duplicado.")

        lat = parse_float(node_elem.get("lat"))
        lon = parse_float(node_elem.get("lon"))
        if lat is None or lon is None:
            issue(issues, "ERRO", dataset, path.name, f"node {xml_id}", "lat/lon ausente ou inválido.")

        nodes[xml_id] = OsmNode(xml_id=xml_id, lat=lat, lon=lon, tags=normalized_tags(node_elem))

    ways = []
    for way_elem in root.findall("way"):
        xml_id = way_elem.get("id") or ""
        refs = [ref.get("ref") or "" for ref in way_elem.findall("nd")]
        if not xml_id:
            issue(issues, "ERRO", dataset, path.name, "way", "way sem atributo id.")
        ways.append(OsmWay(xml_id=xml_id, refs=refs, tags=normalized_tags(way_elem)))

    return OsmData(floor=floor, path=path, nodes=nodes, ways=ways)


def parse_float(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def expected_floor_values(floor: str) -> set[str]:
    if floor == "Exterior":
        # No ficheiro exterior há entradas de edifícios que podem guardar o piso
        # real de entrada (por exemplo floor=1). Basta a tag existir.
        return set()
    digits = "".join(char for char in floor if char.isdigit())
    return {digits} if digits else set()


def node_display_name(node: OsmNode) -> str:
    name = node.tags.get("roomname") or node.tags.get("name") or "sem nome"
    nodeid = node.tags.get("nodeid") or "sem nodeID"
    return f"{nodeid} - {name}"


def validate_nodes(dataset: str, osm: OsmData, issues: list[Issue]) -> None:
    """Valida tags obrigatórias dos nós e duplicação de nodeID por piso."""

    nodeids: dict[str, list[str]] = defaultdict(list)
    expected_floors = expected_floor_values(osm.floor)

    for xml_id, node in osm.nodes.items():
        tags = node.tags
        nodeid = tags.get("nodeid", "").strip()
        target = f"node {xml_id}"

        if not nodeid:
            issue(issues, "ERRO", dataset, osm.path.name, target, "tag nodeID/nodeid em falta.")
        else:
            nodeids[nodeid].append(xml_id)
            if not nodeid.isdigit():
                issue(issues, "ERRO", dataset, osm.path.name, target, f"nodeid não numérico: {nodeid!r}.")

        for required in ("building", "floor", "type"):
            if not tags.get(required, "").strip():
                issue(issues, "ERRO", dataset, osm.path.name, target, f"tag {required!r} em falta.")

        if not (tags.get("roomname") or tags.get("name")):
            issue(issues, "AVISO", dataset, osm.path.name, target, "sem roomname/name para apresentar ao utilizador.")

        floor_value = tags.get("floor", "").strip().lower()
        if expected_floors and floor_value and floor_value not in expected_floors:
            issue(
                issues,
                "ERRO",
                dataset,
                osm.path.name,
                target,
                f"floor={floor_value!r} não corresponde ao ficheiro {osm.floor}.",
            )

        if "accessibilty" in tags:
            issue(
                issues,
                "ERRO",
                dataset,
                osm.path.name,
                target,
                "tag antiga 'accessibilty' encontrada; usa 'accessibility'.",
            )

        accessibility = tags.get("accessibility")
        if accessibility is not None and not accessibility.strip().isdigit():
            issue(
                issues,
                "AVISO",
                dataset,
                osm.path.name,
                target,
                f"accessibility não numérico: {accessibility!r}.",
            )

    for nodeid, xml_ids in sorted(nodeids.items(), key=lambda item: numeric_sort_key(item[0])):
        if len(xml_ids) > 1:
            joined = ", ".join(xml_ids)
            issue(
                issues,
                "ERRO",
                dataset,
                osm.path.name,
                f"nodeid {nodeid}",
                f"nodeid duplicado nos nós XML: {joined}.",
            )


def numeric_sort_key(value: str) -> tuple[int, str]:
    """Ordena ids numéricos como números, deixando texto não numérico no fim."""

    try:
        return (int(value), value)
    except ValueError:
        return (sys.maxsize, value)


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


def validate_ways(dataset: str, osm: OsmData, issues: list[Issue], strict_way_tags: bool) -> None:
    """Valida ways como arestas do grafo de navegação.

    Cada way deve dizer que tipo de deslocação representa, que acessibilidade
    permite e se é bidirecional. Também comparo a acessibilidade da way com a
    dos nós ligados para evitar uma aresta "mais acessível" do que os seus
    próprios endpoints.
    """

    missing_edge_type = 0
    missing_accessibility = 0
    missing_bidirectional = 0

    for way in osm.ways:
        target = f"way {way.xml_id or 'sem id'}"
        refs = [ref for ref in way.refs if ref]
        expected_accessibility_values = []

        if len(refs) < 2:
            issue(issues, "ERRO", dataset, osm.path.name, target, "tem menos de dois nós.")

        for ref in refs:
            node = osm.nodes.get(ref)
            if node is None:
                issue(issues, "ERRO", dataset, osm.path.name, target, f"referencia nó inexistente: {ref}.")
                continue

            node_accessibility = node.tags.get("accessibility", "").strip()
            if node_accessibility.isdigit():
                expected_accessibility_values.append(int(node_accessibility))

        for first, second in zip(refs, refs[1:]):
            if first == second:
                issue(issues, "AVISO", dataset, osm.path.name, target, f"referência repetida consecutiva: {first}.")

        if "type" in way.tags:
            issue(
                issues,
                "ERRO",
                dataset,
                osm.path.name,
                target,
                "tag 'type' encontrada numa way; usa 'edge_type' para classificar arestas.",
            )

        edge_type = way.tags.get("edge_type", "").strip()
        if not edge_type:
            missing_edge_type += 1
        elif edge_type not in ALLOWED_EDGE_TYPES:
            issue(
                issues,
                "ERRO",
                dataset,
                osm.path.name,
                target,
                f"edge_type inválido na way: {edge_type!r}; valores aceites: {', '.join(sorted(ALLOWED_EDGE_TYPES))}.",
            )

        accessibility = way.tags.get("accessibility", "").strip()
        if not accessibility:
            missing_accessibility += 1
        elif not accessibility.isdigit():
            issue(issues, "ERRO", dataset, osm.path.name, target, f"accessibility não numérico: {accessibility!r}.")
        else:
            way_accessibility = int(accessibility)
            expected_accessibility = max(expected_accessibility_values) if expected_accessibility_values else 1
            if way_accessibility < expected_accessibility:
                issue(
                    issues,
                    "ERRO",
                    dataset,
                    osm.path.name,
                    target,
                    (
                        f"accessibility={way_accessibility} é menor do que o máximo dos nós "
                        f"referenciados ({expected_accessibility})."
                    ),
                )
            elif way_accessibility > expected_accessibility and edge_type not in {"stairs", "elevator"}:
                issue(
                    issues,
                    "AVISO",
                    dataset,
                    osm.path.name,
                    target,
                    (
                        f"accessibility={way_accessibility} é maior do que o máximo dos nós "
                        f"referenciados ({expected_accessibility}); confirma se é intencional."
                    ),
                )

        if "bidirrectional" in way.tags:
            issue(
                issues,
                "ERRO",
                dataset,
                osm.path.name,
                target,
                "tag antiga 'bidirrectional' encontrada; usa 'bidirectional'.",
            )
        if "bidirectional" not in way.tags:
            missing_bidirectional += 1
        elif str(way.tags.get("bidirectional", "")).strip().lower() != "true":
            issue(issues, "ERRO", dataset, osm.path.name, target, "bidirectional deve ter valor true.")

    severity = "ERRO" if strict_way_tags else "AVISO"
    if missing_edge_type:
        issue(
            issues,
            severity,
            dataset,
            osm.path.name,
            "ways",
            f"{missing_edge_type} way(s) sem edge_type; o core assume connection por defeito.",
        )
    if missing_accessibility:
        issue(
            issues,
            severity,
            dataset,
            osm.path.name,
            "ways",
            f"{missing_accessibility} way(s) sem accessibility; o core assume 1.",
        )
    if missing_bidirectional:
        issue(
            issues,
            severity,
            dataset,
            osm.path.name,
            "ways",
            f"{missing_bidirectional} way(s) sem bidirectional; a app assume bidirecional.",
        )


def split_transition_labels(value: str) -> list[str]:
    """Separa labels de transição compostas, por exemplo elevador/escadas."""

    return [part.strip().upper() for part in value.split(";") if part.strip()]


def validate_transitions(dataset: str, osms: list[OsmData], issues: list[Issue]) -> None:
    """Confirma se cada label de transição aparece nos pisos necessários."""

    groups: dict[str, list[tuple[OsmData, OsmNode]]] = defaultdict(list)

    for osm in osms:
        for node in osm.nodes.values():
            transition = node.tags.get("transition", "").strip()
            if not transition:
                continue
            for label in split_transition_labels(transition):
                groups[label].append((osm, node))

    if not groups:
        issue(issues, "ERRO", dataset, "OSM", "transition", "nenhuma transition encontrada nos nós.")
        return

    for label, entries in sorted(groups.items()):
        floors = {osm.floor for osm, _node in entries}
        readable_nodes = ", ".join(f"{osm.floor}:{node_display_name(node)}" for osm, node in entries)

        if len(entries) < 2:
            issue(
                issues,
                "ERRO",
                dataset,
                "OSM",
                label,
                f"transition sem par; encontrada apenas em {readable_nodes}.",
            )
            continue

        if len(floors) < 2:
            issue(
                issues,
                "AVISO",
                dataset,
                "OSM",
                label,
                f"transition aparece só em {sorted(floors)}; confirma se deve ligar espaços diferentes.",
            )

        types = {node.tags.get("type", "") for _osm, node in entries}
        if "ELEVADOR" in label and "elevator" not in types:
            issue(issues, "AVISO", dataset, "OSM", label, "label parece de elevador mas não há node type=elevator.")
        if "ESCADAS" in label and "stairs" not in types:
            issue(issues, "AVISO", dataset, "OSM", label, "label parece de escadas mas não há node type=stairs.")


def validate_graph(dataset: str, core: ModuleType, issues: list[Issue]) -> None:
    """Constrói o grafo real da app e valida se os dados chegam a ser utilizáveis."""

    try:
        graph, _floor_graphs = core.build_campus_graph()
    except Exception as error:  # noqa: BLE001 - queremos reportar qualquer falha da app.
        issue(issues, "ERRO", dataset, "navigation_core", "build_campus_graph", str(error))
        return

    node_ids = list(graph.nodes)
    isolated = [node_id for node_id in node_ids if not list(graph.neighbors(node_id))]
    for node_id in isolated[:10]:
        issue(
            issues,
            "ERRO",
            dataset,
            "grafo",
            node_id,
            "nó isolado; não tem arestas no grafo final.",
        )
    if len(isolated) > 10:
        issue(issues, "ERRO", dataset, "grafo", "isolados", f"mais {len(isolated) - 10} nó(s) isolado(s).")

    components = connected_components(graph)
    if len(components) > 1:
        sizes = ", ".join(str(len(component)) for component in components[:8])
        issue(
            issues,
            "ERRO",
            dataset,
            "grafo",
            "componentes",
            f"grafo desconexo: {len(components)} componentes; tamanhos principais: {sizes}.",
        )

    selectable_nodes = core.collect_selectable_nodes(graph)
    if not selectable_nodes:
        issue(issues, "ERRO", dataset, "grafo", "seleção", "não há nós selecionáveis para origem/destino.")
        return

    validate_profile_reachability(core, dataset, graph, selectable_nodes, mobility_reduced=False, issues=issues)
    validate_critical_routes(core, dataset, graph, selectable_nodes, issues)


def connected_components(graph) -> list[set[str]]:
    """Calcula componentes ligadas para detetar partes isoladas do grafo."""

    remaining = set(graph.nodes)
    components = []
    while remaining:
        start = remaining.pop()
        component = {start}
        queue = deque([start])
        while queue:
            node = queue.popleft()
            for neighbor in graph.neighbors(node):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    component.add(neighbor)
                    queue.append(neighbor)
        components.append(component)
    return sorted(components, key=len, reverse=True)


def edge_allowed(edge: dict, mobility_reduced: bool) -> bool:
    """Aplica no validador a mesma regra base de perfil usada pelo Dijkstra."""

    edge_type = edge.get("edge_type")
    if mobility_reduced and edge_type == "stairs":
        return False
    if not mobility_reduced and edge_type == "elevator":
        return False
    return True


def reachable_nodes(graph, start: str, mobility_reduced: bool) -> set[str]:
    """Devolve os nós alcançáveis a partir de uma origem para um dado perfil."""

    reached = {start}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for neighbor in graph.neighbors(node):
            if neighbor in reached:
                continue
            if not edge_allowed(graph[node][neighbor], mobility_reduced):
                continue
            reached.add(neighbor)
            queue.append(neighbor)
    return reached


def validate_profile_reachability(
    core: ModuleType,
    dataset: str,
    graph,
    selectable_nodes: list,
    mobility_reduced: bool,
    issues: list[Issue],
) -> None:
    """Garante que os destinos selecionáveis têm rota para o perfil analisado."""

    profile = "mobilidade reduzida" if mobility_reduced else "normal"
    origin = reachability_origin(selectable_nodes, mobility_reduced)
    if origin is None:
        issue(
            issues,
            "ERRO",
            dataset,
            "grafo",
            f"perfil {profile}",
            "não existe origem selecionável compatível para validar conectividade.",
        )
        return
    reached = reachable_nodes(graph, origin.node_id, mobility_reduced)
    expected_targets = [
        node
        for node in selectable_nodes
        if profile_allows_destination(core, graph.nodes[node.node_id], mobility_reduced)
    ]
    unreachable = [node for node in expected_targets if node.node_id not in reached]

    for node in unreachable[:10]:
        issue(
            issues,
            "ERRO",
            dataset,
            "grafo",
            f"perfil {profile}",
            f"sem rota de {origin.label} para {node.label} ({node.building} · {node.floor}).",
        )
    if len(unreachable) > 10:
        issue(
            issues,
            "ERRO",
            dataset,
            "grafo",
            f"perfil {profile}",
            f"mais {len(unreachable) - 10} destino(s) selecionável(eis) sem rota.",
        )


LABEL_ALIASES = {
    "2 - Entrada Piso 1": ("2 - entradaP1",),
    "1 - entrada Piso 0": ("1 - entradaP0",),
    "4 - entrada Piso 1": ("4 - entradaP1",),
    "3 - entrada Piso -1": ("3 - entradaP0",),
    "1 - entrada Piso 1": ("1 - entradaP1",),
}


def reachability_origin(
    selectable_nodes: list,
    mobility_reduced: bool,
) -> object | None:
    """Escolhe uma origem representativa para testar alcance no campus."""

    preferred = (
        [("ECT2", "Exterior", "1 - entrada Piso 0"), ("ECT1", "Exterior", "3 - entrada Piso -1")]
        if mobility_reduced
        else [("ECT1", "Exterior", "2 - Entrada Piso 1")]
    )

    for building, floor, label in preferred:
        node = find_node(selectable_nodes, building, floor, label)
        if node is not None:
            return node

    return next(
        (
            node
            for node in selectable_nodes
            if fallback_profile_allows_destination({"type": node.node_type}, mobility_reduced)
        ),
        None,
    )


def fallback_profile_allows_destination(node_data: dict, mobility_reduced: bool) -> bool:
    """Regra mínima usada antes de existir uma instância carregada do core."""

    node_type = str(node_data.get("type", "")).lower()
    return not (mobility_reduced and node_type == "stairs")


def profile_allows_destination(core: ModuleType, node_data: dict, mobility_reduced: bool) -> bool:
    """Encapsula a regra de perfil para manter o validador alinhado com o core."""

    return core.node_data_allowed_for_profile(node_data, mobility_reduced)


def validate_critical_routes(core: ModuleType, dataset: str, graph, nodes: list, issues: list[Issue]) -> None:
    """Testa manualmente rotas que não podem quebrar sem eu reparar."""

    tests = [
        ("normal", "ECT1", "Exterior", "2 - Entrada Piso 1", "ECT2", "Piso3", "39 - F3.27", False),
        ("normal", "ECT2", "Piso1", "1 - entrada Piso 1", "ECT2", "Piso3", "39 - F3.27", False),
        ("mobilidade reduzida", "ECT2", "Exterior", "1 - entrada Piso 0", "ECT2", "Piso3", "39 - F3.27", True),
    ]

    for profile, origin_building, origin_floor, origin_label, dest_building, dest_floor, dest_label, reduced in tests:
        origin = find_node(nodes, origin_building, origin_floor, origin_label)
        destination = find_node(nodes, dest_building, dest_floor, dest_label)
        target = f"{profile}: {origin_label} -> {dest_label}"

        if origin is None or destination is None:
            issue(
                issues,
                "AVISO",
                dataset,
                "rotas críticas",
                target,
                "teste ignorado porque a origem ou o destino não existe.",
            )
            continue

        path, distance = core.calculate_path(graph, origin.node_id, destination.node_id, reduced)
        if not path:
            issue(issues, "ERRO", dataset, "rotas críticas", target, "rota indisponível.")
        elif distance <= 0:
            issue(issues, "ERRO", dataset, "rotas críticas", target, f"distância inválida: {distance}.")


def find_node(nodes: list, building: str, floor: str, label: str) -> object | None:
    """Procura um ponto selecionável, aceitando labels antigas como aliases."""

    for candidate in (label, *LABEL_ALIASES.get(label, ())):
        for node in nodes:
            if node.building == building and node.floor == floor and node.label == candidate:
                return node
    return None


def validate_dataset(name: str, root: Path, strict_way_tags: bool) -> list[Issue]:
    """Valida um dataset completo, incluindo OSM bruto, transições e grafo final."""

    issues: list[Issue] = []
    try:
        core = load_navigation_core(name, root)
    except Exception as error:  # noqa: BLE001 - queremos reportar qualquer falha de import.
        issue(issues, "ERRO", name, "navigation_core.py", "import", str(error))
        return issues

    osm_files = {floor: root / "OSM Pisos" / filename for floor, filename in OSM_FILENAMES.items()}
    osms = []

    for floor, path in osm_files.items():
        data = parse_osm_file(name, floor, path, issues)
        if data is None:
            continue
        validate_nodes(name, data, issues)
        validate_ways(name, data, issues, strict_way_tags)
        osms.append(data)

    if osms:
        validate_transitions(name, osms, issues)
    validate_graph(name, core, issues)
    return issues


def print_dataset_summary(name: str, root: Path, issues: list[Issue]) -> None:
    """Mostra o relatório de validação de forma legível na consola."""

    counts = Counter(issue.severity for issue in issues)
    print(f"\nDataset: {name} ({root})")
    if issues:
        for current in issues:
            print(current.format())
    else:
        print("[OK] Sem erros nem avisos.")
    print(f"Resumo {name}: {counts.get('ERRO', 0)} erro(s), {counts.get('AVISO', 0)} aviso(s).")


def main() -> int:
    """Ponto de entrada CLI. Devolve código de erro se existir algum ERRO."""

    args = parse_args()
    all_issues: list[Issue] = []

    print("Validação dos ficheiros OSM da navegação UTAD")
    for name, root in dataset_roots(args.dataset):
        issues = validate_dataset(name, root, args.strict_way_tags)
        print_dataset_summary(name, root, issues)
        all_issues.extend(issues)

    total = Counter(issue.severity for issue in all_issues)
    print(f"\nTotal: {total.get('ERRO', 0)} erro(s), {total.get('AVISO', 0)} aviso(s).")
    return 1 if total.get("ERRO", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
