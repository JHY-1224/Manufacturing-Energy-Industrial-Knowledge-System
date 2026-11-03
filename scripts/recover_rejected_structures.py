"""Inspectable recovery attempts and source graphics; never overwrite original text."""
import argparse,json,sys,hashlib
from pathlib import Path
from urllib.parse import unquote,urlparse
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.preprocessors.structured_tables import pdf_table

def recover_pdf(original,page_number):
 import fitz
 with fitz.open(original) as doc:
  page=doc[page_number-1];out=[]
  for strategy in ['lines_strict','text']:
   kwargs={'strategy':strategy}
   if strategy=='text':kwargs.update(min_words_vertical=3,min_words_horizontal=1)
   for i,detected in enumerate(page.find_tables(**kwargs).tables):
    if detected.row_count<2 or detected.col_count<2:continue
    table=pdf_table(page,detected,f'p{page_number:04d}_{strategy}_{i+1:02d}');out.append(table)
  return out

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--rejected',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False);(a.output/'attempts').mkdir();items=json.loads(a.rejected.read_text());seen={}
 for item in items:
  e=json.loads(Path(item['source_record']).read_text());original=Path(unquote(urlparse(e['original_uri']).path));page=e['layout'].get('page')
  if original.suffix.lower()!='.pdf':continue
  key=(str(original),page)
  if key not in seen:seen[key]=recover_pdf(original,page)
  record={'candidate_id':item['candidate_id'],'source_sha256':hashlib.sha256(original.read_bytes()).hexdigest(),'page':page,'attempts':seen[key]}
  (a.output/'attempts'/(item['candidate_id']+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2));print(item['candidate_id'],[(x['row_count'],x['col_count'],x['source_method'],x['flags']) for x in record['attempts']],flush=True)
if __name__=='__main__':main()
