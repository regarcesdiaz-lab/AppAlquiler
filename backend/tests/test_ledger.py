"""Ledger math: rent charges, FIFO, late fees and balances."""

import datetime as dt
from decimal import Decimal

import pytest

from app.domain.ledger import (
    Credit,
    Debit,
    DebitKind,
    LateFeeRules,
    allocate,
    balance,
    late_fee_cap,
    late_fee_for_charge,
    late_fee_to_post,
    lease_balance,
    money,
    rent_charge,
    rent_charges_through,
)
from app.domain.rule_templates import PRESETS, LateFeeFrequency, LateFeeType, RuleTemplate

D = Decimal
RENT = D("25000.00")


def rent(period: str, day: int = 1, amount: Decimal = RENT) -> Debit:
    year, month = map(int, period.split("-"))
    return Debit(f"rent-{period}", DebitKind.RENT, period, dt.date(year, month, day), amount)


def pay(key: str, on: dt.date, amount: str) -> Credit:
    return Credit(key, on, D(amount))


# -- money -------------------------------------------------------------------------------------


def test_money_rounds_half_up_to_cents() -> None:
    assert money(D("10.005")) == D("10.01")
    assert money(D("10.004")) == D("10.00")
    assert money(3) == D("3.00")
    assert money("1250.5") == D("1250.50")


def test_money_refuses_floats() -> None:
    with pytest.raises(TypeError):
        money(0.1)  # type: ignore[arg-type]


# -- rent charges ------------------------------------------------------------------------------


def test_rent_charge_uses_due_day() -> None:
    charge = rent_charge("2026-11", RENT, 15)
    assert (charge.period, charge.due_on, charge.amount) == ("2026-11", dt.date(2026, 11, 15), RENT)


@pytest.mark.parametrize(
    ("period", "due_day", "expected"),
    [
        ("2027-02", 29, dt.date(2027, 2, 28)),  # non-leap February
        ("2028-02", 29, dt.date(2028, 2, 29)),
        ("2026-04", 31, dt.date(2026, 4, 30)),
        ("2026-02", 30, dt.date(2026, 2, 28)),
    ],
)
def test_rent_charge_in_short_months_falls_on_the_last_day(
    period: str, due_day: int, expected: dt.date
) -> None:
    assert rent_charge(period, RENT, due_day).due_on == expected


def test_first_charge_is_never_due_before_the_lease_starts() -> None:
    charge = rent_charge("2026-11", RENT, 5, lease_start=dt.date(2026, 11, 20))
    assert charge.due_on == dt.date(2026, 11, 20)
    # Later months use the due day as usual.
    assert rent_charge("2026-12", RENT, 5, lease_start=dt.date(2026, 11, 20)).due_on == dt.date(
        2026, 12, 5
    )


def test_rent_must_be_positive() -> None:
    with pytest.raises(ValueError):
        rent_charge("2026-11", D("0"), 1)


def test_rent_charges_through_today_one_per_month() -> None:
    charges = rent_charges_through(
        rent_amount=RENT,
        due_day=31,
        lease_start=dt.date(2026, 11, 3),
        lease_end=None,
        through=dt.date(2027, 3, 10),
    )
    assert [c.period for c in charges] == ["2026-11", "2026-12", "2027-01", "2027-02", "2027-03"]
    assert [c.due_on for c in charges] == [
        dt.date(2026, 11, 30),
        dt.date(2026, 12, 31),
        dt.date(2027, 1, 31),
        dt.date(2027, 2, 28),
        dt.date(2027, 3, 31),
    ]


def test_rent_charges_stop_at_the_end_of_the_lease() -> None:
    charges = rent_charges_through(
        rent_amount=RENT,
        due_day=1,
        lease_start=dt.date(2026, 1, 1),
        lease_end=dt.date(2026, 3, 15),
        through=dt.date(2026, 11, 20),
    )
    assert [c.period for c in charges] == ["2026-01", "2026-02", "2026-03"]


def test_no_charges_before_the_lease_starts() -> None:
    charges = rent_charges_through(
        rent_amount=RENT,
        due_day=1,
        lease_start=dt.date(2027, 1, 1),
        lease_end=None,
        through=dt.date(2026, 12, 15),
    )
    assert charges == []


# -- FIFO --------------------------------------------------------------------------------------


def test_partial_payment_goes_to_the_oldest_charge() -> None:
    result = allocate(
        [rent("2026-11"), rent("2026-10")], [pay("p1", dt.date(2026, 11, 5), "30000")]
    )
    october, november = result.debits
    assert october.debit.period == "2026-10" and october.is_settled
    assert october.settled_on == dt.date(2026, 11, 5)
    assert november.paid == D("5000.00") and november.outstanding == D("20000.00")
    assert november.settled_on is None
    assert result.outstanding == D("20000.00")
    assert result.unapplied_credit == D("0.00")


