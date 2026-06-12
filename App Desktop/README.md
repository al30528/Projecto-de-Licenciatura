# App Desktop - Navegação UTAD

Esta pasta contém a aplicação desktop Python/Tkinter usada para testar e validar
a navegação pedestre indoor/outdoor antes de evoluir para a app móvel.

A app desktop é autónoma: usa o seu próprio `navigation_core.py`, a sua própria
pasta `OSM Pisos/` e as suas próprias imagens. Não depende da pasta `app movel/`.

## Conteúdo

- `app_desktop.py`: interface desktop principal.
- `navigation_core.py`: leitura OSM, grafo, Dijkstra, filtros por perfil e instruções.
- `navegacao_campus_vscode.py`: script base/legado para testar rotas por terminal.
- `requirements.txt`: dependências da app desktop.
- `OSM Pisos/`: dados OSM usados pela app desktop.
- `Imagens ECT2/`: imagens e calibrações dos pisos.

## Instalar dependências

```bash
cd "App Desktop"
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Correr a app desktop

```bash
cd "App Desktop"
python app_desktop.py
```

## Correr o script base

```bash
cd "App Desktop"
python navegacao_campus_vscode.py --piso Piso1
```

## Validação

Os scripts de validação ficam na raiz do repositório e testam desktop e mobile:

```bash
python validar_osm.py
python testar_rotas.py
```
