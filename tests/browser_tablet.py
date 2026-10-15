from playwright.sync_api import sync_playwright
from pathlib import Path
import json
import os
import atexit
import struct
import threading
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
    def png(shade):
        return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',800,480,8,0,0,0,0))
                +chunk(b'IDAT',zlib.compress((b'\0'+bytes([shade])*800)*480))+chunk(b'IEND',b''))
    sports=['india_men','man_utd','cal_fb','f1']

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_GET(self):
            url=urlsplit(self.path)
            query=parse_qs(url.query)
            if query.get('k')!=['local-test']:
                self.send_error(401)
                return
            headers={}
            if url.path=='/tablet':
                content=(REPO/'server/web/ipad.html').read_bytes()
                mime='text/html; charset=utf-8'
            elif url.path.startswith('/tablet/fonts/') and url.path.removeprefix('/tablet/fonts/') in FONT_FILES:
                content=(REPO/'server/fonts'/url.path.removeprefix('/tablet/fonts/')).read_bytes()
                mime='font/ttf'
            elif url.path=='/api/tablet/frame':
                sport=query.get('sport',[None])[0]
                if sport is not None and sport not in sports:
                    self.send_error(400)
                    return
                name=query.get('page',['quad'])[0]
                version='4470:'+name+(':'+ (sport or 'all') if name=='sports' else '')
                headers={'X-Version':version}
                if query.get('v')==[version]:
                    self.send_response(204)
                    self.send_header('X-Version',version)
                    self.end_headers()
                    return
                content,mime=png(255 if sport is None else 190+sports.index(sport)*10),'image/png'
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
    api_requests=[]
    frame_responses=[]
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
    def capture_api(request):
        url=urlsplit(request.url)
        if url.path.startswith('/api/tablet/'):
            api_requests.append((url.path,parse_qs(url.query)))
    page.on('request',capture_api)
    page.on('response',lambda response: frame_responses.append(response.status)
            if urlsplit(response.url).path=='/api/tablet/frame' else None)
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
    def wait_frame(identity):
        page.wait_for_function('(id)=>visiblePage===id&&surface.firstElementChild===frames.get(id)?.img',arg=identity)
        page.wait_for_timeout(700)
        assert page.locator('#surface > img.frame').count()==1
        assert page.locator('.frame').evaluate('(n)=>n.complete&&n.naturalWidth===800&&n.naturalHeight===480')
        assert page.locator('.board,.detail,.srow').count()==0
        fills_surface('.frame')
    wait_frame('sports')
    assert page.locator('#sports-targets').is_visible()
    assert page.locator('#quadrants').is_hidden()
    assert page.locator('#sports-targets button').evaluate_all('ns=>ns.map(n=>n.dataset.sport)')==['india_men','man_utd','cal_fb','f1']
    assert page.locator('.frame').evaluate('(n)=>getComputedStyle(n).objectFit')=='contain'
    # Pixel coordinates are relative to the contained bitmap, including portrait letterboxing.
    for width,height in [(1180,820),(800,480),(1600,600),(820,1180),(390,844)]:
        page.set_viewport_size({'width':width,'height':height})
        page.wait_for_timeout(100)
        fw=min(width,height*5/3)
        fh=fw*3/5
        overlay=page.locator('#sports-targets').bounding_box()
        expected={'x':(width-fw)/2,'y':(height-fh)/2+fh*66/480,'width':fw,'height':fh*414/480}
        assert all(abs(overlay[k]-v)<1 for k,v in expected.items()),(overlay,expected)
        for index,row in enumerate(page.locator('#sports-targets button').all()):
            rect=row.bounding_box()
            assert abs(rect['height']-overlay['height']/4)<1
            assert abs(rect['y']-(overlay['y']+index*overlay['height']/4))<1
            assert row.evaluate('(n)=>getComputedStyle(n).backgroundColor')=='rgba(0, 0, 0, 0)'
        fills_surface('.frame')
        page.screenshot(path=str(ROOT/f'eink-sports-{width}x{height}.png'))
    page.set_viewport_size({'width':1180,'height':820})
    checks.append('sports PNG with four transparent contain-fit row targets below y66 in landscape and portrait')
    # Exercise every actual row target, then reuse the overview cache.
    for sport in ['india_men','man_utd','cal_fb','f1']:
        page.locator('#sports-targets [data-sport='+sport+']').click()
        wait_frame('sports/'+sport)
        assert page.locator('#sports-targets').is_hidden()
        assert page.url.endswith('#sports/'+sport)
        assert parse_qs(urlsplit(page.url).query)==parse_qs(urlsplit(URL).query)
        page.keyboard.press('Escape')
        wait_frame('sports')
    assert page.evaluate("['sports','sports/india_men','sports/man_utd','sports/cal_fb','sports/f1'].every(id=>frames.has(id))")
    assert page.evaluate("async()=>!(await sameBlob(frames.get('sports').blob,frames.get('sports/man_utd').blob))")
    checks.append('all row taps load distinct focused PNGs and cache identities; Escape restores overview')
    page.locator('#sports-targets [data-sport=india_men]').click()
    wait_frame('sports/india_men')
    page.keyboard.press('ArrowRight')
    wait_frame('sports/man_utd')
    wake()
    page.locator('#next').click()
    wait_frame('sports/cal_fb')
    page.evaluate("""()=>{const v=document.querySelector('#viewport');
        const start=new Event('touchstart'),end=new Event('touchend');
        Object.defineProperty(start,'touches',{value:[{clientX:500,clientY:300}]});
        Object.defineProperty(end,'changedTouches',{value:[{clientX:300,clientY:300}]});
        v.dispatchEvent(start);v.dispatchEvent(end);} """)
    wait_frame('sports/f1')
    page.keyboard.press('ArrowRight')
    wait_frame('sports/india_men')
    page.keyboard.press('ArrowLeft')
    wait_frame('sports/f1')
    wake()
    page.locator('#back').click()
    wait_frame('sports')
    page.evaluate("location.hash='sports/man_utd'")
    wait_frame('sports/man_utd')
    page.reload(wait_until='networkidle')
    wait_frame('sports/man_utd')
    checks.append('focused arrows, swipe, wraparound, Back, hash and reload navigation')
    # Focus and overview must each survive an offline API reload independently.
    page.route('**/api/tablet/**',lambda route:route.abort())
    for identity in ['sports/man_utd','sports','sports/f1']:
        page.goto(URL+'#'+identity,wait_until='networkidle')
        wait_frame(identity)
        page.wait_for_function('document.querySelector("#status").innerText.includes("last display retained")')
        assert page.locator('#sports-targets').is_hidden()==('/' in identity)
    page.unroute('**/api/tablet/**')
    checks.append('IndexedDB offline reload isolates overview and multiple focused sports frames')
    # Cancellation must reconcile a cached target even before its full refresh commits.
    page.evaluate("navigate('sports','india_men')")
    page.emulate_media(reduced_motion='reduce')
    wait_frame('sports/india_men')
    page.emulate_media(reduced_motion='no-preference')
    # Hold an actual focused decode across superseding navigation, then release it.
    page.evaluate("""()=>{window.originalReadyImage=readyImage;window.decodeHeld=false;
        readyImage=async blob=>{const decoded=await originalReadyImage(blob);
            window.decodeHeld=true;await new Promise(resolve=>window.releaseDecode=resolve);return decoded;};
        frames.delete('sports/cal_fb');navigate('sports','cal_fb');}""")
    page.wait_for_function('window.decodeHeld')
    page.evaluate("navigate('sports','man_utd')")
    wait_frame('sports/man_utd')
    page.evaluate('()=>{readyImage=originalReadyImage;releaseDecode();}')
    page.wait_for_timeout(300)
    assert page.evaluate("!frames.has('sports/cal_fb')&&visiblePage==='sports/man_utd'")
    checks.append('reduced-motion cancellation and superseded focused decoder retain navigation ownership')
    page.evaluate("navigate('sports')")
    wait_frame('sports')
    wake()
    page.locator('#reload').click()
    page.wait_for_function("document.querySelector('#status').innerText==='Sports'")
    assert 204 in frame_responses
    sports_requests=[q for path,q in api_requests if q.get('page')==['sports']]
    assert any('sport' not in q for q in sports_requests)
    assert {q['sport'][0] for q in sports_requests if 'sport' in q}=={'india_men','man_utd','cal_fb','f1'}
    assert all(q.get('k')==['local-test'] for _,q in api_requests)
    assert all(path=='/api/tablet/frame' for path,_ in api_requests)
    assert any(q.get('v')==['4470:sports:man_utd'] and q.get('sport')==['man_utd'] for q in sports_requests)
    checks.append('authenticated frame requests include sport and focus-specific conditional versions; no sports JSON requests')
    assert page.evaluate('''async()=>{const original=frames.get('sports');const discarded=await rememberFrame('sports',original.blob,'obsolete',()=>false);return discarded===null&&frames.get('sports')===original;}''')
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
    print(json.dumps({'checks':checks,'errors':errors},indent=2))
    browser.close()
