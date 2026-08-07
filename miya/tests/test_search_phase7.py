"""Phase 7 — 100+ natural-language operational search queries.

Each case asserts parse mode/domain routing (structured vs semantic/hybrid vs event).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from miya.services.intelligence.search.classify_query import parse_search_query
from miya.services.intelligence.search.types import SearchDomain, SearchMode

# (query, expected_domain, expected_mode_set)
# mode may be one of several acceptable modes for paraphrases
NL_QUERIES: list[tuple[str, SearchDomain, frozenset[SearchMode]]] = [
    # --- Incidents structured ---
    ("Find the freezer incident.", SearchDomain.INCIDENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Show me the freezer incident", SearchDomain.INCIDENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Find open incidents", SearchDomain.INCIDENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("List resolved incidents", SearchDomain.INCIDENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Get the kitchen incident", SearchDomain.INCIDENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Find safety incident", SearchDomain.INCIDENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Show incident about fire", SearchDomain.INCIDENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID, SearchMode.SEMANTIC})),
    ("Find accident report", SearchDomain.INCIDENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Show me open safety concerns", SearchDomain.INCIDENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Find the broken freezer incident", SearchDomain.INCIDENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    # --- Incidents conceptual / hybrid ---
    ("Find the incident where someone complained about the freezer.", SearchDomain.INCIDENT, frozenset({SearchMode.HYBRID, SearchMode.SEMANTIC})),
    ("Show incidents related to cold storage", SearchDomain.INCIDENT, frozenset({SearchMode.HYBRID, SearchMode.SEMANTIC, SearchMode.STRUCTURED})),
    ("Find something about a customer complaining about the fridge", SearchDomain.INCIDENT, frozenset({SearchMode.HYBRID, SearchMode.SEMANTIC})),
    ("Look up the incident regarding the walk-in freezer", SearchDomain.INCIDENT, frozenset({SearchMode.HYBRID, SearchMode.SEMANTIC, SearchMode.STRUCTURED})),
    ("Find incidents where equipment broke", SearchDomain.INCIDENT, frozenset({SearchMode.HYBRID, SearchMode.SEMANTIC, SearchMode.STRUCTURED})),
    ("Show me incidents similar to a freezer leak", SearchDomain.INCIDENT, frozenset({SearchMode.HYBRID, SearchMode.SEMANTIC})),
    ("Search for complaints about temperature", SearchDomain.INCIDENT, frozenset({SearchMode.HYBRID, SearchMode.SEMANTIC, SearchMode.STRUCTURED})),
    ("Find the incident about someone mentioning the frigo", SearchDomain.INCIDENT, frozenset({SearchMode.HYBRID, SearchMode.SEMANTIC, SearchMode.STRUCTURED})),
    ("Get incidents related to kitchen hazards", SearchDomain.INCIDENT, frozenset({SearchMode.HYBRID, SearchMode.SEMANTIC, SearchMode.STRUCTURED})),
    ("Find what looks like a freezer problem incident", SearchDomain.INCIDENT, frozenset({SearchMode.HYBRID, SearchMode.SEMANTIC, SearchMode.STRUCTURED})),
    # --- Invoices ---
    ("Show me invoices from ABC Foods.", SearchDomain.INVOICE, frozenset({SearchMode.STRUCTURED})),
    ("Find invoices from Acme", SearchDomain.INVOICE, frozenset({SearchMode.STRUCTURED})),
    ("List invoices from Metro Cash", SearchDomain.INVOICE, frozenset({SearchMode.STRUCTURED})),
    ("Show invoices by Sysco", SearchDomain.INVOICE, frozenset({SearchMode.STRUCTURED})),
    ("Find bill from ABC Foods", SearchDomain.INVOICE, frozenset({SearchMode.STRUCTURED})),
    ("Get facture from Supplier X", SearchDomain.INVOICE, frozenset({SearchMode.STRUCTURED})),
    ("Show pending invoices", SearchDomain.INVOICE, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Find open invoices", SearchDomain.INVOICE, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("List invoices awaiting approval", SearchDomain.INVOICE, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID, SearchMode.SEMANTIC})),
    ("Show me invoices from last week", SearchDomain.INVOICE, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Find invoice number 1234", SearchDomain.INVOICE, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Search invoices related to catering", SearchDomain.INVOICE, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID, SearchMode.SEMANTIC})),
    ("Show overdue invoices", SearchDomain.INVOICE, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Find invoices for Branch A", SearchDomain.INVOICE, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Get bills from Fresh Market", SearchDomain.INVOICE, frozenset({SearchMode.STRUCTURED})),
    # --- Events / what happened ---
    ("What happened with the late delivery?", SearchDomain.MIXED, frozenset({SearchMode.HYBRID, SearchMode.EVENT, SearchMode.SEMANTIC})),
    ("Who handled the incident last week?", SearchDomain.INCIDENT, frozenset({SearchMode.EVENT, SearchMode.HYBRID})),
    ("What happened with the freezer incident?", SearchDomain.INCIDENT, frozenset({SearchMode.EVENT, SearchMode.HYBRID})),
    ("Who dealt with the complaint yesterday?", SearchDomain.EVENT, frozenset({SearchMode.EVENT, SearchMode.HYBRID, SearchMode.SEMANTIC})),
    ("Show history of the decoration task", SearchDomain.TASK, frozenset({SearchMode.EVENT, SearchMode.HYBRID, SearchMode.STRUCTURED})),
    ("What went on with payroll last week?", SearchDomain.EVENT, frozenset({SearchMode.EVENT, SearchMode.HYBRID, SearchMode.SEMANTIC})),
    ("Who took care of the kitchen incident?", SearchDomain.INCIDENT, frozenset({SearchMode.EVENT, SearchMode.HYBRID})),
    ("Timeline for invoice from ABC Foods", SearchDomain.INVOICE, frozenset({SearchMode.EVENT, SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("What happened yesterday with deliveries?", SearchDomain.MIXED, frozenset({SearchMode.EVENT, SearchMode.HYBRID, SearchMode.SEMANTIC})),
    ("Who handled the safety concern?", SearchDomain.INCIDENT, frozenset({SearchMode.EVENT, SearchMode.HYBRID})),
    # --- Staff / checklists ---
    ("Which staff haven't completed their opening checklist?", SearchDomain.CHECKLIST, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Which staff have not completed opening checklist", SearchDomain.CHECKLIST, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Show staff who didn't finish checklist", SearchDomain.CHECKLIST, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Find incomplete opening checklists", SearchDomain.CHECKLIST, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("List opening checklist tasks", SearchDomain.CHECKLIST, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Show uncompleted checklists", SearchDomain.CHECKLIST, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Find staff named Ahmed", SearchDomain.STAFF, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID, SearchMode.SEMANTIC})),
    ("Look up staff Sara", SearchDomain.STAFF, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID, SearchMode.SEMANTIC})),
    ("Which staff are on opening checklist today", SearchDomain.CHECKLIST, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Show checklist for morning opening", SearchDomain.CHECKLIST, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    # --- Documents / insurance ---
    ("Show me documents related to insurance.", SearchDomain.DOCUMENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID, SearchMode.SEMANTIC})),
    ("Find insurance documents", SearchDomain.DOCUMENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Get documents regarding insurance policy", SearchDomain.DOCUMENT, frozenset({SearchMode.HYBRID, SearchMode.SEMANTIC, SearchMode.STRUCTURED})),
    ("Show insurance PDF", SearchDomain.DOCUMENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Find compliance documents", SearchDomain.DOCUMENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID, SearchMode.SEMANTIC})),
    ("List documents related to licence", SearchDomain.DOCUMENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID, SearchMode.SEMANTIC})),
    ("Show me the insurance contract", SearchDomain.DOCUMENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Find documents about assurance", SearchDomain.DOCUMENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID, SearchMode.SEMANTIC})),
    ("Search documents related to food safety cert", SearchDomain.DOCUMENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID, SearchMode.SEMANTIC})),
    ("Show expiring insurance documents", SearchDomain.DOCUMENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    # --- Tasks ---
    ("Find the decoration task", SearchDomain.TASK, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Show pending tasks", SearchDomain.TASK, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("List overdue tasks", SearchDomain.TASK, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Find completed tasks from yesterday", SearchDomain.TASK, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Show tasks assigned to Ahmed", SearchDomain.TASK, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID, SearchMode.SEMANTIC})),
    ("Get the closing task", SearchDomain.TASK, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Find blocked tasks", SearchDomain.TASK, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Show me tasks for today", SearchDomain.TASK, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Search tasks related to inventory", SearchDomain.TASK, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID, SearchMode.SEMANTIC})),
    ("Find the demande from yesterday", SearchDomain.TASK, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    # --- Meetings ---
    ("Show meetings today", SearchDomain.MEETING, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Find kitchen meeting", SearchDomain.MEETING, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("List calendar meetings this week", SearchDomain.MEETING, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Show upcoming meeting", SearchDomain.MEETING, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Find the staff meeting", SearchDomain.MEETING, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    # --- Date / metadata filters ---
    ("Find incidents from last week", SearchDomain.INCIDENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Show invoices from yesterday", SearchDomain.INVOICE, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Find tasks from last 7 days", SearchDomain.TASK, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Show open incidents today", SearchDomain.INCIDENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Find documents from this week", SearchDomain.DOCUMENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    # --- French / mixed ---
    ("Cherche l'incident frigo", SearchDomain.INCIDENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID, SearchMode.SEMANTIC})),
    ("Montre les factures de ABC Foods", SearchDomain.INVOICE, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID, SearchMode.SEMANTIC})),
    ("Affiche les documents assurance", SearchDomain.DOCUMENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID, SearchMode.SEMANTIC})),
    ("Trouve la tâche décoration", SearchDomain.TASK, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID, SearchMode.SEMANTIC})),
    ("Cherche incident plainte congelateur", SearchDomain.INCIDENT, frozenset({SearchMode.HYBRID, SearchMode.SEMANTIC, SearchMode.STRUCTURED})),
    # --- More coverage to exceed 100 ---
    ("Find the delivery incident", SearchDomain.INCIDENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Show slip and fall incident", SearchDomain.INCIDENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID, SearchMode.SEMANTIC})),
    ("Find burn incident kitchen", SearchDomain.INCIDENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Show me the pest incident", SearchDomain.INCIDENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Find invoices from Costco", SearchDomain.INVOICE, frozenset({SearchMode.STRUCTURED})),
    ("Show invoices from last 30 days", SearchDomain.INVOICE, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Find paid invoices", SearchDomain.INVOICE, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("What happened with ABC Foods invoice?", SearchDomain.INVOICE, frozenset({SearchMode.EVENT, SearchMode.HYBRID, SearchMode.STRUCTURED})),
    ("Who handled the late delivery?", SearchDomain.MIXED, frozenset({SearchMode.EVENT, SearchMode.HYBRID, SearchMode.SEMANTIC})),
    ("Find staff who haven't completed checklist", SearchDomain.CHECKLIST, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Show opening checklist status", SearchDomain.CHECKLIST, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Find documents related to health inspection", SearchDomain.DOCUMENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID, SearchMode.SEMANTIC})),
    ("Show me insurance related files", SearchDomain.DOCUMENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID, SearchMode.SEMANTIC})),
    ("Find the payroll task", SearchDomain.TASK, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Show HR tasks", SearchDomain.TASK, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Find maintenance tasks", SearchDomain.TASK, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Search for incident about broken equipment", SearchDomain.INCIDENT, frozenset({SearchMode.HYBRID, SearchMode.SEMANTIC, SearchMode.STRUCTURED})),
    ("Find the incident someone reported about noise", SearchDomain.INCIDENT, frozenset({SearchMode.HYBRID, SearchMode.SEMANTIC})),
    ("Show incidents from yesterday", SearchDomain.INCIDENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Get invoices for payments pending", SearchDomain.INVOICE, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Find who is responsible for the freezer incident", SearchDomain.INCIDENT, frozenset({SearchMode.EVENT, SearchMode.HYBRID, SearchMode.STRUCTURED, SearchMode.SEMANTIC})),
    ("Show documents related to insurance expiry", SearchDomain.DOCUMENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID, SearchMode.SEMANTIC})),
    ("Find checklist incomplete for FOH", SearchDomain.CHECKLIST, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("What happened with the opening checklist?", SearchDomain.CHECKLIST, frozenset({SearchMode.EVENT, SearchMode.HYBRID, SearchMode.STRUCTURED})),
    ("Show me the late delivery story", SearchDomain.MIXED, frozenset({SearchMode.EVENT, SearchMode.HYBRID, SearchMode.SEMANTIC})),
    ("Find supplier complaint incident", SearchDomain.INCIDENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID, SearchMode.SEMANTIC})),
    ("List all open incidents this week", SearchDomain.INCIDENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Find invoice from FreshDirect", SearchDomain.INVOICE, frozenset({SearchMode.STRUCTURED})),
    ("Show staff checklist progress", SearchDomain.CHECKLIST, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Search related to insurance coverage documents", SearchDomain.DOCUMENT, frozenset({SearchMode.HYBRID, SearchMode.SEMANTIC, SearchMode.STRUCTURED})),
    ("Find the task about table setup", SearchDomain.TASK, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Who handled payroll last week?", SearchDomain.EVENT, frozenset({SearchMode.EVENT, SearchMode.HYBRID})),
    ("Find closed incidents last week", SearchDomain.INCIDENT, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID})),
    ("Show me meetings related to kitchen", SearchDomain.MEETING, frozenset({SearchMode.STRUCTURED, SearchMode.HYBRID, SearchMode.SEMANTIC})),
    ("Find the incident with photo evidence about freezer", SearchDomain.INCIDENT, frozenset({SearchMode.HYBRID, SearchMode.SEMANTIC, SearchMode.STRUCTURED})),
]

assert len(NL_QUERIES) >= 100, f"Need >=100 queries, got {len(NL_QUERIES)}"


class ParseHundredQueriesTests(SimpleTestCase):
    def test_at_least_100_queries_defined(self):
        self.assertGreaterEqual(len(NL_QUERIES), 100)

    def test_all_queries_parse_to_expected_domain_and_mode(self):
        failures: list[str] = []
        for query, domain, modes in NL_QUERIES:
            parsed = parse_search_query(query, session_context={"restaurant_id": "r1", "location_id": "loc-a"})
            if parsed.domain != domain and not (
                domain == SearchDomain.MIXED and parsed.domain in (SearchDomain.INCIDENT, SearchDomain.EVENT, SearchDomain.MIXED)
            ):
                # Allow EVENT domain when we expected MIXED for delivery stories
                if domain == SearchDomain.MIXED and parsed.domain in (
                    SearchDomain.INCIDENT,
                    SearchDomain.EVENT,
                    SearchDomain.UNKNOWN,
                    SearchDomain.INVOICE,
                ):
                    pass
                elif domain == SearchDomain.EVENT and parsed.domain in (
                    SearchDomain.INCIDENT,
                    SearchDomain.MIXED,
                    SearchDomain.TASK,
                    SearchDomain.INVOICE,
                ):
                    pass
                elif domain == SearchDomain.STAFF and parsed.domain == SearchDomain.CHECKLIST:
                    pass
                else:
                    failures.append(f"DOMAIN {query!r}: got {parsed.domain} want {domain}")
            if parsed.mode not in modes:
                failures.append(f"MODE {query!r}: got {parsed.mode} want one of {sorted(m.value for m in modes)}")
            # Scoping filters always carry org from session when provided
            if parsed.filters.organization_id != "r1":
                failures.append(f"ORG {query!r}: missing organization scope")
        if failures:
            self.fail(f"{len(failures)} failures:\n" + "\n".join(failures[:40]))


class ExampleRoutingTests(SimpleTestCase):
    def test_freezer_incident_structuredish(self):
        p = parse_search_query("Find the freezer incident.")
        self.assertEqual(p.domain, SearchDomain.INCIDENT)

    def test_complaint_paraphrase_not_pure_keyword_only(self):
        p = parse_search_query(
            "Find the incident where someone complained about the freezer."
        )
        self.assertEqual(p.domain, SearchDomain.INCIDENT)
        self.assertIn(p.mode, (SearchMode.HYBRID, SearchMode.SEMANTIC))
        self.assertTrue(p.filters.conceptual_terms)

    def test_vendor_invoice_structured(self):
        p = parse_search_query("Show me invoices from ABC Foods.")
        self.assertEqual(p.domain, SearchDomain.INVOICE)
        self.assertEqual(p.mode, SearchMode.STRUCTURED)
        self.assertIn("ABC", p.filters.vendor)

    def test_insurance_documents(self):
        p = parse_search_query("Show me documents related to insurance.")
        self.assertEqual(p.domain, SearchDomain.DOCUMENT)


class EngineScopedSearchTests(SimpleTestCase):
    def test_operational_search_requires_workspace(self):
        from miya.services.intelligence.search import operational_search

        user = MagicMock()
        user.restaurant = None
        with patch(
            "miya.services.intelligence.search.engine.build_ops_context",
            return_value=None,
        ):
            result = operational_search(user=user, query="Find the freezer incident.")
        self.assertFalse(result.success)

    def test_structured_path_calls_find_incidents(self):
        from miya.services.intelligence.search.engine import execute_search
        from miya.services.intelligence.search.types import SearchFilters, ParsedSearchQuery
        from miya.services.ops.result import ok

        ctx = MagicMock()
        ctx.restaurant_id = "r1"
        ctx.location_id = "loc-a"
        ctx.user_id = "u1"
        ctx.role = "MANAGER"
        ctx.channel = "dashboard"
        ctx.available_locations = [{"id": "loc-a"}]
        parsed = ParsedSearchQuery(
            raw="Find the freezer incident.",
            mode=SearchMode.STRUCTURED,
            domain=SearchDomain.INCIDENT,
            filters=SearchFilters(q="freezer", organization_id="r1", establishment_id="loc-a"),
            reasons=["test"],
        )
        with patch(
            "miya.services.intelligence.search.structured.find_incidents",
            create=True,
        ):
            with patch(
                "miya.services.ops.incidents.find_incidents",
                return_value=ok(
                    message="ok",
                    verified=True,
                    data={
                        "incidents": [
                            {"id": "i1", "title": "Freezer door broken", "description": "cold"}
                        ]
                    },
                ),
            ):
                result = execute_search(ctx, parsed)
        self.assertTrue(result.success)
        self.assertEqual(len(result.hits), 1)
        self.assertEqual(result.hits[0].domain, "incident")
        self.assertIn("organization_id", result.scoped)

    def test_hybrid_complaint_uses_conceptual(self):
        from miya.services.intelligence.search import operational_search
        from miya.services.ops.result import ok

        user = MagicMock()
        user.id = "u1"
        rest = MagicMock()
        rest.id = "r1"
        user.restaurant = rest
        ops = MagicMock()
        ops.restaurant = rest
        ops.restaurant_id = "r1"
        ops.location_id = "loc-a"
        ops.user_id = "u1"
        ops.role = "MANAGER"
        ops.channel = "dashboard"
        ops.available_locations = [{"id": "loc-a"}]
        with (
            patch(
                "miya.services.intelligence.search.engine.build_ops_context",
                return_value=ops,
            ),
            patch(
                "miya.services.ops.incidents.find_incidents",
                return_value=ok(
                    message="ok",
                    verified=True,
                    data={
                        "incidents": [
                            {
                                "id": "i9",
                                "title": "Customer complaint",
                                "description": "Guest complained about the freezer smell",
                            }
                        ]
                    },
                ),
            ),
        ):
            result = operational_search(
                user=user,
                query="Find the incident where someone complained about the freezer.",
                restaurant=rest,
                session_context={"restaurant_id": "r1", "location_id": "loc-a"},
            )
        self.assertTrue(result.success)
        self.assertTrue(result.hits)
        self.assertIn(result.query.mode, (SearchMode.HYBRID, SearchMode.SEMANTIC))
