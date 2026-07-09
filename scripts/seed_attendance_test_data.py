#!/usr/bin/env python3
"""Seed deterministic attendance test data for the local stack.

The script pulls active members from tt-auth and creates or updates attendance
rows for upcoming trainings from tt-agenda.
"""

from __future__ import annotations

import argparse
import math
import os
import random
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import requests
from flask import current_app

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))


def _running_inside_container() -> bool:
    return Path('/.dockerenv').exists() or os.environ.get('RUNNING_IN_DOCKER') == '1'


def _default_internal_url(service_name: str, host_port: int) -> str:
    if _running_inside_container():
        return f'http://{service_name}:5000'
    return f'http://localhost:{host_port}'

from app import create_app
from app.config import Config
from app.extensions import db
from app.jwt_utils import fetch_trainings_from_agenda_for_teams
from app.models import Attendance


DEFAULT_RATIOS = (
    ('attending', 0.7),
    ('maybe', 0.1),
    ('declined', 0.2),
)


class SeedConfig(Config):
    """Local-only config for seeding from the developer machine."""

    SECRET_KEY = os.environ.get('SECRET_KEY', 'tt-attendance-dev-secret')
    TT_AUTH_INTERNAL_URL = os.environ.get('TT_AUTH_INTERNAL_URL') or _default_internal_url('tt-auth', 8085)
    TT_MEMBERS_INTERNAL_URL = os.environ.get('TT_MEMBERS_INTERNAL_URL') or _default_internal_url('tt-members', 8088)
    TT_AGENDA_INTERNAL_URL = os.environ.get('TT_AGENDA_INTERNAL_URL') or _default_internal_url('tt-agenda', 8086)
    TT_INFRA_INTERNAL_URL = os.environ.get('TT_INFRA_INTERNAL_URL') or _default_internal_url('tt-infra', 8084)
    INTERNAL_API_SECRET = os.environ.get('INTERNAL_API_SECRET', 'tt-internal-dev-secret-change-me')


@dataclass(frozen=True)
class SeedResult:
    training_id: str
    training_title: str | None
    total_users: int
    counts: dict[str, int]


def _stable_seed(base_seed: int, value: str) -> int:
    token = str(value)
    extra = sum((index + 1) * ord(char) for index, char in enumerate(token))
    return base_seed + extra


def _status_counts(total: int, ratios=DEFAULT_RATIOS) -> dict[str, int]:
    if total <= 0:
        return {status: 0 for status, _ in ratios}

    raw_counts = [total * weight for _, weight in ratios]
    counts = [math.floor(value) for value in raw_counts]
    remaining = total - sum(counts)
    fractions = [raw - count for raw, count in zip(raw_counts, counts)]
    remainder_order = sorted(range(len(ratios)), key=lambda index: (-fractions[index], index))

    for index in remainder_order[:remaining]:
        counts[index] += 1

    return {status: count for (status, _), count in zip(ratios, counts)}


def _build_assignment_list(total: int, rng: random.Random) -> list[str]:
    counts = _status_counts(total)
    assignments: list[str] = []
    for status, _ in DEFAULT_RATIOS:
        assignments.extend([status] * counts[status])
    rng.shuffle(assignments)
    return assignments


def _member_id(member) -> int | None:
    if isinstance(member, dict):
        value = member.get('id')
    else:
        value = getattr(member, 'id', None)
    return int(value) if value is not None else None


def _auth_internal_request(method: str, path: str, *, params=None, json=None):
    auth_base = (current_app.config.get('TT_AUTH_INTERNAL_URL') or 'http://tt-auth:5000').rstrip('/')
    secret = current_app.config.get('INTERNAL_API_SECRET')
    if not secret:
        return None, 'INTERNAL_API_SECRET ist nicht konfiguriert.'

    try:
        response = requests.request(
            method,
            f'{auth_base}{path}',
            params=params,
            json=json,
            headers={'X-TT-Internal-Secret': secret},
            timeout=5,
        )
        return response, None
    except requests.RequestException as exc:
        return None, str(exc)


def _fetch_members_from_auth() -> list[dict]:
    """Load active members from tt-auth via the team-manager list endpoint."""
    last_error = None
    for approver_auth_user_id in range(1, 26):
        response, error = _auth_internal_request(
            'GET',
            '/api/team-manager/members',
            params={'approver_auth_user_id': approver_auth_user_id},
        )
        if error:
            last_error = error
            continue
        if response.status_code != 200:
            last_error = f'{response.status_code} {response.text}'
            continue
        payload = response.json() or {}
        users = payload.get('users') or []
        if users:
            return users
    raise RuntimeError(
        'No members could be loaded from tt-auth. '
        f'Last error: {last_error or "unknown"}'
    )


