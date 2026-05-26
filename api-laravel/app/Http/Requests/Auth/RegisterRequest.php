<?php

namespace App\Http\Requests\Auth;

use Illuminate\Foundation\Http\FormRequest;
use Illuminate\Validation\Rule;

class RegisterRequest extends FormRequest
{
    public function authorize(): bool
    {
        return true;
    }

    public function rules(): array
    {
        return [
            'email' => ['required', 'string', 'email'],
            'password' => ['required', 'string', 'min:8', 'max:256'],
            'display_name' => ['required', 'string', 'max:255'],
            'role' => ['sometimes', 'string', Rule::in(['user', 'superadmin'])],
        ];
    }
}
