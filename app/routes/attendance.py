from urllib.parse import urlencode, urljoin, urlparse

from flask import Blueprint, current_app, render_template, request, redirect, url_for, jsonify, flash, session
from ..extensions import db
from ..models import Attendance
from ..forms import AttendanceForm
from ..jwt_utils import (
    get_current_user,
    fetch_trainings_from_agenda_for_teams,
    fetch_training_occurrence_from_agenda,
    fetch_user_from_auth,
    create_sso_token,
    verify_sso_token,
)
from datetime import datetime, timezone
import requests

bp = Blueprint('attendance', __name__)

_WEEKDAY_SHORT = ['MO', 'DI', 'MI', 'DO', 'FR', 'SA', 'SO']


def _format_date_label(date_iso):
    if not date_iso:
        return None
    try:
        dt = datetime.strptime(date_iso, '%Y-%m-%d')
    except ValueError:
        return date_iso
    weekday = _WEEKDAY_SHORT[dt.weekday()]
    return f'{weekday} {dt.strftime("%d.%m.%Y")}'


def _is_safe_url(target):
    if not target:
        return False
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc


def _visible_team_codes(current_user):
    if not current_user:
        return []
    permissions = current_user.get('permissions') or []
    if current_user.get('role') == 'admin' or '*' in permissions:
        return []

    memberships = current_user.get('memberships') or []
    team_codes = []
    for membership in memberships:
        if not isinstance(membership, dict):
            continue
        if membership.get('is_active') is False:
            continue
        team_code = (membership.get('team_code') or '').strip().upper()
        if team_code and team_code not in team_codes:
            team_codes.append(team_code)
    return team_codes


def _auth_login_url(next_page):
    auth_base_url = current_app.config.get('AUTH_BASE_URL', 'http://localhost:8085').rstrip('/')
    query = {'next_service': 'tt-attendance'}
    if next_page:
        query['next'] = next_page
    return f"{auth_base_url}/?{urlencode(query)}"


@bp.route('/login')
def login():
    next_page = request.args.get('next')
    return redirect(_auth_login_url(next_page or url_for('attendance.index')))


def _fetch_active_member_count(team_code):
    team_code = (team_code or '').strip().upper()
    if not team_code:
        return None

    auth_url = current_app.config.get('TT_AUTH_INTERNAL_URL', 'http://tt-auth:5000').rstrip('/')
    secret = current_app.config.get('INTERNAL_API_SECRET')
    try:
        response = requests.get(
            f'{auth_url}/api/internal/teams/{team_code}/active-member-count',
            headers={'X-TT-Internal-Secret': secret},
            timeout=5,
        )
        if response.status_code == 200:
            payload = response.json() or {}
            return int(payload.get('active_player_count', payload.get('active_member_count', 0)))
    except (requests.RequestException, TypeError, ValueError):
        pass
    return None


def _fetch_member_profile(auth_user_id):
    members_url = current_app.config.get('TT_MEMBERS_INTERNAL_URL', 'http://tt-members:5000').rstrip('/')
    secret = current_app.config.get('INTERNAL_API_SECRET')
    try:
        response = requests.get(
            f'{members_url}/api/internal/users/{auth_user_id}',
            headers={'X-TT-Internal-Secret': secret},
            timeout=5,
        )
        if response.status_code == 200:
            payload = response.json() or {}
            return payload.get('user') or {}
    except (requests.RequestException, TypeError, ValueError):
        pass
    return {}


_POSITION_GROUP_FALLBACKS = [
    {'key': 'OL', 'label': 'OL', 'sort_order': 1},
    {'key': 'DL', 'label': 'DL', 'sort_order': 2},
    {'key': 'LB', 'label': 'LB', 'sort_order': 3},
    {'key': 'RB', 'label': 'RB', 'sort_order': 4},
    {'key': 'DB', 'label': 'DB', 'sort_order': 5},
    {'key': 'TE', 'label': 'TE', 'sort_order': 6},
    {'key': 'WR', 'label': 'WR', 'sort_order': 7},
    {'key': 'QB', 'label': 'QB', 'sort_order': 8},
]

