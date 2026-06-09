# Navegação Pedestre Indoor na UTAD

Projecto de navegação pedestre para o campus da Universidade de Trás-os-Montes e Alto Douro (UTAD), com foco em percursos interiores representados através de ficheiros OpenStreetMap (`.osm`).

O script principal lê os ficheiros OSM dos pisos, constrói um grafo com `NetworkX`, calcula distâncias entre nós e permite encontrar caminhos entre uma origem e um destino.

## Estrutura do projecto

```text
.
├── navegacao_campus_vscode.py
├── OSM Pisos/
│   ├── Exterior.osm
│   ├── Piso1.osm
│   ├── Piso2.osm
│   └── Piso3.osm
├── README.md
├── requirements.txt
└── .gitignore
```

Notas:

- A pasta `OSM Pisos/` contém os ficheiros de dados usados pelo script.
- Os ficheiros `.osm` não devem ser alterados sem validação prévia, porque representam a base cartográfica do projecto.
- A pasta `Nova pasta/`, PDFs de apoio e imagens locais de WhatsApp são ignorados no Git.

## Requisitos

- Python 3.10 ou superior recomendado.
- `pip` para instalar dependências.

Dependências Python:

- `matplotlib`
- `networkx`

## Instalação

No terminal, dentro da pasta do projecto:

```bash
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
pip install -r requirements.txt
```

## Como correr

Executar o script com o piso por defeito (`Piso1`):

```bash
python navegacao_campus_vscode.py
```

Escolher um piso:

```bash
python navegacao_campus_vscode.py --piso Piso1
python navegacao_campus_vscode.py --piso Piso2
python navegacao_campus_vscode.py --piso Piso3
python navegacao_campus_vscode.py --piso Exterior
```

Indicar origem e destino:

```bash
python navegacao_campus_vscode.py --piso Piso2 --origem 32 --destino 19
```

Usar directamente um ficheiro OSM:

```bash
python navegacao_campus_vscode.py --ficheiro "OSM Pisos/Piso3.osm" --origem 1 --destino 38
```

## Protótipo desktop

Também existe uma primeira interface desktop para validar a navegação passo a passo antes de avançar para uma futura aplicação móvel.

Executar:

```bash
python app_desktop.py
```

Na interface é possível:

- escolher perfil normal ou mobilidade reduzida;
- escolher piso de origem e piso de destino;
- seleccionar origem e destino;
- calcular a rota;
- ver a imagem do piso posicionada/rodada segundo a calibração do JOSM/PicLayer;
- ver tiles OpenStreetMap Carto como fundo do grafo exterior;
- navegar entre pisos através de escadas/elevador quando existem ligações nos dados OSM;
- confirmar a chegada a cada ponto antes de receber a próxima indicação.

## Protótipo Android

Na branch `android-app-prototype` existe uma segunda aplicação Python pensada para Android, baseada na lógica do protótipo desktop mas construída com Kivy.

A app móvel está isolada na pasta `app movel/`. Essa pasta contém os ficheiros da aplicação e cópias das pastas necessárias para funcionar de forma autónoma:

- `app movel/OSM Pisos/`;
- `app movel/Imagens ECT2/`.

Ficheiros principais:

- `app movel/navigation_core.py`: lógica comum de grafos, filtros por edifício/piso, cálculo de rota e texto de navegação;
- `app movel/app_android.py`: interface móvel em Kivy;
- `app movel/main.py`: ponto de entrada usado pelo Buildozer;
- `app movel/buildozer.spec`: configuração inicial para gerar APK;
- `app movel/requirements-android.txt`: dependências da app móvel.

A app Android carrega tiles OpenStreetMap Carto como fundo do mapa exterior, mostra a atribuição no mapa e guarda cache local dos tiles já usados. Para isso, o `buildozer.spec` inclui a permissão Android `INTERNET`.

Para testar no computador, instala as dependências Android:

```bash
cd "app movel"
pip install -r requirements-android.txt
```

E executa:

```bash
python app_android.py
```

Para gerar APK, usa Linux ou WSL com Buildozer instalado:

```bash
cd "app movel"
pip install buildozer
buildozer android debug
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
