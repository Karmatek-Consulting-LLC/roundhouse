"""Generate server.py and Dockerfile from a ServerSpec."""

from __future__ import annotations

from pathlib import Path

from app.models import (
    Primitive,
    PromptPrimitive,
    ResourcePrimitive,
    ResourceTemplatePrimitive,
    ServerSpec,
    ToolPrimitive,
)

PYTHON_TYPE_MAP = {
    "str": "str",
    "int": "int",
    "float": "float",
    "bool": "bool",
    "list": "list",
    "dict": "dict",
}


def _py_type(t: str) -> str:
    return PYTHON_TYPE_MAP.get(t, "str")


def _param_signature(params: list) -> str:
    """Build a Python function signature from parameters."""
    parts = []
    # Required params first, then optional
    required = [p for p in params if p.required]
    optional = [p for p in params if not p.required]
    for p in required:
        parts.append(f"{p.name}: {_py_type(p.type)}")
    for p in optional:
        default = repr(p.default) if p.default is not None else "None"
        parts.append(f"{p.name}: {_py_type(p.type)} = {default}")
    return ", ".join(parts)


def _indent(code: str, level: int = 1) -> str:
    """Indent a block of code."""
    prefix = "    " * level
    lines = code.rstrip().split("\n")
    return "\n".join(prefix + line if line.strip() else "" for line in lines)


def _generate_tool(tool: ToolPrimitive) -> str:
    sig = _param_signature(tool.parameters)
    body = tool.code.strip() if tool.code.strip() else 'return "Not implemented"'
    docstring = tool.description or tool.name
    return f'''
@mcp.tool()
def {tool.name}({sig}) -> str:
    """{docstring}"""
{_indent(body)}
'''


def _generate_resource(res: ResourcePrimitive) -> str:
    body = res.code.strip() if res.code.strip() else f'return "{res.name}"'
    docstring = res.description or res.name
    return f'''
@mcp.resource("{res.uri}")
def {res.name}() -> str:
    """{docstring}"""
{_indent(body)}
'''


def _generate_resource_template(rt: ResourceTemplatePrimitive) -> str:
    # Extract parameter names from URI template like {param}
    import re
    param_names = re.findall(r"\{(\w+)\}", rt.uri_template)
    sig = ", ".join(f"{p}: str" for p in param_names)
    body = rt.code.strip() if rt.code.strip() else f'return "{rt.name}"'
    docstring = rt.description or rt.name
    return f'''
@mcp.resource("{rt.uri_template}")
def {rt.name}({sig}) -> str:
    """{docstring}"""
{_indent(body)}
'''


def _generate_prompt(prompt: PromptPrimitive) -> str:
    sig = _param_signature(prompt.parameters)
    body = prompt.code.strip() if prompt.code.strip() else 'return "Not implemented"'
    docstring = prompt.description or prompt.name
    return f'''
@mcp.prompt()
def {prompt.name}({sig}) -> str:
    """{docstring}"""
{_indent(body)}
'''


def _generate_primitive(p: Primitive) -> str:
    if isinstance(p, ToolPrimitive):
        return _generate_tool(p)
    elif isinstance(p, ResourcePrimitive):
        return _generate_resource(p)
    elif isinstance(p, ResourceTemplatePrimitive):
        return _generate_resource_template(p)
    elif isinstance(p, PromptPrimitive):
        return _generate_prompt(p)
    return ""


def generate_server_py(spec: ServerSpec) -> str:
    """Generate a complete FastMCP server.py from a ServerSpec."""
    primitives_code = "\n".join(
        _generate_primitive(p) for p in spec.primitives
    )

    lines = [
        'from fastmcp import FastMCP',
        '',
        f'mcp = FastMCP("{spec.name}")',
        '',
        primitives_code.strip(),
        '',
        'if __name__ == "__main__":',
        '    mcp.run(',
        '        transport="streamable-http",',
        '        host="0.0.0.0",',
        '        port=8000,',
        '        stateless_http=True,',
        '        json_response=True,',
        '    )',
        '',
    ]
    return "\n".join(lines)


def generate_dockerfile(spec: ServerSpec) -> str:
    """Generate a Dockerfile for the server."""
    pip_install = "fastmcp"
    if spec.pip_packages:
        pip_install += " " + " ".join(spec.pip_packages)

    lines = [
        'FROM python:3.12-slim',
        'WORKDIR /app',
        f'RUN pip install --no-cache-dir {pip_install}',
        'COPY server.py .',
        'EXPOSE 8000',
        'CMD ["python", "server.py"]',
        '',
    ]
    return "\n".join(lines)


def write_build_context(spec: ServerSpec, output_dir: Path) -> Path:
    """Write server.py and Dockerfile to the build context directory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "server.py").write_text(generate_server_py(spec))
    (output_dir / "Dockerfile").write_text(generate_dockerfile(spec))
    return output_dir