def _resolve_members(user_ids: list[int] | None = None) -> list[dict]:
    members = _fetch_members_from_auth()
    if user_ids:
        wanted_ids = {int(user_id) for user_id in user_ids}
        members = [member for member in members if _member_id(member) in wanted_ids]
    return members


def _resolve_trainings(args) -> list[dict]:
    if args.training_id:
        return [{'id': training_id} for training_id in args.training_id]

    team_codes = args.team_code or None
    trainings = fetch_trainings_from_agenda_for_teams(team_codes=team_codes, limit=args.limit)
    return trainings


def _seed_training(training: dict, users: list, seed: int, clear_existing: bool) -> SeedResult:
    training_id = str(training['id'])
    rng = random.Random(_stable_seed(seed, training_id))
    assignments = _build_assignment_list(len(users), rng)
    now = datetime.now(timezone.utc)
    counts = {'attending': 0, 'maybe': 0, 'declined': 0}

    if clear_existing:
        Attendance.query.filter_by(training_id=training_id).delete(synchronize_session=False)

    ordered_users = list(users)
    rng.shuffle(ordered_users)

    for user, status in zip(ordered_users, assignments):
        counts[status] += 1
        user_id = _member_id(user)
        if user_id is None:
            continue
        attendance = Attendance.query.filter_by(training_id=training_id, user_id=user_id).first()
        if attendance is None:
            attendance = Attendance(training_id=training_id, user_id=user_id, status=status)
            db.session.add(attendance)
        else:
            attendance.status = status
            attendance.updated_at = now

        attendance.reason = None
        if status == 'attending':
            attendance.presence_status = 'present'
            attendance.presence_marked_at = now
        else:
            attendance.presence_status = None
            attendance.presence_marked_at = None

    return SeedResult(
        training_id=training_id,
        training_title=training.get('title'),
        total_users=len(users),
        counts=counts,
    )


def parse_args(argv: Iterable[str] | None = None):
    parser = argparse.ArgumentParser(
        description='Seed deterministic attendance test data for the local tt-attendance stack.'
    )
    parser.add_argument(
        '--training-id',
        action='append',
        default=[],
        help='Seed only the given training occurrence id. Can be passed multiple times.',
    )
    parser.add_argument(
        '--team-code',
        action='append',
        default=[],
        help='Limit agenda trainings to the given team code. Can be passed multiple times.',
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=None,
        help='Limit the number of agenda trainings fetched when no explicit training id is given.',
    )
    parser.add_argument(
        '--user-id',
        action='append',
        type=int,
        default=[],
        help='Seed only the given user ids. Can be passed multiple times.',
    )
    parser.add_argument(
        '--clear-existing',
        action='store_true',
        help='Delete existing attendance rows for the selected trainings before seeding.',
    )
    parser.add_argument(
        '--seed',
        type=int,
        default=1337,
        help='Base seed for deterministic user shuffling.',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Print the planned distribution without writing to the database.',
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)

    if not _running_inside_container() and not (
        os.environ.get('SQLALCHEMY_DATABASE_URI') or os.environ.get('DATABASE_URL')
    ):
        print(
            'No reachable SQLALCHEMY_DATABASE_URI found on the host. '
            'Run the seed inside the tt-attendance container or export a host-reachable database URL.',
            file=sys.stderr,
        )
        return 1

    app = create_app(SeedConfig)

    with app.app_context():
        try:
            users = _resolve_members(args.user_id or None)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1

        if not users:
            print(
                'No members found in tt-auth. '
                'Make sure tt-auth is running and contains active members.',
                file=sys.stderr,
            )
            return 1

        trainings = _resolve_trainings(args)
        if not trainings:
            print(
                'No trainings found. Pass --training-id or make sure tt-agenda is reachable.',
                file=sys.stderr,
            )
            return 1

        results: list[SeedResult] = []
        for training in trainings:
            results.append(_seed_training(training, users, args.seed, args.clear_existing))

        if args.dry_run:
            db.session.rollback()
            print('Dry run only. Planned assignments:')
        else:
            db.session.commit()
            print('Attendance test data written successfully.')

        for result in results:
            title = f" - {result.training_title}" if result.training_title else ''
            print(
                f"{result.training_id}{title}: "
                f"{result.counts['attending']} attending, "
                f"{result.counts['maybe']} maybe, "
                f"{result.counts['declined']} declined "
                f"for {result.total_users} members"
            )

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
