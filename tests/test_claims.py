"""The README makes claims. These check them against the code.

A number in a README is a promise to whoever reads it, and the cheapest way to break
one is to change the code and forget the prose. Every claim here was true when it was
written; the point of the file is that it stays that way, or the suite says so.

Deliberately not checked: anything measured on a live account (latency, pass rates,
recovery times). Those belong in the reports the runs actually write, not in an
assertion that would have to be loosened until it meant nothing.
"""

from __future__ import annotations

import pathlib
import re

README = pathlib.Path(__file__).resolve().parents[1] / "README.md"
PYPROJECT = pathlib.Path(__file__).resolve().parents[1] / "pyproject.toml"


def readme() -> str:
    return README.read_text(encoding="utf-8")


def test_the_nine_tools_are_nine() -> None:
    from beacon import voice_tools

    assert "nine tools" in readme()
    assert len(voice_tools.TOOL_SCHEMAS) == 9
    assert len(voice_tools.TOOL_FUNCTIONS) == 9


def test_the_three_allowlisted_actions_are_three() -> None:
    """'Three actions. That is all.' is the whole safety pitch; it has to be true."""
    from beacon.remediation import registry

    assert len(registry.REGISTRY) == 3
    for action in registry.REGISTRY:
        assert action in readme(), f"{action} is allowlisted but not in the README"


def test_every_channel_opens_the_session_with_the_same_tools() -> None:
    from beacon import voice_brief, voice_tools

    assert list(voice_brief.TOOL_NAMES) == [s["name"] for s in voice_tools.TOOL_SCHEMAS]


def test_every_consent_tool_can_be_attributed_after_the_fact() -> None:
    """A consent tool with no phrase pattern would pass the audit unexamined."""
    from beacon.phone import attest
    from beacon.phone.bridge import CONSENT_TOOLS

    assert set(attest.CONSENT_PATTERNS) == set(CONSENT_TOOLS)


def test_one_confidence_bar_across_every_channel() -> None:
    """The README says 85%; a second number anywhere would make that a half-truth."""
    from beacon.phone import attest
    from beacon.voice_turn import _MIN_CONSENT_CONFIDENCE

    assert attest.MIN_CONFIDENCE == _MIN_CONSENT_CONFIDENCE == 0.85
    assert "85" in readme()


def test_the_phone_leg_really_does_forward_mu_law_untouched() -> None:
    """The README stopped claiming transcoding the day the code stopped doing it."""
    from beacon.phone.bridge import PHONE_ENCODING

    assert PHONE_ENCODING == "audio/pcmu"
    text = readme()
    assert "untouched" in text
    assert "transcoded 8 kHz" not in text, "the old transcoding claim is back"


def test_the_release_links_point_at_the_version_that_is_built() -> None:
    version = re.search(r'^version = "(.+)"', PYPROJECT.read_text(), re.M)
    assert version is not None
    tag = f"v{version.group(1)}"
    links = set(re.findall(r"releases/download/(v[\d.]+)/", readme()))
    assert links, "the README should link the release artefacts"
    assert links == {tag}, f"README links {links} but the package is {tag}"


def test_the_readme_does_not_promise_a_carrier_we_do_not_have() -> None:
    """The phone path is real and proven; a live phone number is not claimed."""
    text = readme().lower()
    for overclaim in ("call our number", "phone us on", "dial beacon at"):
        assert overclaim not in text


def test_the_allowlist_size_is_stated_correctly_everywhere_it_is_stated() -> None:
    """'Two allowlisted actions' survived a year after a third was added.

    The count appears in the README, in the console's own FAQ and in the judge card,
    and every one of them is a claim about what this can do to somebody's account.
    """
    import re

    from beacon.remediation import registry

    words = {1: "one", 2: "two", 3: "three", 4: "four"}
    right = words[len(registry.REGISTRY)]
    web = pathlib.Path(__file__).resolve().parents[1] / "web/src/components"
    texts = {"README.md": readme()}
    for name in ("Landing.tsx", "NightBoard.tsx"):
        texts[name] = (web / name).read_text(encoding="utf-8")

    # Only the *size* of the allowlist. "one allowlisted fix" is a different claim
    # — exactly one fix is proposed — and it is correct, so it must not be caught.
    pattern = re.compile(
        r"\b(one|two|three|four)\s+allowlisted\s+(actions?|writes?)\b", re.I
    )
    for where, text in texts.items():
        for said, _noun in pattern.findall(text):
            assert said.lower() == right, (
                f"{where} says '{said} allowlisted' but the registry has "
                f"{len(registry.REGISTRY)}"
            )


def test_every_route_the_docs_name_actually_exists() -> None:
    """A documented endpoint that 404s is worse than an undocumented one.

    Placeholders are normalised: the prose writes `<id>` where the code writes
    `<session_id>`, and that difference is a house style, not a mistake. Paths under
    `/v1/` and `/v2/` are AssemblyAI's own API, not ours, and are not ours to have.
    """
    import re

    root = pathlib.Path(__file__).resolve().parents[1]

    def shape(route: str) -> str:
        return re.sub(r"<[^>]+>", "<>", route.rstrip("/"))

    real = {
        shape(m.group(2))
        for p in (root / "src/beacon").rglob("*.py")
        for m in re.finditer(
            r'@app\.(get|post|delete)\("([^"]+)"\)', p.read_text(encoding="utf-8")
        )
    }
    assert real, "no routes found in the source"

    pages = [root / "README.md", *(root / "docs").glob("*.md")]
    missing: list[str] = []
    for page in pages:
        for m in re.finditer(
            r"`(?:GET|POST|DELETE) (/[a-zA-Z0-9/<>_-]+)`",
            page.read_text(encoding="utf-8"),
        ):
            route = m.group(1)
            if route.startswith(("/v1/", "/v2/")):
                continue  # AssemblyAI's API, documented where it is used
            if shape(route) not in real:
                missing.append(f"{page.name} names {route}")
    assert not missing, "documented routes that do not exist: " + "; ".join(missing)
