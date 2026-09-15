"""Repairing the careers URLs a crawl actually reads.

Built from the one source error an otherwise clean overnight pass produced:

    TU Munchen: https://www.tum.de/en/about-tum/working-at-tum: HTTP 404

The page had moved. `check-urls` could not help, because it reads a companies
file and the crawl reads `setup.json`, so the only repair on offer was to run
the whole resolution pipeline again for one URL.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from acide.models import SetupConfig, TargetSource

DEAD = "https://www.tum.de/en/about-tum/working-at-tum"
LIVE = "https://www.tum.de/en/about-tum/careers"

_HOMEPAGE = (
    '<nav><a href="/en/about-tum">About</a>'
    '<a href="/en/about-tum/careers">Careers</a></nav>'
)


def _config(**overrides) -> SetupConfig:
    config = SetupConfig(
        targets=[
            TargetSource(company="TU Munchen", source_type="browser", board_token=DEAD),
            TargetSource(
                company="ExampleCorp", source_type="greenhouse", board_token="examplecorp"
            ),
        ]
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def _mock_moved_page() -> None:
    respx.get(DEAD).mock(return_value=httpx.Response(404))
    respx.get("https://www.tum.de/").mock(return_value=httpx.Response(200, html=_HOMEPAGE))
    respx.get(LIVE).mock(return_value=httpx.Response(200, html="<h1>Careers</h1>"))


def _run(*argv: str, monkeypatch) -> None:
    from acide.__main__ import main

    monkeypatch.setattr("sys.argv", ["acide", *argv])
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 0


@respx.mock
def test_a_rotted_target_url_is_found_and_proposed(monkeypatch, capsys):
    from acide import config as config_module

    config_module.save(_config())
    _mock_moved_page()

    _run("check-urls", "--targets", monkeypatch=monkeypatch)

    printed = capsys.readouterr().out
    assert "TU Munchen" in printed
    assert LIVE in printed
    assert "Nothing was saved" in printed
    # A dry run leaves the configuration exactly as it was.
    assert config_module.load(refresh=True).targets[0].board_token == DEAD


@respx.mock
def test_apply_repoints_the_target_and_leaves_the_rest_alone(monkeypatch, capsys):
    from acide import config as config_module

    config_module.save(_config())
    _mock_moved_page()

    _run("check-urls", "--targets", "--apply", monkeypatch=monkeypatch)

    saved = config_module.load(refresh=True)
    assert saved.targets[0].board_token == LIVE
    # A board token is not a URL and is none of this command's business.
    assert saved.targets[1].board_token == "examplecorp"
    assert "1 target(s) repointed" in capsys.readouterr().out


@respx.mock
def test_a_url_that_still_answers_is_left_alone(monkeypatch, capsys):
    from acide import config as config_module

    config_module.save(_config())
    respx.get(DEAD).mock(return_value=httpx.Response(200, html="<h1>Working at TUM</h1>"))

    _run("check-urls", "--targets", "--apply", monkeypatch=monkeypatch)

    assert config_module.load(refresh=True).targets[0].board_token == DEAD
    assert "Nothing to rewrite" in capsys.readouterr().out


@respx.mock
def test_one_shared_page_is_fetched_once_for_every_target_on_it(monkeypatch):
    """631 organizations resolved to 581 distinct URLs; the duplicates are real."""
    from acide import config as config_module

    config = _config()
    config.targets = [
        TargetSource(company="Thales Alenia", source_type="browser", board_token=DEAD),
        TargetSource(company="Thales SIX", source_type="browser", board_token=DEAD),
        TargetSource(company="Thales DIS", source_type="jsonld", board_token=DEAD),
    ]
    config_module.save(config)
    dead = respx.get(DEAD).mock(return_value=httpx.Response(404))
    respx.get("https://www.tum.de/").mock(return_value=httpx.Response(200, html=_HOMEPAGE))
    respx.get(LIVE).mock(return_value=httpx.Response(200, html="<h1>Careers</h1>"))

    _run("check-urls", "--targets", "--apply", monkeypatch=monkeypatch)

    assert dead.call_count == 1, "one visit, however many targets share the page"
    assert [target.board_token for target in config_module.load(refresh=True).targets] == [
        LIVE,
        LIVE,
        LIVE,
    ]


@respx.mock
def test_failed_narrows_to_what_the_last_crawl_could_not_read(monkeypatch, capsys):
    from acide import config as config_module
    from acide import db

    config = _config()
    config.targets = [
        TargetSource(company="TU Munchen", source_type="browser", board_token=DEAD),
        TargetSource(
            company="Healthy", source_type="browser", board_token="https://healthy.example/jobs"
        ),
    ]
    config_module.save(config)
    db.record_source_run("browser", DEAD, "TU Munchen", status="error", detail="HTTP 404")
    db.record_source_run(
        "browser", "https://healthy.example/jobs", "Healthy", status="ok", postings=4
    )
    _mock_moved_page()
    healthy = respx.get("https://healthy.example/jobs").mock(return_value=httpx.Response(200))

    _run("check-urls", "--targets", "--failed", "--apply", monkeypatch=monkeypatch)

    assert not healthy.called, "a source that worked is not re-checked"
    saved = config_module.load(refresh=True)
    assert saved.targets[0].board_token == LIVE
    assert "Only the 1 of 2" in capsys.readouterr().out


def test_neither_a_file_nor_targets_is_an_error(monkeypatch, capsys):
    from acide.__main__ import main

    monkeypatch.setattr("sys.argv", ["acide", "check-urls"])
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 1
    assert "--targets" in capsys.readouterr().err


def test_sources_points_at_the_repair_when_a_rendered_page_failed(monkeypatch, capsys):
    from acide import config as config_module
    from acide import db

    config = _config()
    config.targets = [
        TargetSource(company="TU Munchen", source_type="browser", board_token=DEAD)
    ]
    config_module.save(config)
    db.record_source_run("browser", DEAD, "TU Munchen", status="error", detail=f"{DEAD}: HTTP 404")

    _run("sources", monkeypatch=monkeypatch)

    printed = capsys.readouterr().out
    assert "failed, by cause" in printed
    assert "acide check-urls --targets --failed" in printed


@respx.mock
def test_a_page_the_crawl_read_is_never_repointed_on_a_plain_404(monkeypatch, capsys):
    """126 pages of one probe report answered 4xx to a plain request and rendered fine.

    A `browser` target is read by a browser; many sites refuse anything else,
    and with 404 as often as 403 — which reads as "deleted" and is not. So a
    source whose last crawl worked keeps its URL whatever a plain fetch says.
    """
    from acide import config as config_module
    from acide import db

    config = _config()
    config.targets = [
        TargetSource(company="TU Munchen", source_type="browser", board_token=DEAD)
    ]
    config_module.save(config)
    db.record_source_run("browser", DEAD, "TU Munchen", status="ok", postings=11)
    _mock_moved_page()

    _run("check-urls", "--targets", "--apply", monkeypatch=monkeypatch)

    assert config_module.load(refresh=True).targets[0].board_token == DEAD
    printed = capsys.readouterr().out
    assert "refuses a plain request, but the crawl read it" in printed
    assert "Nothing to rewrite" in printed
