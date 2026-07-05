VALID_ROLES = {"user", "admin"}


def _normalize_service_name(value):
    text = str(value or "").strip().lower()
    if text.startswith("tt-"):
        text = text[3:]
    return text


def normalize_role(value, default="user"):
    role = str(value or default).strip().lower()
    return role if role in VALID_ROLES else default


def normalize_permissions(value):
    if not isinstance(value, list):
        return []
    normalized = []
    seen = set()
    for item in value:
        if not isinstance(item, str):
            continue
        permission = item.strip()
        if not permission or permission in seen:
            continue
        normalized.append(permission)
        seen.add(permission)
    return normalized


def normalize_memberships(value):
    if not isinstance(value, list):
        return []
    return [dict(entry) for entry in value if isinstance(entry, dict)]


def normalize_role_permissions(value):
    if not isinstance(value, dict):
        return {}

    normalized = {}
    for service_name, permissions in value.items():
        key = _normalize_service_name(service_name)
        if not key:
            continue
        normalized_permissions = normalize_permissions(permissions)
        if normalized_permissions:
            normalized[key] = normalized_permissions
    return normalized


def has_role_permission(role_permissions, permission_key, service_name):
    permission = str(permission_key or "").strip().lower()
    service = _normalize_service_name(service_name)
    if not permission or not service:
        return False

    normalized = normalize_role_permissions(role_permissions)
    global_permissions = normalized.get("*", [])
    if "admin" in global_permissions or permission in global_permissions:
        return True

    service_permissions = normalized.get(service, [])
    return "admin" in service_permissions or permission in service_permissions


def normalize_auth_payload(payload):
    claims = dict(payload or {})
    permissions = normalize_permissions(claims.get("permissions"))
    role_permissions = normalize_role_permissions(claims.get("role_permissions"))
    memberships = normalize_memberships(claims.get("memberships"))
    platform_role = normalize_role(claims.get("platform_role"))
    service_role = normalize_role(claims.get("service_role") or claims.get("role"))

    if platform_role == "admin" or "*" in permissions:
        platform_role = "admin"
        service_role = "admin"

    claims["platform_role"] = platform_role
    claims["service_role"] = service_role
    claims["role"] = service_role
    claims["permissions"] = permissions
    claims["role_permissions"] = role_permissions
    claims["memberships"] = memberships

    return {
        "platform_role": platform_role,
        "service_role": service_role,
        "permissions": permissions,
        "role_permissions": role_permissions,
        "memberships": memberships,
        "claims": claims,
    }


def is_platform_admin(platform_role=None, permissions=None):
    return normalize_role(platform_role) == "admin" or "*" in normalize_permissions(permissions)


def is_service_admin(service_role=None, permissions=None, role_permissions=None, service_name=None):
    if normalize_role(service_role) == "admin" or "*" in normalize_permissions(permissions):
        return True
    if service_name:
        return has_role_permission(role_permissions, "admin", service_name)
    return False