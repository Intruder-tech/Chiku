import json
from pathlib import Path

import pytest

from chiku.cancel import CancelToken
from chiku.config import MemorySettings
from chiku.memory import MemoryRejected, MemoryStore, prompt_section, sensitive_reason
from chiku.tools.base import Permission, ToolContext
from chiku.tools.memory_tools import ForgetTool, RememberTool, SearchMemoryTool


@pytest.fixture
def store(tmp_path: Path):
    s = MemoryStore(tmp_path / "m.db")
    yield s
    s.close()


def ctx() -> ToolContext:
    return ToolContext(CancelToken())


def test_add_update_delete_search_export(store: MemoryStore) -> None:
    a = store.add("User prefers tea over coffee", "preference")
    b = store.add("User's sister is Asha", "person")
    assert store.add("user prefers TEA over coffee").id == a.id  # case-insensitive dedupe
    assert [i.id for i in store.search("tea")] == [a.id]
    store.update(b.id, "User's sister is Asha, a doctor")
    assert store.get(b.id).text.endswith("doctor")
    assert json.loads(store.export_json())[0]["text"]
    assert store.delete(a.id) and not store.delete(a.id)
    assert [i.id for i in store.all()] == [b.id]
    assert store.clear() == 1 and store.all() == []


def test_persists_across_instances(tmp_path: Path) -> None:
    s1 = MemoryStore(tmp_path / "m.db")
    s1.add("User lives in Pune")
    s1.close()
    s2 = MemoryStore(tmp_path / "m.db")
    assert [i.text for i in s2.all()] == ["User lives in Pune"]
    s2.close()


@pytest.mark.parametrize("text", [
    "my wifi password is hunter22", "PIN number: 4821", "the OTP is 552211",
    "card 4111 1111 1111 1111", "api key sk-abcdefghijklmnopqrstu", "PAN ABCDE1234F",
    "aadhaar 1234 5678 9012", "ssn 123-45-6789",
])
def test_secrets_are_refused(store: MemoryStore, text: str) -> None:
    assert sensitive_reason(text)
    with pytest.raises(MemoryRejected):
        store.add(text)
    assert store.all() == []


@pytest.mark.parametrize("text", ["User likes jazz", "Meeting room is 4111", "Born in 1998",
                                  "User's phone is a Pixel 9"])
def test_ordinary_facts_allowed(text: str) -> None:
    assert sensitive_reason(text) is None


def test_length_and_empty_rejected(store: MemoryStore) -> None:
    with pytest.raises(MemoryRejected):
        store.add("   ")
    with pytest.raises(MemoryRejected):
        store.add("x" * 400)


def test_prompt_section_limits(store: MemoryStore) -> None:
    for i in range(5):
        store.add(f"fact number {i}")
    text = prompt_section(store.all(), 3)
    assert text.count("\n- [") == 3 and "2 more" in text
    assert prompt_section(store.all(), 0) == ""


def test_remember_permission_depends_on_user_request(store: MemoryStore) -> None:
    said = {"text": ""}
    tool = RememberTool(store, lambda: MemorySettings(), lambda: said["text"])
    args = tool.Input(text="User prefers metric units", category="preference")
    said["text"] = "I like metric units"
    assert tool.permission_for(args) is Permission.CONFIRM  # model's own idea
    said["text"] = "please remember that I like metric units"
    assert tool.permission_for(args) is Permission.AUTO
    res = tool.run(args, ctx())
    assert res.ok and store.all()[0].source == "user asked"
    off = RememberTool(store, lambda: MemorySettings(enabled=False), lambda: "remember x")
    assert off.permission_for(args) is Permission.DENY
    trusting = RememberTool(store, lambda: MemorySettings(ask_before_model_saves=False),
                            lambda: "hi")
    assert trusting.permission_for(args) is Permission.AUTO


def test_remember_tool_refuses_secret(store: MemoryStore) -> None:
    tool = RememberTool(store, lambda: MemorySettings(), lambda: "remember my password")
    res = tool.run(tool.Input(text="Password is hunter22"), ctx())
    assert not res.ok and res.error_code == "rejected"


def test_remember_splits_multiple_facts(store: MemoryStore) -> None:
    tool = RememberTool(store, lambda: MemorySettings(), lambda: "remember this")
    res = tool.run(tool.Input(text="User is vegetarian\nUser lives in Pune"), ctx())
    assert res.ok and sorted(i.text for i in store.all()) == ["User is vegetarian",
                                                              "User lives in Pune"]


def test_forget_asks_when_entry_holds_more_than_requested(store: MemoryStore) -> None:
    store.add("User is vegetarian and they live in Pune")
    t = ForgetTool(store, lambda: MemorySettings(), lambda: "forget that I live in Pune")
    args = t.Input(query="I live in Pune")
    assert t.permission_for(args) is Permission.CONFIRM  # would also delete "vegetarian"
    assert "vegetarian" in t.describe(args)
    store.clear()
    store.add("User lives in Pune")
    assert t.permission_for(args) is Permission.AUTO


def test_forget_and_search_tools(store: MemoryStore) -> None:
    item = store.add("User's favourite colour is green")
    forget = ForgetTool(store, lambda: MemorySettings(), lambda: "forget my favourite colour")
    assert forget.permission_for(forget.Input(query="colour")) is Permission.AUTO
    assert "green" in forget.describe(forget.Input(memory_id=item.id))
    assert forget.run(forget.Input(query="favourite colour"), ctx()).ok
    assert store.all() == []
    assert forget.run(forget.Input(memory_id=999), ctx()).error_code == "not_found"
    sneaky = ForgetTool(store, lambda: MemorySettings(), lambda: "what's the weather")
    assert sneaky.permission_for(sneaky.Input(memory_id=1)) is Permission.CONFIRM
    store.add("User has a dog named Bruno")
    found = SearchMemoryTool(store, lambda: MemorySettings()).run(
        SearchMemoryTool.Input(query="dog"), ctx())
    assert found.data["memories"][0]["text"].endswith("Bruno")
