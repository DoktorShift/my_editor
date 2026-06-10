"""Qt dialogs and widgets for the Telegram publisher.

All UI modules import from :mod:`publishers.telegram.facade` (for the
singleton facade) and from sibling :mod:`publishers.telegram.*`
modules. Nothing here reaches outside ``publishers/`` - no Nostr
imports, no editor-internal dependencies beyond Qt itself.
"""
