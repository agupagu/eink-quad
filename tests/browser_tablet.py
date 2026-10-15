from playwright.sync_api import sync_playwright
from pathlib import Path
import json
import os
import atexit
import struct
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

# Deterministic browser-only fixtures; no live sports/network dependency. The
# production font route is covered separately by backend authentication tests.
FONT_FILES={f'{family}-{weight}.ttf' for family in ('Barlow','BarlowCondensed')
            for weight in ('Regular','Medium','SemiBold','Bold')}
REPO=Path(__file__).resolve().parents[1]

def start_fixture_server():
    def chunk(kind, data):
        return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data))
    png=(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',800,480,8,0,0,0,0))
         +chunk(b'IDAT',zlib.compress((b'\0'+b'\xff'*800)*480))+chunk(b'IEND',b''))
    svg=b'<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48"><circle cx="24" cy="24" r="20" fill="black"/></svg>'
    now=int(time.time())
    sports=[]
    for key,label,opponent in [('india_men','India Men','Australia'),('man_utd','Manchester United','Liverpool'),
                               ('cal_fb','Cal Bears','Stanford'),('f1','Formula 1','United States GP')]:
        game={'opponent':opponent,'start':now+3600,'home':True,'opp_logo':'/fixture-logo.svg',
              'tv':'Sports TV','odds':{'details':'Fixture line','total':2.5,'win_pct':55}}
        sports.append({'key':key,'label':label,'record':'6–2','logo':'/fixture-logo.svg',
                       'news':'Browser fixture headline','live':None,
                       'last':dict(game,start=now-86400,us=2,them=1,result='W'), 'next':game})

    # Long-form non-scoreboard events must not be ellipsized or labelled "vs".
    sports[0]['last'].update(us=None,them=None,
        detail='India 387/6 (50 overs), Australia 384 all out (49.3 overs)')
    sports[3]['last'].update(us=None,them=None,detail='Final',
        opponent='Singapore Airlines Singapore Grand Prix')
    sports[3]['next']['opponent']='MSC Cruises United States Grand Prix'

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_GET(self):
            url=urlsplit(self.path)
            query=parse_qs(url.query)
            if url.path!='/fixture-logo.svg' and query.get('k')!=['local-test']:
                self.send_error(401)
                return
            headers={}
            if url.path=='/tablet':
                content=(REPO/'server/web/ipad.html').read_bytes()
                mime='text/html; charset=utf-8'
            elif url.path.startswith('/tablet/fonts/') and url.path.removeprefix('/tablet/fonts/') in FONT_FILES:
                content=(REPO/'server/fonts'/url.path.removeprefix('/tablet/fonts/')).read_bytes()
                mime='font/ttf'
            elif url.path in ('/tablet/india-cricket.svg','/fixture-logo.svg'):
                content,mime=svg,'image/svg+xml'
            elif url.path=='/api/tablet/sports':
                content,mime=json.dumps({'sports':sports}).encode(),'application/json'
            elif url.path=='/api/tablet/frame':
                version='fixture-'+query.get('page',['quad'])[0]
                headers={'X-Version':version}
                if query.get('v')==[version]:
                    self.send_response(204)
                    self.send_header('X-Version',version)
                    self.end_headers()
                    return
                content,mime=png,'image/png'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(len(content)))
            for name,value in headers.items():
                self.send_header(name,value)
            self.end_headers()
            self.wfile.write(content)

    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    atexit.register(server.server_close)
    atexit.register(server.shutdown)
    return f'http://127.0.0.1:{server.server_port}/tablet?k=local-test'

