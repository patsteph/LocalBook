"""A peer edits sources and the notebook list through the desktop's own handlers, only where it can see."""
import types

import pytest
from services.sync import phone_edits, phone_library
from tests.test_phone_library import NB_OPEN, NB_PRIVATE, _call, lib  # noqa: F401


class Model:
    """Stands in for the pydantic request models: keeps whatever it is given."""
    def __init__(self, **kw):
        self.__dict__.update(kw)


@pytest.fixture()
def apis(monkeypatch):
    calls = []

    async def delete_source(nb, sid): calls.append(("delete", nb, sid)); return {"cleanup_notes": None}
    async def move_source(nb, sid, req): calls.append(("move", nb, sid, req.target_notebook_id)); return {"reindexed": True}
    async def create_notebook(req): calls.append(("create", req.title, req.color)); return {"id": "nb-new", "title": req.title, "color": req.color}
    async def rename_notebook(nb, req): calls.append(("rename", nb, req.title)); return {}
    async def update_notebook_color(nb, req): calls.append(("color", nb, req.color)); return {}
    async def delete_notebook(nb): calls.append(("delete_nb", nb)); return {}
    async def move_notebook(nb, req): calls.append(("section", nb, req.section_id)); return {}
    async def create_section(req): calls.append(("section_create", req.name)); return {"id": "sec-1", "name": req.name}
    async def update_section(sid, req): calls.append(("section_rename", sid, req.name)); return {}
    async def delete_section(sid): calls.append(("section_delete", sid)); return True

    sources = types.SimpleNamespace(delete_source=delete_source, move_source=move_source, MoveSourceRequest=Model)
    notebooks = types.SimpleNamespace(create_notebook=create_notebook, rename_notebook=rename_notebook,
                                      update_notebook_color=update_notebook_color, delete_notebook=delete_notebook,
                                      move_notebook=move_notebook, create_section=create_section, update_section=update_section,
                                      delete_section=delete_section, NotebookCreate=Model, NotebookRename=Model,
                                      NotebookColorUpdate=Model, NotebookMove=Model, SectionCreate=Model, SectionUpdate=Model)
    monkeypatch.setattr(phone_edits, "_sources_api", lambda: sources)
    monkeypatch.setattr(phone_edits, "_notebooks_api", lambda: notebooks)
    return calls


def test_a_peer_deletes_and_moves_sources_it_can_see(lib, apis):
    r = _call(lib, "/phone/library/source/delete", {"notebook_id": NB_OPEN, "source_id": "s1"})
    assert r.status_code == 200 and r.json()["deleted"] is True
    r = _call(lib, "/phone/library/source/move", {"notebook_id": NB_OPEN, "source_id": "s2", "target_notebook_id": NB_PRIVATE})
    assert r.status_code == 200 and r.json()["reindexed"] is True
    assert apis == [("delete", NB_OPEN, "s1"), ("move", NB_OPEN, "s2", NB_PRIVATE)]


def test_a_device_only_notebook_is_untouchable_and_not_a_target(lib, apis):
    phone_library.set_excluded([NB_PRIVATE])
    assert _call(lib, "/phone/library/source/delete", {"notebook_id": NB_PRIVATE, "source_id": "p1"}).status_code == 404
    assert _call(lib, "/phone/library/source/move", {"notebook_id": NB_OPEN, "source_id": "s1", "target_notebook_id": NB_PRIVATE}).status_code == 404
    assert _call(lib, "/phone/library/notebook/edit", {"action": "rename", "notebook_id": NB_PRIVATE, "title": "x"}).status_code == 404
    assert apis == []


def test_bad_ids_and_empty_titles_are_refused_before_any_handler_runs(lib, apis):
    assert _call(lib, "/phone/library/source/delete", {"notebook_id": NB_OPEN, "source_id": "s1; DROP"}).status_code == 400
    assert _call(lib, "/phone/library/notebook/edit", {"action": "create", "title": "   "}).status_code == 400
    assert _call(lib, "/phone/library/notebook/edit", {"action": "color", "notebook_id": NB_OPEN, "color": "blue"}).status_code == 400
    assert _call(lib, "/phone/library/notebook/edit", {"action": "dance"}).status_code == 400
    assert apis == []


def test_notebook_and_section_actions_reach_the_desktop_handlers(lib, apis):
    e = lambda body: _call(lib, "/phone/library/notebook/edit", body)
    assert e({"action": "create", "title": "  Trips  2027 ", "color": "#112233"}).json() == {"id": "nb-new", "title": "Trips 2027", "color": "#112233"}
    assert e({"action": "rename", "notebook_id": NB_OPEN, "title": "Open again"}).status_code == 200
    assert e({"action": "color", "notebook_id": NB_OPEN, "color": "#AABBCC"}).status_code == 200
    assert e({"action": "section", "notebook_id": NB_OPEN, "section_id": "sec-1"}).status_code == 200
    assert e({"action": "section", "notebook_id": NB_OPEN, "section_id": None}).status_code == 200
    assert e({"action": "section_create", "name": "Work"}).json() == {"id": "sec-1", "name": "Work"}
    assert e({"action": "section_rename", "section_id": "sec-1", "name": "Play"}).status_code == 200
    assert e({"action": "section_delete", "section_id": "sec-1"}).status_code == 200
    assert e({"action": "delete", "notebook_id": NB_OPEN}).status_code == 200
    assert e({"action": "sections"}).json() == {"sections": []}
    assert [c[0] for c in apis] == ["create", "rename", "color", "section", "section", "section_create", "section_rename", "section_delete", "delete_nb"]
    assert ("section", NB_OPEN, None) in apis
