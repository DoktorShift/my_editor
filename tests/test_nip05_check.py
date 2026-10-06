"""Checking other people's Nostr addresses (NIP-05) with their domains."""

from PySide6.QtNetwork import QNetworkReply, QNetworkRequest

from nostr import nip05_check
from nostr.nip05_check import Nip05Check, names_key, parse_address, shown_address
from tests.membership_fakes import FakeNam, FakeReply, json_reply

ALICE = "a1" * 32
MALLORY = "f0" * 32


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


def checker(script=None, *, responder=None):
    nam = FakeNam(script, responder=responder)
    clock = Clock()
    check = Nip05Check(nam=nam, clock=clock)
    seen = []
    check.checked.connect(lambda pubkey, text: seen.append((pubkey, text)))
    return check, nam, clock, seen


def names(**listed):
    return json_reply({"names": listed})


# -- addresses -----------------------------------------------------------------

def test_an_address_is_read_without_regard_to_case():
    address = parse_address(" Alice@Example.COM ")
    assert (address.name, address.domain) == ("alice", "example.com")
    assert address.url == "https://example.com/.well-known/nostr.json?name=alice"


def test_text_that_is_not_an_address_is_not_read():
    for text in ("", "alice", "alice@", "@example.com", "alice@example",
                 "al ice@example.com", "alice@exa mple.com", "https://example.com/@alice"):
        assert parse_address(text) is None, text


def test_a_domain_in_the_persons_own_network_is_never_asked():
    for text in ("alice@localhost", "alice@router", "alice@nas.local", "alice@10.0.0.1",
                 "alice@192.168.1.20", "alice@169.254.169.254", "alice@127.0.0.1"):
        assert parse_address(text) is None, text


def test_the_domains_own_name_shows_as_the_domain():
    assert shown_address("_@example.com") == "example.com"
    assert shown_address("alice@example.com") == "alice@example.com"


def test_the_key_is_read_from_the_names_without_regard_to_case():
    assert names_key({"names": {"alice": ALICE.upper()}}, "alice") == ALICE
    assert names_key({"names": {"Alice": ALICE}}, "alice") == ALICE


def test_an_answer_without_a_proper_key_gives_none():
    for answer in (None, [], {"names": []}, {"names": {}}, {"names": {"alice": 5}},
                   {"names": {"alice": "npub1notahexkey"}}, {"names": {"alice": "ab" * 31}}):
        assert names_key(answer, "alice") is None, answer


# -- checking ------------------------------------------------------------------

def test_an_address_its_domain_confirms_counts():
    check, nam, _clock, seen = checker([names(alice=ALICE)])
    assert check.confirmed(ALICE, "alice@example.com") is None
    request = nam.calls[0][1]
    assert request.url().toString() == "https://example.com/.well-known/nostr.json?name=alice"
    nam.issued[0].finish()
    assert seen == [(ALICE, "alice@example.com")]
    assert check.confirmed(ALICE, "alice@example.com") is True
    assert len(nam.calls) == 1


def test_a_borrowed_address_does_not_count():
    # Mallory writes Alice's address into her own profile.
    check, nam, _clock, seen = checker([names(alice=ALICE)])
    assert check.confirmed(MALLORY, "alice@example.com") is None
    nam.issued[0].finish()
    assert check.confirmed(MALLORY, "alice@example.com") is False
    assert check.confirmed(ALICE, "alice@example.com") is True
    assert len(nam.calls) == 1


def test_redirects_are_not_followed():
    check, nam, _clock, _seen = checker([FakeReply(status=302)])
    check.confirmed(ALICE, "alice@example.com")
    request = nam.calls[0][1]
    assert request.attribute(QNetworkRequest.Attribute.RedirectPolicyAttribute) \
        == QNetworkRequest.RedirectPolicy.ManualRedirectPolicy
    nam.issued[0].finish()
    assert check.confirmed(ALICE, "alice@example.com") is False


def test_a_broken_answer_does_not_count():
    for reply in (FakeReply(body=b"<html>not json</html>"), FakeReply(body=b"\xff\xfe"),
                  FakeReply(status=404), FakeReply(status=500),
                  FakeReply(status=0, error=QNetworkReply.NetworkError.HostNotFoundError)):
        check, nam, _clock, seen = checker([reply])
        check.confirmed(ALICE, "alice@example.com")
        nam.issued[0].finish()
        assert check.confirmed(ALICE, "alice@example.com") is False
        assert seen == [(ALICE, "alice@example.com")]


def test_an_oversized_answer_is_dropped():
    check, nam, _clock, _seen = checker([names(alice=ALICE)])
    check.confirmed(ALICE, "alice@example.com")
    reply = nam.issued[0]
    reply.downloadProgress.emit(nip05_check.MAX_ANSWER_BYTES + 1, -1)
    assert reply.aborted
    reply.finish()
    assert check.confirmed(ALICE, "alice@example.com") is False


def test_an_address_that_cannot_be_checked_is_refused_without_asking():
    check, nam, _clock, seen = checker()
    assert check.confirmed(ALICE, "alice@router") is False
    assert check.confirmed(ALICE, "not an address") is False
    assert check.confirmed("not a key", "alice@example.com") is False
    assert nam.calls == [] and seen == []


def test_many_people_with_one_address_share_one_request():
    check, nam, _clock, seen = checker([names(alice=ALICE)])
    check.confirmed(ALICE, "alice@example.com")
    check.confirmed(MALLORY, "Alice@example.com")
    check.confirmed(ALICE, "alice@example.com")
    assert len(nam.calls) == 1
    nam.issued[0].finish()
    assert seen == [(ALICE, "alice@example.com"), (MALLORY, "Alice@example.com")]


def test_only_a_few_checks_run_at_once():
    check, nam, _clock, seen = checker(responder=lambda *_: names())
    for n in range(nip05_check.AT_ONCE + 3):
        check.confirmed(ALICE, f"user{n}@example.com")
    assert len(nam.calls) == nip05_check.AT_ONCE
    nam.issued[0].finish()
    assert len(nam.calls) == nip05_check.AT_ONCE + 1
    nam.settle()
    assert len(nam.calls) == nip05_check.AT_ONCE + 3
    assert len(seen) == nip05_check.AT_ONCE + 3


def test_a_verdict_is_asked_again_once_it_runs_out():
    check, nam, clock, _seen = checker(responder=lambda *_: names(alice=ALICE))
    check.confirmed(ALICE, "alice@example.com")
    nam.settle()
    clock.now += nip05_check.CONFIRMED_FOR_S - 1
    assert check.confirmed(ALICE, "alice@example.com") is True
    clock.now += 2
    assert check.confirmed(ALICE, "alice@example.com") is None
    assert len(nam.calls) == 2


def test_an_unreachable_domain_is_asked_again_sooner_than_a_refusal():
    assert nip05_check.UNREACHABLE_FOR_S < nip05_check.REFUSED_FOR_S \
        < nip05_check.CONFIRMED_FOR_S
    check, nam, clock, _seen = checker(
        [FakeReply(status=0, error=QNetworkReply.NetworkError.TimeoutError)])
    check.confirmed(ALICE, "alice@example.com")
    nam.issued[0].finish()
    clock.now += nip05_check.UNREACHABLE_FOR_S + 1
    assert check.confirmed(ALICE, "alice@example.com") is None


def test_forget_drops_every_verdict():
    check, nam, _clock, _seen = checker(responder=lambda *_: names(alice=ALICE))
    check.confirmed(ALICE, "alice@example.com")
    nam.settle()
    check.forget()
    assert check.confirmed(ALICE, "alice@example.com") is None
