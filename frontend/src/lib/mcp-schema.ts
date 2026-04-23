import type {
  McpJsonSchema,
  McpLivePrompt,
  McpLiveResource,
  McpLiveTool,
  Primitive,
  ToolParameter,
} from "@/lib/api";

/**
 * Adapters from live MCP /tools/list /resources/list /prompts/list responses
 * to our internal Primitive shape, so the existing TestPrimitiveDialog can be
 * reused against code-mode servers where we don't have a stored spec.
 */

const JSON_SCHEMA_TO_PYTHON_TYPE: Record<string, string> = {
  string: "str",
  integer: "int",
  number: "float",
  boolean: "bool",
  array: "list",
  object: "dict",
};

function pyType(t: string | undefined): string {
  if (!t) return "str";
  return JSON_SCHEMA_TO_PYTHON_TYPE[t] ?? "str";
}

function toolParametersFromSchema(schema: McpJsonSchema | undefined): ToolParameter[] {
  if (!schema?.properties) return [];
  const required = new Set(schema.required ?? []);
  return Object.entries(schema.properties).map(([name, prop]) => ({
    name,
    type: pyType(prop?.type),
    description: prop?.description ?? "",
    required: required.has(name),
    default:
      prop?.default !== undefined && prop?.default !== null
        ? String(prop.default)
        : null,
  }));
}

export function liveToolToPrimitive(t: McpLiveTool): Primitive {
  return {
    kind: "tool",
    name: t.name,
    description: t.description ?? "",
    parameters: toolParametersFromSchema(t.inputSchema),
    code: "",
    // FastMCP's tools/list doesn't include return_type - default str.
    return_type: "str",
  };
}

export function liveResourceToPrimitive(r: McpLiveResource): Primitive {
  if (r.uriTemplate) {
    return {
      kind: "resource_template",
      name: r.name,
      uri_template: r.uriTemplate,
      description: r.description ?? "",
      mime_type: r.mimeType ?? "text/plain",
      code: "",
    };
  }
  return {
    kind: "resource",
    name: r.name,
    uri: r.uri ?? "",
    description: r.description ?? "",
    mime_type: r.mimeType ?? "text/plain",
    code: "",
  };
}

export function livePromptToPrimitive(p: McpLivePrompt): Primitive {
  const parameters: ToolParameter[] = (p.arguments ?? []).map((arg) => ({
    name: arg.name,
    type: "str",
    description: arg.description ?? "",
    required: arg.required ?? false,
    default: null,
  }));
  return {
    kind: "prompt",
    name: p.name,
    description: p.description ?? "",
    parameters,
    code: "",
  };
}