def test_overpayment_leaves_a_credit_in_favour() -> None:
    result = allocate([rent("2026-11")], [pay("p1", dt.date(2026, 11, 1), "26000")])
    assert result.outstanding == D("0.00")
    assert result.unapplied_credit == D("1000.00")


def test_late_fee_of_a_month_is_paid_before_the_next_rent() -> None:
    fee = Debit("fee-2026-10", DebitKind.LATE_FEE, "2026-10", dt.date(2026, 10, 5), D("1250"))
    result = allocate(
        [rent("2026-11"), fee, rent("2026-10")], [pay("p1", dt.date(2026, 11, 10), "27500")]
    )
    assert [s.debit.key for s in result.debits] == ["rent-2026-10", "fee-2026-10", "rent-2026-11"]
    assert result.status_of("fee-2026-10").is_settled
    assert result.status_of("rent-2026-11").outstanding == D("23750.00")


def test_rent_goes_before_a_late_fee_due_the_same_day() -> None:
    fee = Debit("fee-x", DebitKind.LATE_FEE, "2026-10", dt.date(2026, 11, 1), D("500"))
    result = allocate([fee, rent("2026-11")], [pay("p1", dt.date(2026, 11, 1), "100")])
    assert result.status_of("rent-2026-11").paid == D("100")
    assert result.status_of("fee-x").paid == D("0")


def test_several_payments_record_who_paid_what_and_when() -> None:
    result = allocate(
        [rent("2026-10"), rent("2026-11")],
        [
            pay("late", dt.date(2026, 11, 20), "25000"),
            pay("early", dt.date(2026, 10, 15), "15000"),
        ],
    )
    assert [(a.credit_key, a.debit_key, a.amount) for a in result.applications] == [
        ("early", "rent-2026-10", D("15000")),
        ("late", "rent-2026-10", D("10000")),
        ("late", "rent-2026-11", D("15000")),
    ]
    assert result.status_of("rent-2026-10").settled_on == dt.date(2026, 11, 20)


def test_no_debits_means_every_payment_is_credit() -> None:
    result = allocate([], [pay("p", dt.date(2026, 11, 1), "500")])
    assert result.unapplied_credit == D("500")
    assert result.debits == []


def test_exact_cents_never_drift() -> None:
    debits = [rent(f"2026-{m:02d}", amount=D("10000.01")) for m in range(1, 4)]
    credits = [pay(f"p{i}", dt.date(2026, 3, i + 1), "3333.34") for i in range(9)]
    result = allocate(debits, credits)
    assert result.outstanding + sum(c.amount for c in credits) - result.unapplied_credit == sum(
        d.amount for d in debits
    )
    assert result.outstanding == D("0.00") or result.unapplied_credit == D("0.00")


def test_debits_and_credits_must_be_positive() -> None:
    with pytest.raises(ValueError):
        Debit("x", DebitKind.RENT, "2026-11", dt.date(2026, 11, 1), D("0"))
    with pytest.raises(ValueError):
        Credit("x", dt.date(2026, 11, 1), D("-1"))


# -- late fees ---------------------------------------------------------------------------------

DUE = dt.date(2026, 11, 1)
STANDARD = LateFeeRules(3, LateFeeType.PERCENT, D("5.00"))
STRICT = LateFeeRules(1, LateFeeType.PERCENT, D("0.50"), LateFeeFrequency.DAILY, D("10.00"))


def fee(rules: LateFeeRules, as_of: dt.date, **kwargs) -> Decimal:
    return late_fee_for_charge(
        rent_amount=kwargs.pop("rent_amount", RENT),
        due_on=DUE,
        rules=rules,
        agreed=kwargs.pop("agreed", True),
        as_of=as_of,
        **kwargs,
    )


def test_no_fee_during_grace() -> None:
    assert fee(STANDARD, dt.date(2026, 11, 4)) == D("0.00")  # due + 3 grace days


def test_once_fee_starts_the_day_after_grace() -> None:
    assert fee(STANDARD, dt.date(2026, 11, 5)) == D("1250.00")
    assert fee(STANDARD, dt.date(2026, 12, 20)) == D("1250.00")  # once: never grows


def test_daily_fee_grows_each_day_until_the_cap() -> None:
    assert fee(STRICT, dt.date(2026, 11, 2)) == D("0.00")
    assert fee(STRICT, dt.date(2026, 11, 3)) == D("125.00")  # day 1 of fees
    assert fee(STRICT, dt.date(2026, 11, 12)) == D("1250.00")  # day 10
    assert fee(STRICT, dt.date(2026, 11, 22)) == D("2500.00")  # day 20 = 10 % cap
    assert fee(STRICT, dt.date(2027, 1, 30)) == D("2500.00")


