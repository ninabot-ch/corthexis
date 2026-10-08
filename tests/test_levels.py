"""Levels (2.1) — the five classification levels, the clearance carried by a scope entry
(``project@N``), and the recaller's own checks: nothing above the caller's clearance is
handed out, even by a store that ignores the scope. Against Postgres: test_levels_pg.py."""


# ---- levels and scopes (engine) -----------------------------------------------------------
def test_levels_parse_labels_and_fail_closed(monkeypatch):
    from corthexis import levels as L
    assert L.parse(None) is None and L.parse("") is None
    assert L.parse("confidential") == 3 and L.parse("RESTRICTED") == 4 and L.parse(1) == 1
    assert L.parse("typo-level") == L.MAX           # unknown = restricted, never public
    monkeypatch.setenv("CORTHEXIS_CLASSIFICATION_LABELS", "Public,Interne,Projet,Confidentiel,Secret")
    assert L.parse("secret") == 4 and L.label(1) == "Interne" and L.ident(1) == "team"
    monkeypatch.delenv("CORTHEXIS_CLASSIFICATION_LABELS")
    monkeypatch.setenv("SOKKAN_CLASSIFICATION_LABELS", "A,B,C,D,E")   # read as a fallback
    assert L.label(4) == "E" and L.parse("e") == 4
    monkeypatch.setenv("SOKKAN_CLASSIFICATION_LABELS", "only,four,labels,here")   # ignored
    assert L.label(4) == "Restricted"
    assert L.highest([1, 3, None, 2]) == 3 and L.highest([]) == L.DEFAULT


def test_scope_entries_carry_the_clearance_and_only_narrow():
    from corthexis import scope as S
    assert S.normalize(["radio@3", "shared"]) == ("radio@3", "shared")
    assert S.normalize(["radio@4", "radio@1"]) == ("radio@1",)        # the lower one wins
    assert S.normalize(["radio@9", "../x", "radio"]) == ("radio",)      # invalid dropped
    sc = S.normalize(["radio@3", "shared"])
    assert S.allows(sc, "radio", 3) and not S.allows(sc, "radio", 4)
    assert S.allows(sc, "shared", 2) and not S.allows(sc, "shared", 3)  # bare = default
    assert not S.allows(sc, "default", 0) and S.allows(None, "x", 4)
    assert S.projects_of(sc) == ("radio", "shared")
    assert S.from_env("radio@3,shared") == sc


def test_filter_hits_drops_what_a_store_returned_above_the_clearance():
    from corthexis import scope as S
    from corthexis.search import Hit
    hits = [Hit("a", 1, 1, 0, None, "", 1, "indexed", project="radio", level=4),
            Hit("b", 1, 1, 0, None, "", 1, "indexed", project="radio", level=2),
            Hit("c", 1, 1, 0, None, "", 1, "indexed", project="radio")]   # no level = default
    assert [h.note_name for h in S.filter_hits(hits, ("radio@3",))] == ["b", "c"]
    assert [h.note_name for h in S.filter_hits(hits, ("radio@1",))] == []


def test_recaller_never_forces_a_quoted_note_above_the_clearance():
    """Channel 5 (quoted names) with a store that IGNORES the scope: the recaller's own
    checks still drop the classified note."""
    from corthexis import recall as R
    from corthexis.contract import NoteRecord
    from corthexis.search import Hit

    class Gen:
        id, dim, embed_identity, chunk_count = 1, 2, "x", 1

    class Leaky:
        def active_generation(self):
            return Gen()

        def search(self, *a, **k):
            return [Hit("radio-keys", 0.9, 0.9, 0.5, None, "k", 1, "indexed",
                        project="radio", level=4)]

        def existing_names(self, names, projects=None):
            return {"radio-keys"}

        def resolve_note(self, name, projects=None):
            return NoteRecord("radio-keys", "d", "project", 0, "/x.md", None, "indexed",
                              "secret body", "radio", 4)

        def recalled_notes(self, *a, **k):
            return set()

        def log_recall(self, *a, **k):
            return 0

        def log_recall_turn(self, *a, **k):
            pass

    from test_scope import Emb
    rc = R.Recaller(Leaky(), Emb(), R.RecallConfig(threshold=0.1, top_k=10), profile="leger")
    q = "what about radio-keys and the radio keys rotation"
    res = rc.recall(q, projects=("radio@2",))
    assert [h.note_name for h in res.hits] == []
    res = rc.recall(q, projects=("radio@4",))
    assert "radio-keys" in [h.note_name for h in res.hits]
