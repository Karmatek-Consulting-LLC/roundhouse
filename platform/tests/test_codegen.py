"""Codegen safety tests (run: cd platform && python -m unittest discover -v)."""

import os
import unittest

# app.config is imported transitively; satisfy required env at import time.
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-for-codegen-tests")
os.environ.setdefault("ADMIN_EMAIL", "test@example.com")
os.environ.setdefault("ADMIN_PASSWORD", "test-password")

from app.codegen import generate_server_py
from app.models import ServerSpec, ToolPrimitive


class TestCodegenDocstrings(unittest.TestCase):
    def test_tool_dict_return_type_emits_dict_annotation(self) -> None:
        spec = ServerSpec(
            name="demo",
            primitives=[
                ToolPrimitive(
                    kind="tool",
                    name="get_data",
                    description="Returns a dict",
                    parameters=[],
                    code='return {"a": 1}',
                    return_type="dict",
                )
            ],
        )
        code = generate_server_py(spec)
        compile(code, "<generated>", "exec")
        self.assertIn("def get_data() -> dict:", code)

    def test_double_quotes_in_tool_description_compile(self) -> None:
        spec = ServerSpec(
            name="demo",
            primitives=[
                ToolPrimitive(
                    kind="tool",
                    name="hello",
                    description='This tool returns "Hello, world!"',
                    parameters=[],
                    code='return "ok"',
                )
            ],
        )
        code = generate_server_py(spec)
        compile(code, "<generated>", "exec")
        self.assertNotIn('"""This tool returns', code)
        self.assertIn("'This tool returns", code)


if __name__ == "__main__":
    unittest.main()