_POSITION_BADGE_STYLES = {
    'ALL_PLAYERS': {
        'icon': 'bi-people-fill',
        'classes': 'bg-indigo-500/15 text-indigo-50 border-indigo-400/30',
    },
    'OL': {
        'icon': 'bi-shield',
        'classes': 'bg-sky-500/15 text-sky-50 border-sky-400/30',
    },
    'DL': {
        'icon': 'bi-shield-fill',
        'classes': 'bg-emerald-500/15 text-emerald-50 border-emerald-400/30',
    },
    'LB': {
        'icon': 'bi-compass',
        'classes': 'bg-amber-500/15 text-amber-50 border-amber-400/30',
    },
    'RB': {
        'icon': 'bi-lightning-charge',
        'classes': 'bg-violet-500/15 text-violet-50 border-violet-400/30',
    },
    'DB': {
        'icon': 'bi-broadcast',
        'classes': 'bg-rose-500/15 text-rose-50 border-rose-400/30',
    },
    'TE': {
        'icon': 'bi-collection',
        'classes': 'bg-fuchsia-500/15 text-fuchsia-50 border-fuchsia-400/30',
    },
    'WR': {
        'icon': 'bi-stars',
        'classes': 'bg-cyan-500/15 text-cyan-50 border-cyan-400/30',
    },
    'QB': {
        'icon': 'bi-circle',
        'classes': 'bg-orange-500/15 text-orange-50 border-orange-400/30',
    },
    'UNASSIGNED': {
        'icon': 'bi-question-circle',
        'classes': 'bg-slate-500/15 text-slate-50 border-slate-400/30',
    },
}


def _normalize_position_key(value):
    return (value or '').strip().upper()


def _fetch_position_group_defs():
    infra_url = current_app.config.get('TT_INFRA_INTERNAL_URL', 'http://tt-infra:5000').rstrip('/')
    secret = current_app.config.get('INTERNAL_API_SECRET')
    try:
        response = requests.get(
            f'{infra_url}/api/master-data/positions',
            headers={'X-TT-Internal-Secret': secret},
            timeout=5,
        )
        if response.status_code == 200:
            payload = response.json() or {}
            positions = []
            for item in payload.get('positions') or []:
                key = _normalize_position_key(item.get('key'))
                if not key:
                    continue
                positions.append({
                    'key': key,
                    'label': (item.get('label') or key).strip() or key,
                    'sort_order': int(item.get('sort_order') or 0),
                })
            positions.sort(key=lambda item: (item.get('sort_order') or 0, item['label'], item['key']))
            if positions:
                return positions
    except (requests.RequestException, TypeError, ValueError):
        pass
    return list(_POSITION_GROUP_FALLBACKS)


def _fetch_team_player_roster(team_code):
    team_code = (team_code or '').strip().upper()
    if not team_code:
        return []

    auth_url = current_app.config.get('TT_AUTH_INTERNAL_URL', 'http://tt-auth:5000').rstrip('/')
    secret = current_app.config.get('INTERNAL_API_SECRET')
    try:
        response = requests.get(
            f'{auth_url}/api/internal/teams/{team_code}/players',
            headers={'X-TT-Internal-Secret': secret},
            timeout=5,
        )
        if response.status_code == 200:
            payload = response.json() or {}
            return payload.get('players') or []
    except (requests.RequestException, TypeError, ValueError):
        pass
    return []


