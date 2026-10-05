"""The interface (T035-T038).

    streamlit run ledgermind/app/streamlit_app.py

Presentation only. Every figure shown is a value an analysis returned, passed through
untouched; the arithmetic lives in `view.py` as pure functions so a test can prove that
(FR-030). This is the only module permitted to import pandas.

What it exists to make visible rather than merely claimed: the analyses running one by one,
the offer coming from the tools rather than the model, and for every figure in the memo, the
analysis that produced it and the full-precision value behind what was written.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# `streamlit run path/to/app.py` puts the SCRIPT's directory on sys.path, not the working
# directory the way `python -m` does -- so `ledgermind` is not importable and every import
# below fails with ModuleNotFoundError. Adding the repository root fixes it for any launch
# method, which is the point: a reader following the README should not have to know this.
#
# The alternative is making the package installable, which plan.md declined on the grounds
# that LedgerMind is run from the repository root and never distributed. That reasoning
# still holds; this is the three lines it costs.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pandas as pd
import streamlit as st

from ledgermind.agent import loop
from ledgermind.app import view
from ledgermind.data import load
from ledgermind.llm.base import ProviderError
from ledgermind.llm.ollama import OllamaProvider

EVAL_REPORT = Path("eval_report.json")

st.set_page_config(page_title="LedgerMind", page_icon="\U0001f4d2", layout="wide")


def _merchant_options(root: Path) -> list[str]:
    """Discovered from the filesystem, not from the generator's manifest.

    The interface must not depend on generation code (Article III), and discovering them
    this way also means it works against real merchant records rather than only generated
    ones.
    """
    return load.available_merchants(root)


def _table(rows, columns):
    return pd.DataFrame([{c: r[c] for c in columns} for r in rows])


# --------------------------------------------------------------------------------------
# Sidebar
# --------------------------------------------------------------------------------------

st.sidebar.title("LedgerMind")
st.sidebar.caption("A grounded underwriter for revenue-based financing.")

root = Path(st.sidebar.text_input("Merchant directory", value=str(load.DEFAULT_ROOT)))
available = _merchant_options(root)

if not available:
    st.sidebar.error("No merchants generated.")
    st.sidebar.code("python -m ledgermind.data.gen")
    st.stop()

merchant_id = st.sidebar.selectbox("Merchant", available)
model = st.sidebar.text_input("Ollama model", value="qwen2.5")
run_clicked = st.sidebar.button("Underwrite", type="primary", width="stretch")

st.sidebar.divider()
st.sidebar.caption(
    "Every figure in the memo is checked against the analysis results before it is "
    "displayed. A memo containing a figure no analysis produced is rejected and rewritten."
)

# --------------------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------------------

if run_clicked:
    merchant = load.load_merchant(root / merchant_id)
    st.subheader(merchant_id)
    st.caption(
        f"{len(merchant['transactions'])} bank transactions · "
        f"{len(merchant['sales'])} sales records"
    )

    # T036: the analyses appear as they run. The loop emits events synchronously, so
    # writing into the status container from the observer streams them.
    with st.status("Running analyses…", expanded=True) as status:
        def observe(event: str, payload: dict) -> None:
            if event == "registered":
                status.write(
                    f"**{payload['tool']}** — {len(payload['keys'])} facts recorded"
                )
            elif event == "tool_error":
                status.write(f":orange[{payload['tool']} could not run] — {payload['error']}")
            elif event == "verification":
                if payload["passed"]:
                    status.write(f":green[memo verified on attempt {payload['attempt']}]")
                else:
                    status.write(f":red[memo rejected] — {payload['reason']}")
            elif event == "retry":
                status.write(f"asking the model again (attempt {payload['attempt']})")
            elif event == "fallback":
                status.write(":orange[falling back to the templated memo]")

        try:
            decision = loop.run(merchant, OllamaProvider(model=model), observer=observe)
            status.update(label="Done", state="complete", expanded=False)
        except ProviderError as exc:
            status.update(label="Model unreachable", state="error")
            st.error(f"{exc}\n\nIs Ollama running?")
            st.stop()
        except loop.LoopError as exc:
            status.update(label="No decision reached", state="error")
            st.error(str(exc))
            st.stop()

    st.session_state["decision"] = decision

decision = st.session_state.get("decision")

if decision is None:
    st.info("Pick a merchant and press **Underwrite**.")
else:
    # T038: the fallback must never be substituted silently.
    if decision.used_fallback:
        st.error(
            "**Narrative verification failed.** The model could not produce a memo whose "
            "every figure traced to an analysis, so the text below is generated from the "
            "recorded values alone. No model-written explanation accompanies it."
        )
        if view.unresolved_rows(decision):
            st.caption(
                "Figures the model wrote that no analysis produced: "
                + ", ".join(view.unresolved_rows(decision))
            )

    left, right = st.columns([3, 2], gap="large")

    with left:
        st.markdown("#### Memo")
        st.caption(
            ("Templated from recorded values" if decision.used_fallback else "Written by "
             f"{decision.provider_name}")
            + f" · verified on attempt {decision.verification.attempt}"
        )
        st.write(decision.memo)

        # T037: every figure in the memo, the analysis behind it, and the recorded value.
        st.markdown("#### Where each figure came from")
        rows = view.provenance_rows(decision)
        if rows:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "in the memo": r["wrote"],
                            "produced by": "\n".join(r["analyses"]),
                            "recorded value": "\n".join(
                                view.format_value(v) for v in r["recorded"]
                            ),
                        }
                        for r in rows
                    ]
                ),
                width="stretch",
                hide_index=True,
            )
            st.caption(
                "Where a figure lists more than one analysis, several recorded values match "
                "it. Resolution checks that a figure came from an analysis, not which one, "
                "so every match is shown rather than one being guessed at."
            )
        else:
            st.caption("This memo quotes no figures.")

    with right:
        st.markdown("#### Offer")
        st.caption("Computed by `compute_offer`. Rendered directly; nothing here recalculates.")
        for row in view.offer_rows(decision):
            st.markdown(f"**{row['label']}**  \n{view.format_value(row['value'])}")

        st.markdown("#### Analyses")
        for section in view.analysis_sections(decision):
            with st.expander(section["title"], expanded=section["title"] == "Risk"):
                st.dataframe(
                    pd.DataFrame(
                        [
                            {"": r["label"], "value": view.format_value(r["value"])}
                            for r in section["rows"]
                        ]
                    ),
                    width="stretch",
                    hide_index=True,
                )

    with st.expander(f"The full fact ledger — {len(decision.facts)} recorded values"):
        st.caption(
            "Everything the analyses produced during this run. The memo may quote any of "
            "these and nothing else."
        )
        st.dataframe(
            pd.DataFrame(
                [{"fact": k, "value": view.format_value(v)} for k, v in decision.facts.items()]
            ),
            width="stretch",
            hide_index=True,
            height=320,
        )

# --------------------------------------------------------------------------------------
# T038: the evaluation report
# --------------------------------------------------------------------------------------

st.divider()
st.markdown("### Evaluation")

if not EVAL_REPORT.exists():
    st.caption("No report yet.")
    st.code("python -m ledgermind.eval.harness --json eval_report.json")
else:
    report = json.loads(EVAL_REPORT.read_text(encoding="utf-8"))
    st.caption(
        f"{report['merchant_count']} merchants · {report['provider']} · "
        f"{report['elapsed_seconds']}s"
    )

    columns = st.columns(5)
    columns[0].metric(
        "Grounding", f"{report['grounding_faithfulness']:.1%}",
        f"{report['grounding_resolved']}/{report['grounding_total']} numerals",
    )
    columns[1].metric("Monotonicity", "pass" if report["monotonicity_holds"] else "FAIL")
    columns[2].metric("Policy breaches", len(report["policy_breaches"]))
    columns[3].metric("Vs recorded answers", f"{len(report['truth_mismatches'])} mismatches")
    columns[4].metric("Consistency", "pass" if report["consistency_holds"] else "FAIL")

    if report["monotonicity_pairs"]:
        st.caption("More volatility must never buy a larger advance (SC-001)")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "steadier": p["steady"],
                        "its advance": view.format_value(p["steady_advance"]),
                        "more erratic": p["erratic"],
                        "its advance": view.format_value(p["erratic_advance"]),
                        "holds": "yes" if p["holds"] else "NO",
                    }
                    for p in report["monotonicity_pairs"]
                ]
            ),
            width="stretch",
            hide_index=True,
        )

    st.caption(
        "Grounding is 100% by construction — a memo that failed was never displayed — "
        "so it checks the guardrail rather than the model. Monotonicity is the measure that "
        "can fail while the rest pass, because it tests whether the lending rules are sane."
    )

    if report["declines"]:
        with st.expander(f"Declined — {len(report['declines'])} merchants"):
            st.dataframe(
                pd.DataFrame(report["declines"]), width="stretch", hide_index=True
            )

    # A statement, not a conditional expression. Written as an expression, Streamlit's
    # magic treats the bare value as something to display and renders the DeltaGenerator
    # the call returned -- its repr, followed by a table of all its members.
    if report["passed"]:
        st.success("ALL CRITERIA PASS")
    else:
        st.error("Criteria failed")
