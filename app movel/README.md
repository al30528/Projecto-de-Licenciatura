# App móvel - Navegação UTAD

Esta pasta contém o protótipo Python/Kivy da aplicação móvel Android.

A app móvel é autónoma: usa o seu próprio `navigation_core.py`, a sua própria
pasta `OSM Pisos/` e as suas próprias imagens. Não depende da pasta
`App Desktop/`.

## Conteúdo

- `app_android.py`: interface móvel.
- `navigation_core.py`: lógica de grafos, filtros e navegação.
- `main.py`: entrada usada pelo Buildozer.
- `buildozer.spec`: configuração para gerar APK.
- `requirements-android.txt`: dependências para testar no computador.
- `OSM Pisos/`: dados OSM necessários para construir o grafo.
- `Imagens ECT2/`: imagens e calibrações usadas no mapa interior.

## Fluxo da app

1. Página inicial: escolha do perfil (`Normal` ou `Mobilidade reduzida`).
2. Página de planeamento: seleção de origem/destino e visualização do mapa.
3. Página de navegação: resumo da rota, instrução do passo atual, mapa, botão `Próximo ponto` e botão `Cancelar`.

## Testar no computador

```bash
cd "app movel"
pip install -r requirements-android.txt
python app_android.py
```

## Gerar APK

Usa Linux ou WSL:

```bash
cd "app movel"
pip install buildozer
buildozer android debug
```

O APK fica em `bin/`.

## Validação

Os scripts de validação ficam na raiz do repositório e testam desktop e mobile:

```bash
python validar_osm.py
python testar_rotas.py
```
