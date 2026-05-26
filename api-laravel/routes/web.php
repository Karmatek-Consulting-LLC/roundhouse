<?php

use Illuminate\Support\Facades\Route;

// SPA fallback - any non-/api/* path that Laravel doesn't route (i.e. client-side
// routes like /servers, /login) returns the React shell. Assets under /frontend/*
// are served statically by Octane/Swoole before this ever fires.
Route::fallback(function () {
    if (request()->is('api/*')) {
        abort(404);
    }
    return response()->file(public_path('frontend/index.html'));
});