def _fetch_team_player_position_counts(team_code, approver_auth_user_id):
    team_code = (team_code or '').strip().upper()
    if not team_code or not approver_auth_user_id:
        return {}

    auth_url = current_app.config.get('TT_AUTH_INTERNAL_URL', 'http://tt-auth:5000').rstrip('/')
    cache = {}
    position_counts = {}
    secret = current_app.config.get('INTERNAL_API_SECRET')
    try:
        response = requests.get(
            f'{auth_url}/api/team-manager/members',
            params={'approver_auth_user_id': approver_auth_user_id},
            headers={'X-TT-Internal-Secret': secret},
            timeout=5,
        )
        if response.status_code != 200:
            return {}
        payload = response.json() or {}
        for user in payload.get('users') or []:
            memberships = user.get('active_memberships') or []
            if not any(
                (membership.get('member_role') or '').strip().lower() == 'player'
                and ((membership.get('team') or {}).get('code') or membership.get('team_code') or '').strip().upper() == team_code
                for membership in memberships
            ):
                continue
            auth_user_id = user.get('auth_user_id')
            if not auth_user_id:
                continue
            member_profile = cache.get(auth_user_id)
            if member_profile is None:
                member_profile = _fetch_member_profile(auth_user_id)
                cache[auth_user_id] = member_profile
            position = (member_profile.get('position') or '').strip().upper()
            if position:
                position_counts[position] = position_counts.get(position, 0) + 1
    except (requests.RequestException, TypeError, ValueError):
        return {}
    return position_counts


def _build_player_entry(player, attendance_by_user_id, member_profile_cache, position_defs_by_key):
    auth_user_id = player.get('auth_user_id') or player.get('id')
    if not auth_user_id:
        return None

    attendance = attendance_by_user_id.get(auth_user_id)
    if attendance:
        status = attendance.status
        reason = attendance.reason
        updated_at = attendance.updated_at
    else:
        status = 'open'
        reason = None
        updated_at = None

    member_profile = member_profile_cache.get(auth_user_id)
    if member_profile is None:
        member_profile = _fetch_member_profile(auth_user_id)
        member_profile_cache[auth_user_id] = member_profile

    position_key = _normalize_position_key(member_profile.get('position'))
    position_label = position_defs_by_key.get(position_key, {}).get('label') if position_key else None

    display_name = (
        player.get('display_name')
        or member_profile.get('display_name')
        or player.get('username')
        or member_profile.get('username')
        or f'User {auth_user_id}'
    )

    return {
        'user_id': auth_user_id,
        'display_name': display_name,
        'username': player.get('username') or member_profile.get('username'),
        'first_name': player.get('first_name') or member_profile.get('first_name'),
        'last_name': player.get('last_name') or member_profile.get('last_name'),
        'status': status,
        'reason': reason,
        'updated_at': updated_at,
        'position_key': position_key or None,
        'position_label': position_label or (position_key if position_key else 'Ohne Gruppe'),
    }


def _make_batch_card(batch_key, label, players, icon=None, classes=None, position_label=None):
    groups = {
        'attending': [],
        'maybe': [],
        'declined': [],
        'open': [],
    }
    for player in players:
        groups.setdefault(player['status'], []).append(player)

    icon = icon or _POSITION_BADGE_STYLES.get(batch_key, {}).get('icon', 'bi-dot')
    classes = classes or _POSITION_BADGE_STYLES.get(batch_key, {}).get('classes', 'bg-slate-500/15 text-slate-50 border-slate-400/30')

    return {
        'key': batch_key,
        'short_label': 'ALL' if batch_key == 'ALL_PLAYERS' else ('—' if batch_key == 'UNASSIGNED' else label),
        'label': label,
        'position_label': position_label or label,
        'icon': icon,
        'classes': classes,
        'players': players,
        'groups': groups,
        'attending_count': len(groups['attending']),
        'maybe_count': len(groups['maybe']),
        'declined_count': len(groups['declined']),
        'open_count': len(groups['open']),
        'total_count': len(players),
    }


