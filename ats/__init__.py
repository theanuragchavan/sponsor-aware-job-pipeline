"""Direct ingestion from employers' own applicant-tracking job boards.

Adzuna is keyword-first: search for terms and hope the employer sponsors. This
package is the other direction — pull straight from a known employer's board.
The data is better too: the full job description, the real apply URL on the
company's own site rather than an aggregator redirect, and (Greenhouse) a
genuine application deadline.

Layout mirrors the existing separation:
    clients.py   HTTP and parsing, nothing else
    mapping.py   raw payload -> tracker row
    boards.py    the curated registry of which employer uses which board
"""
