from django.apps import AppConfig


class FinanceConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'finance'
    verbose_name = 'Finance & Payables'

    def ready(self):
        # Wire the Invoice post_save hook so Miya's agent_list_invoices
        # feed is invalidated whenever an invoice is created, paid, or
        # edited (admin, agent, or scheduled tasks).
        import finance.signals  # noqa: F401