def _build_training_batches(training_id, team_code, roster=None, position_defs=None, member_profile_cache=None):
    team_code = (team_code or '').strip().upper()
    attendance_rows = Attendance.query.filter_by(training_id=training_id).all()
    attendance_by_user_id = {attendance.user_id: attendance for attendance in attendance_rows}
    roster = roster if roster is not None else _fetch_team_player_roster(team_code)
    position_defs = position_defs if position_defs is not None else _fetch_position_group_defs()
    position_defs_by_key = {position['key']: position for position in position_defs}
    member_profile_cache = member_profile_cache if member_profile_cache is not None else {}
    expected_player_count = _fetch_active_member_count(team_code)

    players = []
    roster_user_ids = set()
    for player in roster:
        entry = _build_player_entry(player, attendance_by_user_id, member_profile_cache, position_defs_by_key)
        if entry:
            players.append(entry)
            roster_user_ids.add(entry['user_id'])

    for user_id, attendance in attendance_by_user_id.items():
        if user_id in roster_user_ids:
            continue
        user_info = fetch_user_from_auth(user_id) or {}
        if not _is_player_for_team(user_info, team_code):
            continue
        fallback_player = {
            'auth_user_id': user_id,
            'id': user_id,
            'username': user_info.get('username'),
            'display_name': user_info.get('display_name') or user_info.get('username') or f'User {user_id}',
            'first_name': user_info.get('first_name'),
            'last_name': user_info.get('last_name'),
        }
        entry = _build_player_entry(fallback_player, attendance_by_user_id, member_profile_cache, position_defs_by_key)
        if entry:
            players.append(entry)
            roster_user_ids.add(entry['user_id'])

    batches = []
    batches.append(_make_batch_card(
        'ALL_PLAYERS',
        'Alle Spieler (ohne Coaches)',
        players,
        icon=_POSITION_BADGE_STYLES['ALL_PLAYERS']['icon'],
        classes=_POSITION_BADGE_STYLES['ALL_PLAYERS']['classes'],
        position_label='Alle Spieler',
    ))

    for position in position_defs:
        position_players = [player for player in players if player['position_key'] == position['key']]
        style = _POSITION_BADGE_STYLES.get(position['key'], _POSITION_BADGE_STYLES['UNASSIGNED'])
        batches.append(_make_batch_card(
            position['key'],
            position['label'],
            position_players,
            icon=style['icon'],
            classes=style['classes'],
            position_label=position['label'],
        ))

    unassigned_players = [player for player in players if not player['position_key'] or player['position_key'] not in position_defs_by_key]
    if unassigned_players:
        batches.append(_make_batch_card(
            'UNASSIGNED',
            'Ohne Gruppe',
            unassigned_players,
            icon=_POSITION_BADGE_STYLES['UNASSIGNED']['icon'],
            classes=_POSITION_BADGE_STYLES['UNASSIGNED']['classes'],
            position_label='Ohne Gruppe',
        ))

    overall = batches[0]
    responded_count = overall['attending_count'] + overall['maybe_count'] + overall['declined_count']
    summary = {
        'attending': overall['attending_count'],
        'maybe': overall['maybe_count'],
        'declined': overall['declined_count'],
        'open': max((expected_player_count if expected_player_count is not None else overall['total_count']) - responded_count, 0),
        'players': expected_player_count if expected_player_count is not None else overall['total_count'],
    }
    return summary, batches


def _build_summary(occurrence_id, team_code=None):
    attendances = Attendance.query.filter_by(training_id=occurrence_id).all()
    summary = {'attending': 0, 'maybe': 0, 'declined': 0, 'open': 0, 'players': 0}
    user_cache = {}

    for attendance in attendances:
        user_info = user_cache.get(attendance.user_id)
        if user_info is None:
            user_info = fetch_user_from_auth(attendance.user_id) or {}
            user_cache[attendance.user_id] = user_info
        if not _is_player_for_team(user_info, team_code):
            continue
        summary[attendance.status] = summary.get(attendance.status, 0) + 1
        summary['players'] += 1

    expected_count = _fetch_active_member_count(team_code)
    if expected_count is not None:
        responded = summary['attending'] + summary['maybe'] + summary['declined']
        summary['open'] = max(expected_count - responded, 0)

    return summary


