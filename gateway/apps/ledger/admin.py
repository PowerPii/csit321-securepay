from django.contrib import admin

from apps.ledger.models import JournalEntry, JournalLine, LedgerAccount, Payout


class ReadOnly(admin.ModelAdmin):
    """Ledger rows are written by apps/ledger/posting.py only, and never changed."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class LineInline(admin.TabularInline):
    model = JournalLine
    fields = ("account", "amount", "currency", "memo")
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(JournalEntry)
class JournalEntryAdmin(ReadOnly):
    list_display = ("public_id", "kind", "source_id", "merchant", "occurred_at", "backfilled")
    list_filter = ("kind", "backfilled")
    search_fields = ("public_id", "source_id", "payment__reference")
    inlines = [LineInline]


@admin.register(LedgerAccount)
class LedgerAccountAdmin(ReadOnly):
    list_display = ("kind", "merchant", "currency", "created_at")
    list_filter = ("kind", "currency")


@admin.register(Payout)
class PayoutAdmin(ReadOnly):
    list_display = ("public_id", "merchant", "amount", "currency", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("public_id",)
