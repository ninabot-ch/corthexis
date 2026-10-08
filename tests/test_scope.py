"""Projects (2.1) — the memory recall never crosses a project boundary.

Unit level (no database): corthexis.scope, the Recaller (ranking AND quoted names), the command
hook entry point, and the service behind the MCP tools (its scope from the environment).
The same rule against a real Postgres is in test_scope_pg.py.
"""
import io
import json

import pytest

from corthexis import recall as R
from corthexis import scope as S
from corthexis.contract import Generation, NoteRecord
from corthexis.search import Hit


def H(name, project, score=0.9, level=None):
    return Hit(name, score, score, 0.1, None, f"snippet of {name}", 3, "indexed",
               description=f"about {name}", generation=7, project=project, level=level)


def NR(name, project):
    return NoteRecord(name, f"about {name}", "project", 0, f"/m/{name}.md", None, "indexed",
                      f"body of {name}", project)


class Emb:
    rerank_policy = "off"

    def embed_query(self, text, timeout=30):
        return [1.0, 0.0]


class TwoProjectStore:
    """Notes of two projects. ``honours_scope=False`` = a store that ignores the scope
    (old implementation): the Recaller's own filter must still hold."""

    def __init__(self, honours_scope=True):
        self.honours = honours_scope
        self.gen = Generation(7, "llamacpp:embeddinggemma-300m-q8@768", 2, "2026-01-01", "active")
        self.notes = {
            "radio-deploy-runbook": NR("radio-deploy-runbook", "radio"),
            "radio-db-credentials-rotation": NR("radio-db-credentials-rotation", "radio"),
            "tv-deploy-runbook": NR("tv-deploy-runbook", "tv"),
            "tv-secret-roadmap": NR("tv-secret-roadmap", "tv"),
            "legacy-note": NR("legacy-note", "default"),
        }
        self.search_kw = []

    def active_generation(self):
        return self.gen

    def search(self, qv, text, k, rerank=None, **kw):
        self.search_kw.append(kw)
        hits = [H(n, r.project, level=r.level) for n, r in self.notes.items()]
        if self.honours and kw.get("projects") is not None:
            hits = [h for h in hits if h.project in kw["projects"]]
        return hits[:k]

    def recalled_notes(self, sid, agent_id=None):
        return set()

    def existing_names(self, names, projects=None):
        out = {n for n in names if n in self.notes}
        if self.honours and projects is not None:
            out = {n for n in out if self.notes[n].project in projects}
        return out

    def get_note(self, name, project="default"):
        n = self.notes.get(name)
        return n if n is not None and n.project == project else None

    def resolve_note(self, name, projects=None):
        n = self.notes.get(name)
        if n is None or projects == ():
            return None
        if self.honours and projects is not None and n.project not in projects:
            return None
        return n

    def find_note_by_path(self, stem, projects=None):
        return None

    def links(self, name, project="default"):
        return {"note": name, "links": [], "backlinks": []}

    def log_access(self, via, notes, actor=None, session_id=None, query=None):
        self.accessed = [(via, getattr(n, "name", None), session_id) for n in notes]
        return len(notes)

    def log_recall(self, *a, **k):
        pass

    def log_recall_turn(self, *a, **k):
        pass


def rec(store, **cfg):
    return R.Recaller(store, Emb(), R.RecallConfig(threshold=0.1, top_k=10, **cfg),
                      profile="leger")


# ---- corthexis.scope -----------------------------------------------------------------------

def test_scope_normalisation_is_fail_closed():
    assert S.normalize(None) is None
    assert S.normalize([]) == ()
    assert S.normalize("radio") == ("radio",)            # a string is ONE project
    assert S.normalize(["tv", "radio", "tv"]) == ("radio", "tv")
    assert S.normalize(["Bad Slug", "../x", ""]) == ()   # invalid names never widen
    assert S.from_env(None) is None and S.from_env("  ") is None
    assert S.from_env("radio, tv") == ("radio", "tv")
    assert S.project_of({"project": None}) == "default"  # unknown = default, not "everywhere"
    assert S.project_of(H("x", "Weird Name")) == "default"
    assert not S.visible(H("x", None), ("radio",))
    assert S.visible(H("x", None), ("default",))


# ---- Recaller -------------------------------------------------------------------------

@pytest.mark.parametrize("honours", [True, False])
def test_recall_only_injects_notes_of_the_session_project(honours):
    st = TwoProjectStore(honours_scope=honours)
    res = rec(st).recall("how do we deploy the player to production", projects=("radio",))
    assert res.notes and set(res.notes) <= {"radio-deploy-runbook",
                                            "radio-db-credentials-rotation"}
    assert all("tv-" not in line for line in res.context.splitlines())
    assert st.search_kw[-1] == {"projects": ("radio",)}


@pytest.mark.parametrize("honours", [True, False])
def test_a_quoted_note_name_of_another_project_is_not_forced(honours):
    """The name is a strong recall signal — it must not become a way around the scope."""
    st = TwoProjectStore(honours_scope=honours)
    res = rec(st).recall("please read tv-secret-roadmap and tell me the plan",
                         projects=("radio",))
    assert "tv-secret-roadmap" not in res.notes
    assert "tv-secret-roadmap" not in res.forced
    # the same quote inside the right project is forced as before
    res = rec(st).recall("please read tv-secret-roadmap and tell me the plan",
                         projects=("tv",))
    assert "tv-secret-roadmap" in res.notes


def test_empty_scope_recalls_nothing_and_no_scope_is_unchanged():
    st = TwoProjectStore()
    res = rec(st).recall("how do we deploy the player to production", projects=())
    assert res.skipped == "no-project" and not res.notes and not st.search_kw
    res = rec(st).recall("how do we deploy the player to production")
    assert {"radio-deploy-runbook", "tv-deploy-runbook", "legacy-note"} <= set(res.notes)
    assert st.search_kw[-1] == {}     # no scope → the store is called exactly as in 3.1


