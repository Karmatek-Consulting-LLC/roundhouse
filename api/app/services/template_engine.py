"""Load template bundles (template.yaml + *.j2), render with user variables,
copy non-template files to the build context."""
from __future__ import annotations

import shutil
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined  # noqa: F401 - StrictUndefined available for caller use


class TemplateEngine:
    def __init__(self, templates_dir: Path | str, servers_dir: Path | str):
        self.templates_dir = Path(templates_dir)
        self.servers_dir = Path(servers_dir)

    def list_templates(self) -> list[dict]:
        if not self.templates_dir.is_dir():
            return []
        out: list[dict] = []
        for entry in sorted(self.templates_dir.iterdir()):
            meta_path = entry / "template.yaml"
            if meta_path.is_file():
                meta = self._load_meta(meta_path)
                if meta:
                    out.append(meta)
        return out

    def get_template(self, name: str) -> dict | None:
        meta_path = self.templates_dir / name / "template.yaml"
        if not meta_path.is_file():
            return None
        return self._load_meta(meta_path)

    def render(self, template_name: str, server_name: str, config: dict[str, str]) -> Path:
        template_dir = self.templates_dir / template_name
        if not template_dir.is_dir():
            raise ValueError(f"Template '{template_name}' not found")
        meta = self._load_meta(template_dir / "template.yaml")
        if not meta:
            raise ValueError(f"Invalid template metadata for '{template_name}'")

        variables: dict[str, str] = {"server_name": server_name}
        for var in meta["variables"]:
            name = var["name"]
            if name in config:
                variables[name] = config[name]
            elif var["default"] is not None:
                variables[name] = var["default"]
            elif var["required"]:
                raise ValueError(f"Required variable '{name}' not provided")

        output_dir = self.servers_dir / server_name
        output_dir.mkdir(parents=True, exist_ok=True)

        env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            autoescape=False,
            keep_trailing_newline=True,
        )

        for j2 in template_dir.glob("*.j2"):
            rendered = env.get_template(j2.name).render(**variables)
            out_name = j2.name[:-3]  # strip .j2
            (output_dir / out_name).write_text(rendered, encoding="utf-8")

        for entry in template_dir.iterdir():
            if not entry.is_file():
                continue
            ext = entry.suffix.lower().lstrip(".")
            if ext in ("j2", "yaml"):
                continue
            shutil.copy2(entry, output_dir / entry.name)

        return output_dir

    def cleanup(self, server_name: str) -> None:
        d = self.servers_dir / server_name
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)

    def _load_meta(self, path: Path) -> dict | None:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return None
        if not isinstance(data, dict) or not data.get("name"):
            return None
        variables: list[dict] = []
        for v in data.get("variables", []) or []:
            if not isinstance(v, dict) or not v.get("name"):
                continue
            variables.append({
                "name": v["name"],
                "description": v.get("description", ""),
                "default": (None if "default" not in v else str(v["default"])),
                "required": bool(v.get("required", False)),
            })
        return {
            "name": data["name"],
            "description": data.get("description", ""),
            "variables": variables,
        }
