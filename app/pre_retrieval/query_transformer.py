class QueryTransformer:
    def transform(self, query: str) -> dict:
        filters: dict[str, str] = {}
        domain_terms = {
            "风电功率预测": "风电功率预测",
            "风电": "风电故障诊断",
            "塔架": "风电故障诊断",
            "负荷": "区域负荷预测",
            "功率预测": "风电功率预测",
            "储能": "储能EMS",
            "SOC": "储能EMS",
            "SOH": "储能EMS",
            "Agent": "Agent案例库",
        }
        for term, domain in domain_terms.items():
            if term.lower() in query.lower():
                filters["domain"] = domain
                break
        # Chunk structure (Child/Table/Summary) is not a business intent.
        # Infer only a domain hint; explicit user filters remain authoritative.
        return filters
