"""Enforce imported local-only material policy before any model request."""
import ipaddress
from urllib.parse import urlsplit

from app.runtime.models import RuntimeAdapterError
from app.knowledge.catalog import base_policy


def ensure_local_only(runtime, records, require_local=False):
    if runtime.mode!='configured' or not (require_local or any(item.metadata.get('allow_cloud_upload') is False or base_policy(item.metadata.get('knowledge_base_id')) for item in records)):
        return
    for adapter in (runtime.embedding,runtime.reranker,runtime.chain):
        endpoint=getattr(adapter,'endpoint',None)
        if endpoint is None:raise RuntimeAdapterError('local_only_material','privacy')
        host=urlsplit(endpoint.url).hostname or ''
        try:local=ipaddress.ip_address(host).is_loopback
        except ValueError:local=host.lower()=='localhost'
        if not local:raise RuntimeAdapterError('local_only_material','privacy')
