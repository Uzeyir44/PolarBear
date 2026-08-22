"""Service layer — business operations that span more than one router.

Competition completion/expiration lives here (see competition_expiration.py) so
the manual endpoint, the automatic background sweeper, and tests all share one
winner-calculation / completion implementation.
"""