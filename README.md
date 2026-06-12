# Navegação Pedestre Indoor na UTAD

Projecto de navegação pedestre indoor/outdoor para o campus da Universidade de Trás-os-Montes e Alto Douro (UTAD), com foco na navegação entre edifícios, pisos e salas a partir de dados OpenStreetMap (`.osm`) preparados no JOSM.

A aplicação lê os ficheiros OSM dos pisos e do exterior, constrói um grafo navegável, calcula rotas com Dijkstra e apresenta instruções passo a passo. O utilizador pode escolher perfil normal ou mobilidade reduzida; a rota é ajustada para evitar elevador no perfil normal e escadas no perfil de mobilidade reduzida.

## Estrutura do projecto

```text
.
├── App Desktop/
│   ├── app_desktop.py
│   ├── navegacao_campus_vscode.py
│   ├── navigation_core.py
│   ├── requirements.txt
│   ├── OSM Pisos/
│   │   ├── Exterior.osm
│   │   ├── Piso1.osm
│   │   ├── Piso2.osm
│   │   └── Piso3.osm
│   └── Imagens ECT2/
├── validar_osm.py
├── testar_rotas.py
├── app movel/
│   ├── app_android.py
│   ├── main.py
│   ├── navigation_core.py
│   ├── buildozer.spec
│   ├── OSM Pisos/
│   └── Imagens ECT2/
├── README.md
└── .gitignore
```

Notas:

- A pasta `App Desktop/` contém a aplicação desktop completa, incluindo core próprio, OSM, imagens e requisitos.
- A pasta `app movel/` contém uma versão autónoma para Kivy/Android, com cópias próprias dos OSM e imagens.
- As duas aplicações têm `navigation_core.py` próprio para não dependerem uma da outra.
- Cada pasta de aplicação tem um README próprio com instruções específicas.
- Os ficheiros `.osm` não devem ser alterados sem validação prévia, porque representam a base cartográfica do projecto.
- A pasta `Nova pasta/`, PDFs de apoio e imagens locais de WhatsApp são ignorados no Git.

## Requisitos

- Python 3.10 ou superior recomendado.
- `pip` para instalar dependências.

Dependências Python:

- `matplotlib`
- `networkx`

Notas:

- `matplotlib` é usado pela app desktop para desenhar mapas, grafos e rotas.
- `networkx` mantém compatibilidade com scripts de exploração/legado.
- A app Android usa Kivy e tem dependências próprias em `app movel/requirements-android.txt`.

## Instalação

No terminal, dentro da pasta da app desktop:

```bash
cd "App Desktop"
python -m venv .venv
```

Activar o ambiente virtual no Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Instalar as dependências:

```bash
pip install -r requirements.txt
```

Se estiveres a usar Anaconda, podes criar um ambiente equivalente:

```bash
conda create -n navegacao-utad python=3.11
conda activate navegacao-utad
cd "App Desktop"
pip install -r requirements.txt
```

## Como correr o script base

Executar o script com o piso por defeito (`Piso1`):

```bash
cd "App Desktop"
python navegacao_campus_vscode.py
```

Escolher um piso:

```bash
cd "App Desktop"
python navegacao_campus_vscode.py --piso Piso1
python navegacao_campus_vscode.py --piso Piso2
python navegacao_campus_vscode.py --piso Piso3
python navegacao_campus_vscode.py --piso Exterior
```

Indicar origem e destino:

```bash
cd "App Desktop"
python navegacao_campus_vscode.py --piso Piso2 --origem 32 --destino 19
```

Usar directamente um ficheiro OSM:

```bash
cd "App Desktop"
python navegacao_campus_vscode.py --ficheiro "OSM Pisos/Piso3.osm" --origem 1 --destino 38
```

## Protótipo desktop

A interface desktop é a versão principal de validação funcional. Permite testar a navegação passo a passo antes de gerar a app móvel.

Executar:

```bash
cd "App Desktop"
python app_desktop.py
```

Na interface é possível:

