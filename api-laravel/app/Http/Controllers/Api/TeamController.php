<?php

namespace App\Http\Controllers\Api;

use App\Http\Controllers\Controller;
use App\Models\Team;
use App\Models\TeamMembership;
use App\Models\User;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Http\Response;
use Illuminate\Validation\Rule;
use Symfony\Component\HttpKernel\Exception\HttpException;

class TeamController extends Controller
{
    public function index(Request $request): JsonResponse
    {
        $user = $request->user();
        $query = Team::query()->orderBy('name');

        if (! $user->isSuperadmin()) {
            $teamIds = TeamMembership::query()
                ->where('user_id', $user->id)
                ->pluck('team_id');
            $query->whereIn('id', $teamIds);
        }

        return response()->json($query->get()->map(fn (Team $t) => $this->teamToArray($t)));
    }

    public function store(Request $request): JsonResponse
    {
        $data = $this->validateTeamRequest($request);

        if (Team::query()->where('name', $data['name'])->exists()) {
            throw new HttpException(409, 'Team name already exists');
        }

        $team = Team::query()->create($data);

        return response()->json($this->teamToArray($team), 201);
    }

    public function show(string $teamId): JsonResponse
    {
        return response()->json($this->teamToArray($this->findOr404($teamId)));
    }

    public function update(Request $request, string $teamId): JsonResponse
    {
        $team = $this->findOr404($teamId);
        $this->assertCanManage($request->user(), $team);

        $data = $this->validateTeamRequest($request);
        $team->update($data);

        return response()->json($this->teamToArray($team));
    }

    public function destroy(string $teamId): Response
    {
        $team = $this->findOr404($teamId);
        $team->delete();
        return response()->noContent();
    }

    public function addMember(Request $request, string $teamId): JsonResponse
    {
        $team = $this->findOr404($teamId);
        $this->assertCanManage($request->user(), $team);

        $data = $this->validateMemberRequest($request);
        $target = User::query()->find($data['user_id']);
        if (! $target) {
            throw new HttpException(404, 'User not found');
        }

        $exists = TeamMembership::query()
            ->where('team_id', $team->id)
            ->where('user_id', $target->id)
            ->exists();
        if ($exists) {
            throw new HttpException(409, 'User already in team');
        }

        TeamMembership::query()->create([
            'team_id' => $team->id,
            'user_id' => $target->id,
            'role' => $data['role'] ?? 'member',
        ]);

        return response()->json($this->teamToArray($team->fresh()), 201);
    }

    public function updateMember(Request $request, string $teamId, string $userId): JsonResponse
    {
        $team = $this->findOr404($teamId);
        $this->assertCanManage($request->user(), $team);

        $data = $this->validateMemberRequest($request);

        $membership = $this->findMembership($team->id, $userId);
        $membership->role = $data['role'] ?? 'member';
        $membership->save();

        return response()->json($this->teamToArray($team->fresh()));
    }

    public function removeMember(Request $request, string $teamId, string $userId): JsonResponse
    {
        $team = $this->findOr404($teamId);
        $this->assertCanManage($request->user(), $team);

        $membership = $this->findMembership($team->id, $userId);
        $membership->delete();

        return response()->json($this->teamToArray($team->fresh()));
    }

    private function findOr404(string $teamId): Team
    {
        $team = Team::query()->find($teamId);
        if (! $team) {
            throw new HttpException(404, 'Team not found');
        }
        return $team;
    }

    private function findMembership(string $teamId, string $userId): TeamMembership
    {
        $membership = TeamMembership::query()
            ->where('team_id', $teamId)
            ->where('user_id', $userId)
            ->first();
        if (! $membership) {
            throw new HttpException(404, 'Member not found');
        }
        return $membership;
    }

    private function assertCanManage(User $user, Team $team): void
    {
        if ($user->isSuperadmin()) {
            return;
        }
        $isAdmin = TeamMembership::query()
            ->where('team_id', $team->id)
            ->where('user_id', $user->id)
            ->where('role', 'admin')
            ->exists();
        if (! $isAdmin) {
            throw new HttpException(403, 'Not a team admin');
        }
    }

    private function validateTeamRequest(Request $request): array
    {
        return $request->validate([
            'name' => ['required', 'string', 'max:255'],
            'description' => ['sometimes', 'string'],
        ]);
    }

    private function validateMemberRequest(Request $request): array
    {
        return $request->validate([
            'user_id' => ['required', 'string'],
            'role' => ['sometimes', 'string', Rule::in(['admin', 'member'])],
        ]);
    }

    private function teamToArray(Team $team): array
    {
        $members = $team->memberships()->with('user')->get()->map(function (TeamMembership $m) {
            return [
                'user_id' => (string) $m->user_id,
                'email' => $m->user->email,
                'display_name' => $m->user->display_name,
                'role' => $m->role,
            ];
        });

        return [
            'id' => (string) $team->id,
            'name' => $team->name,
            'description' => $team->description ?? '',
            'members' => $members,
        ];
    }
}
