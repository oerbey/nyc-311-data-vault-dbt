"""Run from anywhere: python -m streamlit run /path/to/dashboard/app.py."""

import sys
from pathlib import Path

# Streamlit executes a file, so make the repository package importable from any cwd.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import altair as alt
import pandas as pd
import streamlit as st

from dashboard.data import (
    BUILD_COMMAND,
    QUERIES,
    DashboardUnavailable,
    agency_summary,
    context_filter,
    database_path,
    duration_summary,
    exact_label,
    export_json,
    namespaces,
    options,
    read_data,
    select_values,
    status_label,
)

st.set_page_config(
    page_title="NYC 311 service analytics", page_icon="📊", layout="wide"
)


EVENT_NAMES = {"created_request_count": "Created", "closed_request_count": "Closed"}


def metric_columns(items):
    # Two columns allow labels and values to fit more comfortably at narrow widths.
    for start in range(0, len(items), 2):
        batch = items[start : start + 2]
        for column, (label, value) in zip(st.columns(len(batch)), batch, strict=True):
            column.metric(label, value)


def pick(label, frame, column, key, format_func=exact_label):
    values = options(frame, column)
    selected = st.multiselect(
        label, values, default=values, format_func=format_func, key=key
    )
    with st.expander(f"Full selected values: {label}"):
        if selected:
            st.code(
                "\n".join(format_func(value) for value in selected),
                language=None,
                wrap_lines=True,
            )
        else:
            st.caption("No values selected.")
    return selected