def _is_coach_for_team(current_user, team_code):
    team_code = (team_code or '').strip().upper()
    if not current_user or not team_code:
        return False
    if current_user.get('role') == 'admin':
        return True

    memberships = current_user.get('memberships') or []
    for membership in memberships:
        if not isinstance(membership, dict):
            continue
        if not membership.get('is_active', True):
            continue
        member_role = (membership.get('member_role') or '').strip().lower()
        if member_role not in {'coach', 'head_coach', 'team_betreuer', 'team_manager'}:
            continue
        membership_team_code = (membership.get('team_code') or '').strip().upper()
        if membership_team_code == team_code:
            return True
    return False


def _is_player_for_team(user_info, team_code):
    team_code = (team_code or '').strip().upper()
    if not team_code:
        return True
    if not user_info:
        return True

    memberships = user_info.get('active_memberships') or user_info.get('memberships') or []
    for membership in memberships:
        if not isinstance(membership, dict):
            continue
        member_role = (membership.get('member_role') or '').strip().lower()
        if member_role != 'player':
            continue
        team = membership.get('team') or {}
        membership_team_code = (team.get('code') or membership.get('team_code') or '').strip().upper()
        if membership_team_code == team_code:
            return True
    return False


@bp.route('/auth/sso')
def sso_login():
    token = (request.args.get('token') or '').strip()
    if not token:
        flash('SSO-Token fehlt.', 'danger')
        return redirect(url_for('attendance.index'))

    payload = verify_sso_token(token)
    if not payload:
        flash('Ungültiger SSO-Token.', 'danger')
        return redirect(url_for('attendance.index'))

    claims = payload.get('claims') or payload
    username = (payload.get('username') or claims.get('username') or '').strip()
    if not username:
        flash('SSO-Token enthält keinen Benutzernamen.', 'danger')
        return redirect(url_for('attendance.index'))

    current_user = {
        'id': int(payload.get('sub') or claims.get('sub')),
        'username': username,
        'role': payload.get('service_role') or payload.get('role') or payload.get('platform_role') or 'user',
        'display_name': payload.get('display_name') or claims.get('display_name') or username,
        'memberships': payload.get('memberships') or claims.get('memberships') or [],
        'permissions': payload.get('permissions') or claims.get('permissions') or [],
        'teams': payload.get('teams') or claims.get('teams') or [],
        'member_roles': payload.get('member_roles') or claims.get('member_roles') or [],
    }

    next_page = request.args.get('next')
    target = next_page if next_page and _is_safe_url(next_page) else url_for('attendance.index')
    session['current_user'] = current_user
    session['user_id'] = current_user['id']
    session['username'] = current_user['username']
    session['user_role'] = current_user['role']
    session['platform_role'] = current_user['role']
    session['display_name'] = current_user['display_name']
    session['memberships'] = current_user['memberships']
    session['permissions'] = current_user['permissions']
    session['teams'] = current_user['teams']
    session['member_roles'] = current_user['member_roles']
    return redirect(target)


@bp.route('/')
def index():
    """Main attendance page - show upcoming trainings with 3-button system."""
    current_user = get_current_user(request)
    if not current_user:
        return redirect(_auth_login_url(request.base_url))

    # Fetch trainings from tt-agenda
    trainings = fetch_trainings_from_agenda_for_teams(_visible_team_codes(current_user) or None)

    # Get user's existing attendances
    my_attendances = {
        a.training_id: a
        for a in Attendance.query.filter_by(user_id=current_user['id']).all()
    }

    trainings_with_status = []
    for t in trainings:
        aid = str(t.get('id', ''))
        attendance = my_attendances.get(aid)
        status = attendance.status if attendance else None
        reason = attendance.reason if attendance else None
        trainings_with_status.append({
            'id': aid,
            'title': t.get('title', 'Training'),
            'team_code': t.get('team_code'),
            'date': t.get('date'),
            'date_label': _format_date_label(t.get('date')),
            'time': t.get('time'),
            'start_time': t.get('start_time'),
            'end_time': t.get('end_time'),
            'location': t.get('location'),
            'is_cancelled': bool(t.get('is_cancelled', False)),
            'my_status': status,
            'my_reason': reason,
        })

    return render_template(
        'attendance.html',
        current_user=current_user,
        trainings=trainings_with_status,
        active_tab='attendance',
    )


