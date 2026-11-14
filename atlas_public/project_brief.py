"""Render the versioned Atlas project brief as a static, source-linked web page.

The single structured source is docs/atlas_project_brief.json.
This publishes public-safe operating context, NOT private source archives.
Only Python's standard library is needed for Cloudflare Pages.
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from urllib.parse import urlsplit
import shutil

REPO = Path(__file__).resolve().parents[1]
BRIEF = REPO / "docs" / "atlas_project_brief.json"
STYLESHEET = Path(__file__).with_name("project_brief.css")


def esc(value):
    return html.escape(str(value), quote=True)


def source_link(item):
    url = str(item["url"])
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != "github.com" or parts.username or parts.password:
        raise ValueError("Project brief evidence links must be safe official GitHub HTTPS URLs")
    return f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(item["label"])}</a>'


def render(brief):
    if brief.get("schema_version") != 1 or brief.get("project") != "World Economic Atlas":
        raise ValueError("Unknown Atlas project brief version")
    if not (brief.get("updated_date") and len(brief.get("workstreams", [])) >= 4
            and brief.get("direction") and brief.get("assistant_start_here")):
        raise ValueError("Incomplete Atlas operating brief")
    for item in brief["workstreams"]:
        if not item.get("name") or not item.get("evidence") or not item.get("next") or not item.get("links"):
            raise ValueError("Project workstreams need independently traceable evidence and next action")

    def list_items(items, ordered=False):
        tag = "ol" if ordered else "ul"
        return f"<{tag}>" + "".join(f"<li>{esc(x)}</li>" for x in items) + f"</{tag}>"

    metrics = "".join(
        f'<div class="brief-metric"><span>{esc(x["label"])}</span>'
        f'<strong>{esc(x["value"])}</strong><p>{esc(x["detail"])}</p></div>'
        for x in brief["numbers"]
    )
    workstreams = "".join(
        '<article class="brief-work-card">'
        f'<div class="brief-card-head"><h3>{esc(w["name"])}</h3>'
        f'<span class="brief-state">{esc(w["stage"])}</span></div>'
        f'<p>{esc(w["evidence"])}</p>'
        f'<div class="brief-next"><strong>Next action</strong><p>{esc(w["next"])}</p></div>'
        f'<div class="brief-evidence">{"".join(source_link(z) for z in w["links"])}</div>'
        '</article>'
        for w in brief["workstreams"]
    )
    decisions = "".join(
        '<li class="brief-decision">'
        f'<time datetime="{esc(d["date"])}">{esc(d["date"])}</time>'
        f'<div><strong>{esc(d["title"])}</strong><p>{esc(d["description"])}</p>'
        + source_link({"url": d["evidence_url"], "label": "Original evidence"}) +
        '</div></li>'
        for d in reversed(brief["decisions"])
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="theme-color" content="#111f36">
  <meta name="description" content="World Economic Atlas: current project direction, source completion, decision log, and verified source evidence.">
  <title>Project Brief &amp; Decision Log — World Economic Atlas</title>
  <link rel="stylesheet" href="/project/project.css">
</head>
<body>
  <header class="brief-top">
    <a class="brief-brand" href="/"><span class="brief-mark">A</span><span><strong>WORLD ECONOMIC ATLAS</strong><small>Project brief &amp; direction</small></span></a>
    <nav aria-label="Site navigation"><a href="/">Explore data</a><a aria-current="page" href="/project/">Project brief</a><a href="https://github.com/diegoduque16/Global-economic-policy-tool" target="_blank" rel="noopener noreferrer">GitHub</a></nav>
  </header>
  <div class="brief-shell">
    <aside class="brief-side" aria-label="On this page">
      <span class="brief-side-eyebrow">OPERATING REFERENCE</span>
      <strong>Start here next time</strong>
      <p>The agreed direction, evidence and next steps for continuing Atlas without repeating old assumptions.</p>
      <nav>
        <a href="#direction">Direction</a>
        <a href="#numbers">Reference figures</a>
        <a href="#workstreams">Collection workstreams</a>
        <a href="#decisions">Decision log</a>
        <a href="#next-actions">Next actions</a>
        <a href="#assistants">For future sessions</a>
      </nav>
      <div class="brief-aside-note">Snapshot, not a live collector dashboard. Follow the linked GitHub jobs for current results.</div>
    </aside>
    <main class="brief-main">
      <section class="brief-hero" id="direction">
        <div class="brief-kicker">PROJECT REFERENCE · {esc(brief["updated_date"])}</div>
        <h1>{esc(brief["headline"])}</h1>
        <p class="brief-lead">{esc(brief["mission"])}</p>
        <div class="brief-update"><span class="brief-dot" aria-hidden="true"></span><span>{esc(brief["status_basis"])}</span></div>
      </section>

      <section class="brief-section">
        <div class="brief-section-title"><span>01</span><h2>Latest agreed direction</h2></div>
        {list_items(brief["direction"])}
      </section>

      <section class="brief-section" id="numbers">
        <div class="brief-section-title"><span>02</span><h2>Numbers with clear boundaries</h2></div>
        <p class="brief-context">These are different stages and vintage-specific datasets. Source observations, native files and public exports can overlap: never sum them into one total.</p>
        <div class="brief-metrics">{metrics}</div>
      </section>

      <section class="brief-section" id="workstreams">
        <div class="brief-section-title"><span>03</span><h2>Where data completion stands</h2></div>
        <p class="brief-context">Evidence reflects the dated checkpoint only. Click a run for its latest results. “Provider complete” is never implied by a selected indicator set.</p>
        <div class="brief-work-grid">{workstreams}</div>
      </section>

      <section class="brief-section" id="decisions">
        <div class="brief-section-title"><span>04</span><h2>Decisions and changes</h2></div>
        <p class="brief-context">Latest first, with a direct link to the supporting repository change or production run.</p>
        <ol class="brief-timeline">{decisions}</ol>
      </section>

      <section class="brief-section" id="next-actions">
        <div class="brief-section-title"><span>05</span><h2>The next work to do</h2></div>
        {list_items(brief["next_actions"], ordered=True)}
      </section>

      <section class="brief-section" id="assistants">
        <div class="brief-section-title"><span>06</span><h2>Instructions for continuing Atlas</h2></div>
        <p class="brief-context">The source of truth is a versioned, machine-readable project brief in the GitHub repository. A new chat should read it and then check live workflow outcomes before making claims.</p>
        {list_items(brief["assistant_start_here"], ordered=True)}
        <div class="brief-machine">
          <strong>Reusable starting point</strong>
          <p>Continue World Economic Atlas. Read the current Project Brief at <a href="/project/brief.json">/project/brief.json</a> (or <a href="https://github.com/diegoduque16/Global-economic-policy-tool/blob/main/docs/atlas_project_brief.json" target="_blank" rel="noopener noreferrer">the GitHub source</a>), check the latest linked GitHub runs, then continue the completion-first data collection plan. Distinguish source verified from integrated and publicly released.</p>
        </div>
      </section>

      <footer>World Economic Atlas · This page is an operational project record, not a real-time data feed or a statement that unpublished source data are public.</footer>
    </main>
  </div>
</body>
</html>
"""


def build(source=BRIEF, destination=None):
    source = Path(source)
    dest = Path(destination or (REPO / "atlas_public" / "dist" / "project"))
    brief = json.loads(source.read_text(encoding="utf-8"))
    markup = render(brief)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "index.html").write_text(markup, encoding="utf-8")
    # A single canonical source, exposed both in Git and as the web's
    # machine-readable starting point; do not maintain a separate copy of facts.
    shutil.copy2(source, dest / "brief.json")
    shutil.copy2(STYLESHEET, dest / "project.css")
    return {"project_brief_version": brief["schema_version"],
            "updated_date": brief["updated_date"],
            "workstreams": len(brief["workstreams"]),
            "decision_entries": len(brief["decisions"])}


if __name__ == "__main__":
    print(json.dumps(build(), indent=2))
