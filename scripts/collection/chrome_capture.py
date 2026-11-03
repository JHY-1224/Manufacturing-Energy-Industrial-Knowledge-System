"""Capture rendered Google Images cards from a user-selected Chrome tab on macOS."""
import json
from pathlib import Path
import subprocess
import time
from urllib.parse import urlsplit,parse_qs


class BrowserBlocked(RuntimeError):pass


class ChromeCapture:
    def __init__(self,tab_id):self.tab_id=int(tab_id)

    def js(self,code):
        script=f'tell application "Google Chrome" to execute tab id {self.tab_id} of window 1 javascript '+json.dumps(code,ensure_ascii=False)
        try:result=subprocess.run(['osascript','-e',script],capture_output=True,text=True,timeout=40)
        except subprocess.TimeoutExpired as exc:raise BrowserBlocked('Chrome page execution timed out during navigation; completed files are retained') from exc
        if result.returncode:raise BrowserBlocked(result.stderr.strip())
        return json.loads(result.stdout)

    def navigate(self,url):
        parts=urlsplit(url)
        if parts.scheme!='https' or parts.hostname!='www.google.com' or parts.path!='/search':raise ValueError('Only observed Google search links are navigated')
        query=parse_qs(parts.query).get('q',[''])[0]
        script=f'tell application "Google Chrome" to set URL of tab id {self.tab_id} of window 1 to '+json.dumps(url,ensure_ascii=False)
        current=self.js('JSON.stringify(location.href)')
        if parse_qs(urlsplit(current).query).get('q',[''])[0]!=query:
            subprocess.run(['osascript','-e',script],check=True,capture_output=True,timeout=20)
            time.sleep(2.5)
        for _ in range(70):
            state=self.js("JSON.stringify({href:location.href,ready:document.readyState,blocked:location.pathname.startsWith('/sorry')||!!document.querySelector('iframe[src*=recaptcha]'),cards:document.querySelectorAll('div.DeNS1c').length})")
            if state['blocked']:raise BrowserBlocked('Google verification requires user action; collection stopped')
            if parse_qs(urlsplit(state['href']).query).get('q',[''])[0]==query and state['ready']=='complete' and state['cards']>5:
                time.sleep(.8);return
            time.sleep(.5)
        raise BrowserBlocked('Search page did not finish loading usable image cards')

    def capture(self,expected_url=None):
        current=self.js('JSON.stringify(location.href)')
        parts=urlsplit(current)
        if parts.hostname!='www.google.com' or parts.path!='/search':raise BrowserBlocked('Controlled tab left Google Images; progress retained')
        if expected_url and parse_qs(parts.query).get('q')!=parse_qs(urlsplit(expected_url).query).get('q'):
            raise BrowserBlocked('Controlled search tab changed query; progress retained for resume')
        # These selectors and fields were inspected in the user's actual Google page.
        code="""Array.from(document.querySelectorAll('div.DeNS1c')).forEach(e=>e.dispatchEvent(new MouseEvent('mouseover',{bubbles:true})));JSON.stringify({search_url:location.href,title:document.title,collected_at:new Date().toISOString(),chips:Array.from(document.querySelectorAll('a.nPDzT[aria-label]')).map(a=>({name:a.innerText.trim(),url:a.href,label:a.getAttribute('aria-label')})).filter(a=>a.name&&a.url.includes('/search?')),images:Array.from(document.querySelectorAll('a[href*=\"/imgres?\"]')).map(a=>{const i=a.querySelector('img');const u=new URL(a.href);return i?{id:u.searchParams.get('tbnid'),caption:i.alt,original_url:u.searchParams.get('imgurl'),source_page:u.searchParams.get('imgrefurl'),thumbnail_url:i.currentSrc||i.src,search_url:location.href,google_result_link:a.href,width_hint:u.searchParams.get('w'),height_hint:u.searchParams.get('h')}:null}).filter(i=>i&&i.caption&&i.original_url&&i.source_page)})"""
        return self.js(code)

    def scroll(self):self.js('JSON.stringify((window.scrollBy(0,innerHeight*1.6),true))')


def save_snapshot(directory,value):
    from uuid import uuid4
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    path=directory/(uuid4().hex+'.json')
    path.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
    return path
