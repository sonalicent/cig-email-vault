from email_pipeline.thread_splitter import split_thread


def test_direct_email_yields_single_wrapper_unit():
    body = "Hi team,\n\nHere are the numbers.\n\nThanks,\nAlice"
    units = split_thread(body)
    assert len(units) == 1
    assert units[0].is_wrapper is True
    assert units[0].order == 0
    assert "Here are the numbers." in units[0].body
    assert units[0].headers == {}


def test_forward_with_empty_wrapper_drops_wrapper_and_splits_quoted():
    body = (
        "________________________________\n"
        "From: Bob Sender <bob@example.com>\n"
        "Sent: Wednesday, July 8, 2026 12:47 PM\n"
        "To: Carol <carol@example.com>\n"
        "Subject: Re: Project timeline\n"
        "\n"
        "Ok cool, appreciate the update.\n"
        "________________________________\n"
        "From: Alice Example <alice@example.com>\n"
        "Sent: Wednesday, July 8, 2026 3:43 PM\n"
        "To: Bob <bob@example.com>\n"
        "Subject: Project timeline\n"
        "\n"
        "Hey Bob, 120 days is the timeline.\n"
    )
    units = split_thread(body)
    assert len(units) == 2
    assert all(not u.is_wrapper for u in units)
    assert units[0].from_ == "Bob Sender <bob@example.com>"
    assert units[0].subject == "Re: Project timeline"
    assert units[0].date == "Wednesday, July 8, 2026 12:47 PM"
    assert "appreciate the update" in units[0].body
    assert units[1].from_ == "Alice Example <alice@example.com>"
    assert "120 days" in units[1].body


def test_reply_with_top_text_keeps_wrapper_plus_quoted():
    body = (
        "Thanks, that works for me.\n"
        "\n"
        "________________________________\n"
        "From: Bob Sender <bob@example.com>\n"
        "Sent: Monday, July 6, 2026 9:00 AM\n"
        "To: Alice <alice@example.com>\n"
        "Subject: Meeting\n"
        "\n"
        "Can we meet Monday?\n"
    )
    units = split_thread(body)
    assert len(units) == 2
    assert units[0].is_wrapper is True
    assert "Thanks, that works for me." in units[0].body
    assert units[1].from_ == "Bob Sender <bob@example.com>"


def test_external_email_banner_is_detected_and_preserved():
    body = (
        "________________________________\n"
        "From: Outside Vendor <vendor@vendor.com>\n"
        "Sent: Tuesday, July 7, 2026 8:00 AM\n"
        "To: Alice <alice@example.com>\n"
        "Subject: Quote\n"
        "\n"
        "CAUTION: EXTERNAL EMAIL\n"
        "\n"
        "Here is your quote.\n"
    )
    units = split_thread(body)
    assert len(units) == 1
    assert units[0].is_external is True
    assert "EXTERNAL EMAIL" in units[0].body


def test_wrapped_header_value_continuation_is_joined():
    body = (
        "________________________________\n"
        "From: Bob Sender <bob@example.com>\n"
        "Sent: Monday, July 6, 2026 9:00 AM\n"
        "To: Alice <alice@example.com>; Carol <carol@example.com>;\n"
        " Dave <dave@example.com>\n"
        "Subject: Group note\n"
        "\n"
        "Body text.\n"
    )
    units = split_thread(body)
    assert len(units) == 1
    assert "dave@example.com" in units[0].to


def test_empty_body_yields_no_units():
    assert split_thread("") == []
    assert split_thread("   \n  \n") == []


def test_nested_forward_hops_with_empty_bodies_are_dropped():
    # Nested Outlook forwards stack a header-only block per hop; only the final
    # quoted email carries real content.
    body = (
        "\n"
        "________________________________\n"
        "From: Kirthana Natarajan <Kirthana.Natarajan@bloomenergy.com>\n"
        "Sent: Thursday, July 30, 2026 4:36 PM\n"
        "To: CIG Vault <CIG.Vault@bloomenergy.com>\n"
        "Subject: Fw: Jupiter DRAFT L1 Schedule\n"
        "\n"
        "\n"
        "________________________________\n"
        "From: Kirthana Natarajan <Kirthana.Natarajan@bloomenergy.com>\n"
        "Sent: Monday, July 27, 2026 8:57 AM\n"
        "To: CIG Vault <CIG.Vault@bloomenergy.com>\n"
        "Subject: Fw: Jupiter DRAFT L1 Schedule\n"
        "\n"
        "\n"
        "________________________________\n"
        "From: Tyler.Roark <Tyler.Roark@kiewit.com>\n"
        "Sent: Friday, July 10, 2026 3:24 PM\n"
        "To: Michael.Sofferin <Michael.Sofferin@kiewit.com>\n"
        "Subject: Fw: Jupiter DRAFT L1 Schedule\n"
        "\n"
        "Please find attached the current Draft Level 1 CPM Schedule.\n"
    )
    units = split_thread(body)
    assert len(units) == 1
    assert units[0].from_ == "Tyler.Roark <Tyler.Roark@kiewit.com>"
    assert "Draft Level 1 CPM Schedule" in units[0].body
