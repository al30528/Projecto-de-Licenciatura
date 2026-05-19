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

