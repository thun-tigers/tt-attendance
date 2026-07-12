from collections import defaultdict
from datetime import date, datetime, timezone

from .extensions import db
from .models import Attendance, AttendanceEligibility
from .jwt_utils import fetch_team_members_from_auth


def _required_roles(training):
    return set((training.get('category_meta') or {}).get('required_for') or ['player'])


def ensure_eligibility(training):
    """Create the denominator snapshot once for an occurrence."""
    training_id = str(training.get('id'))
    if not training_id or training.get('is_cancelled'):
        return []
    existing = AttendanceEligibility.query.filter_by(training_id=training_id).all()
    if existing:
        return existing
    team_code = (training.get('team_code') or '').strip().upper()
    required_roles = _required_roles(training)
    as_of = training.get('date')
    members = fetch_team_members_from_auth(team_code, as_of=as_of)
    now = datetime.now(timezone.utc)
    rows = []
    seen = set()
    for member in members:
        user_id = member.get('auth_user_id')
        if not user_id or user_id in seen:
            continue
        seen.add(user_id)
        role = (member.get('member_role') or 'player').strip().lower()
        rows.append(AttendanceEligibility(
            training_id=training_id,
            user_id=int(user_id),
            team_code=team_code,
            member_role=role,
            response_required=role in required_roles,
            presence_tracked=bool((training.get('category_meta') or {}).get('show_presence_tracking', True)),
            source='approximated' if not member.get('valid_from') else 'category',
            snapshot_at=now,
        ))
    if rows:
        db.session.add_all(rows)
        db.session.commit()
    return rows


def summarize(training, ensure=True):
    eligibility = ensure_eligibility(training) if ensure else AttendanceEligibility.query.filter_by(training_id=str(training.get('id'))).all()
    required = [row for row in eligibility if row.response_required]
    required_ids = {row.user_id for row in required}
    attendances = Attendance.query.filter(Attendance.training_id == str(training.get('id')), Attendance.user_id.in_(required_ids or {-1})).all()
    by_user = {row.user_id: row for row in attendances}
    status = {'attending': 0, 'maybe': 0, 'declined': 0, 'no_response': 0}
    for user_id in required_ids:
        value = by_user.get(user_id)
        status[value.status if value and value.status in status else 'no_response'] += 1
    present = sum(1 for row in attendances if row.presence_status == 'present')
    unexcused = sum(1 for row in attendances if row.presence_status == 'unexcused')
    responded = len(attendances)
    return {
        'training_id': str(training.get('id')),
        'title': training.get('title'),
        'date': training.get('date'),
        'team_code': training.get('team_code'),
        'category': training.get('category'),
        'approximate': any(row.source == 'approximated' for row in eligibility),
        'eligible': len(required),
        'responded': responded,
        'status': status,
        'presence': {'present': present, 'unexcused': unexcused, 'tracked': present + unexcused},
        'response_rate': round(responded / len(required), 4) if required else None,
        'attendance_rate': round(present / (present + unexcused), 4) if (present + unexcused) else None,
    }


def aggregate(trainings):
    rows = [summarize(training) for training in trainings if not training.get('is_cancelled')]
    totals = defaultdict(int)
    for row in rows:
        totals['eligible'] += row['eligible']
        totals['responded'] += row['responded']
        for key, value in row['status'].items():
            totals[key] += value
        for key, value in row['presence'].items():
            if key != 'tracked':
                totals[key] += value
        totals['presence_tracked'] += row['presence']['tracked']
    totals = dict(totals)
    totals['response_rate'] = round(totals['responded'] / totals['eligible'], 4) if totals.get('eligible') else None
    totals['attendance_rate'] = round(totals.get('present', 0) / totals['presence_tracked'], 4) if totals.get('presence_tracked') else None
    return {'summary': totals, 'trainings': rows}


def personal_aggregate(trainings, user_id):
    rows = []
    for training in trainings:
        if training.get('is_cancelled') or (training.get('date') and training.get('date') > date.today().isoformat()):
            continue
        eligibility = ensure_eligibility(training)
        eligible = next((row for row in eligibility if row.user_id == user_id and row.response_required), None)
        attendance = Attendance.query.filter_by(training_id=str(training.get('id')), user_id=user_id).first()
        status = attendance.status if attendance else 'no_response'
        present = 1 if attendance and attendance.presence_status == 'present' else 0
        unexcused = 1 if attendance and attendance.presence_status == 'unexcused' else 0
        rows.append({'training_id': str(training.get('id')), 'title': training.get('title'), 'date': training.get('date'), 'team_code': training.get('team_code'), 'mine': attendance.status if attendance else None, 'eligible': 1 if eligible else 0, 'responded': 1 if attendance and eligible else 0, 'status': {'attending': int(status == 'attending'), 'maybe': int(status == 'maybe'), 'declined': int(status == 'declined'), 'no_response': int(status == 'no_response')}, 'presence': {'present': present, 'unexcused': unexcused, 'tracked': present + unexcused}, 'response_rate': float(bool(attendance and eligible)), 'attendance_rate': float(bool(present)) if present + unexcused else None})
    summary = defaultdict(int)
    for row in rows:
        summary['eligible'] += row['eligible']; summary['responded'] += row['responded']
        for key, value in row['status'].items(): summary[key] += value
        summary['present'] += row['presence']['present']; summary['unexcused'] += row['presence']['unexcused']; summary['presence_tracked'] += row['presence']['tracked']
    summary = dict(summary)
    summary['response_rate'] = round(summary['responded'] / summary['eligible'], 4) if summary.get('eligible') else None
    summary['attendance_rate'] = round(summary.get('present', 0) / summary['presence_tracked'], 4) if summary.get('presence_tracked') else None
    return {'summary': summary, 'trainings': rows}


def player_aggregate(trainings):
    players = {}
    for training in trainings:
        if training.get('is_cancelled') or (training.get('date') and training.get('date') > date.today().isoformat()):
            continue
        eligibility = ensure_eligibility(training)
        required = {row.user_id for row in eligibility if row.response_required}
        attendance = {row.user_id: row for row in Attendance.query.filter(Attendance.training_id == str(training.get('id')), Attendance.user_id.in_(required or {-1})).all()}
        for user_id in required:
            item = players.setdefault(user_id, {'auth_user_id': user_id, 'eligible': 0, 'responded': 0, 'attending': 0, 'maybe': 0, 'declined': 0, 'no_response': 0, 'present': 0, 'unexcused': 0})
            item['eligible'] += 1
            row = attendance.get(user_id)
            if not row:
                item['no_response'] += 1
                continue
            item['responded'] += 1
            item[row.status] = item.get(row.status, 0) + 1
            if row.presence_status in ('present', 'unexcused'):
                item[row.presence_status] += 1
    for item in players.values():
        item['response_rate'] = round(item['responded'] / item['eligible'], 4) if item['eligible'] else None
        tracked = item['present'] + item['unexcused']
        item['attendance_rate'] = round(item['present'] / tracked, 4) if tracked else None
    return sorted(players.values(), key=lambda item: item['auth_user_id'])