@bp.route('/coach')
def coach_dashboard():
    """Coach overview of all training attendances."""
    current_user = get_current_user(request)
    if not current_user:
        return redirect(_auth_login_url(request.base_url))

    # Check if user has coach role
    is_coach = current_user.get('role') in ('admin', 'coach', 'head_coach', 'team_betreuer')
    if not is_coach:
        return redirect(url_for('attendance.index'))

    # Fetch trainings with attendance counts
    trainings = fetch_trainings_from_agenda_for_teams(_visible_team_codes(current_user) or None)

    # Build summary per training
    roster_cache = {}
    position_defs = _fetch_position_group_defs()
    member_profile_cache = {}
    training_summaries = []
    for t in trainings:
        tid = str(t.get('id', ''))
        team_code = t.get('team_code')
        cache_key = (team_code or '').strip().upper()
        if cache_key not in roster_cache:
            roster_cache[cache_key] = _fetch_team_player_roster(team_code)
        summary, batches = _build_training_batches(
            tid,
            team_code,
            roster=roster_cache[cache_key],
            position_defs=position_defs,
            member_profile_cache=member_profile_cache,
        )

        training_summaries.append({
            'id': tid,
            'title': t.get('title', 'Training'),
            'team_code': team_code,
            'date': t.get('date'),
            'time': t.get('time'),
            'summary': summary,
            'total': summary.get('players', 0),
            'batches': batches,
        })

    return render_template(
        'coach_dashboard.html',
        current_user=current_user,
        trainings=training_summaries,
        is_coach=is_coach,
        active_tab='coach',
    )


@bp.route('/coach/training/<occurrence_id>')
def coach_training_detail(occurrence_id):
    """Detailed view of one training's attendance for coaches."""
    current_user = get_current_user(request)
    if not current_user or current_user.get('role') not in ('admin', 'coach', 'head_coach', 'team_betreuer'):
        return redirect(url_for('attendance.index'))

    # Fetch training info from agenda
    agenda_url = current_app.config.get('TT_AGENDA_INTERNAL_URL', 'http://tt-agenda:5000')
    training = {}
    try:
        sso_token = create_sso_token()
        resp = requests.get(
            f'{agenda_url}/api/trainings/{occurrence_id}',
            headers={
                'Authorization': f'Bearer {sso_token}',
                'X-TT-Internal-Secret': current_app.config.get('INTERNAL_API_SECRET'),
            },
            timeout=5,
        )
        if resp.status_code == 200:
            training = resp.json()
    except requests.RequestException:
        pass

    # Get attendances with user details
    summary, batches = _build_training_batches(occurrence_id, training.get('team_code'))
    participants = batches[0]['players'] if batches else []
    participants = sorted(
        participants,
        key=lambda item: (
            {'attending': 0, 'maybe': 1, 'declined': 2, 'open': 3}.get(item['status'], 4),
            (item.get('position_label') or ''),
            (item.get('display_name') or item.get('username') or ''),
        ),
    )

    return render_template(
        'coach_training_detail.html',
        current_user=current_user,
        training=training,
        participants=participants,
        summary=summary,
        coach_status_base_url=url_for('attendance.coach_set_participant_status', occurrence_id=occurrence_id, user_id=0),
        is_coach=True,
        active_tab='coach',
    )


