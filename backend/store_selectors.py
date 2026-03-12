"""CSS and text selectors for Beatport and Traxsource automation.

Named ``store_selectors`` to avoid shadowing Python's stdlib ``selectors``
module.  All selectors live here so cart_builder.py stays free of magic
strings.  Update these when site layouts change — the cart builder logic
shouldn't need to change.
"""

# ---------------------------------------------------------------------------
# Beatport
# ---------------------------------------------------------------------------

BEATPORT_BASE_URL = "https://www.beatport.com"
BEATPORT_LOGIN_URL = "https://www.beatport.com/account/login"
BEATPORT_CART_URL = "https://www.beatport.com/cart"

# Login page
BP_EMAIL_INPUT = 'input[name="username"], input[type="email"]'
BP_PASSWORD_INPUT = 'input[name="password"], input[type="password"]'
BP_LOGIN_BUTTON = 'button[type="submit"]'
BP_LOGGED_IN_INDICATOR = '[class*="account"], a[href*="/account"]'

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
# Traxsource
# ---------------------------------------------------------------------------

TRAXSOURCE_BASE_URL = "https://www.traxsource.com"
TRAXSOURCE_LOGIN_URL = "https://www.traxsource.com/login"
TRAXSOURCE_CART_URL = "https://www.traxsource.com/cart"

# Login page
TS_EMAIL_INPUT = 'input[name="email"], input#email'
TS_PASSWORD_INPUT = 'input[name="password"], input#password'
TS_LOGIN_BUTTON = 'button[type="submit"], input[type="submit"]'
TS_LOGGED_IN_INDICATOR = 'a[href*="/account"], [class*="user-menu"]'

# Search (fallback)
TS_SEARCH_URL = "https://www.traxsource.com/search?term={query}"
TS_SEARCH_RESULT_LINK = '.trk-cell.title a'

# Track page — format selection and add-to-cart
TS_WAV_BUY_BUTTON = (
    'a:has-text("WAV"), '
    'button:has-text("WAV"), '
    '[class*="buy-wav"]'
)
TS_ADD_TO_CART = (
    'button:has-text("Add to Cart"), '
    'a:has-text("Add to Cart"), '
    '[class*="add-cart"]'
)

# Cart page
TS_CART_ITEM = '[class*="cart-item"], .cart-trk'
TS_CART_TOTAL = '[class*="total"], .cart-total'

# Cookie / consent
TS_COOKIE_ACCEPT = (
    'button:has-text("Accept"), '
    'button:has-text("I Agree"), '
    '[class*="cookie"] button'
)


# ---------------------------------------------------------------------------
# Shared timing constants
# ---------------------------------------------------------------------------

NAV_TIMEOUT_MS = 30_000
ACTION_DELAY_SEC = 1.5
LOGIN_WAIT_SEC = 3.0
PAGE_LOAD_WAIT_SEC = 2.0
