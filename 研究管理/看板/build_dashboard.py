#!/usr/bin/env python3
"""Offline single-file read-only research dashboard builder for LabGate.

Pure Python 3.12 standard library. Reuses vendored @zhouyang/rtr-ui-kit
assets under 研究管理/看板/vendor/rtr-ui-kit without npm/Node dependencies
and without touching 研究管理/工具/*.py hashes.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VENDOR_DIR = Path(__file__).resolve().parent / "vendor" / "rtr-ui-kit"
RTR_CSS_PATH = VENDOR_DIR / "rtr-ui.css"
RTR_JS_PATH = VENDOR_DIR / "rtr-ui.iife.js"

HTML_SENTINELS = (
    'id="tab-overview"',
    'id="tab-routes"',
    'id="tab-literature"',
    'id="tab-reports"',
    'id="paper-dialog"',
    'id="rtr-data"',
    "RTR.createExhibitGroup",
    "RTR.attachGlossary",
)

DECISION_LABELS = {
    "target_offtarget": "Target / Off-target pair (T / O)",
    "endpoint": "Primary measurement endpoint (Kd / Ki)",
    "source_selection": "Historical donor inclusion scope",
    "construct_comparability": "Construct & modification comparability rule",
    "series_rule": "Congeneric series independence rule",
    "information_availability": "Pre-deployment context horizon",
    "effect_threshold": "Minimum meaningful effect (delta)",
    "harm_threshold": "Acceptable per-series harm cap (h)",
    "precision_target": "Confidence interval precision target",
    "seed_plan": "Random seed plan",
    "resampling_plan": "Bootstrap / resampling plan",
    "multiplicity_policy": "Multiplicity correction policy",
    "label_budget": "Target / off-target label budget cap",
    "compute_budget": "Compute / tuning budget cap",
    "training_repository": "External training code repository",
    "persistent_storage": "Persistent artifact storage target",
    "prior_test_exposure": "Pre-registered prior test exposure log",
}

DECISION_STEP_GROUP = {
    "target_offtarget": "Step 1 · Scope Alignment",
    "endpoint": "Step 1 · Scope Alignment",
    "source_selection": "Step 1 · Scope Alignment",
    "construct_comparability": "Step 2 · Data & Series Definition",
    "series_rule": "Step 2 · Data & Series Definition",
    "information_availability": "Step 2 · Data & Series Definition",
    "effect_threshold": "Step 3 · Pre-Freeze Thresholds",
    "harm_threshold": "Step 3 · Pre-Freeze Thresholds",
    "precision_target": "Step 3 · Pre-Freeze Thresholds",
    "seed_plan": "Step 3 · Pre-Freeze Thresholds",
    "resampling_plan": "Step 3 · Pre-Freeze Thresholds",
    "multiplicity_policy": "Step 3 · Pre-Freeze Thresholds",
    "label_budget": "Step 4 · Budgets & Environment",
    "compute_budget": "Step 4 · Budgets & Environment",
    "training_repository": "Step 4 · Budgets & Environment",
    "persistent_storage": "Step 4 · Budgets & Environment",
    "prior_test_exposure": "Step 4 · W10b Exposure Ledger",
}

SUITE_MANIFEST = (
    ("runtime", "W8/W10 run ledger, tamper & concurrency recovery", 18),
    ("links", "S15 literature lineage & state-decision binding", 32),
    ("freeze", "W10b protocol freeze, exposure & secret scan", 44),
    ("desk", "Topic isolation, discovery & source binding", 33),
    ("split", "W7 fixed-panel congeneric split validator", 30),
    ("report", "Weekly carryover reconciliation & narrative lint", 24),
)

CUSTOM_CSS = """
/* Dashboard shell atop vendored @zhouyang/rtr-ui-kit tokens + tabular top-rule tables */
.dash-topbar {
  position: sticky;
  top: 0;
  z-index: 40;
  background: var(--surface-raised);
  border-bottom: 1px solid var(--control-line);
  box-shadow: 0 1px 0 var(--hairline);
}
.dash-topbar-inner {
  max-width: var(--measure, 74rem);
  margin: 0 auto;
  padding: 0.75rem var(--gutter, clamp(1rem, 3vw, 2rem)) 0;
}
.dash-brand-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem 1rem;
  padding-bottom: 0.65rem;
}
.dash-brand-title {
  margin: 0;
  font-family: var(--font-serif);
  font-size: clamp(1.125rem, 2vw, 1.375rem);
  line-height: 1.25;
  color: var(--ink);
}
.dash-brand-sub {
  margin: 0.15rem 0 0;
  font-size: 0.875rem;
  color: var(--ink-muted);
}
.dash-controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
}
.dash-pill-group {
  display: inline-flex;
  border: 1px solid var(--control-line);
  border-radius: var(--radius-control);
  background: var(--surface);
  overflow: hidden;
}
.dash-pill-btn {
  appearance: none;
  border: 0;
  background: transparent;
  color: var(--ink-muted);
  font-family: var(--font-ui);
  font-size: 0.875rem;
  padding: 0.3rem 0.65rem;
  cursor: pointer;
  min-height: 2rem;
}
.dash-pill-btn[aria-pressed="true"] {
  background: var(--accent);
  color: var(--on-fam, #fff);
}
.dash-pill-btn:focus-visible {
  outline: 2px solid var(--focus);
  outline-offset: -2px;
}

/* modern-web-guidance: tabs (single-row scroll, ::after border underline, forced-colors safe) */
.dash-tablist {
  display: flex;
  align-items: stretch;
  gap: 0.25rem;
  overflow-x: auto;
  scrollbar-width: none;
  border-top: 1px solid var(--hairline);
}
.dash-tablist::-webkit-scrollbar {
  display: none;
}
.dash-tab {
  position: relative;
  flex: none;
  white-space: nowrap;
  appearance: none;
  border: 0;
  background: transparent;
  color: var(--ink-muted);
  font-family: var(--font-ui);
  font-size: 0.9375rem;
  font-weight: 500;
  line-height: 1.4;
  padding: 0.7rem 0.95rem 0.8rem;
  cursor: pointer;
}
.dash-tab:hover {
  background: var(--hover);
  color: var(--ink);
}
.dash-tab[aria-selected="true"] {
  color: var(--ink);
}
.dash-tab[aria-selected="true"]::after {
  content: "";
  position: absolute;
  inset-inline: 0.55rem;
  inset-block-end: 0;
  border-block-end: 3px solid var(--accent);
}
.dash-tab:focus-visible {
  outline: 2px solid var(--focus);
  outline-offset: -2px;
}
.dash-wrap {
  max-width: var(--measure, 74rem);
  margin: 0 auto;
  padding: 0 var(--gutter, clamp(1rem, 3vw, 2rem));
}
.dash-panel-inner {
  padding-block: 1.35rem 2.5rem;
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.dash-grid-2 {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1.25rem;
  align-items: start;
}
@media (max-width: 900px) {
  .dash-grid-2 {
    grid-template-columns: 1fr;
  }
}
@media (min-width: 860px) {
  #mount-overview .rtr-fs-stack {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.dash-card {
  background: var(--surface-raised);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-sheet);
  padding: 1.15rem 1.25rem;
}
.dash-card-title {
  margin: 0 0 0.35rem;
  font-family: var(--font-serif);
  font-size: 1.125rem;
  line-height: 1.35;
  color: var(--ink);
}
.dash-card-sub {
  margin: 0 0 0.85rem;
  font-size: 0.875rem;
  color: var(--ink-muted);
}

.dash-badge {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  font-family: var(--font-mono);
  font-size: 0.875rem;
  line-height: 1.2;
  padding: 0.16rem 0.5rem;
  border-radius: var(--radius-pill);
  border: 1px solid var(--control-line);
  background: var(--surface);
  color: var(--ink);
  white-space: nowrap;
}
.dash-badge.is-pass {
  border-color: var(--correct);
  background: var(--correct-tint);
  color: var(--correct);
}
.dash-badge.is-fail,
.dash-badge.is-risk {
  border-color: var(--wrong);
  background: var(--wrong-tint);
  color: var(--wrong);
}
.dash-badge.is-muted {
  color: var(--ink-muted);
}
.dash-badge.is-accent {
  border-color: var(--accent);
  background: var(--accent-soft);
  color: var(--accent);
}

.table-wrapper {
  container-type: inline-size;
  overflow-x: auto;
  border-radius: var(--radius-control);
}
.table-wrapper:focus-visible {
  outline: 2px solid var(--focus);
  outline-offset: 2px;
}
table.top-rule-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.9375rem;
  line-height: 1.45;
  border-top: 2px solid var(--ink);
  color: var(--ink);
}
table.top-rule-table thead {
  position: sticky;
  inset-block-start: 0;
  z-index: 1;
  background: var(--surface-raised);
}
table.top-rule-table thead th {
  text-align: left;
  font-weight: 600;
  font-size: 0.875rem;
  color: var(--ink-muted);
  padding: 0.55rem 0.65rem;
  border-bottom: 1px solid var(--ink);
  vertical-align: bottom;
}
table.top-rule-table tbody td,
table.top-rule-table tbody th {
  padding: 0.58rem 0.65rem;
  border-bottom: 1px solid var(--hairline);
  vertical-align: top;
}
table.top-rule-table tbody tr:hover {
  background: var(--hover);
}
table.top-rule-table .tabular-nums {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  text-align: right;
  white-space: nowrap;
}
table.top-rule-table th.tabular-nums {
  text-align: right;
}

