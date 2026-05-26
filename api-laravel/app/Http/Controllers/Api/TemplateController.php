<?php

namespace App\Http\Controllers\Api;

use App\Http\Controllers\Controller;
use App\Services\Mcp\TemplateEngine;
use Illuminate\Http\JsonResponse;
use Symfony\Component\HttpKernel\Exception\HttpException;

class TemplateController extends Controller
{
    public function __construct(private readonly TemplateEngine $engine) {}

    public function index(): JsonResponse
    {
        return response()->json($this->engine->listTemplates());
    }

    public function show(string $name): JsonResponse
    {
        $t = $this->engine->getTemplate($name);
        if (! $t) {
            throw new HttpException(404, 'Template not found');
        }
        return response()->json($t);
    }
}
