import pytest
from voicetrack.fallback import parse


# --- Income cases ---

def test_gifted_me():
    r = parse("my friend gifted me 3000")
    assert r["type"] == "income"
    assert r["amount"] == 3000.0
    assert r["category"] == "Other"

def test_sent_me():
    r = parse("ahmed sent me 5000")
    assert r["type"] == "income"
    assert r["amount"] == 5000.0

def test_returned():
    r = parse("my brother returned 2000 he owed me")
    assert r["type"] == "income"
    assert r["amount"] == 2000.0

def test_client_paid_me():
    r = parse("client paid me 8000 for the project")
    assert r["type"] == "income"
    assert r["category"] == "Freelance"

def test_received_salary():
    r = parse("received salary 45000")
    assert r["type"] == "income"
    assert r["category"] == "Salary"

def test_got_paid():
    r = parse("got paid 12000 today")
    assert r["type"] == "income"


# --- Expense cases ---

def test_paid_cab():
    r = parse("paid 500 for cab")
    assert r["type"] == "expense"
    assert r["category"] == "Transport"
    assert r["amount"] == 500.0

def test_gave_friend():
    r = parse("i gave my friend 500")
    assert r["type"] == "expense"
    assert r["amount"] == 500.0

def test_bought_groceries():
    r = parse("bought groceries 1200")
    assert r["type"] == "expense"
    assert r["category"] == "Food & Groceries"

def test_electricity_bill():
    r = parse("electricity bill 2500")
    assert r["type"] == "expense"
    assert r["category"] == "Utilities"


# --- Edge cases ---

def test_no_number():
    r = parse("random string with no numbers")
    assert r["amount"] == 0.0
    assert r["category"] == "Other"
    assert r["confidence"] == "low"

def test_all_low_confidence():
    for text in [
        "my friend gifted me 3000",
        "ahmed sent me 5000",
        "paid 500 for cab",
        "random string with no numbers",
    ]:
        assert parse(text)["confidence"] == "low"
