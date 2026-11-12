from .http_provider import FactsProvider

class OpenFoodFactsProvider(FactsProvider):
    source = "OpenFoodFacts"
    base_url = "https://world.openfoodfacts.org"
