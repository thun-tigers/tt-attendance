VALID_ROLES = {"user", "admin"}


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


def normalize_auth_payload(payload):
    claims = dict(payload or {})
    permissions = normalize_permissions(claims.get("permissions"))
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
    claims["memberships"] = memberships

    return {
        "platform_role": platform_role,
        "service_role": service_role,
        "permissions": permissions,
        "memberships": memberships,
        "claims": claims,
    }