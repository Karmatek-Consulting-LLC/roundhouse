<?php

use App\Http\Middleware\ForceJsonResponse;
use App\Http\Middleware\RequireSuperadmin;
use Illuminate\Foundation\Application;
use Illuminate\Foundation\Configuration\Exceptions;
use Illuminate\Foundation\Configuration\Middleware;
use Illuminate\Auth\AuthenticationException;
use Illuminate\Http\Request;
use Illuminate\Validation\ValidationException;
use Symfony\Component\HttpKernel\Exception\HttpExceptionInterface;

return Application::configure(basePath: dirname(__DIR__))
    ->withRouting(
        web: __DIR__.'/../routes/web.php',
        api: __DIR__.'/../routes/api.php',
        commands: __DIR__.'/../routes/console.php',
        health: '/up',
        apiPrefix: 'api',
    )
    ->withMiddleware(function (Middleware $middleware): void {
        $middleware->api(prepend: [ForceJsonResponse::class]);
        // Preserve raw Python source byte-for-byte (trailing newlines matter).
        $middleware->trimStrings(except: ['source']);
        $middleware->alias([
            'superadmin' => RequireSuperadmin::class,
        ]);
    })
    ->withExceptions(function (Exceptions $exceptions): void {
        $exceptions->render(function (AuthenticationException $e, Request $request) {
            if ($request->is('api/*') || $request->expectsJson()) {
                return response()->json(['detail' => 'Not authenticated'], 401);
            }
            return null;
        });

        $exceptions->render(function (ValidationException $e, Request $request) {
            if ($request->is('api/*') || $request->expectsJson()) {
                return response()->json([
                    'detail' => $e->validator->errors()->first() ?: $e->getMessage(),
                    'errors' => $e->errors(),
                ], 422);
            }
            return null;
        });

        $exceptions->render(function (\Throwable $e, Request $request) {
            if (! ($request->is('api/*') || $request->expectsJson())) {
                return null;
            }

            $status = $e instanceof HttpExceptionInterface ? $e->getStatusCode() : 500;
            $message = $e->getMessage() ?: 'Server error';

            if ($status >= 500 && ! config('app.debug')) {
                $message = 'Server error';
            }

            return response()->json(['detail' => $message], $status);
        });
    })->create();
