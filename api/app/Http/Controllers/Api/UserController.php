<?php

namespace App\Http\Controllers\Api;

use App\Http\Controllers\Controller;
use App\Models\User;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Http\Response;
use Illuminate\Support\Facades\Hash;
use Symfony\Component\HttpKernel\Exception\HttpException;

class UserController extends Controller
{
    public function index(): JsonResponse
    {
        $users = User::query()->orderBy('email')->get();
        return response()->json($users->map->toApiArray());
    }

    public function show(string $userId): JsonResponse
    {
        return response()->json($this->findOr404($userId)->toApiArray());
    }

    public function setPassword(Request $request, string $userId): Response
    {
        $data = $request->validate([
            'new_password' => ['required', 'string', 'min:8', 'max:256'],
        ]);

        $user = $this->findOr404($userId);
        $user->password_hash = Hash::make($data['new_password']);
        $user->save();

        return response()->noContent();
    }

    public function destroy(Request $request, string $userId): Response
    {
        if ((string) $request->user()->id === $userId) {
            throw new HttpException(400, 'Cannot delete yourself');
        }

        $user = $this->findOr404($userId);
        $user->delete();

        return response()->noContent();
    }

    private function findOr404(string $userId): User
    {
        $user = User::query()->find($userId);
        if (! $user) {
            throw new HttpException(404, 'User not found');
        }
        return $user;
    }
}