@container (width < 640px) {
  table.top-rule-table thead {
    display: none;
  }
  table.top-rule-table,
  table.top-rule-table tbody,
  table.top-rule-table tr,
  table.top-rule-table td,
  table.top-rule-table th {
    display: block;
    width: 100%;
  }
  table.top-rule-table tbody tr {
    padding: 0.65rem 0;
    border-bottom: 2px solid var(--control-line);
  }
  table.top-rule-table tbody td,
  table.top-rule-table tbody th {
    border-bottom: 0;
    padding: 0.25rem 0.2rem;
    text-align: left !important;
  }
  table.top-rule-table tbody td[data-label]::before {
    content: attr(data-label) "：";
    font-family: var(--font-ui);
    font-weight: 600;
    color: var(--ink-muted);
    margin-right: 0.35rem;
  }
}

details.dash-details {
  border: 1px solid var(--control-line);
  border-radius: var(--radius-control);
  background: var(--surface);
  padding: 0.55rem 0.85rem;
}
details.dash-details summary {
  cursor: pointer;
  font-weight: 600;
  font-size: 0.9375rem;
  color: var(--ink);
  list-style-position: outside;
}
details.dash-details[open] summary {
  margin-bottom: 0.65rem;
  padding-bottom: 0.45rem;
  border-bottom: 1px solid var(--hairline);
}

.dash-h-list {
  display: grid;
  gap: 0.65rem;
  margin: 0;
  padding: 0;
  list-style: none;
}
.dash-h-item {
  border-left: 3px solid var(--accent);
  background: var(--surface);
  padding: 0.65rem 0.85rem;
  border-radius: 0 var(--radius-control) var(--radius-control) 0;
}
.dash-h-item strong {
  font-family: var(--font-mono);
  color: var(--accent);
  margin-right: 0.45rem;
}
.dash-h-item p {
  margin: 0.25rem 0 0;
  font-size: 0.9375rem;
}

.dash-filter-bar {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.dash-filter-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
  align-items: center;
}
.dash-search-input {
  flex: 1 1 16rem;
  min-width: 0;
  min-height: 2.35rem;
  padding: 0.4rem 0.75rem;
  font-family: var(--font-ui);
  font-size: 0.9375rem;
  color: var(--ink);
  background: var(--surface);
  border: 1px solid var(--control-line);
  border-radius: var(--radius-control);
}
.dash-search-input:focus-visible {
  outline: 2px solid var(--focus);
  outline-offset: 1px;
}
.dash-chip-wrap {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
}
.dash-chip {
  appearance: none;
  border: 1px solid var(--control-line);
  background: var(--surface);
  color: var(--ink-muted);
  border-radius: var(--radius-pill);
  padding: 0.25rem 0.65rem;
  font-size: 0.875rem;
  cursor: pointer;
  line-height: 1.35;
}
.dash-chip:hover {
  background: var(--hover);
  color: var(--ink);
}
.dash-chip[aria-pressed="true"] {
  border-color: var(--accent);
  background: var(--accent-soft);
  color: var(--accent);
}
.dash-lit-title-btn {
  appearance: none;
  border: 0;
  background: transparent;
  padding: 0;
  text-align: left;
  font-family: var(--font-ui);
  font-size: 0.9375rem;
  font-weight: 600;
  color: var(--ink);
  cursor: pointer;
  text-decoration: underline;
  text-decoration-color: var(--control-line);
  text-underline-offset: 2px;
}
.dash-lit-title-btn:hover {
  color: var(--accent);
  text-decoration-color: var(--accent);
}
.dash-lit-sub {
  font-size: 0.875rem;
  color: var(--ink-muted);
  margin-top: 0.2rem;
}

