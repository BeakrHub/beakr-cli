"""Organization credentials, audit trail and sign-in policy, over unlisted routes."""

from __future__ import annotations

from typing import Any

from beakr_sdk._calls import Flow
from beakr_sdk._resource import M
from beakr_sdk.resources import org_admin as _public
from beakr_sdk.resources._base import collect_pages
from beakr_sdk.resources.org_admin import _UNSET, _OrgScoped

from beakr_cli._private_api._generated import operations as ops

CREDENTIAL_KINDS = ("api_key", "access_token")

# The fields each PUT accepts (engine: AuthPolicyIn).
POLICY_FIELDS = (
    "allow_personal_keys",
    "max_key_lifetime_days",
    "require_step_up_mfa",
    "step_up_for_sign_ins",
    "allow_non_expiring_keys",
    "allowed_clients",
    "restrict_oauth_clients",
)


class Credentials(_OrgScoped[M]):
    """Every live API key and CLI/agent token in the organization (secrets never included)."""

    def list(self, *, org_id: str | None = None) -> Any:
        def flow() -> Flow:
            resolved = yield from self._org_id_flow(org_id)
            return (yield from collect_pages(ops.build_org_admin_list_org_credentials(resolved)))

        return self._drive(flow())

    def revoke(self, kind: str, credential_id: str, *, org_id: str | None = None) -> Any:
        """Revoke one credential. Revoking an access token revokes its whole sign-in."""
        if kind not in CREDENTIAL_KINDS:
            raise ValueError(f"kind must be one of {', '.join(CREDENTIAL_KINDS)}")
        return self._org_call(
            org_id,
            lambda org: ops.build_org_admin_revoke_org_credential(org, kind, credential_id),
        )


class Audit(_OrgScoped[M]):
    """The organization's audit trail, newest first."""

    def list(
        self,
        *,
        cursor: str | None = None,
        limit: int | None = None,
        before: str | None = None,
        org_id: str | None = None,
    ) -> Any:
        """One page: ``{data, next_cursor, has_more}``. Pass ``next_cursor`` as ``cursor``.

        ``before`` (an ISO timestamp) starts the first page at events older than it.
        """
        return self._org_call(
            org_id,
            lambda org: ops.build_org_admin_org_audit_log(
                org, cursor=cursor, limit=limit, before=before
            ),
        )


class AuthPolicy(_OrgScoped[M]):
    """The organization's sign-in and key policy."""

    def get(self, *, org_id: str | None = None) -> Any:
        return self._org_call(org_id, ops.build_org_admin_get_auth_policy)

    def set(
        self,
        *,
        allow_personal_keys: Any = _UNSET,
        max_key_lifetime_days: Any = _UNSET,
        require_step_up_mfa: Any = _UNSET,
        allowed_clients: Any = _UNSET,
        restrict_oauth_clients: Any = _UNSET,
        step_up_for_sign_ins: Any = _UNSET,
        allow_non_expiring_keys: Any = _UNSET,
        org_id: str | None = None,
    ) -> Any:
        """Change the fields you pass; every other field keeps its stored value.

        The engine replaces the whole policy on write, so this reads the current
        policy first and sends it back with only your changes. A field left out
        is never reset to a default (an omitted ``require_step_up_mfa`` used to
        opt the org out of step-up silently).

        ``allowed_clients`` is advisory (a CLI sign-in or key names its own
        client). ``restrict_oauth_clients``: ``True`` allows only approved OAuth
        client IDs; ``False`` lets any app a person approves connect.
        ``allow_non_expiring_keys`` lets members create keys that never expire
        (otherwise new keys expire in 90 days, at most a year); a
        ``max_key_lifetime_days`` cap still wins.
        """
        changes = {
            name: value
            for name, value in {
                "allow_personal_keys": allow_personal_keys,
                "max_key_lifetime_days": max_key_lifetime_days,
                "require_step_up_mfa": require_step_up_mfa,
                "allowed_clients": allowed_clients,
                "restrict_oauth_clients": restrict_oauth_clients,
                "step_up_for_sign_ins": step_up_for_sign_ins,
                "allow_non_expiring_keys": allow_non_expiring_keys,
            }.items()
            if value is not _UNSET
        }
        return self._read_modify_write(
            org_id,
            ops.build_org_admin_get_auth_policy,
            ops.build_org_admin_put_auth_policy,
            changes,
            POLICY_FIELDS,
        )


class OrgAdmin(_public.OrgAdmin[M]):
    """:class:`beakr_sdk.resources.org_admin.OrgAdmin` plus the CLI's unlisted calls."""

    def __init__(self, client: Any) -> None:
        super().__init__(client)
        self.credentials: Credentials[M] = Credentials(client)
        self.audit: Audit[M] = Audit(client)
        self.policy: AuthPolicy[M] = AuthPolicy(client)
