"""Optional local query translation. Translation is a search aid, never evidence."""
import re

def should_translate(question,scoped):
    # Prefer an explicitly named Chinese source to translating it because an
    # unrelated English document happens to share the selected industry.
    chinese=lambda s:''.join(re.findall(r'[\u3400-\u9fff]',s))
    q=chinese(question);grams={q[i:i+4] for i in range(len(q)-3)}
    for c in scoped:
        if c.metadata.get('source_language')!='zh':continue
        title=chinese(c.metadata.get('source_title',''));named={title[i:i+4] for i in range(len(title)-3)}
        if len(grams & named)>=3:return False
    return bool(re.search(r'[\u3400-\u9fff]',question)) and not re.search(r'查找.*图片|定位图片|来源标题|来源介绍',question) and any(c.metadata.get('source_language')=='en' for c in scoped)
