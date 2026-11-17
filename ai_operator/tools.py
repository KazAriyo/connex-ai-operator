from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .clock import DateError, pretty, resolve_date, resolve_range, today, week_start
from .store import CRMStore

LeadStatus = Literal["new", "contacted", "viewing_booked", "offer_made", "won", "lost"]
ACTIVE_STATUSES = {"new", "contacted", "viewing_booked", "offer_made"}


class ToolError(Exception):
    pass


@dataclass
class PreparedAction:
    summary: str
    apply: Callable[[], dict]


@dataclass
class ToolSpec:
    name: str
    mode: Literal["read", "write"]
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    run: Optional[Callable[[CRMStore, Any], dict]] = None
    prepare: Optional[Callable[[CRMStore, Any], PreparedAction]] = None

    @property
    def is_write(self) -> bool:
        return self.mode == "write"

    def json_schema(self) -> dict:
        return clean_schema(self.input_model.model_json_schema())

    def validate(self, args: Any) -> BaseModel:
        if not isinstance(args, dict):
            raise ToolError(f"Arguments for {self.name} must be a JSON object.")
        try:
            return self.input_model.model_validate(args)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in err['loc']) or 'input'}: {err['msg']}" for err in exc.errors()
            )
            raise ToolError(f"Invalid arguments for {self.name}: {problems}") from None

    def check_output(self, data: dict) -> dict:
        try:
            return self.output_model.model_validate(data).model_dump()
        except ValidationError as exc:
            raise ToolError(f"Internal error: {self.name} produced output that does not match its schema "
                            f"({exc.error_count()} problem(s)).") from None


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Output(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LeadOut(_Output):
    id: str
    name: str
    type: Literal["buyer", "tenant", "vendor", "landlord"]
    source: str
    status: LeadStatus
    assigned_to: Optional[str]
    interest: str
    budget_gbp: Optional[int]
    area: str
    created_on: str
    last_contacted: Optional[str]
    phone: str
    email: str
    notes: str


class TaskOut(_Output):
    id: str
    title: str
    due: str
    status: Literal["open", "done"]
    related_to: Optional[str]
    created_on: str


class TaskRowOut(TaskOut):
    related_lead_name: Optional[str]
    overdue: bool


class TaskRefOut(_Output):
    id: str
    title: str
    due: str


class EventOut(_Output):
    id: str
    title: str
    type: str
    start: str
    end: str
    location: str
    lead_id: Optional[str]
    staff: str


class SearchLeadsOutput(_Output):
    today: str
    total_matching: int
    leads: list[LeadOut]
    not_contacted_since: Optional[str] = None
    note: Optional[str] = None


class InsightsOutput(_Output):
    today: str
    period: str
    period_start: Optional[str]
    new_leads: int
    new_leads_unassigned: int
    new_leads_by_source: dict[str, int]
    new_lead_names: list[str]
    total_leads: int
    leads_by_status: dict[str, int]
    active_leads_not_contacted_in_7_days: int
    active_leads_not_contacted_in_7_days_names: list[str]
    open_tasks: int
    overdue_tasks: list[TaskRefOut]
    events_next_7_days: int


class CalendarOutput(_Output):
    today: str
    start_date: str
    end_date: str
    count: int
    events: list[EventOut]
    note: Optional[str] = None


class ListTasksOutput(_Output):
    today: str
    count: int
    tasks: list[TaskRowOut]
    note: Optional[str] = None


class CreateTaskOutput(_Output):
    created: TaskOut
    due_pretty: str


class UpdateLeadStatusOutput(_Output):
    updated: LeadOut
    previous_status: LeadStatus


class CompleteTaskOutput(_Output):
    completed: TaskOut


def clean_schema(schema: dict) -> dict:
    def walk(node: Any, in_properties: bool = False) -> Any:
        if isinstance(node, dict):
            if in_properties:
                return {k: walk(v) for k, v in node.items()}
            node = {k: walk(v, in_properties=(k == "properties"))
                    for k, v in node.items() if k != "title"}
            any_of = node.get("anyOf")
            if isinstance(any_of, list):
                non_null = [s for s in any_of if s.get("type") != "null"]
                if len(non_null) == 1 and len(non_null) != len(any_of):
                    merged = {**non_null[0], **{k: v for k, v in node.items() if k != "anyOf"}}
                    if merged.get("default", "x") is None:
                        merged.pop("default")
                    return merged
            return node
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    out = walk(schema)
    out.setdefault("properties", {})
    out["type"] = "object"
    return out


def _date(expr: str, field: str) -> date:
    try:
        return resolve_date(expr)
    except DateError as exc:
        raise ToolError(f"{field}: {exc}") from None


def resolve_lead(store: CRMStore, ref: str) -> dict:
    ref = (ref or "").strip()
    if not ref:
        raise ToolError("No lead was given.")
    if re.fullmatch(r"[Ll]-?\d{1,4}", ref):
        lead_id = "L-" + re.sub(r"\D", "", ref).zfill(3)
        lead = store.lead_by_id(lead_id)
        if not lead:
            raise ToolError(f"There is no lead with id {lead_id}.")
        return lead

    wanted = ref.casefold()
    exact = [l for l in store.leads if l["name"].casefold() == wanted]
    if len(exact) == 1:
        return exact[0]
    words = wanted.split()
    partial = [l for l in store.leads if all(w in l["name"].casefold() for w in words)]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        options = ", ".join(f"{l['name']} ({l['id']})" for l in partial)
        raise ToolError(f"'{ref}' matches more than one lead: {options}. Ask the user which one.")
    raise ToolError(f"No lead called '{ref}' exists in the CRM.")


def _lead_card(l: dict) -> dict:
    return {k: l[k] for k in (
        "id", "name", "type", "source", "status", "assigned_to", "interest",
        "budget_gbp", "area", "created_on", "last_contacted", "phone", "email", "notes",
    )}


class SearchLeadsInput(_Input):
    query: Optional[str] = Field(
        None, description="Keywords matched against name, area, interest, source, type and notes, e.g. 'Hackney' or 'Rightmove buyer'.")
    status: Optional[LeadStatus] = Field(None, description="Only leads with this status.")
    unassigned_only: bool = Field(False, description="Only leads with no agent assigned.")
    not_contacted_since: Optional[str] = Field(
        None, description="Only leads never contacted, or last contacted before this date. "
                          "Accepts 'start of week', '7 days ago', 'today' or YYYY-MM-DD.")
    created_since: Optional[str] = Field(
        None, description="Only leads created on or after this date. Same formats as above.")
    include_closed: bool = Field(
        False, description="Include 'won' and 'lost' leads. Off by default; ignored if status is set.")
    limit: int = Field(10, ge=1, le=50, description="Maximum number of leads to return.")


def search_leads(store: CRMStore, a: SearchLeadsInput) -> dict:
    words = [w for w in re.split(r"\W+", (a.query or "").casefold()) if w]
    cutoff = _date(a.not_contacted_since, "not_contacted_since") if a.not_contacted_since else None
    created = _date(a.created_since, "created_since") if a.created_since else None

    matches = []
    for l in store.leads:
        hay = " ".join(str(l.get(k) or "") for k in ("name", "area", "interest", "source", "type", "notes", "id")).casefold()
        if words and not all(w in hay for w in words):
            continue
        if a.status and l["status"] != a.status:
            continue
        if not a.status and not a.include_closed and l["status"] not in ACTIVE_STATUSES:
            continue
        if a.unassigned_only and l["assigned_to"]:
            continue
        if cutoff and l["last_contacted"] and date.fromisoformat(l["last_contacted"]) >= cutoff:
            continue
        if created and date.fromisoformat(l["created_on"]) < created:
            continue
        matches.append(l)

    matches.sort(key=lambda l: l["created_on"], reverse=True)
    result = {
        "today": today().isoformat(),
        "total_matching": len(matches),
        "leads": [_lead_card(l) for l in matches[: a.limit]],
    }
    if cutoff:
        result["not_contacted_since"] = cutoff.isoformat()
    if not matches:
        result["note"] = "No leads matched these filters."
    return result


class GetInsightsInput(_Input):
    period: Literal["this_week", "last_7_days", "this_month", "all_time"] = Field(
        "this_week", description="Period for the 'new leads' figures. 'this_week' runs Monday to today.")


def get_insights(store: CRMStore, a: GetInsightsInput) -> dict:
    t = today()
    start = {
        "this_week": week_start(t),
        "last_7_days": t - timedelta(days=6),
        "this_month": t.replace(day=1),
        "all_time": date.min,
    }[a.period]

    new = [l for l in store.leads if start <= date.fromisoformat(l["created_on"]) <= t]
    by_source: dict[str, int] = {}
    for l in new:
        by_source[l["source"]] = by_source.get(l["source"], 0) + 1

    by_status: dict[str, int] = {}
    for l in store.leads:
        by_status[l["status"]] = by_status.get(l["status"], 0) + 1

    stale_cut = t - timedelta(days=7)
    stale = [l for l in store.leads if l["status"] in ACTIVE_STATUSES and (
        not l["last_contacted"] or date.fromisoformat(l["last_contacted"]) < stale_cut)]

    open_tasks = [x for x in store.tasks if x["status"] == "open"]
    overdue = [x for x in open_tasks if date.fromisoformat(x["due"]) < t]
    upcoming = [e for e in store.events
                if t <= datetime.fromisoformat(e["start"]).date() <= t + timedelta(days=6)]

    return {
        "today": t.isoformat(),
        "period": a.period,
        "period_start": None if a.period == "all_time" else start.isoformat(),
        "new_leads": len(new),
        "new_leads_unassigned": sum(1 for l in new if not l["assigned_to"]),
        "new_leads_by_source": by_source,
        "new_lead_names": [l["name"] for l in new],
        "total_leads": len(store.leads),
        "leads_by_status": by_status,
        "active_leads_not_contacted_in_7_days": len(stale),
        "active_leads_not_contacted_in_7_days_names": [l["name"] for l in stale],
        "open_tasks": len(open_tasks),
        "overdue_tasks": [{"id": x["id"], "title": x["title"], "due": x["due"]} for x in overdue],
        "events_next_7_days": len(upcoming),
    }


class GetCalendarInput(_Input):
    range: Optional[Literal["today", "tomorrow", "this_week", "next_week", "next_7_days", "this_month"]] = Field(
        None, description="A named date range. Use this OR start/end.")
    start: Optional[str] = Field(None, description="Start date: YYYY-MM-DD or a word like 'friday'.")
    end: Optional[str] = Field(None, description="End date (inclusive). Defaults to start.")


def get_calendar(store: CRMStore, a: GetCalendarInput) -> dict:
    if a.start:
        s = _date(a.start, "start")
        e = _date(a.end, "end") if a.end else s
    else:
        try:
            s, e = resolve_range(a.range or "today")
        except DateError as exc:
            raise ToolError(str(exc)) from None
    if e < s:
        raise ToolError(f"End date {e} is before start date {s}.")
    if (e - s).days > 62:
        raise ToolError("Please ask for at most about two months of calendar at a time.")

    events = sorted(
        (ev for ev in store.events if s <= datetime.fromisoformat(ev["start"]).date() <= e),
        key=lambda ev: ev["start"],
    )
    out = {"today": today().isoformat(), "start_date": s.isoformat(), "end_date": e.isoformat(),
           "count": len(events), "events": events}
    if not events:
        out["note"] = "No events in this range."
    return out


class ListTasksInput(_Input):
    status: Literal["open", "done", "all"] = Field("open", description="Which tasks to list.")
    related_to: Optional[str] = Field(None, description="Only tasks for this lead (id like L-001 or name).")


def list_tasks(store: CRMStore, a: ListTasksInput) -> dict:
    lead = resolve_lead(store, a.related_to) if a.related_to else None
    t = today()
    rows = []
    for x in store.tasks:
        if a.status != "all" and x["status"] != a.status:
            continue
        if lead and x["related_to"] != lead["id"]:
            continue
        rel = store.lead_by_id(x["related_to"]) if x["related_to"] else None
        rows.append({**x, "related_lead_name": rel["name"] if rel else None,
                     "overdue": x["status"] == "open" and date.fromisoformat(x["due"]) < t})
    rows.sort(key=lambda r: r["due"])
    out = {"today": t.isoformat(), "count": len(rows), "tasks": rows}
    if not rows:
        out["note"] = "No tasks matched."
    return out


class CreateTaskInput(_Input):
    title: str = Field(..., description="Short task title, e.g. 'Call Aisha Khan'.")
    due: str = Field(..., description="Due date: YYYY-MM-DD or words like 'tomorrow', 'friday', 'in 3 days'. "
                                      "Pass the user's words; the system works out the date.")
    related_to: Optional[str] = Field(None, description="Lead this task is about: id (L-001) or full name.")


def prepare_create_task(store: CRMStore, a: CreateTaskInput) -> PreparedAction:
    title = a.title.strip()
    if not 2 <= len(title) <= 120:
        raise ToolError("Task title must be between 2 and 120 characters.")
    due = _date(a.due, "due")
    if due < today():
        raise ToolError(f"The due date {pretty(due)} is in the past.")
    lead = resolve_lead(store, a.related_to) if a.related_to else None

    summary = f'Create task "{title}", due {pretty(due)}'
    summary += f", linked to {lead['name']} ({lead['id']})." if lead else "."

    def apply() -> dict:
        with store.lock:
            if lead and not store.lead_by_id(lead["id"]):
                raise ToolError(f"Lead {lead['id']} no longer exists.")
            task = {
                "id": store.next_id("T", store.tasks), "title": title, "due": due.isoformat(),
                "status": "open", "related_to": lead["id"] if lead else None,
                "created_on": today().isoformat(),
            }
            store.tasks.append(task)
            store.save()
        return {"created": task, "due_pretty": pretty(due)}

    return PreparedAction(summary, apply)


class UpdateLeadStatusInput(_Input):
    lead_id: str = Field(..., description="Lead id (L-001). A full name is also accepted if it is unique.")
    status: LeadStatus = Field(..., description="The new status.")


def prepare_update_lead_status(store: CRMStore, a: UpdateLeadStatusInput) -> PreparedAction:
    lead = resolve_lead(store, a.lead_id)
    old = lead["status"]
    if old == a.status:
        raise ToolError(f"{lead['name']} ({lead['id']}) is already '{old}'. Nothing to change.")
    touch = a.status == "contacted"
    summary = f"Change {lead['name']} ({lead['id']}) from '{old}' to '{a.status}'"
    summary += f" and set last contacted to {pretty(today())}." if touch else "."

    def apply() -> dict:
        with store.lock:
            current = store.lead_by_id(lead["id"])
            if not current:
                raise ToolError(f"Lead {lead['id']} no longer exists.")
            if current["status"] != old:
                raise ToolError(f"{current['name']}'s status changed to '{current['status']}' "
                                "while waiting for confirmation. Nothing was updated.")
            current["status"] = a.status
            if touch:
                current["last_contacted"] = today().isoformat()
            store.save()
            return {"updated": _lead_card(current), "previous_status": old}

    return PreparedAction(summary, apply)


class CompleteTaskInput(_Input):
    task_id: str = Field(..., description="Task id, e.g. T-003.")


def prepare_complete_task(store: CRMStore, a: CompleteTaskInput) -> PreparedAction:
    tid = "T-" + re.sub(r"\D", "", a.task_id).zfill(3)
    task = next((x for x in store.tasks if x["id"] == tid), None)
    if not task:
        raise ToolError(f"There is no task with id {a.task_id}.")
    if task["status"] == "done":
        raise ToolError(f"Task {tid} is already done.")
    summary = f'Mark task {tid} "{task["title"]}" as done.'

    def apply() -> dict:
        with store.lock:
            current = next((x for x in store.tasks if x["id"] == tid), None)
            if not current:
                raise ToolError(f"Task {tid} no longer exists.")
            if current["status"] == "done":
                raise ToolError(f"Task {tid} was already completed.")
            current["status"] = "done"
            store.save()
            return {"completed": dict(current)}

    return PreparedAction(summary, apply)


TOOLS: dict[str, ToolSpec] = {t.name: t for t in [
    ToolSpec("search_leads", "read",
             "Search leads by keyword, status, assignment or last-contact date. Read-only.",
             SearchLeadsInput, SearchLeadsOutput, run=search_leads),
    ToolSpec("get_insights", "read",
             "Computed CRM stats: new leads in a period (and how many are unassigned), pipeline by status, "
             "leads not contacted in 7 days, open and overdue tasks. Read-only.",
             GetInsightsInput, InsightsOutput, run=get_insights),
    ToolSpec("get_calendar", "read",
             "Calendar events (viewings, valuations, meetings, calls) in a date range. Read-only.",
             GetCalendarInput, CalendarOutput, run=get_calendar),
    ToolSpec("list_tasks", "read",
             "List tasks, optionally for one lead, with overdue flags. Read-only.",
             ListTasksInput, ListTasksOutput, run=list_tasks),
    ToolSpec("create_task", "write",
             "Create a new task. The user is always asked to confirm before it is saved.",
             CreateTaskInput, CreateTaskOutput, prepare=prepare_create_task),
    ToolSpec("update_lead_status", "write",
             "Change a lead's status. The user is always asked to confirm before it is saved.",
             UpdateLeadStatusInput, UpdateLeadStatusOutput, prepare=prepare_update_lead_status),
    ToolSpec("complete_task", "write",
             "Mark an open task as done. The user is always asked to confirm before it is saved.",
             CompleteTaskInput, CompleteTaskOutput, prepare=prepare_complete_task),
]}


def tools_for_mode(mode: str) -> list[ToolSpec]:
    return [t for t in TOOLS.values() if mode == "run" or not t.is_write]
