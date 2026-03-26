from __future__ import annotations

import shutil
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

from app.config import SERVERS_DATA_DIR, TEMPLATES_DIR
from app.models import TemplateResponse, TemplateVariable


class TemplateEngine:
    def __init__(self) -> None:
        self.templates_dir = TEMPLATES_DIR
        self.servers_dir = SERVERS_DATA_DIR

    def list_templates(self) -> list[TemplateResponse]:
        results = []
        if not self.templates_dir.exists():
            return results
        for template_dir in sorted(self.templates_dir.iterdir()):
            meta_path = template_dir / "template.yaml"
            if meta_path.exists():
                meta = self._load_meta(meta_path)
                if meta:
                    results.append(meta)
        return results

    def get_template(self, name: str) -> TemplateResponse | None:
        meta_path = self.templates_dir / name / "template.yaml"
        if not meta_path.exists():
            return None
        return self._load_meta(meta_path)

    def render(self, template_name: str, server_name: str, config: dict[str, str]) -> Path:
        template_dir = self.templates_dir / template_name
        if not template_dir.exists():
            raise ValueError(f"Template '{template_name}' not found")

        meta_path = template_dir / "template.yaml"
        meta = self._load_meta(meta_path)
        if not meta:
            raise ValueError(f"Invalid template metadata for '{template_name}'")

        # Build template variables: defaults + user config
        variables = {"server_name": server_name}
        for var in meta.variables:
            if var.name in config:
                variables[var.name] = config[var.name]
            elif var.default is not None:
                variables[var.name] = var.default
            elif var.required:
                raise ValueError(f"Required variable '{var.name}' not provided")

        # Render Jinja2 templates into build context
        output_dir = self.servers_dir / server_name
        output_dir.mkdir(parents=True, exist_ok=True)

        env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            keep_trailing_newline=True,
        )

        for j2_file in template_dir.glob("*.j2"):
            template = env.get_template(j2_file.name)
            rendered = template.render(**variables)
            output_name = j2_file.stem  # strip .j2
            (output_dir / output_name).write_text(rendered)

        # Copy any non-j2, non-yaml files directly
        for f in template_dir.iterdir():
            if f.suffix not in (".j2", ".yaml") and f.is_file():
                shutil.copy2(f, output_dir / f.name)

        return output_dir

    def cleanup(self, server_name: str) -> None:
        output_dir = self.servers_dir / server_name
        if output_dir.exists():
            shutil.rmtree(output_dir)

    def _load_meta(self, path: Path) -> TemplateResponse | None:
        try:
            data = yaml.safe_load(path.read_text())
            variables = [
                TemplateVariable(**v) for v in data.get("variables", [])
            ]
            return TemplateResponse(
                name=data["name"],
                description=data.get("description", ""),
                variables=variables,
            )
        except Exception:
            return None
