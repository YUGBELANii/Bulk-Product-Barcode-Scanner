from .http_provider import FactsProvider

class OpenBeautyFactsProvider(FactsProvider):
    source = "OpenBeautyFacts"
    base_url = "https://world.openbeautyfacts.org"
