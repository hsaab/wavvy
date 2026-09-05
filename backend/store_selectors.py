"""CSS and text selectors for Beatport automation.

Named ``store_selectors`` to avoid shadowing Python's stdlib ``selectors``
module.  All selectors live here so cart_builder.py stays free of magic
strings.  Update these when site layouts change — the cart builder logic
shouldn't need to change.
"""

# ---------------------------------------------------------------------------
# Beatport
# ---------------------------------------------------------------------------

BEATPORT_BASE_URL = "https://www.beatport.com"
BEATPORT_AUTH_URL = "https://account.beatport.com"
BEATPORT_CART_URL = "https://www.beatport.com/cart"

# Homepage login control — visible "Log In" text on an a or button
BP_LOGIN_TRIGGER = 'a:has-text("Log In"), button:has-text("Log In")'

# Auth page (account.beatport.com) — username/password form
BP_EMAIL_INPUT = "#username"
BP_PASSWORD_INPUT = "#password"
BP_LOGIN_SUBMIT = 'button:has-text("Log In")'
BP_LOGGED_IN_INDICATOR = ".account_avatar"

# Search (fallback when we have no direct URL)
BP_SEARCH_URL = "https://www.beatport.com/search?q={query}"
BP_SEARCH_RESULT_LINK = '.track-title a, a[class*="TrackTitle"]'

# Track page — format selection and add-to-cart
BP_FORMAT_DROPDOWN = (
    'button[class*="format"], '
    '[data-testid="format-selector"], '
    'button[class*="Format"]'
)
BP_WAV_OPTION = 'text=WAV'
BP_ADD_TO_CART = (
    'button[aria-label*="Add track"], '
    'button[class*="AddToCart"], '
    'button[class*="PriceButton"], '
    'button:has-text("Add to Cart"), '
    '[data-testid="add-to-cart"]'
)
BP_PRICE_WAV = 'text=/WAV.*\\$/'

# Cart page
BP_CART_ITEM = '[class*="cart-item"], [class*="CartItem"]'
BP_CART_TOTAL = '[class*="total"], [class*="Total"]'

# Cookie / GDPR consent
BP_COOKIE_ACCEPT = (
    'button:has-text("Accept"), '
    'button:has-text("I Accept"), '
    'button[id*="accept"]'
)


# ---------------------------------------------------------------------------
# Shared timing constants
# ---------------------------------------------------------------------------

NAV_TIMEOUT_MS = 30_000
ACTION_DELAY_SEC = 1.5
LOGIN_WAIT_SEC = 3.0
PAGE_LOAD_WAIT_SEC = 2.0
# Time for you to pass Cloudflare and log in in the Chrome window.
MANUAL_LOGIN_TIMEOUT_MS = 300_000