def test_hook_output_scopes_prompt_and_subagent_recall():
    st = TwoProjectStore(honours_scope=False)
    out = R.hook_output({"hook_event_name": "UserPromptSubmit", "session_id": "s",
                         "prompt": "how do we deploy the player to production"},
                        rec(st), projects=("radio",))
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert "radio-deploy-runbook" in ctx and "tv-" not in ctx
    out = R.hook_output({"hook_event_name": "PreToolUse", "tool_name": "Task",
                         "session_id": "s", "tool_input": {
                             "description": "deploy", "prompt": "deploy the tv player now"}},
                        rec(st), projects=("radio",))
    prompt = out["hookSpecificOutput"]["updatedInput"]["prompt"]
    assert "radio-deploy-runbook" in prompt and "tv-deploy-runbook" not in prompt


def test_command_hook_requires_a_scope_when_asked(monkeypatch):
    """SOKKAN sets CORTHEXIS_RECALL_REQUIRE_SCOPE=1: without CORTHEXIS_RECALL_PROJECTS the
    in-process fallback recalls nothing (and never opens the store)."""
    opened = []
    monkeypatch.setattr(R, "default_recaller", lambda **k: opened.append(1) or rec(
        TwoProjectStore(honours_scope=False)))
    monkeypatch.setenv("CORTHEXIS_RECALL_REQUIRE_SCOPE", "1")
    monkeypatch.delenv("CORTHEXIS_RECALL_PROJECTS", raising=False)
    payload = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "s",
                          "prompt": "how do we deploy the player to production"})
    out = io.StringIO()
    assert R.main(io.StringIO(payload), out) == 0
    assert out.getvalue() == "" and not opened
    monkeypatch.setenv("CORTHEXIS_RECALL_PROJECTS", "radio")
    out = io.StringIO()
    R.main(io.StringIO(payload), out)
    ctx = json.loads(out.getvalue())["hookSpecificOutput"]["additionalContext"]
    assert "radio-deploy-runbook" in ctx and "tv-" not in ctx



# ---- the service behind the MCP tools ----------------------------------------------------

@pytest.fixture()
def svc(monkeypatch):
    from corthexis import service

    st = TwoProjectStore(honours_scope=False)
    monkeypatch.setattr(service, "get_store", lambda: st)
    monkeypatch.setattr(service, "embedder", lambda: Emb())
    monkeypatch.setattr(service, "_reranker", lambda deep=False: None)
    monkeypatch.delenv("CORTHEXIS_RECALL_PROJECTS", raising=False)
    monkeypatch.delenv("SOKKAN_RECALL_PROJECTS", raising=False)
    return service, st


def test_service_without_a_scope_is_unchanged(svc):
    service, st = svc
    names = {d["note_name"] for d in service.search("deploy the player", 10)}
    assert names == set(st.notes) and st.search_kw[-1] == {}
    assert service.get("tv-secret-roadmap").endswith("body of tv-secret-roadmap")
    assert service.get("tv-secret-roadmap.md") is not None
    assert service.get("no-such-note") is None
    assert service.links("tv-secret-roadmap")["note"] == "tv-secret-roadmap"
    assert not hasattr(st, "accessed")            # no scope: nothing to audit


def test_service_reads_its_scope_from_the_environment(svc, monkeypatch):
    service, st = svc
    monkeypatch.setenv("CORTHEXIS_RECALL_PROJECTS", "radio")
    out = service.search("deploy the player", 10)
    assert {d["note_name"] for d in out} == {"radio-deploy-runbook",
                                            "radio-db-credentials-rotation"}
    assert st.search_kw[-1] == {"projects": ("radio",)}
    assert all(d["project"] == "radio" for d in out)
    assert service.get("tv-secret-roadmap") is None          # another project: not a note
    assert service.links("tv-secret-roadmap") == {"error": "note not found: tv-secret-roadmap"}
    assert service.get("radio-deploy-runbook", via="mcp", session_id="s1") is not None
    assert st.accessed == [("mcp", "radio-deploy-runbook", "s1")]
    # an explicit scope wins over the environment; an empty one finds nothing
    assert service.get("tv-secret-roadmap", projects=("tv",)) is not None
    assert service.search("deploy the player", 10, projects=())[0]["empty"]
    monkeypatch.setenv("CORTHEXIS_RECALL_PROJECTS", "../etc")   # invalid: empty, not "all"
    assert service.search("deploy the player", 10)[0]["empty"]
    assert service.get("radio-deploy-runbook") is None


def test_service_filters_a_leaky_store_by_level(svc, monkeypatch):
    service, st = svc
    st.notes["radio-db-credentials-rotation"].level = 4
    monkeypatch.setenv("CORTHEXIS_RECALL_PROJECTS", "radio@3")
    assert {d["note_name"] for d in service.search("rotate the credentials", 10)} == {
        "radio-deploy-runbook"}
    assert service.get("radio-db-credentials-rotation") is None
    monkeypatch.setenv("CORTHEXIS_RECALL_PROJECTS", "radio@4")
    assert service.get("radio-db-credentials-rotation") is not None


def test_narrow_never_widens_the_service_scope():
    from corthexis import service
    assert service.narrow(("radio", "tv@4"), None) == ("radio", "tv@4")
    assert service.narrow(None, ("radio@3",)) == ("radio@3",)
    assert service.narrow(("radio@4", "tv", "x"), ("radio@3", "tv@1")) == ("radio@3", "tv@1")
    assert service.narrow((), ("radio",)) == ()
