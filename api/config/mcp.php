<?php

return [
    /*
     * Base URL reported to clients for MCP server endpoints.
     * Matches MCP_BASE_URL in the Python platform app.
     */
    'base_url' => env('MCP_BASE_URL', 'http://localhost:3080'),

    /*
     * Docker network the MCP server containers/services join.
     */
    'docker_network' => env('MCP_DOCKER_NETWORK', 'mcp-network'),

    /*
     * Docker daemon unix socket path.
     */
    'docker_socket' => env('MCP_DOCKER_SOCKET', '/var/run/docker.sock'),

    /*
     * Where ServerSpec JSON files + build contexts live on disk.
     */
    'servers_data_dir' => env('MCP_SERVERS_DATA_DIR', storage_path('app/servers')),

    /*
     * Directory containing template bundles (template.yaml + *.j2).
     */
    'templates_dir' => env('MCP_TEMPLATES_DIR')
        ? (str_starts_with(env('MCP_TEMPLATES_DIR'), '/') ? env('MCP_TEMPLATES_DIR') : base_path(env('MCP_TEMPLATES_DIR')))
        : base_path('../templates'),

    /*
     * Directory Traefik watches for dynamic config (TLS etc.).
     */
    'traefik_dynamic_dir' => env('MCP_TRAEFIK_DYNAMIC_DIR', storage_path('app/traefik/dynamic')),

    /*
     * Directory where uploaded TLS certs are staged for Traefik.
     */
    'traefik_certs_dir' => env('MCP_TRAEFIK_CERTS_DIR', storage_path('app/traefik/certs')),

    /*
     * Traefik entrypoints applied to generated routers.
     */
    'traefik_entrypoints' => env('MCP_TRAEFIK_ENTRYPOINTS', 'web,websecure'),

    /*
     * Replica bounds for Swarm-mode services.
     */
    'default_server_replicas' => (int) env('MCP_DEFAULT_SERVER_REPLICAS', 1),
    'max_server_replicas' => (int) env('MCP_MAX_SERVER_REPLICAS', 32),
];