def chart_data(title, frame, key):
    """Provide a paged, text table for the same values used by each chart."""
    with st.expander(f"Chart data: {title}"):
        total = len(frame)
        pages = max(1, (total + 24) // 25)
        page = st.number_input(
            "Page",
            min_value=1,
            max_value=pages,
            value=1,
            step=1,
            key=key + "_page",
            help="25 chart rows per page",
        )
        if page is None:
            st.info("Choose a page to view chart data.")
            return
        st.caption(f"{total:,} rows; page {page} of {pages}.")
        st.table(
            frame.iloc[(page - 1) * 25 : page * 25].reset_index(drop=True),
            hide_index=True,
        )


def evidence(frame, namespace, query_id, filters, definitions, derived=None):
    with st.expander("Data and definitions"):
        st.caption(definitions)
        st.caption(
            f"Source: {query_id} reporting projection · {namespace[0]} / {namespace[1]}"
        )
        st.dataframe(frame, hide_index=True, width="stretch")
        st.download_button(
            "Download filtered data (JSON)",
            export_json(frame, namespace, query_id, filters, definitions, derived),
            file_name=f"nyc311-{query_id}.json",
            mime="application/json",
        )
        st.code(
            QUERIES[query_id],
            language="sql",
        )
        if derived is not None:
            st.caption("Pooled statistics use the filtered request population:")
            st.code(QUERIES["population"], language="sql")
        st.caption(
            "SQL parameters: "
            + repr(namespace)
            + ". Filters shown above are applied to these source rows."
        )


def event_trends(data, ns, scope):
    st.header("Event-date trends")
    st.caption(
        "NYC creation and closure dates · labels observed with the latest lifecycle capture"
    )
    frame, population = data["trends"], data["population"]
    selections = {}
    for label, column in zip(
        ["Agency context", "Problem context", "Borough context"],
        ["observed_agency_bk", "observed_complaint_type", "observed_borough"],
        strict=True,
    ):
        selections[column] = pick(label, population, column, scope + column)
    frame = context_filter(frame, selections)
    population = context_filter(population, selections)
    if frame.empty:
        st.info("No usable event dates match the selected context.")
        st.caption(
            f"Retained requests in this context: {len(population):,}. Missing dates are not zero events."
        )
        return
    dates = pd.to_datetime(frame.event_date_nyc).dt.date
    period = st.date_input(
        "Event dates (NYC, inclusive)",
        (min(dates), max(dates)),
        min_value=min(dates),
        max_value=max(dates),
        key=scope + "dates",
    )
    if len(period) != 2:
        st.info("Choose both ends of the event-date range.")
        return
    frame = frame[(dates >= period[0]) & (dates <= period[1])].copy()
    metric_columns(
        [
            ("Usable creation events", int(frame.created_request_count.sum())),
            ("Usable closure events", int(frame.closed_request_count.sum())),
        ]
    )
    st.caption(
        "A request may contribute to both counts on different dates. These are retained sample events, not backlog."
    )
    if frame.empty:
        st.info(
            "No event observations in this range; uncovered dates have not been filled with zeroes."
        )
    else:
        daily = frame.groupby("event_date_nyc", as_index=False)[
            ["created_request_count", "closed_request_count"]
        ].sum()
        chart = (
            alt.Chart(daily.rename(columns=EVENT_NAMES))
            .transform_fold(
                ["Created", "Closed"],
                as_=["event", "requests"],
            )
            .mark_bar()
            .encode(
                x=alt.X("event_date_nyc:T", title="NYC event date"),
                y=alt.Y(
                    "requests:Q", title="Usable events", scale=alt.Scale(zero=True)
                ),
                color=alt.Color(
                    "event:N",
                    title="Event",
                    scale=alt.Scale(range=["#2563eb", "#d97706"]),
                ),
                xOffset="event:N",
                tooltip=["event_date_nyc:T", "event:N", "requests:Q"],
            )
            .properties(height=300)
        )
        st.altair_chart(chart, width="stretch")
        chart_data(
            "Event dates",
            daily.rename(columns={"event_date_nyc": "NYC event date", **EVENT_NAMES}),
            scope + "daily",
        )
        by = st.selectbox(
            "Compare event volumes by",
            ["Agency", "Problem", "Borough"],
            key=scope + "compare",
        )
        dimension = {
            "Agency": "observed_agency_bk",
            "Problem": "observed_complaint_type",
            "Borough": "observed_borough",
        }[by]
        comparison = (
            frame.assign(context=frame[dimension].map(exact_label))
            .groupby("context", as_index=False)[
                ["created_request_count", "closed_request_count"]
            ]
            .sum()
        )
        st.altair_chart(
            alt.Chart(comparison.rename(columns=EVENT_NAMES))
            .transform_fold(
                ["Created", "Closed"],
                as_=["event", "requests"],
            )
            .mark_bar()
            .encode(
                y=alt.Y("context:N", title=by),
                x=alt.X("requests:Q", title="Usable events"),
                color=alt.Color(
                    "event:N", scale=alt.Scale(range=["#2563eb", "#d97706"])
                ),
                yOffset="event:N",
                tooltip=["context:N", "event:N", "requests:Q"],
            )
            .properties(height=max(160, len(comparison) * 35)),
            width="stretch",
        )
        chart_data(
            "Event comparison",
            comparison.rename(columns={"context": by, **EVENT_NAMES}),
            scope + "comparison",
        )
    st.caption(
        f"Selected context, all event dates: {len(population):,} retained requests; "
        f"{int((1 - population.created_event_eligible_count).sum()):,} without usable creation dates; "
        f"{int((1 - population.closed_event_eligible_count).sum()):,} without usable closure dates. "
        "Unusable dates cannot be assigned to the selected date range. Missing calendar days are not imputed."
    )
    evidence(
        frame,
        ns,
        "trends",
        {**selections, "event_date_range_nyc": period},
        "Separate usable created/closed endpoints. Context is lifecycle_capture, not event-time or current assignment.",
    )


def durations(data, ns, scope):
    st.header("Agency and cohort durations")
    st.caption(
        "Latest reported creation-to-closure duration · agency observed with the lifecycle capture"
    )
    population = data["population"]
    agencies = pick(
        "Agency context", population, "observed_agency_bk", scope + "agency"
    )
    cohorts = pick(
        "NYC creation month",
        population,
        "created_cohort_month_nyc",
        scope + "cohort",
        lambda v: "Unknown month" if v is None else str(pd.Timestamp(v).date()),
    )
    selections = {"observed_agency_bk": agencies, "created_cohort_month_nyc": cohorts}
    population = context_filter(population, selections)
    detail = context_filter(data["agency"], selections)
    summary = duration_summary(population)
    fmt = lambda v: "—" if v is None else f"{v:,.2f} h"
    metric_columns(
        [
            ("Retained requests", summary["retained_requests"]),
            ("Eligible durations", summary["eligible_durations"]),
            ("Excluded durations", summary["excluded_durations"]),
            ("Median eligible duration", fmt(summary["median_hours"])),
        ]
    )
    st.caption(
        "Duration = usable closure UTC − creation UTC; zero is eligible. Missing, invalid, DST-uncertain and negative pairs are excluded from durations but retained in counts. "
        "This is not response time, SLA attainment, or a causal agency ranking."
    )
    if population.empty:
        st.info("No retained requests match the selected agency/cohort filters.")
    else:
        pooled = agency_summary(population)
        eligible = pooled[pooled.eligible_durations > 0]
        if eligible.empty:
            st.info(
                "No eligible durations. Mean, median and p90 are unavailable, not zero."
            )
        else:
            st.altair_chart(
                alt.Chart(
                    eligible.rename(
                        columns={
                            "mean_hours": "Mean",
                            "median_hours": "Median",
                            "p90_hours": "P90",
                        }
                    )
                )
                .transform_fold(
                    ["Mean", "Median", "P90"],
                    as_=["measure", "hours"],
                )
                .mark_point(filled=True, size=90)
                .encode(
                    y=alt.Y("agency:N", title="Agency"),
                    x=alt.X(
                        "hours:Q",
                        title="Eligible elapsed hours",
                        scale=alt.Scale(zero=True),
                    ),
                    color=alt.Color("measure:N", title="Measure"),
                    shape="measure:N",
                    tooltip=[
                        "agency:N",
                        "measure:N",
                        alt.Tooltip("hours:Q", format=".2f"),
                        "eligible_durations:Q",
                    ],
                )
                .properties(height=max(180, len(eligible) * 50)),
                width="stretch",
            )
            chart_data(
                "Eligible duration by agency",
                eligible[
                    [
                        "agency",
                        "eligible_durations",
                        "mean_hours",
                        "median_hours",
                        "p90_hours",
                    ]
                ].rename(
                    columns={
                        "agency": "Agency",
                        "eligible_durations": "Eligible durations",
                        "mean_hours": "Mean hours",
                        "median_hours": "Median hours",
                        "p90_hours": "P90 hours",
                    }
                ),
                scope + "duration_chart",
            )
        st.subheader("Coverage and pooled statistics")
        display_columns = {
            "agency": "Agency",
            "retained_requests": "Retained requests",
            "eligible_durations": "Eligible durations",
            "excluded_durations": "Excluded durations",
            "usable_closure_dates": "Usable closure dates",
            "duration_coverage_share": "Duration coverage",
            "reported_closure_date_share": "Closure-date share",
            "mean_hours": "Mean hours",
            "median_hours": "Median hours",
            "p90_hours": "P90 hours",
        }
        st.dataframe(
            pooled.rename(columns=display_columns).style.format(
                {
                    "Duration coverage": "{:.1%}",
                    "Closure-date share": "{:.1%}",
                    "Mean hours": "{:.2f}",
                    "Median hours": "{:.2f}",
                    "P90 hours": "{:.2f}",
                },
                na_rep="—",
            ),
            hide_index=True,
            width="stretch",
        )
        with st.expander("Agency details in a narrow table"):
            agency_choice = st.selectbox(
                "Agency for details",
                pooled.agency.tolist(),
                key=scope + "agency_detail",
            )
            detail_row = (
                pooled.loc[pooled.agency == agency_choice]
                .iloc[0]
                .rename(index=display_columns)
            )
            st.table(
                detail_row.drop(labels="Agency")
                .rename_axis("Measure")
                .reset_index(name="Value"),
                hide_index=True,
            )
        st.caption(
            "Each row pools request-level durations across selected cohorts; subgroup medians and p90s are never averaged. Coverage shares use retained requests as denominator. Agencies without eligible pairs remain in this table and have no plotted duration."
        )
        quality = (
            population.groupby("duration_quality_status", dropna=False)
            .size()
            .reset_index(name="requests")
        )
        st.subheader("Duration exclusions and quality")
        quality["Quality"] = quality.duration_quality_status.str.replace(
            "_", " "
        ).str.capitalize()
        st.bar_chart(
            quality.rename(columns={"requests": "Requests"}), x="Quality", y="Requests"
        )
        chart_data(
            "Duration quality",
            quality[["Quality", "requests"]].rename(columns={"requests": "Requests"}),
            scope + "quality",
        )
        captures = population.snapshot_at_utc.dropna()
        if len(captures):
            st.caption(
                f"Selected lifecycle captures (UTC): {captures.min().isoformat()} to {captures.max().isoformat()}. This is an evidence range, not source freshness."
            )
    evidence(
        detail,
        ns,
        "agency",
        selections,
        "Exact agency/cohort groups. Closure-date share = usable closure dates / retained requests; duration coverage = eligible pairs / retained requests. Neither is a reported Closed-status rate. Pooled chart statistics are recomputed from the request population projection.",
        derived=agency_summary(population),
    )


def observed_status(data, ns, scope):
    st.header("Observed status as of capture time")
    st.caption(
        "Retained identity population · last observed status and independently last observed agency"
    )
    frame = data["status"]
    if frame.empty:
        st.info(
            "No status PIT instants are configured or available. No daily history is fabricated."
        )
        return
    instants = sorted(frame.as_of_utc.unique())
    instant = st.selectbox(
        "As-of instant (UTC)",
        instants,
        index=len(instants) - 1,
        format_func=lambda v: pd.Timestamp(v).isoformat(),
        key=scope + "instant",
    )
    agencies = pick("Observed-as-of agency", frame, "agency_bk", scope + "agency")
    scoped = select_values(frame, "agency_bk", agencies)
    current = scoped[scoped.as_of_utc == instant].copy()
    retained = int(current.request_count.sum())
    known = int(current.status_observed_request_count.sum())
    metric_columns(
        [
            ("Retained", retained),
            ("Observed status", known),
            ("Unknown status", int(current.status_unknown_request_count.sum())),
            ("Status coverage", f"{known / retained:.1%}" if retained else "—"),
        ]
    )
    st.caption(
        "Unknown includes identities not yet captured at this instant. Observed NULL/blank status is included in observed coverage. This is not historical citywide backlog or continuous source freshness."
    )
    if current.empty:
        st.info("No retained identities match the selected agency at this instant.")
    else:
        display = current.assign(
            reported_status=current.apply(status_label, axis=1),
            agency=current.agency_bk.map(exact_label),
        )
        st.altair_chart(
            alt.Chart(display)
            .mark_bar()
            .encode(
                y=alt.Y("reported_status:N", title="Exact reported status / evidence"),
                x=alt.X("sum(request_count):Q", title="Retained"),
                color=alt.Color("agency:N", title="Observed-as-of agency"),
                tooltip=[
                    "reported_status:N",
                    "agency:N",
                    "request_count:Q",
                    "status_class:N",
                ],
            )
            .properties(height=max(200, display.reported_status.nunique() * 35)),
            width="stretch",
        )
        status_rows = display.groupby(
            ["reported_status", "agency", "status_class"], as_index=False, dropna=False
        )["request_count"].sum()
        chart_data(
            "Status by agency",
            status_rows.rename(
                columns={
                    "reported_status": "Reported status",
                    "agency": "Agency",
                    "status_class": "Status class",
                    "request_count": "Requests",
                }
            ),
            scope + "status_chart",
        )
    timeline = scoped.groupby("as_of_utc", as_index=False)[
        ["status_observed_request_count", "status_unknown_request_count"]
    ].sum()
    if not timeline.empty:
        st.subheader("Coverage at retained PIT instants")
        st.altair_chart(
            alt.Chart(
                timeline.rename(
                    columns={
                        "status_observed_request_count": "Observed",
                        "status_unknown_request_count": "Unknown",
                    }
                )
            )
            .transform_fold(
                ["Observed", "Unknown"],
                as_=["coverage", "requests"],
            )
            .mark_point(filled=True, size=80)
            .encode(
                x=alt.X(
                    "as_of_utc:T",
                    title="Selected PIT instant (UTC)",
                    scale=alt.Scale(type="utc"),
                ),
                y=alt.Y("requests:Q", title="Identities", scale=alt.Scale(zero=True)),
                color="coverage:N",
                shape="coverage:N",
                tooltip=["as_of_utc:T", "coverage:N", "requests:Q"],
            )
            .properties(height=230),
            width="stretch",
        )
        chart_data(
            "Status coverage",
            timeline.rename(
                columns={
                    "as_of_utc": "As-of instant (UTC)",
                    "status_observed_request_count": "Observed",
                    "status_unknown_request_count": "Unknown",
                }
            ),
            scope + "coverage",
        )
    st.caption(
        "Points represent only retained PIT instants; gaps are not interpolated. The default calendar uses loaded status-change captures, so agency-only captures may be absent. Counts repeat identities across instants and must not be added. Agency filters follow observed assignment at each instant, so their populations can change."
    )
    evidence(
        current,
        ns,
        "status",
        {"as_of_utc": instant, "agency_bk": agencies},
        "One contribution per retained identity and instant. Unknown before first evidence differs from observed NULL/blank. Agency uses last eligible captured assignment; missing keys never end it. Coverage = observed status / retained identities at the selected instant.",
    )


def main():
    st.title("NYC 311 service analytics")
    try:
        path = database_path()
        available = namespaces(path)
        if not available:
            st.info(
                "The reporting population is empty. Load the synthetic fixtures and build the marts to begin."
            )
            return
        ns = st.sidebar.selectbox(
            "Dataset (source / mode)",
            available,
            format_func=lambda v: f"{v[0]} / {v[1]}",
        )
        st.sidebar.caption(
            "Fictional synthetic fixtures"
            if ns[1] == "synthetic"
            else "Bounded NYC 311 sample; not citywide coverage"
        )
        view = st.sidebar.radio(
            "View", ["Event trends", "Agency & duration", "Observed status"]
        )
        st.sidebar.button(
            "Refresh data",
            help="Reread the built marts. Does not extract or build warehouse data.",
        )
        st.sidebar.caption(
            "NYC event dates and UTC capture instants have separate filters. Data are read from the built reporting views."
        )
        scope = repr(ns) + view
        if st.sidebar.button("Reset view filters"):
            for key in list(st.session_state):
                if str(key).startswith(scope):
                    del st.session_state[key]
        data = read_data(path, ns)
        {
            "Event trends": event_trends,
            "Agency & duration": durations,
            "Observed status": observed_status,
        }[view](data, ns, scope)
    except DashboardUnavailable as exc:
        st.error(str(exc))
        st.caption("From nyc_311_requests, after ingestion:")
        st.code(BUILD_COMMAND, language="bash")


if __name__ == "__main__":
    main()
