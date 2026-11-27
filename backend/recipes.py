"""Persist the saved recipes and the console's selection in one file."""

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


class RecipeLibrary:
    def __init__(self, default_recipe, path=None):
        self.path = Path(path) if path is not None else None
        self.recipes = {default_recipe['id']: default_recipe}
        self.selected = default_recipe['id']
        if self.path is not None:
            try:
                data = json.loads(self.path.read_text(encoding='utf-8'))
            except FileNotFoundError:
                return
            self.recipes = data['recipes']
            self.selected = data['selected']
            if self.selected not in self.recipes:
                raise ValueError(f'The selected recipe is missing from {self.path}.')

    def update(self, recipes, selected):
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent, delete=False) as file:
                temporary_path = Path(file.name)
                try:
                    json.dump({'recipes': recipes, 'selected': selected}, file, ensure_ascii=False, allow_nan=False)
                    file.flush()
                    os.fsync(file.fileno())
                    file.close()
                    temporary_path.replace(self.path)
                finally:
                    temporary_path.unlink(missing_ok=True)
        self.recipes = recipes
        self.selected = selected
