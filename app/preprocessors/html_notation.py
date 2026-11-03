"""Retain script notation and explicit bibliography roles in readable HTML text."""
from urllib.parse import urlsplit

def citation_link(href='',role=''):
 try:value=urlsplit(href or '')
 except ValueError:return role=='doc-biblioref'
 return role=='doc-biblioref' or '/citations/' in value.path or value.fragment.lower().startswith(('ref-','bib-','reference-'))

def readable_html(node):
 def visit(element,in_reference=False):
  tag=element.tag.lower() if isinstance(element.tag,str) else ''
  if tag in {'script','style','iframe','template','noscript'}:return ''
  reference=tag=='a' and citation_link(element.get('href',''),element.get('role',''))
  text=(element.text or '')+''.join(visit(c,in_reference or reference)+(c.tail or '') for c in element)
  if reference:return '〔参考文献编号：'+text+'〕'
  if tag in {'sup','sub'} and not in_reference:return ('^' if tag=='sup' else '_')+'('+text+')'
  return text
 return ' '.join(visit(node).split())
