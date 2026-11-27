"""Database rules for the ledger (v2).

1. Entries and lines are append-only: UPDATE and DELETE are refused, as for the audit trail.
   TRUNCATE is not covered, so test databases can still be reset.
2. Every entry balances: at COMMIT, each entry that was written in the transaction must have
   at least two lines and its lines must sum to zero in every currency. The check is a
   deferred constraint trigger, so the entry and its lines can be inserted one by one inside
   the transaction, and code that skips apps/ledger/posting.py still cannot commit an
   unbalanced entry.
"""
from django.db import migrations

FORWARD = """
CREATE OR REPLACE FUNCTION ledger_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'ledger rows are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER ledger_journalentry_append_only
    BEFORE UPDATE OR DELETE ON ledger_journalentry
    FOR EACH ROW EXECUTE FUNCTION ledger_append_only();
CREATE TRIGGER ledger_journalline_append_only
    BEFORE UPDATE OR DELETE ON ledger_journalline
    FOR EACH ROW EXECUTE FUNCTION ledger_append_only();

CREATE OR REPLACE FUNCTION ledger_entry_balances() RETURNS trigger AS $$
DECLARE
    entry bigint;
    line_count integer;
    off_currency text;
BEGIN
    IF TG_TABLE_NAME = 'ledger_journalentry' THEN
        entry := NEW.id;
    ELSE
        entry := NEW.entry_id;
    END IF;
    SELECT count(*) INTO line_count FROM ledger_journalline WHERE entry_id = entry;
    IF line_count < 2 THEN
        RAISE EXCEPTION 'journal entry % has fewer than two lines', entry;
    END IF;
    SELECT currency INTO off_currency FROM ledger_journalline WHERE entry_id = entry
        GROUP BY currency HAVING sum(amount) <> 0 LIMIT 1;
    IF off_currency IS NOT NULL THEN
        RAISE EXCEPTION 'journal entry % does not balance in %', entry, off_currency;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER ledger_journalentry_balances
    AFTER INSERT ON ledger_journalentry DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION ledger_entry_balances();
CREATE CONSTRAINT TRIGGER ledger_journalline_balances
    AFTER INSERT ON ledger_journalline DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION ledger_entry_balances();
"""

REVERSE = """
DROP TRIGGER IF EXISTS ledger_journalline_balances ON ledger_journalline;
DROP TRIGGER IF EXISTS ledger_journalentry_balances ON ledger_journalentry;
DROP FUNCTION IF EXISTS ledger_entry_balances();
DROP TRIGGER IF EXISTS ledger_journalline_append_only ON ledger_journalline;
DROP TRIGGER IF EXISTS ledger_journalentry_append_only ON ledger_journalentry;
DROP FUNCTION IF EXISTS ledger_append_only();
"""


class Migration(migrations.Migration):
    dependencies = [("ledger", "0001_initial")]

    operations = [migrations.RunSQL(FORWARD, REVERSE)]