- escolher perfil normal ou mobilidade reduzida;
- escolher edifício, piso/área e ponto de origem/destino;
- seleccionar origem e destino;
- calcular a rota;
- ver a imagem do piso posicionada/rodada segundo a calibração do JOSM/PicLayer;
- ver tiles OpenStreetMap Carto como fundo do grafo exterior;
- navegar entre pisos através de escadas/elevador quando existem ligações nos dados OSM;
- receber instruções como "segue pela calçada", "atravessa a estrada", "entra no edifício pelas escadas" ou "sobe pelo elevador até ao Piso 3";
- fazer zoom, centrar a vista no ponto atual e arrastar o mapa com o rato;
- confirmar a chegada a cada ponto antes de receber a próxima indicação.

### Regras de rota

- O cálculo usa Dijkstra.
- Cada aresta tem `length` com a distância real em metros.
- O campo `weight` é o custo interno usado pelo Dijkstra e pode ser ponderado por `edge_type`, para preferir caminhos mais confortáveis quando existem alternativas semelhantes.
- O perfil normal não usa elevador.
- O perfil de mobilidade reduzida não usa escadas nem pontos marcados apenas para acesso por escadas.
- As instruções usam `edge_type` para distinguir calçada, passadeira, estrada, rampa, escadas, elevador e ligações entre pisos.

## Testes automáticos

Antes de fazer commit, ou antes de gerar um novo APK, é possível validar as
rotas principais da app desktop e da app móvel:

```bash
python testar_rotas.py
```

O teste confirma que existem rotas para os percursos críticos, que o perfil de
mobilidade reduzida usa o elevador quando necessário, que o perfil normal não
usa elevador, que origem/destino incompatíveis com o perfil são recusados, e
que instruções importantes como passadeira, calçada, estrada, escadas e elevador
continuam coerentes.

Também existe um validador específico para os ficheiros OSM:

```bash
python validar_osm.py
```

Este validador verifica, entre outros pontos:

- `nodeID` duplicados;
- tags obrigatórias em nodes/ways;
- `edge_type` válido;
- `accessibility` válida;
- consistência mínima das rotas críticas nos datasets desktop e mobile.

## Protótipo Android

Existe uma segunda aplicação Python pensada para Android, baseada na mesma lógica do protótipo desktop mas construída com Kivy e isolada numa pasta própria.

A app móvel está isolada na pasta `app movel/`. Essa pasta contém os ficheiros da aplicação e cópias das pastas necessárias para funcionar de forma autónoma:

- `app movel/OSM Pisos/`;
- `app movel/Imagens ECT2/`.

Ficheiros principais:

- `app movel/navigation_core.py`: lógica própria da app móvel para grafos, filtros por edifício/piso/perfil, cálculo de rota e texto de navegação;
- `app movel/app_android.py`: interface móvel em Kivy;
- `app movel/main.py`: ponto de entrada usado pelo Buildozer;
- `app movel/buildozer.spec`: configuração inicial para gerar APK;
- `app movel/requirements-android.txt`: dependências da app móvel.

A app Android carrega tiles OpenStreetMap Carto como fundo do mapa exterior, mostra a atribuição no mapa e guarda cache local dos tiles já usados. Para isso, o `buildozer.spec` inclui a permissão Android `INTERNET`.

Tal como no desktop, a app móvel suporta:

- escolha inicial do perfil;
- seleção por edifício, piso/área e ponto;
- mapa com zoom por botões, recentrar e gesto pinch-to-zoom no telemóvel;
- navegação passo a passo com confirmação de chegada;
- filtros de mobilidade reduzida.

Para testar no computador, instala as dependências Android:

```bash
cd "app movel"
pip install -r requirements-android.txt
```

E executa:

```bash
python app_android.py
```

Para gerar APK, usa Linux ou WSL com Buildozer instalado. O `python-for-android`
não aceita caminhos com espaços no diretório de build, por isso devo copiar a
app móvel para uma pasta Linux/WSL sem espaços antes de compilar:

```bash
cd /caminho/para/o/repositorio
rm -rf ~/navegacao-utad-mobile
cp -r "app movel" ~/navegacao-utad-mobile
cd ~/navegacao-utad-mobile
pip install buildozer
buildozer -v android debug
```

O APK gerado ficará na pasta `bin/`, que é ignorada pelo Git.

## Estado do repositório

Este projecto está preparado para ser usado com o repositório:

```text
https://github.com/al30528/Projecto-de-Licenciatura
```

Depois de teres Git instalado e disponível no terminal, podes inicializar e ligar ao remoto com:

```bash
git init
git remote add origin https://github.com/al30528/Projecto-de-Licenciatura.git
git status
```
