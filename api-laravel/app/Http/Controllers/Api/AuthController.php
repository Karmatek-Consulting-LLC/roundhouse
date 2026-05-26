<?php

namespace App\Http\Controllers\Api;

use App\Http\Controllers\Controller;
use App\Http\Requests\Auth\ChangePasswordRequest;
use App\Http\Requests\Auth\LoginRequest;
use App\Http\Requests\Auth\RegisterRequest;
use App\Models\User;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Http\Response;
use Illuminate\Support\Facades\Hash;
use Symfony\Component\HttpKernel\Exception\HttpException;

class AuthController extends Controller
{
    public function login(LoginRequest $request): JsonResponse
    {
        $user = User::query()->where('email', $request->string('email'))->first();

        if (! $user || ! Hash::check($request->string('password'), $user->password_hash)) {
            throw new HttpException(401, 'Invalid email or password');
        }

        $token = $user->createToken('api')->plainTextToken;

        return response()->json([
            'access_token' => $token,
            'token_type' => 'bearer',
            'user' => $user->toApiArray(),
        ]);
    }

    public function me(Request $request): JsonResponse
    {
        return response()->json($request->user()->toApiArray());
    }

    public function changePassword(ChangePasswordRequest $request): Response
    {
        $user = $request->user();

        if (! Hash::check($request->string('current_password'), $user->password_hash)) {
            throw new HttpException(400, 'Current password is incorrect');
        }

        if ((string) $request->string('current_password') === (string) $request->string('new_password')) {
            throw new HttpException(400, 'New password must be different from your current password');
        }

        $user->password_hash = Hash::make($request->string('new_password'));
        $user->save();

        return response()->noContent();
    }

    public function register(RegisterRequest $request): JsonResponse
    {
        if (User::query()->where('email', $request->string('email'))->exists()) {
            throw new HttpException(409, 'Email already registered');
        }

        $user = User::query()->create([
            'email' => (string) $request->string('email'),
            'password_hash' => Hash::make($request->string('password')),
            'display_name' => (string) $request->string('display_name'),
            'role' => (string) $request->string('role', 'user'),
        ]);

        return response()->json($user->toApiArray(), 201);
    }
}