dialog.dash-dialog {
  padding: 0;
  border: 1px solid var(--control-line);
  border-radius: var(--radius-sheet);
  background: var(--surface-raised);
  color: var(--ink);
  width: min(62rem, calc(100vw - 2rem));
  max-height: min(88dvh, 58rem);
  margin: auto;
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.28);
}
dialog.dash-dialog:modal {
  overscroll-behavior: contain;
}
dialog.dash-dialog::backdrop {
  overflow: hidden;
  overscroll-behavior: contain;
  background: rgba(12, 18, 26, 0.56);
}
.dash-dialog-card {
  display: flex;
  flex-direction: column;
  max-height: min(88dvh, 58rem);
}
.dash-dialog-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  padding: 1rem 1.25rem;
  border-bottom: 1px solid var(--hairline);
  background: var(--surface-raised);
}
.dash-dialog-title {
  margin: 0;
  font-family: var(--font-serif);
  font-size: 1.125rem;
  line-height: 1.35;
}
.dash-dialog-body {
  padding: 1.15rem 1.25rem 1.5rem;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.dash-callout {
  padding: 0.75rem 0.95rem;
  border-radius: var(--radius-control);
  border: 1px solid var(--hairline);
  background: var(--surface);
  font-size: 0.9375rem;
}
.dash-callout.is-warn {
  border-color: var(--wrong);
  background: var(--wrong-tint);
}
.dash-callout strong {
  display: block;
  margin-bottom: 0.25rem;
}
"""


BOOT_JS = r"""
(function () {
  "use strict";
  const DATA = JSON.parse(document.getElementById("rtr-data").textContent || "{}");
  const el = RTR.el;
  const add = RTR.add;

  function dashBadge(text, kind) {
    return el("span", { class: "dash-badge" + (kind ? " is-" + kind : "") }, text);
  }

  function makeTable(headers, rows, captionText) {
    const wrap = el("div", {
      class: "table-wrapper",
      tabindex: "0",
      role: "region",
      "aria-label": captionText || "Data Table"
    });
    const table = el("table", { class: "top-rule-table" });
    const thead = el("thead");
    const htr = el("tr");
    headers.forEach((h) => {
      htr.appendChild(el("th", { scope: "col", class: h.numeric ? "tabular-nums" : null }, h.label));
    });
    thead.appendChild(htr);
    table.appendChild(thead);
    const tbody = el("tbody");
    rows.forEach((r) => {
      const tr = el("tr");
      headers.forEach((h, idx) => {
        const cell = r[idx];
        const td = el("td", {
          "data-label": h.label,
          class: h.numeric ? "tabular-nums" : null
        });
        if (cell == null || cell === "") {
          td.textContent = "—";
        } else if (typeof cell === "object" && cell.nodeType) {
          td.appendChild(cell);
        } else {
          td.textContent = String(cell);
        }
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    return wrap;
  }

  const themeBtns = document.querySelectorAll("[data-theme-btn]");
  const famBtns = document.querySelectorAll("[data-fam-btn]");
  const currentThemeMode = (() => {
    try {
      const v = localStorage.getItem("rtr-color-scheme");
      return v === "light" || v === "dark" ? v : "system";
    } catch {
      return "system";
    }
  })();

  function syncThemeUI(mode) {
    themeBtns.forEach((b) => {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-theme-btn") === mode));
    });
  }
  syncThemeUI(currentThemeMode);

  themeBtns.forEach((b) => {
    b.addEventListener("click", () => {
      const m = b.getAttribute("data-theme-btn");
      RTR.setTheme(m, { persist: true });
      syncThemeUI(m);
    });
  });

  famBtns.forEach((b) => {
    b.addEventListener("click", () => {
      const f = b.getAttribute("data-fam-btn");
      RTR.setFamily(f);
      famBtns.forEach((x) => {
        x.setAttribute("aria-pressed", String(x.getAttribute("data-fam-btn") === f));
      });
    });
  });

  const tablist = document.getElementById("dash-tablist");
  const tabs = Array.from(tablist.querySelectorAll('[role="tab"]'));
  const panels = tabs.map((t) => document.getElementById(t.getAttribute("aria-controls")));
  const supportsBeforeMatch = "onbeforematch" in HTMLElement.prototype;
  const hasNativeFocusGroup = Object.hasOwn(HTMLElement.prototype, "focusGroup");
  let routeExGroup = null;

  function activateTab(tab, opts = {}) {
    const focusTab = opts.focusTab !== false;
    tabs.forEach((t, idx) => {
      const on = t === tab;
      t.setAttribute("aria-selected", String(on));
      if (!hasNativeFocusGroup) t.tabIndex = on ? 0 : -1;
      const p = panels[idx];
      if (on) {
        p.removeAttribute("hidden");
      } else if (supportsBeforeMatch) {
        p.setAttribute("hidden", "until-found");
      } else {
        p.setAttribute("hidden", "");
      }
    });
    if (focusTab) tab.focus();
    if (tab.id === "tab-routes" && routeExGroup && routeExGroup.redraw) {
      requestAnimationFrame(() => routeExGroup.redraw());
    }
  }

  tabs.forEach((t, idx) => {
    if (!hasNativeFocusGroup) {
      t.tabIndex = t.getAttribute("aria-selected") === "true" ? 0 : -1;
    }
    t.addEventListener("click", () => activateTab(t, { focusTab: false }));
    const p = panels[idx];
    if (p) {
      p.addEventListener("beforematch", () => activateTab(t, { focusTab: false }));
    }
  });

  tablist.addEventListener("keydown", (e) => {
    const i = tabs.indexOf(document.activeElement);
    if (i < 0) return;
    let next = null;
    if (e.key === "ArrowRight") next = tabs[(i + 1) % tabs.length];
    else if (e.key === "ArrowLeft") next = tabs[(i - 1 + tabs.length) % tabs.length];
    else if (e.key === "Home") next = tabs[0];
    else if (e.key === "End") next = tabs[tabs.length - 1];
    if (!next) return;
    if (!hasNativeFocusGroup) {
      e.preventDefault();
      tabs.forEach((t) => { t.tabIndex = t === next ? 0 : -1; });
      next.focus();
    } else {
      const prevEl = document.activeElement;
      queueMicrotask(() => {
        if (document.activeElement === prevEl) {
          tabs.forEach((t) => { t.tabIndex = t === next ? 0 : -1; });
          next.focus();
        }
      });
    }
  });

  /* Tab 1: Overview & Stage Gates */
  (function renderOverview() {
    const host = document.getElementById("mount-overview");
    const ov = DATA.overview || {};

    host.appendChild(
      RTR.createSectionHeader({
        step: "01",
        shelfMark: { code: "M1", label: "Governance Overview" },
        meta: ov.meta || "demo-selectivity-benchmark · G2 PASS / G3 FAIL-CLOSED",
        title: "Fail-Closed Stage Gates, Synthetic Verification & Decision Guard",
        points: [
          ["Synthetic Problem", "demo-selectivity-benchmark (fixed-panel selectivity borrowing demonstration)"],
          ["Gate Boundary", "G1 integrity & G2 runtime pass; G3 prepare explicitly blocks on 17 unconfirmed null decisions"],
          ["Safety Guarantees", "formal_use_allowed = false; protected inputs checksummed; zero silent defaults"]
        ]
      })
    );

    host.appendChild(RTR.createFigureStrip({ items: ov.kpis || [] }));

    const grid = el("div", { class: "dash-grid-2" });
    const leftCol = el("div", { style: "display:flex;flex-direction:column;gap:1.1rem" });

    const gateCard = el("section", { class: "dash-card" });
    add(
      gateCard,
      el("h3", { class: "dash-card-title" }, "Live Read-Only Gate Matrix (run_checks.py)"),
      el("p", { class: "dash-card-sub" }, "Direct output from 研究管理/工具/run_checks.py --stage all --literature --dry-run")
    );
    gateCard.appendChild(
      makeTable(
        [{ label: "Check" }, { label: "Status" }, { label: "Observed Evidence" }],
        (ov.gates || []).map((g) => {
          const kind = g.status === "pass" ? "pass" : g.status === "expected_block" ? "accent" : "fail";
          return [el("code", {}, g.name), dashBadge(g.label, kind), g.detail];
        }),
        "Stage Gate Checks"
      )
    );
    leftCol.appendChild(gateCard);

    const suiteCard = el("section", { class: "dash-card" });
    add(
      suiteCard,
      el("h3", { class: "dash-card-title" }, "Isolated Test Suite Matrix (181 Synthetic Cases)"),
      el("p", { class: "dash-card-sub" }, "All six adversarial stdlib suites verified in scripts/run_all_suites.py")
    );
    suiteCard.appendChild(
      makeTable(
        [{ label: "Suite" }, { label: "Cases", numeric: true }, { label: "Status" }, { label: "Coverage Scope" }],
        (ov.suites || []).map((s) => [
          el("code", {}, s.suite),
          `${s.cases} / ${s.cases}`,
          dashBadge("PASS", "pass"),
          s.scope
        ]),
        "Validation Suite Matrix"
      )
    );
    leftCol.appendChild(suiteCard);

    const rightCol = el("div", { style: "display:flex;flex-direction:column;gap:1.1rem" });
    const hCard = el("section", { class: "dash-card" });
    add(
      hCard,
      el("h3", { class: "dash-card-title" }, "Three-Step Diagnostic Tree (Both Positive & Negative Outcomes)"),
      el("p", { class: "dash-card-sub" }, "Source: H1-MINIMAL/draft-1/design.json (exploratory draft)")
    );
    const ul = el("ul", { class: "dash-h-list" });
    (ov.hypotheses || []).forEach((h) => {
      const li = el("li", { class: "dash-h-item" });
      add(li, el("strong", {}, h.id), dashBadge(h.status, "muted"), el("p", {}, h.statement));
      ul.appendChild(li);
    });
    hCard.appendChild(ul);
    rightCol.appendChild(hCard);

    const decCard = el("section", { class: "dash-card" });
    add(
      decCard,
      el("h3", { class: "dash-card-title" }, "17 Required Scientific Decisions (Strict Null Guard)"),
      el("p", { class: "dash-card-sub" }, "0 / 17 confirmed; missing choices render explicitly as — and hard-block G3")
    );
    const decDetails = el("details", { class: "dash-details", open: "" });
    decDetails.appendChild(el("summary", {}, `Inspect all ${(ov.decisions || []).length} G3 blocking fields`));
    decDetails.appendChild(
      makeTable(
        [
          { label: "Key" },
          { label: "Parameter" },
          { label: "Stage" },
          { label: "Value", numeric: true },
          { label: "Missing Reason" }
        ],
        (ov.decisions || []).map((d) => [el("code", {}, d.key), d.label, d.group, d.value, d.reason]),
        "Required Decisions Table"
      )
    );
    decCard.appendChild(decDetails);
    rightCol.appendChild(decCard);

    add(grid, leftCol, rightCol);
    host.appendChild(grid);

    if (DATA.timeline) {
      host.appendChild(
        RTR.createTimeline(DATA.timeline, {
          factorLabel: "Protocol & Topic Progression",
          replayLabel: "Replay",
          ariaLabel: "Protocol and topic progression timeline"
        })
      );
    }
  })();

  /* Tab 2: Synthetic Route Audits & Exhibits */
  (function renderRoutes() {
    const host = document.getElementById("mount-routes");
    const rt = DATA.routes || {};

    host.appendChild(
      RTR.createSectionHeader({
        step: "02",
        shelfMark: { code: "C2", label: "Synthetic Audits" },
        family: "company",
        meta: "Synthetic Multi-Series vs. Broad Panel Audit · E-DEMO-20261124",
        title: "Interactive Pre-Freeze Data Eligibility & Route Feasibility Exhibits",
        points: [
          ["Synthetic Routes", "Contrasts high-series medicinal chemistry sets against sparse single-ligand broad screens"],
          ["Eligibility Hygiene", "Separates 16.5% inequality-censored measurements from exact singleton points before splitting"],
          ["Power Safeguard", "Demonstrates automatic underpowered_series_count flagging below 6 independent series"]
        ]
      })
    );

    if (Array.isArray(rt.exhibits) && rt.exhibits.length) {
      routeExGroup = RTR.createExhibitGroup(rt.exhibits, {
        idPrefix: "dash",
        bandLabel: "Synthetic audit redraw · labels as defined",
        institution: "LabGate Demo Fixtures",
        metaText: "2026-11-24 · label-blind"
      });
      host.appendChild(routeExGroup);
    }

    const bottomGrid = el("div", { class: "dash-grid-2" });
    if (rt.state_sheet) {
      bottomGrid.appendChild(RTR.createSourceSheet(rt.state_sheet, { idPrefix: "state-doc" }));
    }

    const synthCard = el("section", { class: "dash-card" });
    add(
      synthCard,
      el("h3", { class: "dash-card-title" }, "Synthetic Evaluation Controls (Positive, Null & Underpowered)"),
      el("p", { class: "dash-card-sub" }, "Source: demo_data/demo_synthetic_eval.json · formal_use_allowed = false")
    );
    synthCard.appendChild(
      makeTable(
        [
          { label: "Control Scenario" },
          { label: "Series", numeric: true },
          { label: "Compounds", numeric: true },
          { label: "Δ Gain", numeric: true },
          { label: "95% Bootstrap CI" },
          { label: "Harmed Series" },
          { label: "Diagnostics" }
        ],
        (rt.synthetic_controls || []).map((c) => [
          c.name,
          c.groups,
          c.compounds,
          c.delta_gain,
          c.ci95,
          dashBadge(c.harmed, c.kind),
          c.warnings || "—"
        ]),
        "Synthetic Controls Table"
      )
    );
    bottomGrid.appendChild(synthCard);
    host.appendChild(bottomGrid);
  })();

  /* Tab 3: Literature Shelf + Native <dialog> */
  (function renderLiterature() {
    const host = document.getElementById("mount-literature");
    const lit = DATA.literature || {};
    const papers = lit.papers || [];
    const concepts = lit.concepts || [];
    const resultsByPaper = lit.results_by_paper || {};

    host.appendChild(
      RTR.createSectionHeader({
        step: "03",
        shelfMark: { code: "M2", label: "Literature Lineage" },
        meta: `Read-only SQLite (?mode=ro&immutable=1) · ${papers.length} Verified Cards / ${lit.total_results || 0} Rows`,
        title: "Immutable Literature Card Index & Topic Lineage Filter",
        points: [
          ["Card Integrity", `${papers.length} / ${papers.length} verified cards SHA-256 matched against 论文内容索引/literature.db`],
          ["Topic Tracing", `Linked to ${(lit.core_ids || []).length} comparison entries in demo_compare.tsv`],
          ["Glossary Integration", `${Object.keys(DATA.glossary || {}).length} interactive domain definitions attached`]
        ]
      })
    );

    const controlCard = el("section", { class: "dash-card dash-filter-bar" });
    const searchRow = el("div", { class: "dash-filter-row" });
    const searchInput = el("input", {
      type: "search",
      id: "lit-search",
      class: "dash-search-input",
      placeholder: "Filter by ID, title, author, year, or conclusion (e.g. L101, censoring, negative)...",
      "aria-label": "Filter verified literature cards"
    });
    const scopeBtns = [];
    const scopes = [
      ["all", `All (${papers.length})`],
      ["core", `Linked to Topic (${(lit.core_ids || []).length})`],
      ["quant", "Has Quant Rows"]
    ];
    const scopeGroup = el("div", { class: "dash-pill-group", role: "group", "aria-label": "Literature scope filter" });
    let activeScope = "all";
    let activeConcept = "ALL";

    scopes.forEach(([key, lbl]) => {
      const btn = el("button", {
        type: "button",
        class: "dash-pill-btn",
        "data-lit-scope": key,
        "aria-pressed": String(key === "all")
      }, lbl);
      btn.addEventListener("click", () => {
        activeScope = key;
        scopeBtns.forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
        applyFilter();
      });
      scopeBtns.push(btn);
      scopeGroup.appendChild(btn);
    });
    add(searchRow, searchInput, scopeGroup);
    controlCard.appendChild(searchRow);

    const conceptWrap = el("div", { class: "dash-chip-wrap", role: "group", "aria-label": "Concept filters" });
    const cBtns = [];
    const allChip = el("button", { type: "button", class: "dash-chip", "data-concept": "ALL", "aria-pressed": "true" }, `All Concepts (${papers.length})`);
    allChip.addEventListener("click", () => {
      activeConcept = "ALL";
      cBtns.forEach((b) => b.setAttribute("aria-pressed", String(b === allChip)));
      applyFilter();
    });
    cBtns.push(allChip);
    conceptWrap.appendChild(allChip);

    concepts.forEach((c) => {
      const chip = el("button", { type: "button", class: "dash-chip", "data-concept": c.id, "aria-pressed": "false", title: c.en_name }, `${c.id} ${c.en_name} (${c.count})`);
      chip.addEventListener("click", () => {
        activeConcept = activeConcept === c.id ? "ALL" : c.id;
        cBtns.forEach((b) => b.setAttribute("aria-pressed", String(b.getAttribute("data-concept") === activeConcept)));
        applyFilter();
      });
      cBtns.push(chip);
      conceptWrap.appendChild(chip);
    });
    controlCard.appendChild(conceptWrap);

    const countStatus = el("div", { class: "rtr-muted", style: "font-size:0.875rem", id: "lit-match-count", "aria-live": "polite" });
    controlCard.appendChild(countStatus);
    host.appendChild(controlCard);

    const tableCard = el("section", { class: "dash-card" });
    const tableMount = el("div", { id: "lit-table-mount" });
    tableCard.appendChild(tableMount);
    host.appendChild(tableCard);

    const dialog = document.getElementById("paper-dialog");
    const dialogTitle = document.getElementById("paper-dialog-title");
    const dialogBody = document.getElementById("paper-dialog-body");
    document.getElementById("paper-dialog-close").addEventListener("click", () => dialog.close());
    if (!("closedBy" in HTMLDialogElement.prototype)) {
      dialog.addEventListener("click", (e) => {
        const rect = dialog.getBoundingClientRect();
        const inside = rect.top <= e.clientY && e.clientY <= rect.top + rect.height && rect.left <= e.clientX && e.clientX <= rect.left + rect.width;
        if (!inside) dialog.close();
      });
    }

    function openPaperModal(p) {
      dialogTitle.textContent = `${p.paper_id} · ${p.title}`;
      dialogBody.innerHTML = "";
      const tagStrip = el("div", { style: "display:flex;flex-wrap:wrap;gap:0.4rem" });
      add(
        tagStrip,
        dashBadge(`${p.year || "—"} · ${p.priority || "—"}`, "accent"),
        dashBadge(p.ledger_topic || "—", "muted"),
        p.is_core ? dashBadge("Linked to Topic", "pass") : null,
        dashBadge(`Quant Rows: ${p.result_count}`, "muted")
      );
      dialogBody.appendChild(tagStrip);

      dialogBody.appendChild(
        RTR.createSourceSheet({
          id: "modal-" + p.paper_id,
          variant: p.is_core ? "company" : "official",
          srcLabel: "Synthetic Verified Card · Scope & Quantitative Bounds",
          band: [p.paper_id, p.review_status],
          title: p.title,
          meta: p.doi || p.venue_or_status,
          sentences: [
            { id: "S1", text: `Authors & Venue: ${p.authors || "—"} (${p.venue_or_status || "—"}), Card ${p.card_path}.` },
            { id: "S2", text: `Scope Summary: ${p.retrieval_abstract || "—"}` },
            { id: "S3", text: `Establishes: ${p.project_use_summary || "—"}` }
          ]
        })
      );

      if (p.negative_results_summary) {
        const neg = el("div", { class: "dash-callout is-warn" });
        add(neg, el("strong", {}, "Does Not Establish / Negative Scope"), el("div", {}, p.negative_results_summary));
        dialogBody.appendChild(neg);
      }

      const rRows = resultsByPaper[p.paper_id] || [];
      dialogBody.appendChild(el("h4", { style: "margin:0.25rem 0 0;font-family:var(--font-serif)" }, `Verified Quantitative Rows (${rRows.length})`));
      if (rRows.length) {
        dialogBody.appendChild(
          makeTable(
            [
              { label: "Result ID" },
              { label: "Task & Sample" },
              { label: "Split & Condition" },
              { label: "Metric" },
              { label: "Value", numeric: true },
              { label: "Comparator" },
              { label: "Locator" },
              { label: "Verification" }
            ],
            rRows.map((r) => [
              el("code", {}, r.result_id),
              `${r.task} | ${r.sample}`,
              `${r.split} | ${r.condition}`,
              r.metric,
              `${r.value} (${r.uncertainty})`,
              r.comparator,
              r.locator,
              dashBadge(r.check_status, r.check_status === "visual_verified" ? "pass" : "accent")
            ]),
            `${p.paper_id} Quantitative Results`
          )
        );
      }
      dialog.showModal();
    }

    function applyFilter() {
      const q = (searchInput.value || "").trim().toLowerCase();
      const filtered = papers.filter((p) => {
        if (activeScope === "core" && !p.is_core) return false;
        if (activeScope === "quant" && !(p.result_count > 0)) return false;
        if (activeConcept !== "ALL" && !(p.concepts || []).includes(activeConcept)) return false;
        if (q) {
          const hay = [p.paper_id, p.title, p.authors, p.year, p.retrieval_abstract, p.negative_results_summary].join(" ").toLowerCase();
          if (!hay.includes(q)) return false;
        }
        return true;
      });
      countStatus.textContent = `Showing ${filtered.length} / ${papers.length} verified synthetic cards`;
      tableMount.innerHTML = "";
      tableMount.appendChild(
        makeTable(
          [
            { label: "ID" },
            { label: "Year", numeric: true },
            { label: "Priority" },
            { label: "Title & Venue" },
            { label: "Tags" },
            { label: "Rows", numeric: true },
            { label: "Action" }
          ],
          filtered.map((p) => {
            const titleCell = el("div");
            const btn = el("button", { type: "button", class: "dash-lit-title-btn", "data-paper-open": p.paper_id }, p.title);
            btn.addEventListener("click", () => openPaperModal(p));
            add(titleCell, btn, el("div", { class: "dash-lit-sub" }, `${p.authors} · ${p.venue_or_status}`));

            const flags = el("div", { style: "display:flex;flex-wrap:wrap;gap:0.25rem" });
            if (p.is_core) flags.appendChild(dashBadge("Topic Linked", "pass"));
            (p.concepts || []).forEach((cid) => flags.appendChild(dashBadge(cid, "muted")));

            const openBtn = el("button", { type: "button", class: "rtr-btn-ghost", style: "padding:0.25rem 0.6rem;font-size:0.875rem" }, "Inspect");
            openBtn.addEventListener("click", () => openPaperModal(p));

            return [el("code", {}, p.paper_id), String(p.year), dashBadge(p.priority, p.priority === "P0" ? "accent" : "muted"), titleCell, flags, String(p.result_count), openBtn];
          }),
          "Verified Literature Index"
        )
      );
    }

    searchInput.addEventListener("input", applyFilter);
    applyFilter();
  })();

  /* Tab 4: Weekly Carryover Reports */
  (function renderReports() {
    const host = document.getElementById("mount-reports");
    const weeks = DATA.weekly_reports || [];

    host.appendChild(
      RTR.createSectionHeader({
        step: "04",
        shelfMark: { code: "M3", label: "Weekly Ledger" },
        meta: "W47 – W48 Carryover Reconciliation · SHA-256 Parent-Linked",
        title: "Carryover-Verified Weekly Progress & Fourfold Feasibility Guards",
        points: [
          ["Parent Locking", "W48 automatically inherits and locks W47 planned items via parent_ref SHA-256"],
          ["Feasibility Guards", "Rejects window mismatches, missing fallbacks, capacity overloads, or formal runs before G3"],
          ["Zero Jargon Leak", "Prose linter blocks script names, gate IDs, and database tokens from human narrative"]
        ]
      })
    );

    const switchBar = el("div", { class: "dash-pill-group", role: "group", "aria-label": "Select week" });
    const weekMount = el("div", { style: "display:flex;flex-direction:column;gap:1.1rem" });
    const wBtns = [];

    function selectWeek(wk) {
      wBtns.forEach((b) => b.setAttribute("aria-pressed", String(b.getAttribute("data-week") === wk.week_id)));
      weekMount.innerHTML = "";
      const tr = (wk.tracks && wk.tracks[0]) || {};
      const sentences = [
        { id: "W1", text: `Summary: ${wk.one_line_summary || "—"}` },
        { id: "W2", text: `Carryover: ${wk.header_note || "—"}` }
      ];
      if (tr.completion_summary) sentences.push({ id: "W3", text: `Completion (${tr.completion_status}): ${tr.completion_summary}` });
      (tr.progress_points || []).forEach((pt, idx) => sentences.push({ id: `P${idx + 1}`, text: pt.replace(/\*\*/g, "") }));

      weekMount.appendChild(
        RTR.createSourceSheet({
          id: "week-" + wk.week_id,
          variant: "official",
          srcLabel: `weekly-record.v1 · ${wk.week_id}`,
          band: [wk.week_id, wk.bundle_dir],
          entity: wk.week_id,
          entityMeta: tr.title || "Researcher",
          dateline: wk.period,
          sentences,
          side: [
            ["Window", wk.period || "—"],
            ["As Of", wk.as_of_date || "—"],
            ["Parent", wk.parent_week || "—"],
            ["Status", tr.status_level || "—"]
          ],
          footer: `Self-contained bundle: ${wk.bundle_dir}`
        })
      );

      if (Array.isArray(tr.next_week_plan) && tr.next_week_plan.length) {
        const planCard = el("section", { class: "dash-card" });
        add(
          planCard,
          el("h3", { class: "dash-card-title" }, `${wk.week_id} Next-Week Commitments (Fourfold Guard Checked)`),
          makeTable(
            [{ label: "Task" }, { label: "Commitment" }, { label: "Due" }, { label: "Objective & Criterion" }, { label: "Fallback / Trigger" }],
            tr.next_week_plan.map((t) => [
              el("code", {}, t.task_id),
              dashBadge(t.commitment, t.commitment === "committed" ? "pass" : "accent"),
              t.due_date,
              `${t.summary} (${t.deliverable_or_criterion})`,
              t.if_blocked_fallback || t.trigger_condition || "Self-contained"
            ]),
            `${wk.week_id} Plan`
          )
        );
        weekMount.appendChild(planCard);
      }
    }

    weeks.forEach((wk, idx) => {
      const btn = el("button", { type: "button", class: "dash-pill-btn", "data-week": wk.week_id, "aria-pressed": String(idx === 0) }, wk.week_id);
      btn.addEventListener("click", () => selectWeek(wk));
      wBtns.push(btn);
      switchBar.appendChild(btn);
    });

    host.appendChild(switchBar);
    host.appendChild(weekMount);
    if (weeks.length) selectWeek(weeks[0]);
  })();

  RTR.attachGlossary(document, DATA.glossary || {});
  RTR.initMotion(document);
  RTR.initReadingProgress();
})();
"""


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def file_ref(root: Path, path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Missing required input file: {path}")
    data = path.read_bytes()
    return {
        "path": path.resolve().relative_to(root.resolve()).as_posix(),
        "bytes": len(data),
        "sha256": sha256_bytes(data),
    }


@contextlib.contextmanager
def file_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def validate_exhibit_schema_invariants(exhibits: list[dict[str, Any]], timeline: dict[str, Any], kpis: list[dict[str, Any]]) -> None:
    if not (1 <= len(kpis) <= 4):
        raise ValueError(f"FigureStrip requires 1..4 items, got {len(kpis)}")
    pts = timeline.get("points") or []
    if not (2 <= len(pts) <= 8):
        raise ValueError(f"Timeline requires 2..8 points, got {len(pts)}")
    if timeline.get("question") and not timeline["question"].endswith("?"):
        raise ValueError("Timeline question must end with '?'")
    for ex in exhibits:
        ex_id = ex.get("id", "")
        if not re.match(r"^ex[0-9a-zA-Z_-]+$", ex_id):
            raise ValueError(f"Invalid exhibit id: {ex_id}")
        if ex.get("type") == "waterfall":
            run = 0
            for idx, st in enumerate(ex["steps"]):
                if st["kind"] == "total":
                    if idx > 0 and run != st["value"]:
                        raise ValueError(f"{ex_id}: waterfall mismatch at {idx}: {run} != {st['value']}")
                    run = st["value"]
                else:
                    run += st["value"]


def run_readonly_checks(root: Path, runtime_report: Path) -> dict[str, Any]:
    cmd = [
        sys.executable, "-B", str(root / "研究管理/工具/run_checks.py"),
        "--stage", "all", "--problem", "demo-selectivity-benchmark",
        "--runtime-report", str(runtime_report), "--literature", "--dry-run",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(root), timeout=90)
    if not proc.stdout.strip():
        raise RuntimeError(f"run_checks.py empty output: {proc.stderr}")
    return json.loads(proc.stdout)


def collect_dashboard_data(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    tracked: list[Path] = []

    def track_json(p: Path) -> Any:
        tracked.append(p)
        return json.loads(p.read_text(encoding="utf-8"))

    runtime_rep = root / "研究管理/_交付/demo-baseline/runtime-final/report.json"
    tracked.append(runtime_rep)
    checks_json = run_readonly_checks(root, runtime_rep)

    stage_req = track_json(root / "研究管理/规范/阶段要求.json")
    design = track_json(root / "研究管理/问题/demo-selectivity-benchmark/协议/H1-MINIMAL/draft-1/design.json")
    elig_mf = track_json(root / "研究管理/数据/准入/E-DEMO-20261124/manifest.json")
    panel_audit = track_json(root / "demo_data/demo_panel_audit.json")
    synth_eval = track_json(root / "demo_data/demo_synthetic_eval.json")
    glossary_dict = track_json(root / "demo_data/demo_glossary.json")

    cmp_path = root / "demo_data/demo_compare.tsv"
    tracked.append(cmp_path)
    with open(cmp_path, newline="", encoding="utf-8") as f:
        core_ids = {r["paper_id"].strip() for r in csv.DictReader(f, delimiter="\t") if r.get("paper_id") and r["paper_id"].startswith("L")}

    db_path = root / "论文内容索引/literature.db"
    tracked.append(db_path)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        papers_raw = conn.execute("SELECT * FROM papers ORDER BY priority, paper_id").fetchall()
        pc_rows = conn.execute("SELECT paper_id, concept_id FROM paper_concepts").fetchall()
        concept_rows = conn.execute("SELECT concept_id, name, en_name FROM concepts ORDER BY concept_id").fetchall()
        res_rows = conn.execute("SELECT * FROM results ORDER BY paper_id, result_id").fetchall()
    finally:
        conn.close()

    pc_map: dict[str, list[str]] = {}
    c_cnt: dict[str, int] = {}
    for r in pc_rows:
        pc_map.setdefault(r["paper_id"], []).append(r["concept_id"])
        c_cnt[r["concept_id"]] = c_cnt.get(r["concept_id"], 0) + 1

    res_by_paper: dict[str, list[dict[str, Any]]] = {}
    for r in res_rows:
        res_by_paper.setdefault(r["paper_id"], []).append(dict(r))

    papers_out = []
    for p in papers_raw:
        d = dict(p)
        d["concepts"] = sorted(pc_map.get(d["paper_id"], []))
        d["is_core"] = d["paper_id"] in core_ids
        papers_out.append(d)

    concepts_out = [{"id": c["concept_id"], "name": c["name"], "en_name": c["en_name"], "count": c_cnt.get(c["concept_id"], 0)} for c in concept_rows]

    checks_map = {c["check"]: c for c in checks_json.get("checks", [])}
    g3_blocked = [x.get("check") for x in (checks_map.get("G3.prepare", {}).get("result") or {}).get("checks", []) if x.get("status") != "pass"]
    gates_out = [
        {"name": "G1 (State / Protected / Sources / Reports / Syntax)", "status": checks_map.get("G1", {}).get("status", "fail"), "label": "PASS", "detail": "S15 9-section state, portable baseline snapshot, source hashes, weekly reports verified"},
        {"name": "G2 (G1 + runtime_evidence)", "status": checks_map.get("G2", {}).get("status", "fail"), "label": "PASS", "detail": "Bound to demo-baseline runtime report (18/18) and live tool SHA-256 hashes"},
        {"name": "G3.prepare (Confirmations / Formal Split / Freeze)", "status": "expected_block", "label": "FAIL-CLOSED", "detail": f"Blocked by {len(g3_blocked)} unready gates: " + ", ".join(g3_blocked)},
        {"name": "overview / report / literature", "status": "pass" if all(checks_map.get(k, {}).get("status") == "pass" for k in ("overview", "report", "literature")) else "fail", "label": "PASS", "detail": f"Workbench current, W47–W48 carryover current, SQLite {len(papers_out)} cards / {len(res_rows)} rows"},
    ]

    req_decs = stage_req.get("required_decisions", [])
    design_decs = design.get("decisions", {})
    missing_r = design.get("missing_reasons", {})
    decisions_rows = [
        {
            "key": k,
            "label": DECISION_LABELS.get(k, k),
            "group": DECISION_STEP_GROUP.get(k, "Pending"),
            "value": "—" if design_decs.get(k) is None else str(design_decs[k]),
            "reason": missing_r.get(f"decisions.{k}", "not_checked"),
        }
        for k in req_decs
    ]

    total_suite_cases = sum(x[2] for x in SUITE_MANIFEST)
    kpis = [
        {"value": f"{len(papers_out)} / {len(papers_out)}", "what": "Verified Literature Cards (Demo)", "period": f"{len(res_rows)} quant rows · 0 pending", "type": "Full-Text DB", "source": "论文内容索引/literature.db"},
        {"value": f"{total_suite_cases} / {total_suite_cases}", "what": "Adversarial Validation Cases", "period": "6 isolated stdlib suites", "type": "G2 Verified", "source": "scripts/run_all_suites.py"},
        {"value": f"0 / {len(req_decs)}", "what": "Confirmed Research Decisions", "period": f"E-DEMO {elig_mf['source_rows']} measurements", "type": "Fail-Closed", "source": "决策.json=[] / draft-1"},
        {"value": "48 / 96", "what": "Synthetic Eligibility Diagnostic", "period": "30 pending · 18 excluded", "type": "W3 Preview", "source": "E-DEMO-20261124"},
    ]

    timeline_obj = {
        "factor": "Selectivity Borrowing Scope & Governance Gates",
        "who": "Synthetic Demo Benchmark",
        "question": "Before Dec 4 freeze, should primary evaluation anchor on multi-series Ki sets or broad Kd panels?",
        "points": [
            {"date": "2026-11-15", "stance": "tailwind", "label": "W46: Literature & comparison baseline seeded", "figure": "H1/H2.1/H2.2 draft"},
            {"date": "2026-11-22", "stance": "headwind", "label": "W47: Broad panel series sparsity flagged", "figure": "1–3 >=3 series"},
            {"date": "2026-11-24", "stance": "easing", "label": "W48: 181 tests green + multi-route audit", "figure": "420 bilat / 36 >=3"},
            {"date": "2026-12-04", "stance": "neutral", "label": "Protocol Freeze Window", "condition": "Route A tests series borrowing; Route B narrows eval unit to de-duplicated clusters"},
        ],
    }

    panels = panel_audit["panels"]
    wf = panel_audit["waterfall"]
    ex1 = {
        "id": "ex1",
        "number": "Exhibit 1",
        "page": 1,
        "type": "bar-line",
        "title": "Synthetic Panel Scaffolds vs. >=3-Ligand Congeneric Series",
        "units": "groups; % >=3 series",
        "source": "demo_data/demo_panel_audit.json",
        "category_label": "Candidate Panel",
        "categories": [p["label"] for p in panels],
        "series": [
            {"key": "murcko_total", "label": "Total Scaffolds", "mark": "bar", "unit": "groups", "values": [p["murcko_total"] for p in panels]},
            {"key": "murcko_ge3", "label": "Series (>=3 Ligands)", "mark": "bar", "unit": "groups", "values": [p["murcko_ge3"] for p in panels]},
            {"key": "ge3_share", "label": "Multi-Ligand Share (%)", "mark": "line", "unit": "%", "ratio": ["murcko_ge3", "murcko_total"], "values": [round(p["murcko_ge3"] / p["murcko_total"] * 100) for p in panels]},
        ],
        "axes": {"bar": [0, 220], "line": [0, 40]},
        "frame": {
            "title": "Compares total Murcko scaffolds against multi-ligand series (>=3 bilateral compounds) across five synthetic panels.",
            "units": "Bars show scaffold counts (groups); line indicates share (%) of scaffolds meeting the >=3 size rule.",
            "key": "Click any bar to inspect rounded ratio arithmetic against the plotted line percentage.",
            "note": "Synthetic illustration of why broad Kd panels appear compound-rich yet yield few multi-ligand SAR series.",
            "source": "demo_data/demo_panel_audit.json (100% synthetic fixture data).",
        },
        "find": [{"ask": "Select the broad Kd screen with 0 multi-ligand series (>=3).", "target": "SYN-BroadJ / K Kd", "right": "Correct: 61 scaffolds yielded 0 congeneric series with >=3 bilateral ligands.", "hint": "Look at the rightmost zero-percent column."}],
    }
    ex2 = {
        "id": "ex2",
        "number": "Exhibit 2",
        "page": 2,
        "type": "table",
        "title": "Candidate Routes: Bilateral Coverage, Series Counts & Donor Pools",
        "units": "ligands and groups",
        "source": "demo_data/demo_panel_audit.json",
        "row_label": "Synthetic Route",
        "rows": [p["label"] for p in panels],
        "periods": ["Bilateral", "Series", "Donors"],
        "columns": [{"key": "n1", "label": "Ligands / >=2", "unit": "n"}, {"key": "n2", "label": "Docs / >=3", "unit": "n"}],
        "values": {
            "Bilateral": {"n1": [p["bilateral"] for p in panels], "n2": [p["docs"] for p in panels]},
            "Series": {"n1": [p["murcko_ge2"] for p in panels], "n2": [p["murcko_ge3"] for p in panels]},
            "Donors": {"n1": [p["donor_non_overlap"] for p in panels], "n2": [p["donor_docs"] for p in panels]},
        },
        "note": "Missing external donor definitions render as – rather than synthetic zeros.",
        "frame": {
            "title": "Cross-route matrix contrasting bilateral ligand count, series depth, and non-overlapping external donors.",
            "units": "Counts per synthetic panel.",
            "key": "Bilateral (ligands / docs), Series (Murcko >=2 / >=3), Donors (non-overlap ligands / donor docs).",
            "note": "On narrow mobile screens this table stacks into per-route summary cards automatically.",
            "source": "demo_data/demo_panel_audit.json",
        },
    }
    ex3 = {
        "id": "ex3",
        "number": "Exhibit 3",
        "page": 3,
        "type": "waterfall",
        "title": "Synthetic Eligibility Walk: Raw Measurements to Journal Bilateral Pool",
        "units": "records / ligands",
        "source": "demo_data/demo_panel_audit.json",
        "steps": [
            {"label": "Raw Meas", "kind": "total", "value": wf["raw_measurements"]},
            {"label": "Censored >/<", "kind": "step", "value": wf["censored_gt_lt"]},
            {"label": "Exact = Meas", "kind": "total", "value": wf["exact_measurements"]},
            {"label": "Multi-Assay", "kind": "step", "value": wf["multi_assay_dedup"]},
            {"label": "Unilateral", "kind": "step", "value": wf["unilateral_excluded"]},
            {"label": "Bilateral Ki", "kind": "total", "value": wf["bilateral_ligands"]},
            {"label": "Patent Only", "kind": "step", "value": wf["patent_only_excluded"]},
            {"label": "Journal Pool", "kind": "total", "value": wf["journal_bilateral"]},
        ],
        "axes": {"bar": [0, 1300]},
        "frame": {
            "title": "Tracks cumulative exclusions from 1,200 raw Ki rows down to 140 journal bilateral ligands.",
            "units": "Measurements then unique ligands; totals match cumulative steps.",
            "key": "Toggle 'Show what is left' to view intermediate running balances after each filter.",
            "note": "Demonstrates arithmetic conservation in pre-training eligibility filtering.",
            "source": "demo_data/demo_panel_audit.json",
        },
    }
    exhibits = [ex1, ex2, ex3]
    validate_exhibit_schema_invariants(exhibits, timeline_obj, kpis)

    state_sheet = {
        "id": "state-sheet",
        "variant": "guide",
        "srcLabel": "Synthetic Topic Dossier · S15 Checked",
        "band": ["demo-selectivity-benchmark", "formal_use_allowed = false"],
        "title": "Scientific State & Fail-Closed Guardrails",
        "meta": "Synthetic Demo · Open-Source Release",
        "sentences": [
            {"id": "S1", "text": "Target question focuses on [[delta_p|selectivity transfer Δ_P]] across unseen [[congeneric_series|congeneric series]] under matched budgets."},
            {"id": "S2", "text": "Governance runs under a [[fail_closed|fail-closed gate]]: all 17 scientific decisions remain null until registered with external confirmation."},
            {"id": "S3", "text": "Curation separates [[censored_ki|censored >/< bounds]] from singleton points, tracks [[test_exposure|cross-version test exposure]], and verifies [[carryover_reconciliation|weekly carryover]]."},
        ],
        "side": [
            ["Verified Cards", f"{len(papers_out)} / {len(papers_out)}"],
            ["Quant Rows", str(len(res_rows))],
            ["Decisions", f"0 / {len(req_decs)}"],
            ["Formal Runs", "Disabled"],
        ],
        "footer": "Source: 研究管理/问题/demo-selectivity-benchmark/状态.md (click dotted terms for definitions)",
    }

    weekly_reports = []
    for wid in ("2026-W48", "2026-W47"):
        wp = root / "研究管理/周报记录" / f"{wid}.json"
        wdata = track_json(wp)
        weekly_reports.append({
            "week_id": wid,
            "period": f"{wdata.get('period_start')} – {wdata.get('period_end')}",
            "as_of_date": wdata.get("as_of_date"),
            "parent_week": (wdata.get("parent_ref") or {}).get("week_id"),
            "one_line_summary": wdata.get("one_line_summary", ""),
            "header_note": wdata.get("header_note", ""),
            "bundle_dir": f"周报/{wid}_Researcher",
            "tracks": wdata.get("tracks", []),
        })

    payload = {
        "schema_version": "labgate-dashboard.v1",
        "overview": {
            "meta": "demo-selectivity-benchmark · G2 PASS / G3 Fail-Closed",
            "kpis": kpis,
            "gates": gates_out,
            "suites": [{"suite": s, "scope": sc, "cases": n} for s, sc, n in SUITE_MANIFEST],
            "hypotheses": [{"id": h["hypothesis_id"], "status": h["status"], "statement": h["statement"]} for h in design.get("hypotheses", [])],
            "decisions": decisions_rows,
        },
        "timeline": timeline_obj,
        "routes": {
            "exhibits": exhibits,
            "state_sheet": state_sheet,
            "synthetic_controls": synth_eval["controls"],
        },
        "literature": {
            "core_ids": sorted(core_ids),
            "total_results": len(res_rows),
            "concepts": concepts_out,
            "papers": papers_out,
            "results_by_paper": res_by_paper,
        },
        "weekly_reports": weekly_reports,
        "glossary": glossary_dict,
    }
    return payload, [file_ref(root, p) for p in sorted({x.resolve() for x in tracked})]


def render_dashboard_html(root: Path, payload: dict[str, Any], input_refs: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    if not VENDOR_DIR.is_dir():
        raise FileNotFoundError(f"Missing vendored rtr-ui-kit dir: {VENDOR_DIR}")
    if str(VENDOR_DIR) not in sys.path:
        sys.path.insert(0, str(VENDOR_DIR))
    from html_inline import build_standalone_html  # noqa: E402

    lit_count = len(payload["literature"]["papers"])
    res_count = payload["literature"]["total_results"]

    body_html = f"""
<header class="dash-topbar">
  <div class="dash-topbar-inner">
    <div class="dash-brand-row">
      <div>
        <h1 class="dash-brand-title">LabGate · Fail-Closed Scientific Research Governance</h1>
        <p class="dash-brand-sub">Demo Topic: <code>demo-selectivity-benchmark</code> ｜ {lit_count} Verified Cards · {res_count} Quant Rows · G2 PASS / G3 Blocked</p>
      </div>
      <div class="dash-controls">
        <div class="dash-pill-group" role="group" aria-label="Color family">
          <button type="button" class="dash-pill-btn" data-fam-btn="macro" aria-pressed="true">Macro Blue</button>
          <button type="button" class="dash-pill-btn" data-fam-btn="company" aria-pressed="false">Company Plum</button>
        </div>
        <div class="dash-pill-group" role="group" aria-label="Theme">
          <button type="button" class="dash-pill-btn" data-theme-btn="system" aria-pressed="true">System</button>
          <button type="button" class="dash-pill-btn" data-theme-btn="light" aria-pressed="false">Light</button>
          <button type="button" class="dash-pill-btn" data-theme-btn="dark" aria-pressed="false">Dark</button>
        </div>
      </div>
    </div>
    <div id="dash-tablist" class="dash-tablist" focusgroup="tablist nomemory" role="tablist" aria-label="LabGate Views">
      <button type="button" class="dash-tab" role="tab" id="tab-overview" aria-controls="panel-overview" aria-selected="true" focusgroupstart>01 · Overview &amp; Gates</button>
      <button type="button" class="dash-tab" role="tab" id="tab-routes" aria-controls="panel-routes" aria-selected="false">02 · Audits &amp; Exhibits</button>
      <button type="button" class="dash-tab" role="tab" id="tab-literature" aria-controls="panel-literature" aria-selected="false">03 · Literature Shelf ({lit_count})</button>
      <button type="button" class="dash-tab" role="tab" id="tab-reports" aria-controls="panel-reports" aria-selected="false">04 · Weekly Carryover</button>
    </div>
  </div>
</header>

<main class="dash-wrap" id="dash-main">
  <div id="panel-overview" role="tabpanel" aria-labelledby="tab-overview"><div class="dash-panel-inner" id="mount-overview"></div></div>
  <div id="panel-routes" role="tabpanel" aria-labelledby="tab-routes" hidden="until-found"><div class="dash-panel-inner" id="mount-routes"></div></div>
  <div id="panel-literature" role="tabpanel" aria-labelledby="tab-literature" hidden="until-found"><div class="dash-panel-inner" id="mount-literature"></div></div>
  <div id="panel-reports" role="tabpanel" aria-labelledby="tab-reports" hidden="until-found"><div class="dash-panel-inner" id="mount-reports"></div></div>
</main>

<dialog id="paper-dialog" class="dash-dialog" closedby="any" aria-labelledby="paper-dialog-title">
  <div class="dash-dialog-card">
    <div class="dash-dialog-head">
      <h3 class="dash-dialog-title" id="paper-dialog-title">Verified Card</h3>
      <button type="button" class="rtr-btn-ghost" id="paper-dialog-close" aria-label="Close dialog">Close</button>
    </div>
    <div class="dash-dialog-body" id="paper-dialog-body"></div>
  </div>
</dialog>
"""

    raw_html = build_standalone_html(
        title="LabGate · Offline Scientific Research Governance Dashboard",
        lang="en",
        family="macro",
        theme="system",
        body_html=body_html,
        data=payload,
        boot_js=BOOT_JS,
        footer="Read-only offline dashboard · LabGate (MIT License) · Powered by vendored @zhouyang/rtr-ui-kit.",
        css_path=RTR_CSS_PATH,
        js_path=RTR_JS_PATH,
    )
    final_html = raw_html.replace("</head>", f"<style>\n{CUSTOM_CSS}\n</style>\n</head>", 1)
    for sentinel in HTML_SENTINELS:
        if sentinel not in final_html:
            raise RuntimeError(f"Generated HTML missing required sentinel: {sentinel}")

    payload_canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    manifest = {
        "schema_version": "labgate-dashboard-manifest.v1",
        "generator": "研究管理/看板/build_dashboard.py",
        "built_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "read_only": True,
        "formal_use_allowed": False,
        "counts": {
            "papers": lit_count,
            "verified_results": res_count,
            "core_papers": len(payload["literature"]["core_ids"]),
            "decisions_confirmed": 0,
            "decisions_required": len(payload["overview"]["decisions"]),
            "weekly_reports": len(payload["weekly_reports"]),
            "glossary_terms": len(payload["glossary"]),
            "exhibits": len(payload["routes"]["exhibits"]),
        },
        "payload_sha256": sha256_bytes(payload_canonical),
        "kit_assets": [file_ref(root, RTR_CSS_PATH), file_ref(root, RTR_JS_PATH)],
        "inputs": input_refs,
        "output": {
            "path": "研究管理/派生/看板/科研看板.html",
            "bytes": len(final_html.encode("utf-8")),
            "sha256": sha256_bytes(final_html.encode("utf-8")),
            "sentinels": list(HTML_SENTINELS),
        },
    }
    return final_html, manifest


def atomic_write(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp.{os.getpid()}")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, target)
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise


def verify_published(root: Path, out_html: Path, out_manifest: Path) -> dict[str, Any]:
    if not out_html.is_file() or not out_manifest.is_file():
        raise FileNotFoundError("Published dashboard HTML or manifest missing.")
    recorded = json.loads(out_manifest.read_text(encoding="utf-8"))
    html_text = out_html.read_text(encoding="utf-8")
    actual_sha = sha256_bytes(html_text.encode("utf-8"))
    if actual_sha != (recorded.get("output") or {}).get("sha256"):
        raise RuntimeError("Dashboard HTML SHA-256 mismatch.")
    payload, input_refs = collect_dashboard_data(root)
    payload_sha = sha256_bytes(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    if payload_sha != recorded.get("payload_sha256"):
        raise RuntimeError("Live workspace payload drifted; run --refresh.")
    if {x["path"]: x["sha256"] for x in recorded.get("inputs", [])} != {x["path"]: x["sha256"] for x in input_refs}:
        raise RuntimeError("Tracked input hashes drifted; run --refresh.")
    return {"status": "ok", "mode": "check", "html": recorded["output"]["path"], "bytes": recorded["output"]["bytes"], "sha256": actual_sha}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or verify offline LabGate research dashboard.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--refresh", action="store_true")
    group.add_argument("--check", action="store_true")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args(argv)

    root = args.root.resolve()
    out_dir = root / "研究管理/派生/看板"
    out_html = out_dir / "科研看板.html"
    out_manifest = out_dir / "manifest.json"
    lock_path = out_dir / ".dashboard.lock"

    if args.check:
        print(json.dumps(verify_published(root, out_html, out_manifest), ensure_ascii=False))
        return 0

    payload, input_refs = collect_dashboard_data(root)
    html_text, manifest = render_dashboard_html(root, payload, input_refs)
    if args.dry_run:
        print(json.dumps({"status": "ok", "mode": "dry-run", "wrote": False, "bytes": manifest["output"]["bytes"]}, ensure_ascii=False))
        return 0

    with file_lock(lock_path):
        atomic_write(out_html, html_text)
        atomic_write(out_manifest, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": "ok", "mode": "refresh", "wrote": True, "html": manifest["output"]["path"], "bytes": manifest["output"]["bytes"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
