"""Qt widgets for the marketplace.

The dialog lives here, separated from the controller so the
non-Qt logic can be tested without spinning up a window.
"""

from .marketplace_dialog import MarketplaceDialog

__all__ = ["MarketplaceDialog"]