ROOT=Path(os.environ.get('QUAD_TEST_ARTIFACTS', '.test-artifacts'))
ROOT.mkdir(parents=True, exist_ok=True)
URL=os.environ.get('QUAD_TEST_URL') or start_fixture_server()
with sync_playwright() as p:
    browser=getattr(p,os.environ.get('QUAD_TEST_BROWSER','chromium')).launch(headless=True)
    page=browser.new_page(viewport={'width':1180,'height':820},device_scale_factor=1)
    errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    google_requests=[]
    font_responses=[]
    credential_leaks=[]
    def check_external_request(request):
        url=urlsplit(request.url)
        if url.netloc!=urlsplit(URL).netloc:
            referrer=urlsplit(request.headers.get('referer',''))
            if 'k' in parse_qs(url.query) or 'k' in parse_qs(referrer.query):
                credential_leaks.append(url.hostname)
    page.on('request',check_external_request)
    def block_google(route):
        google_requests.append(urlsplit(route.request.url).hostname)
        route.abort()
    page.route('https://fonts.googleapis.com/**',block_google)
    page.route('https://fonts.gstatic.com/**',block_google)
    def capture_font(response):
        url=urlsplit(response.url)
        if url.path.startswith('/tablet/fonts/'):
            # Never include viewer credentials in printed diagnostics.
            font_responses.append({'file':url.path.rsplit('/',1)[-1], 'status':response.status,
                                   'same_origin':url.netloc==urlsplit(URL).netloc,
                                   'authorized':parse_qs(url.query).get('k')==parse_qs(urlsplit(URL).query).get('k')})
    page.on('response',capture_font)
    page.goto(URL,wait_until='networkidle')
    def wake():
        page.evaluate('()=>{document.body.classList.remove("chrome-hidden");clearTimeout(chromeTimer);}')
    wake()
    page.wait_for_selector('.frame',timeout=30000)
    page.wait_for_timeout(900)
    assert page.locator('#quadrants').is_visible()
    assert page.locator('nav [data-page]').count()==6
    assert page.locator('nav [data-page]').evaluate_all('bs=>bs.map(b=>b.dataset.page)')==['quad','weather','news','sports','crypto','sky']
    assert page.locator('#quadrants button').evaluate_all('bs=>bs.map(b=>b.dataset.page)')==['weather','news','sports','crypto']
    page.screenshot(path=str(ROOT/'eink-home-local.png'))
    checks=['home loaded','six nav buttons in order','home quadrant mapping ends with crypto']
    def fills_surface(selector):
        geometry=page.locator(selector).evaluate("""node=>{
            const rect=node.getBoundingClientRect(), surface=document.querySelector('#surface').getBoundingClientRect();
            return {width:rect.width,height:rect.height,x:rect.x,y:rect.y,
                    surfaceWidth:surface.width,surfaceHeight:surface.height,surfaceX:surface.x,surfaceY:surface.y,
                    scrollWidth:node.scrollWidth,clientWidth:node.clientWidth};
        }""")
        assert abs(geometry['width']-geometry['surfaceWidth'])<1,geometry
        assert abs(geometry['height']-geometry['surfaceHeight'])<1,geometry
        assert abs(geometry['x']-geometry['surfaceX'])<1,geometry
        assert abs(geometry['y']-geometry['surfaceY'])<1,geometry
        assert geometry['scrollWidth']<=geometry['clientWidth']+1,geometry
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    def font_style(selector,family,weight):
        style=page.locator(selector).first.evaluate("""node=>{
            const s=getComputedStyle(node);
            return {family:s.fontFamily,weight:s.fontWeight,size:parseFloat(s.fontSize)};
        }""")
        assert style['family'].split(',')[0].strip('" ')==family,style
        assert style['weight']==str(weight),style
        return style
    page.evaluate('''window.phases=[]; new MutationObserver(()=>window.phases.push(document.querySelector('#stage').className)).observe(document.querySelector('#stage'),{attributes:true,attributeFilter:['class']})''')
    for name in ['weather','news','sky']:
        wake()
        page.locator('nav [data-page='+name+']').click()
        page.wait_for_function('(id)=>document.querySelector(".frame")?.alt.startsWith(id)',arg={'weather':'Weather','news':'News','sky':'Sky'}[name])
        page.wait_for_timeout(800)
        assert page.locator('#stage').get_attribute('class')=='stage'
        checks.append(name+' loaded and refresh settled')
    wake()
    page.locator('nav [data-page=crypto]').click()
    page.wait_for_function('document.querySelector(".frame")?.alt.startsWith("Crypto")')
    page.wait_for_timeout(800)
    assert page.locator('#stage').get_attribute('class')=='stage'
    checks.append('crypto loaded and refresh settled')
    phases=page.evaluate('window.phases')
    for phase in ['phase-invert','phase-black','phase-white']:
        assert any(phase in item for item in phases), phases
    checks.append('real e-ink phase sequence observed')
    page.locator('nav [data-page=sports]').click()
    page.wait_for_function('document.querySelectorAll(".srow").length===4')
    page.wait_for_function('document.querySelector(".board-head")?.innerText.includes("SCORES")')
    page.wait_for_function('!document.querySelector(".rows").innerText.includes("No upcoming event")')
    page.wait_for_timeout(1000)
    # Force every declared face to load: CSS family names alone also pass
    # when the browser silently renders a fallback.
    loaded_fonts=page.evaluate("""async()=>{
        const faces=await Promise.all(['Barlow','Barlow Condensed'].flatMap(family=>
          [400,500,600,700].map(async weight=>{
            const loaded=await document.fonts.load(`${weight} 24px "${family}"`);
            return {family,weight,count:loaded.length,loaded:loaded.every(face=>face.status==='loaded')};
          })));
        await document.fonts.ready;
        return faces;
    }""")
    assert len(loaded_fonts)==8 and all(f['count']==1 and f['loaded'] for f in loaded_fonts),loaded_fonts
    assert {f['file'] for f in font_responses}==FONT_FILES,font_responses
    assert all(f['status']==200 and f['same_origin'] and f['authorized'] for f in font_responses),font_responses
    assert not google_requests,google_requests
    assert not credential_leaks,credential_leaks
    fills_surface('.board')
    f1=page.locator('.srow[data-key=f1]')
    assert 'vs ' not in f1.locator('.scoreline').inner_text()
    assert 'vs ' not in f1.locator('.next-line').inner_text()
    assert f1.locator('.wl').count()==0
    assert page.locator('.srow[data-key=india_men] .wl').count()==0
    for selector in ['.srow[data-key=f1] .scoreline','.srow[data-key=f1] .next-line',
                     '.srow[data-key=india_men] .scoreline']:
        wrapping=page.locator(selector).evaluate("""node=>{
            const s=getComputedStyle(node);
            return s.whiteSpace!=='nowrap' && s.textOverflow!=='ellipsis' && node.scrollWidth<=node.clientWidth+1;
        }""")
        assert wrapping,selector
    checks.append('F1 race names without vs, cricket innings wrap, no cricket/F1 W-L badges')
    font_style('.board-head' ,'Barlow Condensed',700)
    font_style('.who h2','Barlow Condensed',700)
    font_style('.scoreline','Barlow Condensed',600)
    font_style('.next-line','Barlow',600)
    font_style('.record','Barlow Condensed',500)
    # Verify the documented render.py scale at its native 800px width.
    page.set_viewport_size({'width':800,'height':480})
    assert font_style('.who h2','Barlow Condensed',700)['size']==24
    assert font_style('.scoreline','Barlow Condensed',600)['size']==21
    assert font_style('.record','Barlow Condensed',500)['size']==17
    fills_surface('.board')
    for width,height in [(820,1180),(390,844)]:
        page.set_viewport_size({'width':width,'height':height})
        fills_surface('.board')
        for row in page.locator('.srow').all():
            assert row.evaluate('(n)=>n.scrollWidth<=n.clientWidth+1')
        page.screenshot(path=str(ROOT/f'eink-sports-{width}-portrait.png'))
    page.set_viewport_size({'width':1180,'height':820})
    checks.append('eight bundled font faces loaded with Google blocked, authenticated same-origin URLs and no external credential leaks')
    checks.append('sports condensed headings/scores, renderer font sizes, full-width landscape and portrait rows')
    page.screenshot(path=str(ROOT/'eink-sports-local.png'))
    logos=page.locator('.who img').evaluate_all('(imgs)=>imgs.map(i=>({alt:i.alt,url:i.src.split("?")[0],ok:i.complete&&i.naturalWidth>0}))')
    assert len(logos)==4 and all(l['ok'] for l in logos),logos
    checks.append('four sports and logos')
    page.locator('.srow[data-key=india_men]').click()
    page.wait_for_selector('.detail[data-key=india_men]')
    assert page.url.split('?')[1].split('#')[0] == URL.split('?')[1].split('#')[0] if '?' in URL else True
    page.wait_for_timeout(700)
    fills_surface('.detail')
    font_style('.detail-heading h1','Barlow Condensed',700)
    font_style('.detail-grid p','Barlow',500)
    for width,height in [(820,1180),(390,844)]:
        page.set_viewport_size({'width':width,'height':height})
        fills_surface('.detail')
        page.screenshot(path=str(ROOT/f'eink-sports-detail-{width}-portrait.png'))
    page.set_viewport_size({'width':1180,'height':820})
    checks.append('india details, title/body fonts, full-width portrait and token preservation')
    page.keyboard.press('ArrowRight')
    page.wait_for_selector('.detail[data-key=man_utd]')
    page.wait_for_timeout(700)
    wake()
    page.locator('#back').click()
    page.wait_for_selector('.board')
    page.wait_for_timeout(700)
    page.locator('.srow[data-key=india_men]').click()
    page.emulate_media(reduced_motion='reduce')
    page.wait_for_selector('.detail[data-key=india_men]',timeout=3000)
    page.emulate_media(reduced_motion='no-preference')
    wake()
    page.locator('#back').click()
    page.wait_for_selector('.board')
    page.wait_for_timeout(700)
    checks.append('cancelled refresh reconciles sports selection')
    assert page.evaluate('''async()=>{const original=frames.get('quad');const discarded=await rememberFrame('quad',original.blob,'obsolete',()=>false);return discarded===null&&frames.get('quad')===original;}''')
    checks.append('superseded decoder cannot replace frame cache')
    page.evaluate('()=>{document.body.classList.remove("chrome-hidden");chromeSchedule();}')
    page.wait_for_timeout(4400)
    assert page.evaluate('document.body.classList.contains("chrome-hidden")')
    page.evaluate('document.querySelector("#viewport").dispatchEvent(new PointerEvent("pointerdown",{clientX:20,clientY:20}))')
    assert not page.evaluate('document.body.classList.contains("chrome-hidden")')
    checks.append('floating overlays auto-hide on idle and return on edge tap')
    wake()
    page.locator('#settings-open').click()
    page.locator('#paper').select_option('white')
    page.locator('#night').check()
    page.locator('#refresh-style').select_option('none')
    page.locator('#settings-close').click()
    assert page.locator('body').get_attribute('data-night')=='true'
    page.reload(wait_until='networkidle')
    wake()
    assert page.locator('body').get_attribute('data-night')=='true'
    checks.append('display settings persist')
    page.emulate_media(reduced_motion='reduce')
    wake()
    page.locator('#home').click()
    page.wait_for_selector('.frame')
    assert page.locator('#stage').get_attribute('class')=='stage'
    checks.append('reduced motion')
    page.set_viewport_size({'width':820,'height':1180})
    page.wait_for_timeout(300)
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.screenshot(path=str(ROOT/'eink-portrait-local.png'))
    checks.append('portrait no horizontal overflow')
    before=page.locator('.frame').get_attribute('src')
    page.route('**/api/tablet/**', lambda route: route.abort())
    page.locator('#reload').click()
    page.wait_for_function('document.querySelector("#status").innerText.includes("last display retained")')
    assert page.locator('.frame').get_attribute('src')==before
    checks.append('failed fetch retains decoded display')
    page.reload(wait_until='networkidle')
    wake()
    page.wait_for_selector('.frame')
    page.wait_for_function('document.querySelector("#status").innerText.includes("last display retained")')
    checks.append('offline API reload recovers IndexedDB frame')
    page.unroute('**/api/tablet/**')
    page.evaluate('''()=>{window.fetch=(_url,options)=>new Promise((_resolve,reject)=>{options.signal.addEventListener('abort',()=>reject(new DOMException('Injected stalled request','AbortError')),{once:true});});}''')
    wake()
    page.locator('#reload').click()
    page.wait_for_function('document.querySelector("#status").innerText.includes("timed out")',timeout=15000)
    assert page.locator('.frame').count()==1
    checks.append('request timeout reports failure without clearing display')
    page.unroute_all(behavior='ignoreErrors')
    page.wait_for_timeout(300)
    assert not errors, errors
    assert not credential_leaks,credential_leaks
    assert not google_requests,google_requests
    print(json.dumps({'checks':checks,'logos':logos,'fonts':loaded_fonts,'font_responses':font_responses,'errors':errors},indent=2))
    browser.close()
