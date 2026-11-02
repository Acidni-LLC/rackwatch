"""PII scrubber: planted PII is 100% caught and ordinary monitoring text survives."""

from __future__ import annotations

import pytest
from app import db as dbmod
from app.pii import PiiScrubber, UnscrubbedPayloadError, assert_scrubbed, find_unscrubbed, names_pattern

PLANTED = [
    ("Mailbox jane.doe@clientco.example full", "<EMAIL_1>", "jane.doe@clientco.example"),
    ("Call back on 555-0142 please", "<PHONE_1>", "555-0142"),
    ("Callback (305) 555-0142 after 5", "<PHONE_1>", "(305) 555-0142"),
    ("Mobile +1 305 555 0142", "<PHONE_1>", "+1 305 555 0142"),
    ("E164 +13055550142 ok", "<PHONE_1>", "+13055550142"),
    ("Laptop 203.0.113.44 cannot reach gateway", "<IP_1>", "203.0.113.44"),
    ("Host 8.8.8.8 unreachable", "<IP_1>", "8.8.8.8"),
    ("client MAC aa:bb:cc:dd:ee:ff roaming", "<MAC_1>", "aa:bb:cc:dd:ee:ff"),
    ("client mac aabb.ccdd.eeff roaming", "<MAC_1>", "aabb.ccdd.eeff"),
    ("Card 4111 1111 1111 1111 on file", "<CARD_1>", "4111 1111 1111 1111"),
    ("SSN 078-05-1120 in ticket", "<SSN_1>", "078-05-1120"),
    ("user jdoe locked out", "<PERSON_1>", "jdoe"),
    ("username=mgarcia failed", "<PERSON_1>", "mgarcia"),
    ("Hi Maria, your laptop", "<PERSON_1>", "Maria"),
]

CLEAN = [
    "2026-11-02T02:03:11Z %LINK-3-UPDOWN Interface Te1/1/1 down",
    "%ETHCHNL-5-PORT_STATUS Po1 flapping on SW-ACC-02",
    "SFP rx power -24.1 dBm below threshold",
    "Volume 1 usage 98%",
    "Hyper Backup task 'Nightly' failed: no space",
    "SSL-VPN certificate expired 2026-11-01T23:59Z",
    "L-0193 A-0412 INC-2 FIX-SFP-REPLACE dev_A_12",
    "mgmt 192.0.2.12 server 198.51.100.8 private 10.1.2.3",
    "IOS-XE 17.06.05.01 upgrade available",
    "serial FOC2231X0AB JAE24190LQ2 FGT60FTK21004417 AS2219141877",
    "rack A U12 port 443 tcp 8080 rtt 12.5 ms",
    "user tickets rising; user session count 41; user unable to connect",
    "Hi there, Hello team, Dear All",
    "tokens <EMAIL_1> and <PERSON_2> are already safe",
]


@pytest.mark.parametrize(("raw", "token", "value"), PLANTED)
def test_planted_pii_is_tokenized_and_reversible(raw, token, value):
    scrubber = PiiScrubber()
    out = scrubber.scrub(raw)
    assert value not in out
    assert token in out
    assert value in scrubber.detokenize(out)


@pytest.mark.parametrize("text", CLEAN)
def test_ordinary_text_is_unchanged(text):
    assert PiiScrubber().scrub(text) == text
    assert find_unscrubbed(text) == []


def test_same_value_same_token_and_counter_grows():
    scrubber = PiiScrubber()
    out = scrubber.scrub("a@b.example c@d.example a@b.example")
    assert out == "<EMAIL_1> <EMAIL_2> <EMAIL_1>"


def test_names_list_catches_full_names():
    scrubber = PiiScrubber(names=["Jane Doe", "Sam Rivers"])
    assert scrubber.scrub("Reported by Jane Doe at reception") == "Reported by <PERSON_1> at reception"


def test_map_persists_locally_only():
    conn = dbmod.connect(":memory:")
    first = PiiScrubber(conn)
    first.scrub("a@b.example")
    second = PiiScrubber(conn)
    assert second.scrub("a@b.example c@d.example") == "<EMAIL_1> <EMAIL_2>"
    assert second.detokenize("<EMAIL_1>") == "a@b.example"


def test_scrub_obj_keeps_keys_and_scrubs_values():
    scrubber = PiiScrubber()
    out = scrubber.scrub_obj({"email": "a@b.example", "ids": ["L-0001"], "n": 3})
    assert out == {"email": "<EMAIL_1>", "ids": ["L-0001"], "n": 3}


def test_guard_scans_instructions_user_text_and_tool_output():
    with pytest.raises(UnscrubbedPayloadError):
        assert_scrubbed({"instructions": "mail a@b.example", "input": "ok"})
    with pytest.raises(UnscrubbedPayloadError):
        assert_scrubbed(
            {"input": [{"role": "user", "content": [{"type": "input_text", "text": "555-0142"}]}]}
        )
    with pytest.raises(UnscrubbedPayloadError):
        assert_scrubbed(
            {"input": [{"type": "function_call_output", "call_id": "c", "output": "203.0.113.9"}]}
        )
    assert_scrubbed(
        {
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_image", "image_url": "data:image/jpeg;base64,AAAA"}],
                }
            ]
        }
    )


def test_guard_uses_names_list():
    with pytest.raises(UnscrubbedPayloadError):
        assert_scrubbed({"input": "Reported by Jane Doe"}, names_pattern(["Jane Doe"]))
    assert_scrubbed({"input": "Reported by <PERSON_1>"}, names_pattern(["Jane Doe"]))
