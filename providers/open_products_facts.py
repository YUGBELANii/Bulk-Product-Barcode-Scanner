from .http_provider import FactsProvider

class OpenProductsFactsProvider(FactsProvider):
    source = "OpenProductsFacts"
    base_url = "https://world.openproductsfacts.org"