@bp.route('/api/trainings/<occurrence_id>/set-status', methods=['POST'])
def set_status_api(occurrence_id):
    """API endpoint for the 3-button system (AJAX)."""
    current_user = get_current_user(request)
    if not current_user:
        return jsonify({'error': 'unauthorized'}), 401

    training = fetch_training_occurrence_from_agenda(occurrence_id)
    if training and training.get('is_cancelled'):
        return jsonify({'error': 'cancelled_training'}), 409

    data = request.get_json(silent=True) or {}
    status = data.get('status')
    raw_reason = data.get('reason')
    reason = (raw_reason.strip() if isinstance(raw_reason, str) else None) or None

    if status not in ('attending', 'maybe', 'declined'):
        return jsonify({'error': 'invalid_status'}), 400

    attendance = Attendance.query.filter_by(
        training_id=occurrence_id,
        user_id=current_user['id'],
    ).first()

    if attendance:
        attendance.status = status
        attendance.reason = reason
        attendance.updated_at = datetime.now(timezone.utc)
    else:
        attendance = Attendance(
            training_id=occurrence_id,
            user_id=current_user['id'],
            status=status,
            reason=reason,
        )
        db.session.add(attendance)

    db.session.commit()

    # Get updated summary
    all_attendances = Attendance.query.filter_by(training_id=occurrence_id).all()
    summary = {'attending': 0, 'maybe': 0, 'declined': 0}
    for a in all_attendances:
        summary[a.status] = summary.get(a.status, 0) + 1

    return jsonify({
        'status': 'ok',
        'my_status': status,
        'summary': summary,
    })


@bp.route('/api/coach/trainings/<occurrence_id>/participants/<int:user_id>/status', methods=['POST'])
def coach_set_participant_status(occurrence_id, user_id):
    current_user = get_current_user(request)
    if not current_user:
        return jsonify({'error': 'unauthorized'}), 401

    training = fetch_training_occurrence_from_agenda(occurrence_id)
    if not training:
        return jsonify({'error': 'training_not_found'}), 404

    team_code = training.get('team_code')
    if not _is_coach_for_team(current_user, team_code):
        return jsonify({'error': 'forbidden'}), 403

    roster = _fetch_team_player_roster(team_code)
    allowed_user_ids = {
        int(player.get('auth_user_id') or player.get('id'))
        for player in roster
        if player.get('auth_user_id') or player.get('id')
    }
    if user_id not in allowed_user_ids:
        user_info = fetch_user_from_auth(user_id) or {}
        if not _is_player_for_team(user_info, team_code):
            return jsonify({'error': 'user_not_in_team'}), 404

    attendance = Attendance.query.filter_by(training_id=occurrence_id, user_id=user_id).first()
    if not attendance and user_id not in allowed_user_ids:
        return jsonify({'error': 'user_not_in_team'}), 404
    data = request.get_json(silent=True) or {}
    status = (data.get('status') or '').strip().lower()
    raw_reason = data.get('reason')
    reason = (raw_reason.strip() if isinstance(raw_reason, str) else None) or None

    attendance = Attendance.query.filter_by(training_id=occurrence_id, user_id=user_id).first()

    if status == 'open':
        if attendance:
            db.session.delete(attendance)
            db.session.commit()
        summary = _build_summary(occurrence_id, team_code)
        return jsonify({'status': 'ok', 'attendance': None, 'summary': summary})

    if status not in ('attending', 'maybe', 'declined'):
        return jsonify({'error': 'invalid_status'}), 400

    if attendance:
        attendance.status = status
        attendance.reason = reason
        attendance.updated_at = datetime.now(timezone.utc)
    else:
        attendance = Attendance(
            training_id=occurrence_id,
            user_id=user_id,
            status=status,
            reason=reason,
        )
        db.session.add(attendance)

    db.session.commit()
    summary = _build_summary(occurrence_id, team_code)
    return jsonify({
        'status': 'ok',
        'attendance': attendance.to_dict(),
        'summary': summary,
    })
