"""The AgentSpec acceptance case, end to end within M3's ownership.

Walkthrough steps 3 and 4 (docs/architecture/Netra-SPEC.md section 4):

    3. The transcript says "this line" but omits its axes. Check records
       "axes not established from transcript". No comparison is accepted
       yet.
    4. Coordinator requests visual evidence for the selected moment and
       graph. Validate the axes and plotted values against the selected
       source ... If the axes remain unreadable, state that limitation
       instead of claiming the comparison succeeded.

What these tests do establish: the M3 data M1 needs to make that
decision exists, is distinguishable, and refuses to pass when it should.

What they do NOT establish, and nothing in this repository currently
does: that a real provider reading the real fig02 produces the values
compared here, that the lecture plays or is analysable on a real
account, or that the explanation is usable by a blind student. Those
need original media, a live provider and blind participants
respectively (current-scope.md, "Evidence required for acceptance").

Everything below runs against the labelled synthetic fixture in
multimedia.fixtures.ohm_law. No provider is called.
"""

from fixtures.ohm_law import (
    LECTURE_DURATION_MS,
    QUESTION_TIME_MS,
    VIDEO_ID,
    chart_source_check,
    equation_source_check,
    extracted_chart,
    extracted_equation,
    extracted_table,
    table_source_check,
    transcript_evidence,
    visual_evidence,
)
from netra_api.multimedia.equations.validation import validate_equation_against_source
from netra_api.multimedia.figures.charts import AxisOrientation
from netra_api.multimedia.figures.validation import validate_chart_against_source
from netra_api.multimedia.tables.validation import (
    resolve_cell_unit,
    validate_table_against_source,
)
from netra_api.multimedia.video.evidence_resolution import (
    EvidenceSufficiency,
    resolve_moment_evidence,
)
from netra_api.multimedia.video.timestamps import window_around


def _moment(items):
    window = window_around(
        QUESTION_TIME_MS,
        before_ms=6_000,
        after_ms=10_000,
        duration_ms=LECTURE_DURATION_MS,
    )
    return resolve_moment_evidence(items, window, video_id=VIDEO_ID)


def test_step3_transcript_alone_does_not_establish_the_axes():
    """"Check records: axes not established from transcript.\""""

    moment = _moment([transcript_evidence()])

    assert moment.sufficiency is EvidenceSufficiency.TRANSCRIPT_ONLY
    assert moment.supports_visual_claim is False
    assert moment.gap_statement() is not None


def test_step3_the_gap_is_distinguishable_from_having_no_evidence():
    """M1 branches differently on "words only" and "nothing at all"."""

    transcript_only = _moment([transcript_evidence()])
    nothing = _moment([])

    assert transcript_only.sufficiency is not nothing.sufficiency
    assert transcript_only.transcript_items
    assert nothing.transcript_items == []


def test_step4_visual_evidence_is_distinguishable_from_the_transcript_gap():
    """The acceptance requirement: M1 can tell insufficient transcript from
    validated graph evidence."""

    before = _moment([transcript_evidence()])
    after = _moment([transcript_evidence(), visual_evidence()])

    assert before.supports_visual_claim is False
    assert after.supports_visual_claim is True
    assert after.visual_items[0].reference.evidence_id == "ev-lec-visual-48"


def test_step4_the_graph_is_validated_against_the_source_before_it_is_used():
    """Having visual evidence is not the same as having checked the graph."""

    chart = extracted_chart()
    report = validate_chart_against_source(chart, chart_source_check())

    assert chart.axes_established is True
    assert report.is_source_verified is True


def test_step4_unreadable_axes_end_in_a_stated_limitation():
    """"If the axes remain unreadable, state that limitation instead of
    claiming the comparison succeeded.\""""

    chart = extracted_chart(unreadable_axes=True)
    report = validate_chart_against_source(
        chart, chart_source_check(unreadable_axes=True)
    )

    assert chart.axes_established is False
    assert report.is_source_verified is False
    assert report.unreadable
    assert report.mismatches == []


def test_the_graph_axes_match_the_agentspec_exactly():
    chart = extracted_chart()

    x_axis = chart.axis(AxisOrientation.X)
    y_axis = chart.axis(AxisOrientation.Y)

    assert (x_axis.label, x_axis.unit) == ("current", "A")
    assert (y_axis.label, y_axis.unit) == ("voltage", "V")


def test_the_table_rows_match_the_agentspec_exactly():
    table = extracted_table()

    rows = [
        (table.cell_at(row, 0).numeric_value, table.cell_at(row, 1).numeric_value)
        for row in range(1, 4)
    ]

    assert rows == [(1.0, 2.0), (2.0, 4.0), (3.0, 6.0)]
    assert validate_table_against_source(table, table_source_check()).is_source_verified


def test_the_equation_matches_the_agentspec_exactly():
    report = validate_equation_against_source(
        extracted_equation(), equation_source_check()
    )

    assert report.is_source_verified is True


def test_step5_the_graph_and_the_table_agree_on_the_same_measurement():
    """The student's actual question: where does the table show this?

    Deterministic lookup against validated extraction, with the unit
    resolved from the governing header so "4" is reported as 4 V.
    """

    chart = extracted_chart()
    table = extracted_table()

    plotted = {(point.x, point.y) for point in chart.series[0].points}

    row_index = table.find_row_by_value(column_index=0, numeric_value=2.0)
    current = table.cell_at(row_index, 0).numeric_value
    voltage = table.cell_at(row_index, 1).numeric_value

    assert (current, voltage) in plotted
    assert resolve_cell_unit(table, row_index, 0) == "A"
    assert resolve_cell_unit(table, row_index, 1) == "V"


def test_the_constant_ratio_is_derivable_from_validated_extraction_alone():
    """2/1 = 4/2 = 6/3 = 2 ohm, from the cells rather than from prose.

    This does not teach anything — that is M4's — it checks that the
    numbers survive extraction well enough to be reasoned over at all.
    """

    table = extracted_table()

    ratios = {
        table.cell_at(row, 1).numeric_value / table.cell_at(row, 0).numeric_value
        for row in range(1, 4)
    }

    assert ratios == {2.0}


def test_a_wrong_unit_anywhere_blocks_the_comparison():
    """Millivolts would make the ratio 2 milliohms and the answer wrong."""

    chart_report = validate_chart_against_source(
        extracted_chart(y_unit="mV"), chart_source_check()
    )
    table_report = validate_table_against_source(
        extracted_table(voltage_unit="mV"), table_source_check()
    )

    assert chart_report.is_source_verified is False
    assert table_report.is_source_verified is False


def test_repeated_processing_preserves_navigation_ids():
    """multimedia.md: "Check that repeated processing preserves canonical
    references and navigation IDs.\""""

    first = extracted_chart()
    second = extracted_chart()

    assert first.chart_id == second.chart_id
    assert [axis.axis_id for axis in first.axes] == [axis.axis_id for axis in second.axes]
    assert first.reference.evidence_id == second.reference.evidence_id
    assert [p.point_id for p in first.series[0].points] == [
        p.point_id for p in second.series[0].points
    ]


def test_playback_access_never_stands_in_for_visual_evidence():
    """A lecture that plays perfectly still cannot answer "what is shown"
    until visual evidence exists for the moment asked about."""

    moment = _moment([transcript_evidence()])

    assert moment.supports_visual_claim is False
    assert "not an analysis of what is on screen" in moment.gap_statement()
