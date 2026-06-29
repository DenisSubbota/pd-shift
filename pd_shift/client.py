from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from pd_shift.settings import config_value
from pd_shift.parse import normalize_title, ticket_from_incident

OPEN_STATUSES = ("triggered", "acknowledged")


def _resolve_reference_objects(refs: list[dict], payload: dict) -> list[dict]:
    """Join incident.external_references stubs with top-level included objects."""
    if not refs:
        return []
    lookup: dict[str, dict] = {}
    for key in ("external_references", "metadata", "custom_fields", "notes"):
        for item in payload.get(key) or []:
            if isinstance(item, dict) and item.get("id"):
                lookup[item["id"]] = item

    resolved: list[dict] = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        ref_id = ref.get("id")
        if ref_id and ref_id in lookup:
            resolved.append({**ref, **lookup[ref_id]})
        else:
            resolved.append(ref)
    return resolved


class PDError(RuntimeError):
    pass


class PDClient:
    BASE = "https://api.pagerduty.com"

    def __init__(self, token: str | None = None, from_email: str | None = None):
        self.token = (
            token
            or config_value("token", env_names=("PD_TOKEN",))
            or ""
        ).strip()
        if not self.token:
            raise PDError(
                "PD token is not set — use PD_TOKEN, or put token= in ~/.config/pd-shift/conf"
            )
        self.from_email = (
            (from_email or config_value("from_email", env_names=("PD_FROM",)) or "")
            .strip()
            or None
        )

    def _headers(self, *, write: bool = False) -> dict[str, str]:
        headers = {
            "Authorization": f"Token token={self.token}",
            "Accept": "application/vnd.pagerduty+json;version=2",
        }
        if write:
            headers["Content-Type"] = "application/json"
            if self.from_email:
                headers["From"] = self.from_email
        return headers

    def _request(self, method: str, path: str, *, write: bool = False, **kwargs: Any) -> dict:
        url = f"{self.BASE}{path}"
        with httpx.Client(timeout=30.0) as client:
            response = client.request(method, url, headers=self._headers(write=write), **kwargs)
        if response.status_code >= 400:
            detail = response.text.strip() or response.reason_phrase
            raise PDError(f"PagerDuty API {response.status_code}: {detail}")
        if not response.content:
            return {}
        return response.json()

    def me(self) -> dict:
        try:
            return self._request("GET", "/users/me")["user"]
        except PDError as exc:
            if "404" in str(exc) or "403" in str(exc):
                raise PDError(
                    "GET /users/me failed — use a user API token, or set PD_FROM for account keys"
                ) from exc
            raise

    def list_open_incidents(
        self,
        *,
        user_id: str | None = None,
        team_ids: list[str] | None = None,
        service_ids: list[str] | None = None,
    ) -> list[dict]:
        params: list[tuple[str, str | int]] = [
            ("limit", 100),
            ("sort_by", "created_at:desc"),
        ]
        for status in OPEN_STATUSES:
            params.append(("statuses[]", status))
        if user_id:
            params.append(("user_ids[]", user_id))
        for team_id in team_ids or []:
            params.append(("team_ids[]", team_id))
        for service_id in service_ids or []:
            params.append(("service_ids[]", service_id))

        incidents: list[dict] = []
        offset = 0
        while True:
            page_params = [*params, ("offset", offset)]
            payload = self._request("GET", "/incidents", params=page_params)
            incidents.extend(payload.get("incidents", []))
            if not payload.get("more"):
                break
            offset += payload.get("limit", 100)
        return incidents

    def _iter_incident_pages(
        self,
        *,
        since: datetime,
        until: datetime,
        team_ids: list[str] | None = None,
        service_ids: list[str] | None = None,
        statuses: tuple[str, ...] = ("triggered", "acknowledged", "resolved"),
    ):
        params: list[tuple[str, str | int]] = [
            ("limit", 100),
            ("sort_by", "created_at:desc"),
            ("since", since.astimezone(timezone.utc).isoformat()),
            ("until", until.astimezone(timezone.utc).isoformat()),
        ]
        for status in statuses:
            params.append(("statuses[]", status))
        for team_id in team_ids or []:
            params.append(("team_ids[]", team_id))
        for service_id in service_ids or []:
            params.append(("service_ids[]", service_id))

        offset = 0
        page = 0
        while True:
            page += 1
            page_params = [*params, ("offset", offset)]
            payload = self._request("GET", "/incidents", params=page_params)
            batch = payload.get("incidents", [])
            more = bool(payload.get("more"))
            yield page, batch, more
            if not more:
                break
            offset += payload.get("limit", 100)

    def list_incidents_in_range(
        self,
        *,
        since: datetime,
        until: datetime,
        team_ids: list[str] | None = None,
        service_ids: list[str] | None = None,
        statuses: tuple[str, ...] = ("triggered", "acknowledged", "resolved"),
        on_page: Callable[[int, int, bool], None] | None = None,
    ) -> list[dict]:
        incidents: list[dict] = []
        for page, batch, more in self._iter_incident_pages(
            since=since,
            until=until,
            team_ids=team_ids,
            service_ids=service_ids,
            statuses=statuses,
        ):
            incidents.extend(batch)
            if on_page:
                on_page(page, len(incidents), more)
        return incidents

    def _stats_window(self, days: int) -> tuple[datetime, datetime]:
        until = datetime.now(timezone.utc)
        since = until - timedelta(days=days)
        return since, until

    def resolve_reference_in_team_history(
        self,
        ref: str,
        *,
        team_ids: list[str] | None = None,
        days: int = 30,
        on_progress: Callable[[str], None] | None = None,
    ) -> dict | None:
        """Search team history; INC lookups call metadata per incident (slow)."""
        ref = ref.strip()
        if not ref:
            return None

        if not ref.upper().startswith("INC"):
            try:
                payload = self.get_incident(ref, includes=["metadata"])
                incident = payload.get("incident")
                if incident:
                    return incident
            except PDError:
                pass

        since, until = self._stats_window(days)
        ref_upper = ref.upper()
        checked = 0

        for page, batch, more in self._iter_incident_pages(
            since=since,
            until=until,
            team_ids=team_ids,
        ):
            for incident in batch:
                checked += 1
                if not ref_upper.startswith("INC"):
                    if incident.get("id") == ref or str(incident.get("incident_number")) == ref:
                        return incident
                    continue

                context = self.incident_ticket_context(incident["id"])
                ticket = ticket_from_incident(
                    metadata=context.get("metadata"),
                    linked_records=context.get("linked_records", []),
                )
                if ticket and ticket.upper() == ref_upper:
                    return incident

            if on_progress:
                suffix = "+" if more else "done"
                on_progress(
                    f"Looking up {ref}… page {page} ({checked} team incidents checked, {suffix})"
                )

        return None

    def resolve_reference_for_stats(
        self,
        ref: str,
        *,
        open_incidents: list[dict],
        team_ids: list[str] | None = None,
        days: int = 30,
        on_progress: Callable[[str], None] | None = None,
    ) -> dict | None:
        """Find the reference incident; stop paging as soon as it is found."""
        reference = self.resolve_incident_reference(ref, open_incidents)
        if reference:
            return reference
        return self.resolve_reference_in_team_history(
            ref,
            team_ids=team_ids,
            days=days,
            on_progress=on_progress,
        )

    def stats_history_for_reference(
        self,
        reference: dict,
        *,
        team_ids: list[str] | None = None,
        days: int = 30,
        on_page: Callable[[int, int, bool, str], None] | None = None,
    ) -> list[dict]:
        """Fetch history for the reference incident's PD service only."""
        since, until = self._stats_window(days)
        service = reference.get("service") or {}
        service_id = service.get("id")
        service_name = service.get("summary") or "service"
        scope = service_name if service_id else "team"

        incidents: list[dict] = []
        for page, batch, more in self._iter_incident_pages(
            since=since,
            until=until,
            team_ids=team_ids,
            service_ids=[service_id] if service_id else None,
        ):
            incidents.extend(batch)
            if on_page:
                on_page(page, len(incidents), more, scope)
        return incidents

    def get_incident(self, incident_id: str, *, includes: list[str] | None = None) -> dict:
        params: list[tuple[str, str]] = []
        for include in includes or []:
            params.append(("include[]", include))
        return self._request("GET", f"/incidents/{incident_id}", params=params or None)

    def incident_log_entries(self, incident_id: str, *, limit: int = 20) -> list[dict]:
        payload = self._request(
            "GET",
            f"/incidents/{incident_id}/log_entries",
            params=[("limit", limit), ("is_overview", "false")],
        )
        return payload.get("log_entries", [])

    def incident_ticket_context(self, incident_id: str) -> dict:
        payload = self.get_incident(
            incident_id,
            includes=["external_references", "metadata"],
        )
        incident = payload.get("incident") or {}
        refs = list(payload.get("external_references") or [])
        incident_refs = incident.get("external_references") or []
        resolved = refs if refs else _resolve_reference_objects(incident_refs, payload)
        return {
            "metadata": incident.get("metadata") or {},
            "linked_records": resolved,
        }

    def incident_linked_records(self, incident_id: str) -> list[dict]:
        return self.incident_ticket_context(incident_id)["linked_records"]

    def inspect_incident(self, incident_id: str) -> dict:
        """Raw API slices useful for debugging INC / linked-record parsing."""
        includes = ["external_references", "metadata", "custom_fields", "notes"]
        incident_payload = self.get_incident(incident_id, includes=includes)
        incident = incident_payload.get("incident") or {}
        context = self.incident_ticket_context(incident.get("id") or incident_id)
        return {
            "query_id": incident_id,
            "incident_number": incident.get("incident_number"),
            "incident_id": incident.get("id"),
            "title": incident.get("title"),
            "status": incident.get("status"),
            "parsed_inc": ticket_from_incident(
                metadata=context.get("metadata"),
                linked_records=context.get("linked_records", []),
            ),
            "top_level_keys": sorted(incident_payload.keys()),
            "incident_external_references": incident.get("external_references"),
            "top_level_external_references": incident_payload.get("external_references"),
            "incident_metadata": incident.get("metadata"),
            "top_level_metadata": incident_payload.get("metadata"),
            "custom_fields": incident_payload.get("custom_fields") or incident.get("custom_fields"),
            "notes": [
                {
                    "content": note.get("content"),
                    "created_at": note.get("created_at"),
                }
                for note in (incident_payload.get("notes") or [])
            ],
            "log_entries": [
                {
                    "type": entry.get("type"),
                    "summary": entry.get("summary"),
                    "channel": entry.get("channel"),
                }
                for entry in self.incident_log_entries(incident.get("id") or incident_id, limit=15)
            ],
        }

    def incident_notes(self, incident_id: str) -> list[dict]:
        payload = self._request("GET", f"/incidents/{incident_id}/notes")
        return payload.get("notes", [])

    def _write_from_email(self) -> str | None:
        if self.from_email:
            return self.from_email
        try:
            return self.me().get("email")
        except PDError:
            return None

    def _prepare_write(self) -> None:
        from_email = self._write_from_email()
        if from_email:
            self.from_email = from_email
            return
        raise PDError(
            "Write actions require a user API token or PD_FROM (your email) for account REST keys"
        )

    def ack_incidents(self, incident_ids: list[str]) -> None:
        if not incident_ids:
            return
        self._prepare_write()

        body = {
            "incidents": [
                {"id": incident_id, "type": "incident_reference", "status": "acknowledged"}
                for incident_id in incident_ids
            ]
        }
        self._request("PUT", "/incidents", write=True, json=body)

    def merge_incidents(self, parent_id: str, source_ids: list[str]) -> None:
        if not source_ids:
            return
        self._prepare_write()
        body = {
            "source_incidents": [
                {"id": source_id, "type": "incident_reference"}
                for source_id in source_ids
            ]
        }
        self._request("PUT", f"/incidents/{parent_id}/merge", write=True, json=body)

    def rename_incident(self, incident_id: str, title: str) -> None:
        self._prepare_write()
        body = {
            "incidents": [
                {
                    "id": incident_id,
                    "type": "incident_reference",
                    "title": normalize_title(title),
                }
            ]
        }
        self._request("PUT", "/incidents", write=True, json=body)

    def resolve_incident_reference(self, ref: str, incidents: list[dict]) -> dict | None:
        """Find an incident by INC ticket, PD incident number, or PD incident id."""
        ref = ref.strip()
        if not ref:
            return None

        ref_upper = ref.upper()
        if ref_upper.startswith("INC"):
            for incident in incidents:
                context = self.incident_ticket_context(incident["id"])
                ticket = ticket_from_incident(
                    metadata=context.get("metadata"),
                    linked_records=context.get("linked_records", []),
                )
                if ticket and ticket.upper() == ref_upper:
                    return incident
            return None

        for incident in incidents:
            if incident.get("id") == ref:
                return incident
            if str(incident.get("incident_number")) == ref:
                return incident

        try:
            payload = self.get_incident(ref, includes=["metadata"])
        except PDError:
            return None
        return payload.get("incident")
