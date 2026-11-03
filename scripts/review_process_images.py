"""Create numbered contact sheets for actual visual review of process-manufacturing candidates."""
import argparse,json,sqlite3,textwrap
from pathlib import Path
from PIL import Image,ImageOps,ImageDraw,ImageFont


def sheets(root,category):
 root=Path(root);db=sqlite3.connect('file:'+str(root/'collection.sqlite3')+'?mode=ro',uri=True);rows=[json.loads(r[0]) for r in db.execute('select payload from images where category_id=? order by rowid',(category,))];db.close()
 out=root/'visual-review'/category;out.mkdir(parents=True,exist_ok=True);(out/'rows.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
 font_path=Path('/System/Library/Fonts/STHeiti Medium.ttc');font=ImageFont.truetype(str(font_path),17) if font_path.exists() else ImageFont.load_default()
 results=[]
 for start in range(0,len(rows),20):
  batch=rows[start:start+20];canvas=Image.new('RGB',(1600,((len(batch)+3)//4)*340),'white');draw=ImageDraw.Draw(canvas)
  for offset,row in enumerate(batch):
   index=start+offset+1;x=(offset%4)*400;y=(offset//4)*340
   with Image.open(root/row['file']) as im:
    picture=ImageOps.contain(ImageOps.exif_transpose(im).convert('RGB'),(390,260));canvas.paste(picture,(x+(390-picture.width)//2,y+(260-picture.height)//2))
   draw.text((x+8,y+264),f'#{index}  {row["width"]}x{row["height"]}  {row["saved_kind"]}',font=font,fill='#0b2440')
   for line_index,line in enumerate(textwrap.wrap(row['caption'],width=22)[:3]):draw.text((x+8,y+286+line_index*17),line,font=font,fill='#243c52')
  file=out/f'page-{start//20+1:02d}.png';canvas.save(file);results.append(str(file))
 print(json.dumps({'category':category,'images':len(rows),'pages':results},ensure_ascii=False));return rows

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--category',required=True);a=p.parse_args();sheets(a.output,a.category)
