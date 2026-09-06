"""Single source of truth for Astro Modeler product and development build identity."""

PRODUCT_VERSION = (0, 1, 0)
BUILD_NUMBER = 3
FULL_VERSION = ".".join(str(part) for part in (*PRODUCT_VERSION, BUILD_NUMBER))
