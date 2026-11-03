"""Download explicitly selected public technical sources; preserve immutable receipts."""
import hashlib
import ipaddress
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
from datetime import datetime, timezone
try:from .http_read import read_complete
except ImportError:from http_read import read_complete


def public_url(url):
    value=urlsplit(url)
    if value.scheme not in {'http','https'} or not value.hostname or value.username or value.password:
        raise ValueError('Expected a public HTTP(S) source')
    host=value.hostname.lower()
    if host in {'localhost','localhost.localdomain'} or host.endswith(('.local','.internal')):
        raise ValueError('Local addresses are not collection sources')
    try:address=ipaddress.ip_address(host)
    except ValueError:address=None
    if address and not address.is_global:raise ValueError('Private addresses are not collection sources')
    return url


class PublicRedirect(HTTPRedirectHandler):
    def redirect_request(self,request,fp,code,msg,headers,url):
        public_url(url)
        return super().redirect_request(request,fp,code,msg,headers,url)


def download(root, item):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', item['id']):
        raise ValueError('Invalid source identifier')
    folder=Path(root)/'sources'/item['id'];folder.mkdir(parents=True,exist_ok=True)
    receipt=folder/'receipt.json'
    if receipt.exists():
        value=json.loads(receipt.read_text());path=(Path(root)/value['file']).resolve()
        if not path.is_relative_to(Path(root).resolve()) or not path.is_file():
            raise ValueError('Saved source is missing or escapes collection')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=value['sha256'] or len(raw)!=value['bytes']:
            raise ValueError('Saved source checksum changed')
        if value['url']!=item['url']:
            raise ValueError('Source identifier already belongs to another URL')
        return value
    public_url(item['url'])
    request=Request(item['url'],headers={'User-Agent':'IndustrialKnowledgeReference/1.0 (public technical documentation; local reference)'})
    with build_opener(PublicRedirect()).open(request,timeout=40) as response:
        url=public_url(response.geturl());host=urlsplit(url).hostname
        if not any(host==h or host.endswith('.'+h) for h in item['allowed_hosts']):
            raise ValueError('Resolved outside explicitly selected publisher: '+host)
        if int(response.headers.get('Content-Length','0') or 0)>80*1024*1024:raise ValueError('Source exceeds download budget')
        raw=read_complete(response,80*1024*1024)
        if len(raw)>80*1024*1024:raise ValueError('Source exceeds download budget')
        content_type=response.headers.get('Content-Type','')
    suffix='.pdf' if raw.startswith(b'%PDF') else '.docx' if raw.startswith(b'PK') and ('word' in content_type or urlsplit(url).path.endswith('.docx')) else '.html'
    if raw.startswith(b'PK') and suffix!='.docx':suffix='.zip'
    if raw.startswith(bytes.fromhex('D0CF11E0A1B11AE1')):suffix='.doc'
    if content_type.split(';')[0] in {'image/png','image/jpeg','image/gif','image/webp','image/svg+xml'}:
        suffix={'image/png':'.png','image/jpeg':'.jpg','image/gif':'.gif','image/webp':'.webp','image/svg+xml':'.svg'}[content_type.split(';')[0]]
    digest=hashlib.sha256(raw).hexdigest();path=folder/('source-'+digest[:16]+suffix)
    if not path.exists():path.write_bytes(raw)
    value={**item,'resolved_url':url,'sha256':digest,'bytes':len(raw),'content_type':content_type,'file':str(path.relative_to(root)),
           'downloaded_at':datetime.now(timezone.utc).isoformat(),'download_status':'saved','allow_public_redistribution':False,
           'allow_training':False,'allow_cloud_upload':False,'review_status':'candidate_pending_content_review'}
    receipt.write_text(json.dumps(value,ensure_ascii=False,indent=2));return value
