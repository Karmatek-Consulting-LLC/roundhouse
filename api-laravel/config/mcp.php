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
     * Docker daemon endpoint. Either a unix socket path
     * (/var/run/docker.sock) or a tcp://host:port URL pointing at a
     * socket proxy (e.g. tecnativa/docker-socket-proxy).
     *
     * MCP_DOCKER_HOST wins; MCP_DOCKER_SOCKET kept for backwards compat.
     */
    'docker_host' => env('MCP_DOCKER_HOST', env('MCP_DOCKER_SOCKET', '/var/run/docker.sock')),

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
     * Directory Traefik watches for dynamic config (per-server routes).
     */
    'traefik_dynamic_dir' => env('MCP_TRAEFIK_DYNAMIC_DIR', storage_path('app/traefik/dynamic')),

    /*
     * Traefik entrypoints applied to generated per-server routers. TLS is
     * terminated upstream of this stack (cluster ingress / frontend Traefik),
     * so the embedded Traefik only listens on `web` and forwards plain HTTP.
     */
    'traefik_entrypoints' => env('MCP_TRAEFIK_ENTRYPOINTS', 'web'),

    /*
     * Replica bounds for Swarm-mode services.
     */
    'default_server_replicas' => (int) env('MCP_DEFAULT_SERVER_REPLICAS', 1),
    'max_server_replicas' => (int) env('MCP_MAX_SERVER_REPLICAS', 32),
];