def test_fee_stops_on_the_day_the_charge_is_paid() -> None:
    paid = dt.date(2026, 11, 5)  # 3 fee days: the 3rd, 4th and 5th
    assert fee(STRICT, dt.date(2026, 11, 30), settled_on=paid) == D("375.00")
    assert fee(STANDARD, dt.date(2026, 11, 30), settled_on=dt.date(2026, 11, 4)) == D("0.00")


def test_fixed_fee() -> None:
    fixed_once = LateFeeRules(3, LateFeeType.FIXED, D("1000"))
    fixed_daily = LateFeeRules(0, LateFeeType.FIXED, D("200"), LateFeeFrequency.DAILY)
    assert fee(fixed_once, dt.date(2026, 11, 10)) == D("1000.00")
    assert fee(fixed_daily, dt.date(2026, 11, 5)) == D("800.00")  # days 2 to 5


def test_no_fee_when_not_agreed_in_the_lease() -> None:
    assert fee(STANDARD, dt.date(2026, 12, 1), agreed=False) == D("0.00")
    assert fee(STRICT, dt.date(2026, 12, 1), agreed=False) == D("0.00")


def test_no_fee_when_the_rule_set_has_none() -> None:
    flexible = PRESETS[RuleTemplate.FLEXIBLE]
    rules = LateFeeRules(flexible.grace_days, flexible.late_fee_type, flexible.late_fee_value)
    assert fee(rules, dt.date(2026, 12, 31)) == D("0.00")


@pytest.mark.parametrize(
    "rules",
    [
        LateFeeRules(0, LateFeeType.PERCENT, D("25")),  # once, 25 %
        LateFeeRules(0, LateFeeType.FIXED, D("9000")),  # once, fixed above 10 %
        LateFeeRules(0, LateFeeType.FIXED, D("1000"), LateFeeFrequency.DAILY),
        LateFeeRules(0, LateFeeType.PERCENT, D("2"), LateFeeFrequency.DAILY, cap_percent=None),
    ],
)
def test_platform_cap_is_never_exceeded(rules: LateFeeRules) -> None:
    assert fee(rules, dt.date(2027, 6, 1)) == D("2500.00")


def test_rule_set_cap_below_platform_cap() -> None:
    rules = LateFeeRules(0, LateFeeType.PERCENT, D("1"), LateFeeFrequency.DAILY, D("4"))
    assert fee(rules, dt.date(2026, 12, 31)) == D("1000.00")
    assert late_fee_cap(RENT, D("15")) == D("2500.00")  # a cap above 10 % still stops at 10 %


def test_fee_rounds_to_cents() -> None:
    assert fee(STANDARD, dt.date(2026, 12, 1), rent_amount=D("12345.67")) == D("617.28")


def test_late_fee_to_post_is_the_difference_and_never_negative() -> None:
    assert late_fee_to_post(D("375"), D("250")) == D("125.00")
    assert late_fee_to_post(D("250"), D("375")) == D("0.00")


def test_invalid_late_fee_rules() -> None:
    with pytest.raises(ValueError):
        LateFeeRules(-1, LateFeeType.NONE, D("0"))
    with pytest.raises(ValueError):
        LateFeeRules(3, LateFeeType.PERCENT, D("0"))


# -- balances ----------------------------------------------------------------------------------


def test_balance_by_contract_and_period() -> None:
    fee_oct = Debit("fee-2026-10", DebitKind.LATE_FEE, "2026-10", dt.date(2026, 10, 5), D("1250"))
    debits = [rent("2026-10"), fee_oct, rent("2026-11")]
    credits = [pay("p1", dt.date(2026, 10, 20), "20000"), pay("p2", dt.date(2026, 11, 3), "10000")]
    result = lease_balance(debits, credits)
    assert result.total == D("21250.00")
    october = result.period("2026-10")
    assert (october.charged, october.paid, october.outstanding) == (
        D("26250.00"),
        D("26250.00"),
        D("0.00"),
    )
    assert result.period("2026-11").outstanding == D("21250.00")
    assert result.period("2026-12").outstanding == D("0.00")
    assert result.credit_in_favour == D("0.00")


def test_negative_balance_is_a_credit_in_favour() -> None:
    result = lease_balance([rent("2026-11")], [pay("p1", dt.date(2026, 11, 1), "30000")])
    assert result.total == D("-5000.00")
    assert result.credit_in_favour == D("5000.00")
    assert balance([rent("2026-11")], []) == RENT
