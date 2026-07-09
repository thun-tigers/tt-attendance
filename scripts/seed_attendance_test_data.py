#!/usr/bin/env python3
"""Seed deterministic attendance test data for the local stack.

The script uses the users already present in the tt-attendance database and
creates or updates attendance rows for upcoming trainings from tt-agenda.
"""

from __future__ import annotations

import argparse
import math
import random
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from app import create_app
from app.extensions import db
from app.jwt_utils import fetch_trainings_from_agenda_for_teams
from app.models import Attendance, User


DEFAULT_RATIOS = (
    ('attending', 0.7),
    ('maybe', 0.1),
    ('declined', 0.2),
)


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


def _resolve_users(user_ids: list[int] | None = None) -> list[User]:
    query = User.query.order_by(User.id.asc())
    if user_ids:
        query = User.query.filter(User.id.in_(user_ids)).order_by(User.id.asc())
    users = query.all()
    return users


def _resolve_trainings(args) -> list[dict]:
    if args.training_id:
        return [{'id': training_id} for training_id in args.training_id]

    team_codes = args.team_code or None
    trainings = fetch_trainings_from_agenda_for_teams(team_codes=team_codes, limit=args.limit)
    return trainings


def _seed_training(training: dict, users: list[User], seed: int, clear_existing: bool) -> SeedResult:
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
        attendance = Attendance.query.filter_by(training_id=training_id, user_id=user.id).first()
        if attendance is None:
            attendance = Attendance(training_id=training_id, user_id=user.id, status=status)
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
    app = create_app()

    with app.app_context():
        users = _resolve_users(args.user_id or None)
        if not users:
            print(
                'No users found in the tt-attendance database. '
                'Sync users first or pass --user-id explicitly.',
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
                f"for {result.total_users} users"
            )

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
