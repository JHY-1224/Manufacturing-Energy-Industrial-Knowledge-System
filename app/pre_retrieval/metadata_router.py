from .query_transformer import QueryTransformer


class MetadataRouter:
    def route(self, query: str, explicit_filters: dict | None = None) -> dict:
        routed = QueryTransformer().transform(query)
        routed.update({key: value for key, value in (explicit_filters or {}).items() if value is not None and value != "" and value != "all"})
        return routed
