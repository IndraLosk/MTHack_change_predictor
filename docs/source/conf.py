"""Конфигурация Sphinx для документации по коду MTHack_change_predictor."""

import os
import sys

# Пути к исходникам backend и ml-model
BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(BASE, "backend"))
sys.path.insert(0, os.path.join(BASE, "ml-model"))

# Заглушки для сторонних библиотек — чтобы autodoc не спотыкался на импортах,
# если они не установлены на машине сборки.
autodoc_mock_imports = [
    "fastapi",
    "httpx",
    "asyncpg",
    "pandas",
    "numpy",
    "lightgbm",
    "pydantic",
    "uvicorn",
]

project = "MTHack_change_predictor"
author = "MTHack team"
copyright = "2026, MTHack team"
release = "1.0"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.githubpages",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# Докстринги могут использовать Numpy/Google-стиль — napoleon это обрабатывает.
napoleon_google_docstring = True
napoleon_numpy_docstring = True

html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]