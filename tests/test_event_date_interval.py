from horizon_memory.event_date_interval import compile_event_date_interval


GIANNITSA = (
    "After another victory at Giannitsa on 2 November 1912, the Ottoman commander "
    "Hasan Tahsin Pasha surrendered Thessaloniki to the Greeks on 9 November 1912."
)
SMERWICK = (
    "On 10 September 1580, troops commanded by Sebastiano di San Giuseppe landed in "
    "Smerwick. After a siege, commander Di san Giuseppe surrendered on 10 October 1580."
)


def test_aligns_two_events_to_two_full_dates_and_reopens():
    question = ("How many days after the battle at Giannitsa did the Ottoman surrender "
                "Thessaloniki?")
    proof = compile_event_date_interval(question, GIANNITSA)
    assert proof is not None and proof.result == 7
    assert tuple(item.iso_date for item in proof.alignments) == ("1912-11-02", "1912-11-09")
    assert proof.verify(question, GIANNITSA)
    assert not proof.verify(question, GIANNITSA + " tampered")


def test_cross_month_elapsed_days_use_calendar_arithmetic():
    question = ("How many days after Sebastiano di San Giuseppe landed in Smerwick "
                "did he surrender?")
    proof = compile_event_date_interval(question, SMERWICK)
    assert proof is not None and proof.result == 30
    assert proof.verify(question, SMERWICK)


def test_partial_or_open_date_universe_abstains():
    question = "How many days after Alpha landed did Alpha surrender?"
    assert compile_event_date_interval(
        question, "Alpha landed on 1 June 1648 and surrendered on June 3.") is None
    assert compile_event_date_interval(
        question, "Alpha landed on 1 June 1648, paused on 2 June 1648, and surrendered "
                  "on 3 June 1648.") is None


def test_reverse_chronology_and_ambiguous_alignment_abstain():
    assert compile_event_date_interval(
        "How many days after Alpha landed did Beta surrender?",
        "Beta surrendered on 9 November 1912. Alpha landed on 10 November 1912.") is None
    assert compile_event_date_interval(
        "How many days after Alpha landed did Alpha surrender?",
        "Alpha landed on 1 June 1648. Alpha landed and surrendered on 3 June 1648.") is None
