class MetadataIndex:
    @staticmethod
    def match(metadata: dict, filters: dict) -> bool:
        return all(value is None or value == "" or value == "all" or metadata.get(key) == value for key, value in filters.items())
